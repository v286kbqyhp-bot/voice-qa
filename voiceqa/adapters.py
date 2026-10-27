from __future__ import annotations

import array
import base64
import json
from pathlib import Path
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
import wave

from .core import Blocked, append, digest, dump, safe_path


def wav_info(path: Path) -> dict:
    with wave.open(str(path), 'rb') as f:
        if f.getsampwidth() != 2 or f.getcomptype() != 'NONE' or f.getnframes() == 0:
            raise Blocked('asset must be nonempty PCM16 WAV')
        return {'duration_s': f.getnframes() / f.getframerate(), 'sample_rate': f.getframerate(),
                'channels': f.getnchannels(), 'sha256': digest(path), 'path': str(path)}


class MacSay:
    def synthesize(self, text: str, output: Path, voice='Tingting', rate=175):
        subprocess.run(['say', '-v', voice, '-r', str(rate), '-o', str(output),
                        '--data-format=LEI16@16000', text], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class WindowsSapi:
    def synthesize(self, text: str, output: Path, voice=None, rate=0):
        env = dict(os.environ, VQA_TEXT=text, VQA_OUTPUT=str(output), VQA_VOICE=voice or '')
        script = ('Add-Type -AssemblyName System.Speech; '
                  '$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                  'if ($env:VQA_VOICE) {$s.SelectVoice($env:VQA_VOICE)}; '
                  '$s.SetOutputToWaveFile($env:VQA_OUTPUT); $s.Speak($env:VQA_TEXT); $s.Dispose()')
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                       env=env, check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class PortAudioPlayer:
    def play(self, asset, output_device=None):
        import sounddevice as sd
        errors = []
        with wave.open(asset['path'], 'rb') as f:
            with sd.RawOutputStream(samplerate=f.getframerate(), channels=f.getnchannels(),
                                    dtype='int16', device=output_device) as stream:
                while block := f.readframes(2048):
                    if stream.write(block):
                        errors.append('output_underflow')
        return {'backend': 'portaudio', 'errors': errors}


class MacFilePlayer:
    def play(self, asset, output_device=None):
        import sounddevice as sd
        if output_device is not None and output_device != sd.default.device[1]:
            raise Blocked('afplay uses system default output; use a different injected player for explicit routing')
        subprocess.run(['/usr/bin/afplay', asset['path']], check=True, timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {'backend': 'afplay', 'output_route': 'system_default', 'errors': []}


class PcmAudio:
    """One continuous input stream; independent output streams never mute the input."""

    def __init__(self, player=None):
        self.player = player or (MacFilePlayer() if platform.system() == 'Darwin' else PortAudioPlayer())

    def start(self, output: Path, config: dict):
        import sounddevice as sd
        self.output_device = config.get('output_device')
        self.rate = config.get('sample_rate', 16000)
        self.errors = []
        self.frames = 0
        self.peak = 0
        self.blocks = queue.Queue(maxsize=1024)
        self.wave = wave.open(str(output), 'wb')
        self.wave.setparams((1, 2, self.rate, 0, 'NONE', 'not compressed'))
        self.anchor = time.monotonic()

        def write():
            try:
                while True:
                    block = self.blocks.get()
                    if block is None:
                        break
                    self.wave.writeframesraw(block)
                    self.frames += len(block) // 2
                    values = array.array('h', block)
                    self.peak = max(self.peak, max(map(abs, values), default=0))
            except Exception as exc:
                self.errors.append(type(exc).__name__)

        def capture(data, frames, timing, status):
            if status:
                self.errors.append(str(status))
            try:
                self.blocks.put_nowait(bytes(data))
            except queue.Full:
                self.errors.append('capture_queue_overflow')

        self.writer = threading.Thread(target=write, daemon=True)
        self.stream = sd.RawInputStream(samplerate=self.rate, channels=1, dtype='int16',
                                       device=config.get('input_device'), callback=capture)
        self.writer.start()
        try:
            self.stream.start()
        except BaseException:
            self.stream.close()
            self.blocks.put(None)
            self.writer.join(timeout=5)
            self.wave.close()
            raise
        return {'recording_started': True, 'host_monotonic_s': self.anchor,
                'sample_rate': self.rate, 'channels': 1,
                'input_device': str(sd.query_devices(config.get('input_device'), 'input')['name']),
                'output_device': str(sd.query_devices(self.output_device, 'output')['name'])}

    def play(self, asset):
        started = time.monotonic()
        result = self.player.play(asset, self.output_device)
        self.errors.extend(result.get('errors', []))
        return {'start_monotonic_s': started, 'end_monotonic_s': time.monotonic(),
                'timing_kind': 'host_stream_observation_not_physical_onset',
                'asset_sha256': asset['sha256'], **result}

    def stop(self):
        self.stream.stop()
        self.stream.close()
        self.blocks.put(None, timeout=5)
        self.writer.join(timeout=10)
        self.wave.close()
        return {'frames': self.frames, 'peak': self.peak, 'sample_rate': self.rate,
                'errors': self.errors, 'valid_capture': bool(self.frames and self.peak and not self.errors),
                'speaker_attribution': 'unverified', 'physical_onset_timing': 'unverified'}


def redact(text):
    text = re.sub(r'(?i)(Bearer\s+)[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:token|password|secret|authorization|credential|wifi_password)\s*[=:]\s*)[^\s,;]+',
                  r'\1[REDACTED]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED_JWT]', text)
    return text


TAGS = ['P003VoiceRealCanary', 'P003LiveKitRoom', 'P003VoiceUiState', 'P003VoiceExit',
        'P003RtcReceipt', 'P003WakePrompt', 'GodotBridge']


def parse_log(line, received_s, line_number):
    match = re.search(r'\s([A-Za-z][A-Za-z0-9_]+)\s*:\s*(.*)$', line)
    if not match:
        return None
    tag, body = match.groups()
    fields = dict(re.findall(r'\b([a-z_]+)=([A-Za-z0-9_.:-]+)', body))
    event = fields.get('state') or fields.get('action') or fields.get('event')
    if not event:
        return None
    # Names reflect literal logs. No inferred search-body or acoustic events.
    return {'event': event, 'tag': tag, 'trace': fields.get('trace'), 'fields': fields,
            'received_monotonic_s': received_s, 'raw_line': line_number, 'evidence_layer': 'device_log'}


class AdbEvents:
    def start(self, output: Path, config: dict):
        adb = config.get('adb') or shutil.which('adb')
        if not adb:
            candidate = Path.home() / 'Library/Android/sdk/platform-tools/adb'
            adb = str(candidate) if candidate.exists() else None
        if not adb:
            raise Blocked('adb unavailable; set config.adb')
        serial = config.get('adb_serial')
        def read(args):
            return subprocess.check_output([adb, *args], text=True, timeout=10).replace('\r', '')
        devices = [l.split()[0] for l in read(['devices']).splitlines()[1:]
                   if len(l.split()) >= 2 and l.split()[1] == 'device']
        if serial is None and len(devices) == 1:
            serial = devices[0]
        if serial not in devices:
            raise Blocked('select exactly one online device in local config')
        package = config.get('package', 'com.futurus.p003.launcher')
        if not re.fullmatch(r'[A-Za-z0-9_.]+', package):
            raise ValueError('invalid package')
        package_info = read(['-s', serial, 'shell', 'dumpsys', 'package', package])
        active_path = read(['-s', serial, 'shell', 'pm', 'path', package]).strip()
        version = re.search(r'versionCode=(\d+).*?versionName=([^\s]+)', package_info, re.S)
        if not version or not active_path.startswith('package:'):
            raise Blocked('cannot read installed package version')
        self.events = []
        self.lock = threading.Lock()
        self.error = None
        self.seen = set()
        # adb shell joins arguments before invoking Android's shell. Keep this
        # constant format quoted on the remote side (no plan text enters shell).
        stamp = read(['-s', serial, 'shell', "date '+%m-%d %H:%M:%S'"]).strip()
        self.process = subprocess.Popen([adb, '-s', serial, 'logcat', '-v', 'threadtime',
                                         '-T', stamp + '.000', *[x + ':I' for x in TAGS], '*:S'],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.output = output
        def collect():
            try:
                with (output / 'logs/device_redacted.log').open('w', encoding='utf-8') as f:
                    for n, line in enumerate(self.process.stdout, 1):
                        received = time.monotonic()
                        f.write(redact(line))
                        f.flush()
                        parsed = parse_log(line, received, n)
                        if parsed:
                            key = line.strip()
                            if key in self.seen:
                                continue
                            self.seen.add(key)
                            with self.lock:
                                self.events.append(parsed)
                            append(output / 'events.jsonl', parsed)
            except Exception as exc:
                self.error = type(exc).__name__
        (output / 'logs').mkdir(exist_ok=True)
        self.thread = threading.Thread(target=collect, daemon=True)
        self.thread.start()
        return {'version_code': version[1], 'version_name': version[2], 'version_source': 'adb_dumpsys_active_first',
                'adb_transport_hash': __import__('hashlib').sha256(serial.encode()).hexdigest()[:12],
                'cloud_sn_suffix': config.get('cloud_sn_suffix', '待现场核对'), 'log_start_device_time': stamp}

    def all_events(self):
        with self.lock:
            return list(self.events)

    def healthy(self):
        if self.error or self.process.poll() is not None:
            raise Blocked('device log stream interrupted')

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=10)
        self.thread.join(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()


def match_event(event, params, trace=None):
    return (event['event'] == params['event']
            and (not params.get('same_trace') or trace is not None and event.get('trace') == trace)
            and all(event.get('fields', {}).get(k) == str(v) for k, v in params.get('where', {}).items()))


class NativeAdapter:
    def __init__(self, tts=None, audio=None, event_source=None):
        self.tts = tts or (MacSay() if platform.system() == 'Darwin' else WindowsSapi()
                           if platform.system() == 'Windows' else None)
        self.audio = audio or PcmAudio()
        self.logs = event_source or AdbEvents()
        self.trace = None
        self.recording = False
        self.logging = False
        self.cursor = 0

    def capabilities(self):
        return {'api_version': '1.0', 'adapter': 'native', 'tts': self.tts is not None,
                'audio_capture': True, 'audio_play': True, 'device_events': 'adb',
                'asr': False, 'speaker_attribution': False, 'verified_search_body_event': False,
                'playback_backend': type(self.audio.player).__name__ if isinstance(self.audio, PcmAudio) else 'injected'}

    def prepare(self, params, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        if params.get('file'):
            source = Path(params['file'])
            if not source.is_file():
                raise Blocked(f'missing audio asset: {source.name}')
            shutil.copyfile(source, output)
        elif params.get('text') and self.tts:
            opts = {k: params[k] for k in ('voice', 'rate') if k in params}
            self.tts.synthesize(params['text'], output, **opts)
        else:
            raise Blocked('no WAV asset or available TTS provider')
        return {**wav_info(output), 'text': params.get('text'), 'synthetic_fixture': False}

    def begin(self, output, config):
        self.output = output
        metadata = {}
        try:
            if config.get('require_device', True):
                metadata.update(self.logs.start(output, config))
                self.logging = True
            metadata['audio'] = self.audio.start(output / 'audio/mic_raw.wav', config)
            self.recording = True
            self.anchor = time.monotonic()
            return metadata
        except BaseException:
            if self.logging:
                self.logs.stop()
                self.logging = False
            raise

    def play(self, asset):
        if not self.recording:
            raise Blocked('continuous recording is not running')
        if self.logging:
            self.logs.healthy()
        return self.audio.play(asset)

    def wait_event(self, params):
        if params.get('require_audible_device_speech'):
            raise Blocked('speaker-attributed acoustic trigger not implemented by this adapter')
        if not self.logging:
            raise Blocked('device event source not active')
        start = time.monotonic()
        timeout = params.get('timeout_s', 30)
        while time.monotonic() - start < timeout:
            self.logs.healthy()
            events = self.logs.all_events()
            for i in range(self.cursor, len(events)):
                e = events[i]
                self.cursor = i + 1
                if e['received_monotonic_s'] < self.anchor:
                    continue
                if match_event(e, params, self.trace):
                    if params.get('bind_trace'):
                        self.trace = e.get('trace')
                    return e
            time.sleep(0.02)
        raise Blocked('execution_timeout: no matching event; not a product deadline failure')

    def observe(self, seconds):
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            if self.logging:
                self.logs.healthy()
            time.sleep(min(0.05, max(0, seconds - (time.monotonic() - start))))
        return {'observed_s': time.monotonic() - start}

    def finish(self):
        result = {}
        try:
            if self.recording:
                result['audio'] = self.audio.stop()
                self.recording = False
        finally:
            if self.logging:
                self.logs.stop()
                self.logging = False
        return result


class ReplayAdapter:
    def __init__(self, events=None):
        self.events = events or []
        self.now = 0.0
        self.trace = None
        self.used = set()
        self.played = []

    def capabilities(self):
        return {'api_version': '1.0', 'adapter': 'replay', 'real_device': False}

    def prepare(self, params, output):
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), 'wb') as f:
            f.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            f.writeframes(b'\0\0' * 1600)
        return {**wav_info(output), 'text': params.get('text'), 'synthetic_fixture': True}

    def begin(self, output, config):
        return {'mode': 'fixture_replay', 'real_device': False}

    def play(self, asset):
        start = self.now
        self.now += asset['duration_s']
        self.played.append(asset)
        return {'start_s': start, 'end_s': self.now, 'simulated': True}

    def wait_event(self, params):
        end = self.now + params.get('timeout_s', 30)
        for i, event in sorted(enumerate(self.events), key=lambda p: p[1]['at_s']):
            key = event.get('id', i)
            if key in self.used or event['at_s'] < self.now or event['at_s'] > end:
                continue
            if event.get('occurred_s', event['at_s']) < self.now:
                continue
            if match_event(event, params, self.trace):
                self.used.add(key)
                self.now = event['at_s']
                if params.get('bind_trace'):
                    self.trace = event.get('trace')
                return {**event, 'simulated': True}
        self.now = end
        raise Blocked('execution_timeout: fixture contains no current matching event')

    def observe(self, seconds):
        self.now += seconds
        return {'observed_s': seconds, 'simulated': True}

    def finish(self):
        return {'real_device': False, 'fixture_replay': True}


class HttpAdapter:
    """The runner uses exactly this contract for any remote device/environment."""
    def __init__(self, url, token=None):
        if not url.startswith(('http://', 'https://')):
            raise ValueError('adapter URL must use HTTP(S)')
        self.url = url.rstrip('/')
        self.token = token

    def request(self, method, path, data=None):
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        req = urllib.request.Request(self.url + path,
                                     None if data is None else json.dumps(data).encode(),
                                     headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            error = json.loads(exc.read())
            raise Blocked(f"adapter {error.get('code')}: {error.get('message')}") from None

    def capabilities(self):
        return self.request('GET', '/v1/capabilities')

    def prepare(self, params, output):
        params = dict(params)
        if 'file' in params:
            params['wav_base64'] = base64.b64encode(Path(params.pop('file')).read_bytes()).decode()
        response = self.request('POST', '/v1/assets', params)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(base64.b64decode(response.pop('wav_base64'), validate=True))
        info = wav_info(output)
        if info['sha256'] != response['sha256']:
            raise Blocked('remote asset hash mismatch')
        return {**response, **info}

    def begin(self, output, config):
        self.output = output
        portable = {k: v for k, v in config.items() if k in
                    {'require_device', 'input_device', 'output_device', 'sample_rate', 'cloud_sn_suffix', 'operator'}}
        response = self.request('POST', '/v1/sessions', {'config': portable})
        self.session = response['session_id']
        return response

    def play(self, asset):
        return self.request('POST', f'/v1/sessions/{self.session}/play',
                            {'asset_id': asset['asset_id'], 'request_id': uuid.uuid4().hex})

    def wait_event(self, params):
        return self.request('POST', f'/v1/sessions/{self.session}/wait-event', params)

    def observe(self, seconds):
        return self.request('POST', f'/v1/sessions/{self.session}/observe', {'seconds': seconds})

    def finish(self):
        if not hasattr(self, 'session'):
            return {}
        response = self.request('POST', f'/v1/sessions/{self.session}/stop', {})
        for item in response.pop('files', []):
            if not (item['path'].startswith(('audio/', 'logs/')) or item['path'] == 'events.jsonl'):
                raise Blocked('remote evidence path overlaps orchestration files')
            p = safe_path(self.output, item['path'])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(base64.b64decode(item['base64'], validate=True))
            if digest(p) != item['sha256']:
                raise Blocked('remote evidence hash mismatch')
        return response
