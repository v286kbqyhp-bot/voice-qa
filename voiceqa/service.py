"""Reference HTTP adapter, independent from orchestration and YAML."""
from __future__ import annotations

import base64
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
import uuid
import jsonschema

from .adapters import NativeAdapter, wav_info
from .core import Blocked, ROOT, digest, load_yaml

MAX_BODY = 16 * 1024 * 1024


class AdapterServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, root: Path, token=None, adapter_factory=NativeAdapter, config=None, analyzer=None):
        if address[0] not in ('127.0.0.1', 'localhost', '::1') and not token:
            raise ValueError('non-loopback binding requires VQA_API_TOKEN')
        super().__init__(address, Handler)
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.factory = adapter_factory
        self.config = config or {}
        self.analyzer = analyzer
        self.schemas = load_yaml(ROOT / 'docs/openapi.yaml')['components']['schemas']
        self.assets = {}
        self.sessions = {}
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.watchdog = threading.Thread(target=self.expire_sessions, daemon=True)
        self.watchdog.start()

    def expire_sessions(self):
        while not self.closed.wait(0.5):
            with self.lock:
                for session in self.sessions.values():
                    if not session['stopped'] and time.monotonic() > session['deadline']:
                        session['result'] = session['adapter'].finish()
                        session['stopped'] = True

    def server_close(self):
        self.closed.set()
        with self.lock:
            for session in self.sessions.values():
                if not session['stopped']:
                    session['adapter'].finish()
                    session['stopped'] = True
        super().server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Request bodies, auth and transport identities never enter access logs.

    def do_GET(self):
        self.handle_api()

    def do_POST(self):
        self.handle_api()

    def handle_api(self):
        try:
            if self.server.token and not hmac.compare_digest(self.headers.get('Authorization', ''),
                                                             'Bearer ' + self.server.token):
                return self.respond(401, {'code': 'UNAUTHORIZED', 'message': 'adapter token required'})
            size = int(self.headers.get('Content-Length', 0))
            if not 0 <= size <= MAX_BODY:
                return self.respond(413, {'code': 'BODY_TOO_LARGE', 'message': 'maximum 16 MiB'})
            data = json.loads(self.rfile.read(size)) if size else {}
            if not isinstance(data, dict):
                raise ValueError('JSON object required')
            with self.server.lock:
                status, response = self.dispatch(data)
            self.respond(status, response)
        except Blocked as exc:
            self.respond(409, {'code': 'CAPABILITY_BLOCKED', 'message': str(exc)})
        except jsonschema.ValidationError:
            self.respond(400, {'code': 'INVALID_REQUEST', 'message': 'request does not match OpenAPI schema'})
        except (ValueError, KeyError, TypeError) as exc:
            self.respond(400, {'code': 'INVALID_REQUEST', 'message': str(exc)[:180]})
        except Exception as exc:
            self.respond(500, {'code': 'ADAPTER_ERROR', 'message': type(exc).__name__})

    def respond(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self, data):
        if self.command == 'GET' and self.path == '/v1/capabilities':
            return 200, self.server.factory().capabilities()
        if self.command != 'POST':
            return 404, {'code': 'NOT_FOUND', 'message': 'unknown endpoint'}
        def validate(name):
            jsonschema.validate(data, self.server.schemas[name])
        if self.path == '/v1/assets':
            validate('AssetRequest')
            if set(data) - {'text', 'voice', 'rate', 'wav_base64'}:
                raise ValueError('unsupported asset field; host filesystem paths are not accepted')
            asset_id = uuid.uuid4().hex
            output = self.server.root / 'assets' / (asset_id + '.wav')
            output.parent.mkdir(exist_ok=True)
            if data.get('wav_base64'):
                output.write_bytes(base64.b64decode(data['wav_base64'], validate=True))
                asset = wav_info(output)
            else:
                if not isinstance(data.get('text'), str) or not 1 <= len(data['text']) <= 2000:
                    raise ValueError('text must contain 1..2000 characters')
                asset = self.server.factory().prepare(data, output)
            self.server.assets[asset_id] = asset
            return 201, {k: v for k, v in {**asset, 'asset_id': asset_id,
                         'wav_base64': base64.b64encode(output.read_bytes()).decode()}.items() if k != 'path'}
        if self.path == '/v1/sessions':
            validate('SessionRequest')
            if any(not s['stopped'] for s in self.server.sessions.values()):
                raise Blocked('audio resource already has an active session')
            cfg = data.get('config', {})
            allowed = {'require_device', 'input_device', 'output_device', 'sample_rate', 'cloud_sn_suffix',
                       'operator', 'bindings'}
            if not isinstance(cfg, dict) or set(cfg) - allowed:
                raise ValueError('only portable session config fields are accepted; ADB routing is host-side')
            session_id = uuid.uuid4().hex
            output = self.server.root / session_id
            output.mkdir()
            (output / 'audio').mkdir()
            adapter = self.server.factory()
            metadata = adapter.begin(output, {**self.server.config, **cfg})
            self.server.sessions[session_id] = {'adapter': adapter, 'output': output, 'stopped': False,
                                              'deadline': time.monotonic() + 180, 'requests': {}}
            return 201, {'session_id': session_id, 'metadata': metadata, 'lease_s': 180}
        match = re.fullmatch(r'/v1/sessions/([a-f0-9]{32})/(play|wait-event|observe|stop|analyze)', self.path)
        if not match or match[1] not in self.server.sessions:
            return 404, {'code': 'NOT_FOUND', 'message': 'unknown session/endpoint'}
        session = self.server.sessions[match[1]]
        adapter = session['adapter']
        action = match[2]
        if session['stopped'] and action not in ('stop', 'analyze'):
            raise Blocked('session closed or lease expired')
        if action == 'play':
            validate('PlayRequest')
            request_id = data.get('request_id')
            if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 100):
                raise ValueError('request_id must contain 1..100 characters')
            asset_id = data['asset_id']
            if request_id in session['requests']:
                previous = session['requests'][request_id]
                if previous['asset_id'] != asset_id or previous['result'] is None:
                    raise Blocked('request reused or prior playback outcome uncertain; do not repeat')
                return 200, previous['result']
            if request_id:
                session['requests'][request_id] = {'asset_id': asset_id, 'result': None}
            result = adapter.play(self.server.assets[asset_id])
            if request_id:
                session['requests'][request_id]['result'] = result
            return 200, result
        if action == 'analyze':
            if not session['stopped']:
                raise Blocked('stop capture before offline analysis')
            if self.server.analyzer is None:
                raise Blocked('ASR/speaker/semantic analyzer not configured')
            from .analysis import validate_analysis
            output = session['output']
            result = self.server.analyzer.analyze({}, output / 'audio/mic_raw.wav', output / 'events.jsonl')
            return 200, validate_analysis(result, output / 'audio/mic_raw.wav')
        if action == 'wait-event':
            validate('WaitRequest')
            if not isinstance(data.get('event'), str) or not 0 < data.get('timeout_s', 30) <= 60:
                raise ValueError('event and timeout_s 0..60 required')
            return 200, adapter.wait_event(data)
        if action == 'observe':
            validate('ObserveRequest')
            seconds = data['seconds']
            if not isinstance(seconds, (float, int)) or not 0 <= seconds <= 60:
                raise ValueError('seconds must be 0..60')
            return 200, adapter.observe(seconds)
        if not session['stopped']:
            session['result'] = adapter.finish()
            session['stopped'] = True
        files = []
        for p in session['output'].rglob('*'):
            if p.is_file():
                files.append({'path': p.relative_to(session['output']).as_posix(), 'sha256': digest(p),
                              'base64': base64.b64encode(p.read_bytes()).decode()})
        return 200, {**session.get('result', {}), 'files': files}
