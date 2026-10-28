from __future__ import annotations

import array
import base64
from collections import deque
import json
from pathlib import Path
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
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
        self.level_history=deque(maxlen=600)
        self.capture_stopping=threading.Event()
        self.blocks = queue.Queue(maxsize=1024)
        self.wave_file=Path(output).open('wb',buffering=0)
        self.wave = wave.open(self.wave_file, 'wb')
        self.wave.setparams((1, 2, self.rate, 0, 'NONE', 'not compressed'))
        self.anchor = time.monotonic()

        def write():
            try:
                while True:
                    block = self.blocks.get()
                    if block is None:
                        break
                    # Keep a readable header even if the native driver hangs
                    # during shutdown and the child process must be terminated.
                    self.wave.writeframes(block)
                    self.frames += len(block) // 2
                    values = array.array('h', block)
                    self.peak = max(self.peak, max(map(abs, values), default=0))
                    rms=(sum(v*v for v in values)/max(1,len(values)))**0.5
                    self.level_history.append((time.monotonic(),round(rms,2)))
            except Exception as exc:
                self.errors.append(type(exc).__name__)

        def capture(data, frames, timing, status):
            if self.capture_stopping.is_set():
                raise sd.CallbackStop
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
            self.wave_file.close()
            raise
        return {'recording_started': True, 'host_monotonic_s': self.anchor,
                'capture_implementation_sha256':digest(Path(__file__)),
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
        self.capture_stopping.set()
        def shutdown():
            try:
                self.stream.abort()
                self.stream.close()
            except Exception as exc:
                self.errors.append('capture_shutdown_'+type(exc).__name__)
        closing=threading.Thread(target=shutdown,daemon=True)
        closing.start()
        closing.join(timeout=2)
        if closing.is_alive():
            self.errors.append('capture_driver_shutdown_timeout')
        self.blocks.put(None, timeout=5)
        self.writer.join(timeout=10)
        if self.writer.is_alive():
            self.errors.append('capture_writer_shutdown_timeout')
        else:
            self.wave.close()
            if getattr(self,'wave_file',None):self.wave_file.close()
        return {'frames': self.frames, 'peak': self.peak, 'sample_rate': self.rate,
                'errors': self.errors, 'valid_capture': bool(self.frames and self.peak and not self.errors),
                'speaker_attribution': 'unverified', 'physical_onset_timing': 'unverified'}

    def levels(self):
        history=list(self.level_history)
        recent=[rms for stamp,rms in history if time.monotonic()-stamp<0.18]
        return {'rms':sum(recent)/max(1,len(recent)),'updated_monotonic_s':history[-1][0] if history else 0,
                'history':history[-400:],'frames':self.frames,'errors':list(self.errors)}


class ProcessAudio:
    """Own the driver on a child process main thread, with bounded IPC."""
    def __init__(self):
        self.process = None
        self.responses = queue.Queue()
        self.backend_name = 'MacFilePlayer' if platform.system() == 'Darwin' else 'PortAudioPlayer'

    def _call(self, method, *args):
        if self.process is None:
            raise Blocked('audio worker is not running')
        try:
            self.process.stdin.write(json.dumps({'method': method, 'args': args}) + '\n')
            self.process.stdin.flush()
            response = self.responses.get(timeout=130 if method == 'play' else 30)
        except (queue.Empty, BrokenPipeError):
            self.close(force=True)
            raise Blocked('audio worker timeout/disconnect; incomplete capture retained') from None
        if response is None:
            self.close(force=True)
            raise Blocked('audio worker exited before completing action')
        if 'error' in response:
            raise Blocked('audio worker: ' + response['error'])
        return response['result']

    def start(self, output, config):
        from .core import ROOT
        self.responses = queue.Queue()
        logs = output.parent.parent / 'logs'
        logs.mkdir(exist_ok=True)
        self.stderr = (logs / 'audio_worker.log').open('w', encoding='utf-8')
        self.process = subprocess.Popen([sys.executable, '-u', '-m', 'voiceqa.audio_worker'], cwd=ROOT,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                                        text=True, encoding='utf-8')
        def read():
            try:
                for line in self.process.stdout:
                    self.responses.put(json.loads(line))
            finally:
                self.responses.put(None)
        self.reader = threading.Thread(target=read, daemon=True)
        self.reader.start()
        portable = {k:v for k,v in config.items() if k in {'input_device','output_device','sample_rate'}}
        try:
            return self._call('start', str(output.resolve()), portable)
        except BaseException:
            self.close(force=True)
            raise

    def play(self, asset):
        return self._call('play', asset)

    def levels(self):
        return self._call('levels')

    def stop(self):
        try:
            return self._call('stop')
        finally:
            self.close()

    def close(self, force=False):
        if self.process is None:
            return
        if force and self.process.poll() is None:
            self.process.terminate()
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=3)
        self.process.stdout.close()
        self.stderr.close()
        self.process = None


def redact(text):
    text = re.sub(r'(?i)(Bearer\s+)[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:token|password|secret|authorization|credential|wifi_password)\s*[=:]\s*)[^\s,;]+',
                  r'\1[REDACTED]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[REDACTED_JWT]', text)
    text = re.sub(r'\bBJP003[A-Za-z0-9]+\b',lambda m:'<SN末8位:'+m[0][-8:]+'>',text)
    return text


TAGS = ['P003VoiceRealCanary', 'P003LiveKitRoom', 'P003VoiceUiState', 'P003VoiceExit',
        'P003RtcReceipt', 'P003WakePrompt', 'GodotBridge', 'GodotBridgePlugin']


def parse_log(line, received_s, line_number):
    match = re.search(r'\s([A-Za-z][A-Za-z0-9_]+)\s*:\s*(.*)$', line)
    if not match:
        return None
    tag, body = match.groups()
    if tag=='GodotBridgePlugin' and 'payload=' in body:
        try:
            payload=json.loads(body.split('payload=',1)[1])
            data=payload.get('data',{})
            if not isinstance(data,dict) or not str(payload.get('event','')).startswith('voice.'):
                return None
            fields={k:'[REDACTED]' if re.search(r'token|password|secret|authorization|credential',k,re.I)
                    else redact(str(v).lower() if isinstance(v,bool) else str(v))
                    for k,v in data.items() if isinstance(v,(str,int,float,bool))}
            return {'event':payload['event'],'tag':tag,'trace':fields.get('trace_id'),'fields':fields,
                    'device_timestamp_ms':payload.get('ts_ms'),'event_id':payload.get('event_id'),
                    'received_monotonic_s':received_s,'raw_line':line_number,'evidence_layer':'device_log'}
        except (ValueError,TypeError,AttributeError):
            return None
    fields = dict(re.findall(r'\b([a-z_]+)=([A-Za-z0-9_.:-]+)', body))
    fields = {k:'[REDACTED]' if re.search(r'token|password|secret|authorization|credential',k,re.I) else redact(v)
              for k,v in fields.items()}
    event = fields.get('state') or fields.get('action') or fields.get('event')
    if event is None and body.startswith(('remote_audio ','control ','worker_playout ')):
        event=body.split(' ',1)[0]
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
        self.adb=adb
        self.serial=serial
        package = config.get('package', 'com.futurus.p003.launcher')
        if not re.fullmatch(r'[A-Za-z0-9_.]+', package):
            raise ValueError('invalid package')
        self.package=package
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

    def exit_voice(self, trace):
        """One Android Back navigation, scoped to an active Launcher room."""
        from .lifecycle import session_status
        if session_status(self.all_events(),trace)['state']!='active':
            raise Blocked('UI exit requires the bound room to be active')
        activity=subprocess.check_output([self.adb,'-s',self.serial,'shell','dumpsys','activity','activities'],text=True,timeout=10)
        resumed=[s for s in activity.splitlines() if 'mResumedActivity' in s or 'topResumedActivity' in s]
        if not any(self.package+'/' in s for s in resumed):
            raise Blocked('UI exit requires Launcher in foreground')
        screenshots=self.output/'screens';screenshots.mkdir(exist_ok=True)
        stamp=str(time.monotonic_ns())
        evidence={}
        for position in ('before','after'):
            if position=='after':
                subprocess.run([self.adb,'-s',self.serial,'shell','input','keyevent','4'],check=True,timeout=10,capture_output=True)
            target=screenshots/(stamp+'-exit-'+position+'.png')
            target.write_bytes(subprocess.check_output([self.adb,'-s',self.serial,'exec-out','screencap','-p'],timeout=10))
            evidence[position]={'file':target.relative_to(self.output).as_posix(),'sha256':__import__('hashlib').sha256(target.read_bytes()).hexdigest()}
        return {'action':'exit_voice','navigation':'android_back','trace':trace,'command_completed':True,
                'product_exit_verified':False,'screenshots':evidence,'observed_monotonic_s':time.monotonic()}

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
    return (event['event'] in [params['event'],*params.get('alternative_events',[])]
            and (not params.get('same_trace') or trace is not None and event.get('trace') == trace)
            and all(event.get('fields', {}).get(k) == (str(v).lower() if isinstance(v,bool) else str(v)) for k, v in params.get('where', {}).items())
            and (not params.get('text_contains_any') or any(x in event.get('fields',{}).get('text','') for x in params['text_contains_any'])))


def rejection_reason(event, params, trace=None):
    if event['event'] not in [params['event'],*params.get('alternative_events',[])]:
        return 'event_name_mismatch'
    if params.get('same_trace') and trace is None:
        return 'no_bound_trace'
    if params.get('same_trace') and event.get('trace') != trace:
        return 'trace_mismatch'
    return 'field_mismatch'


class Observable:
    _observer = None

    def set_observer(self, observer):
        self._observer = observer

    def _notify(self, event, **details):
        if self._observer:
            self._observer(event, **details)


class NativeAdapter(Observable):
    def __init__(self, tts=None, audio=None, event_source=None):
        self.tts = tts or (MacSay() if platform.system() == 'Darwin' else WindowsSapi()
                           if platform.system() == 'Windows' else None)
        self.audio = audio or ProcessAudio()
        self.logs = event_source or AdbEvents()
        self.trace = None
        self.recording = False
        self.logging = False
        self.cursor = 0
        self.turn_floor=0
        self.last_play_end=0
        self.sound_threshold=80

    def capabilities(self):
        return {'api_version': '1.0', 'adapter': 'native', 'tts': self.tts is not None,
                'audio_capture': True, 'audio_play': True, 'device_events': 'adb',
                'asr': False, 'speaker_attribution': False, 'verified_search_body_event': False,
                'search_execution_receipts': False,
                'device_ui_exit': callable(getattr(type(self.logs),'exit_voice',None)),
                'event_alternatives': True,
                'interaction_wait': callable(getattr(type(self.audio),'levels',None)),
                'playback_backend': getattr(self.audio, 'backend_name', 'injected')}

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
        self.phase_review_file=config.get('phase_review_file')
        self.phase_rules=config.get('phase_rules',[])
        metadata = {}
        try:
            if config.get('require_device', True):
                metadata.update(self.logs.start(output, config))
                self.logging = True
            metadata['audio'] = self.audio.start(output / 'audio/mic_raw.wav', config)
            self.recording = True
            self.anchor = time.monotonic()
            if callable(getattr(type(self.audio),'levels',None)):
                samples=[]
                for _ in range(6):
                    time.sleep(0.1)
                    samples.append(self.audio.levels()['rms'])
                samples.sort()
                self.sound_threshold=max(65,min(400,samples[len(samples)//2]*2.5+30))
                metadata['sound_activity_threshold']=self.sound_threshold
                metadata['speaker_attribution']='unverified; activity assists scheduling only'
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
        self.turn_floor=time.monotonic()
        result=self.audio.play(asset)
        self.last_play_end=time.monotonic()
        return result

    def wait_event(self, params):
        if params.get('require_audible_device_speech'):
            raise Blocked('speaker-attributed acoustic trigger not implemented by this adapter')
        if not self.logging:
            raise Blocked('device event source not active')
        start = time.monotonic()
        timeout = params.get('timeout_s', 30)
        self._notify('wait_started',expected=params,bound_trace=self.trace,timeout_s=timeout)
        while time.monotonic() - start < timeout:
            self.logs.healthy()
            events = self.logs.all_events()
            for i in range(self.cursor, len(events)):
                e = events[i]
                self.cursor = i + 1
                if e['received_monotonic_s'] < self.anchor:
                    self._notify('event_ignored',reason='received_before_run_anchor',received=e)
                    continue
                if params.get('after_last_play') and e['received_monotonic_s']<self.turn_floor:
                    continue
                if match_event(e, params, self.trace):
                    if params.get('bind_trace'):
                        self.trace = e.get('trace')
                    self._notify('event_matched',received=e,bound_trace=self.trace,layer='device_log')
                    return e
                self._notify('event_rejected',reason=rejection_reason(e,params,self.trace),expected=params,received=e)
            time.sleep(0.02)
        self._notify('wait_timeout',expected=params,timeout_s=timeout,bound_trace=self.trace)
        raise Blocked('execution_timeout: no matching event; not a product deadline failure')

    def wait_interaction(self,kind,params):
        from .interaction import wait_interaction
        return wait_interaction(self,kind,params)

    def session_status(self):
        from .lifecycle import session_status
        if not self.logging:
            raise Blocked('device lifecycle observation unavailable')
        self.logs.healthy()
        return session_status(self.logs.all_events(), self.trace)

    def device_action(self, params):
        if params.get('action')!='exit_voice' or not self.logging or self.trace is None:
            raise Blocked('device UI action requires exit_voice and a bound room')
        return self.logs.exit_voice(self.trace)

    def play_cleanup(self, asset, params):
        """Budget the complete file against the device's literal idle countdown."""
        start=time.monotonic()
        while time.monotonic()-start<10:
            lifecycle=self.session_status()
            if lifecycle['state'] in ('ending','closed','superseded'):
                return {'action':'no_op','playback_started':False,'lifecycle':lifecycle}
            countdowns=[e for e in self.logs.all_events() if e['event']=='voice.idle_countdown' and e.get('trace')==self.trace]
            if countdowns and countdowns[-1]['fields'].get('running')=='true':
                from .lifecycle import cleanup_budget
                budget=cleanup_budget(countdowns[-1],time.monotonic(),asset['duration_s'],
                                      params['idle_finish_before_s'],params.get('startup_budget_s',1))
                self._notify('cleanup_budget',**budget)
                if not budget['can_finish_in_budget']:
                    return {'action':'no_op','playback_started':False,'reason':'cleanup_deadline_missed',
                            'budget':budget,'lifecycle':lifecycle}
                result=self.play(asset)
                return {**result,'budget':budget,'host_playback_finished_in_budget':
                        result['end_monotonic_s']<=budget['finish_deadline_monotonic_s'],
                        'physical_onset_and_device_recognition':'not_verified'}
            time.sleep(.04)
        raise Blocked('cleanup idle countdown unavailable; cannot guarantee four-second window')

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


class ReplayAdapter(Observable):
    def __init__(self, events=None):
        self.events = events or []
        self.now = 0.0
        self.trace = None
        self.used = set()
        self.played = []

    def capabilities(self):
        return {'api_version': '1.0', 'adapter': 'replay', 'real_device': False,'event_alternatives':True}

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
        self._notify('wait_started',expected=params,bound_trace=self.trace,simulated=True)
        end = self.now + params.get('timeout_s', 30)
        for i, event in sorted(enumerate(self.events), key=lambda p: p[1]['at_s']):
            key = event.get('id', i)
            if key in self.used or event['at_s'] < self.now or event['at_s'] > end:
                continue
            if event.get('occurred_s', event['at_s']) < self.now:
                self._notify('event_ignored',reason='old_fixture_event',received=event,simulated=True)
                continue
            if match_event(event, params, self.trace):
                self.used.add(key)
                self.now = event['at_s']
                if params.get('bind_trace'):
                    self.trace = event.get('trace')
                self._notify('event_matched',received=event,bound_trace=self.trace,simulated=True)
                return {**event, 'simulated': True}
            self._notify('event_rejected',reason=rejection_reason(event,params,self.trace),received=event,simulated=True)
        self.now = end
        self._notify('wait_timeout',expected=params,simulated=True)
        raise Blocked('execution_timeout: fixture contains no current matching event')

    def observe(self, seconds):
        self.now += seconds
        return {'observed_s': seconds, 'simulated': True}

    def finish(self):
        return {'real_device': False, 'fixture_replay': True}


class HttpAdapter(Observable):
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
        response=self.request('GET', '/v1/capabilities')
        # These optional local methods have no HTTP v1.0 transport endpoint.
        return {**response,'interaction_wait':False,'device_ui_exit':False}

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
        self._notify('wait_started',expected=params,note='远程等待；逐条设备事件请在设备侧监看')
        result=self.request('POST', f'/v1/sessions/{self.session}/wait-event', params)
        self._notify('event_matched',received=result,remote=True)
        return result

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
