"""Append-only progress events and a portable, read-only terminal follower."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
import threading
import time

from .core import append, dump, safe_path

CST = timezone(timedelta(hours=8))
SENSITIVE = re.compile(r'token|password|secret|authorization|credential|wifi_password', re.I)


def scrub(value):
    from .adapters import redact
    if isinstance(value, dict):
        return {k: '[REDACTED]' if SENSITIVE.search(k) else scrub(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    return value


def plain(value):
    # Log text cannot inject terminal control sequences.
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value))


def format_event(record):
    name = record.get('event', 'event')
    labels = {'run_started':'RUN', 'capabilities':'CAPS', 'asset_preparing':'TTS',
              'asset_loading':'FILE','asset_ready':'ASSET', 'capture_starting':'REC', 'capture_started':'REC',
              'step_started':'STEP', 'step_finished':'STEP', 'wait_started':'WAIT',
              'event_matched':'MATCH', 'event_rejected':'MISS', 'event_ignored':'SKIP',
              'wait_timeout':'TIMEOUT', 'capture_stopping':'REC', 'capture_stopped':'REC',
              'run_finished':'DONE', 'execution_blocked':'BLOCKED'}
    detail = record.get('details', {})
    stamp = record.get('time', '')[11:23]
    step = record.get('step') or '—'
    return f"{stamp} +{record.get('elapsed_s',0):7.2f}s [{labels.get(name,name):7}] {plain(step)} | {plain(json.dumps(detail,ensure_ascii=False,separators=(',',':')))}"


class LiveLog:
    def __init__(self, output: Path, verbose=False):
        self.output = output
        self.started = time.monotonic()
        self.step = None
        self.lock = threading.Lock()
        self.verbose = verbose

    def emit(self, event, **details):
        record = {'time':datetime.now(CST).isoformat(timespec='milliseconds'),
                  'elapsed_s':round(time.monotonic()-self.started,3),
                  'event':event,'step':self.step,'details':scrub(details)}
        with self.lock:
            append(self.output/'progress.jsonl',record)
            if self.verbose:
                print(format_event(record),file=sys.stderr,flush=True)


class FileTail:
    def __init__(self):
        self.offset = 0
        self.pending = b''

    def poll(self, path):
        if not path.exists():
            return []
        if path.stat().st_size < self.offset:
            self.offset=0
            self.pending=b''
        with path.open('rb') as f:
            f.seek(self.offset)
            chunk=f.read(256*1024)
            self.offset=f.tell()
        blocks=(self.pending+chunk).split(b'\n')
        self.pending=blocks.pop()
        return [line.decode('utf-8',errors='replace') for line in blocks]


def select_run(root: Path, selected='latest'):
    if selected != 'latest':
        p=safe_path(root,selected)
        return p if p.is_dir() else None
    pointer=root/'active_run.json'
    try:
        p=safe_path(root,json.loads(pointer.read_text(encoding='utf-8'))['run_id'])
        if p.is_dir():
            return p
    except (OSError,ValueError,KeyError):
        pass
    candidates=[p for p in root.iterdir() if p.is_dir() and (p/'run_manifest.json').is_file()] if root.exists() else []
    return max(candidates,key=lambda p:p.name) if candidates else None


def watch(root: Path, selected='latest', raw=False, interval=0.25, stream=None, stop=None):
    stream=stream or sys.stdout
    stop=stop or threading.Event()
    current=None
    tails={}
    last_message=time.monotonic()
    last_status='等待运行'
    print('P003 QA 实时调试｜只读监看｜北京时间｜Ctrl+C 仅停止监看',file=stream,flush=True)
    print('RUN=运行 STEP=步骤 WAIT=等待 MATCH=命中 MISS=未命中 TIMEOUT=运行超时；日志命中不等于产品通过',file=stream,flush=True)
    while not stop.is_set():
        run=select_run(root,selected)
        if run != current:
            current=run
            tails={name:FileTail() for name in ['progress.jsonl','events.jsonl','logs/device_redacted.log','timeline.jsonl']}
            if run:
                try:
                    m=json.loads((run/'run_manifest.json').read_text(encoding='utf-8'))
                except (OSError,ValueError):
                    m={}
                print(f"\n── {plain(run.name)} | {plain(m.get('case_id','准备中'))} | mode={plain(m.get('mode','unknown'))} ──",file=stream,flush=True)
                print(f'证据目录：{run.resolve()}',file=stream,flush=True)
                last_status='已连接该轮日志'
        if run:
            sources=['progress.jsonl','events.jsonl']
            if not (run/'progress.jsonl').exists():
                sources.append('timeline.jsonl')
            if raw:
                sources.append('logs/device_redacted.log')
            for source in sources:
                for line in tails[source].poll(run/source):
                    if not line.strip():
                        continue
                    try:
                        record=scrub(json.loads(line))
                    except ValueError:
                        record=None
                    if source=='progress.jsonl' and record:
                        text=format_event(record)
                        last_status=f"{record.get('step') or '运行'} / {record['event']}"
                    elif record:
                        text=f"[DEVICE] {plain(json.dumps(record,ensure_ascii=False,separators=(',',':')))}"
                    else:
                        text=f'[RAW] {plain(scrub(line))}'
                    print(text,file=stream,flush=True)
                    last_message=time.monotonic()
        if time.monotonic()-last_message >= 5:
            print(f"[{datetime.now(CST).strftime('%H:%M:%S')}] 正在监看｜{plain(last_status)}｜暂无新日志",file=stream,flush=True)
            last_message=time.monotonic()
        stop.wait(interval)
