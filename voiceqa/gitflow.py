from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .core import ROOT, digest, dump, load_case, load_yaml, safe_path
from .runner import verify_bundle


def gate(root=ROOT):
    from .speech_library import validate_library
    library_assets=validate_library(root/'speech-library')
    cases = list((root / 'cases').rglob('*.yaml'))
    if not cases:
        raise ValueError('no versioned YAML cases')
    for path in cases:
        load_case(path)
    forbidden={'records','reports','sources','reviews','archive','data'}
    for name in forbidden:
        directory=root/name
        if directory.exists() and any(directory.iterdir()):
            raise ValueError('test inputs, evidence and receipts belong in local data_root, not Git: '+name)
    candidates=[root/n for n in subprocess.check_output(['git','-C',str(root),'ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0') if n] if (root/'.git').exists() else root.rglob('*')
    for path in candidates:
        relative = path.relative_to(root).parts
        if any(p in {'.git', '.venv', '.local', 'runs', 'batches', '__pycache__'} for p in relative) or not path.is_file():
            continue
        if path.resolve() in library_assets:
            if path.stat().st_size>2_000_000:
                raise ValueError('speech-library clip exceeds 2 MB: '+path.name)
            continue
        if path.stat().st_size > 2_000_000 or path.suffix.lower() in {'.wav', '.aiff', '.log', '.apk', '.jks', '.pem'}:
            raise ValueError('raw/large evidence must remain outside tracked snapshot: ' + path.name)
        if path.name in {'.env', 'config.local.yaml'}:
            raise ValueError('local configuration must not be tracked')
        if re.search(r'(RECEIPT|FEEDBACK|P003.*20[0-9]{6})',path.name,re.I):raise ValueError('receipt/report files belong in local data_root: '+path.name)
        text = path.read_text(encoding='utf-8')
        if re.search(r'\bBJP003[0-9]{14}\b',text):raise ValueError('full device SN must remain local: '+path.name)
        if re.search(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.)', text):
            raise ValueError('potential credential in snapshot: ' + path.name)
    print(f'GATE PASS: {len(cases)} reusable example cases; skill-only tree, asset hashes and credential scan verified')


def export_snapshot(root: Path, output: Path, ref=None):
    if ref is None:
        raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '--stage', '-z'])
        for entry in raw.split(b'\0'):
            if not entry:
                continue
            meta, name = entry.split(b'\t', 1)
            mode, sha, stage = meta.split()
            if mode not in (b'100644', b'100755') or stage != b'0':
                raise ValueError('only regular, resolved staged files supported')
            target = safe_path(output, name.decode())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(['git', '-C', str(root), 'cat-file', 'blob', sha.decode()]))
            if mode==b'100755':target.chmod(0o755)
    else:
        raw = subprocess.check_output(['git', '-C', str(root), 'ls-tree', '-r', '-z', ref])
        for entry in raw.split(b'\0'):
            if not entry:
                continue
            meta, name = entry.split(b'\t', 1)
            mode, kind, sha = meta.split()
            if mode not in (b'100644', b'100755') or kind != b'blob':
                raise ValueError('only regular files supported in release snapshot')
            target = safe_path(output, name.decode())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(['git', '-C', str(root), 'cat-file', 'blob', sha.decode()]))
            if mode==b'100755':target.chmod(0o755)


def check_snapshot(root, ref=None):
    with tempfile.TemporaryDirectory(prefix='voiceqa-gate-') as temp:
        snapshot = Path(temp)
        export_snapshot(root, snapshot, ref)
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        env['PYTHONPATH'] = str(snapshot)
        subprocess.run([sys.executable, 'qa.py', 'gate'], cwd=snapshot, env=env, check=True)
        subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],
                       cwd=snapshot, env=env, check=True)


def hook(name):
    if name == 'pre-commit':
        subprocess.run(['git', 'diff', '--cached', '--check'], cwd=ROOT, check=True)
        check_snapshot(ROOT)
    elif name == 'pre-push':
        refs = {line.split()[1] for line in sys.stdin if line.strip() and set(line.split()[1]) != {'0'}}
        for ref in sorted(refs):
            check_snapshot(ROOT, ref)
    elif name == 'post-commit':
        from .core import append
        from .runner import now
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        from .storage import data_root
        append(data_root() / 'work/hook-events.jsonl', {'event': 'commit_recorded', 'commit': sha, 'time': now()})
    else:
        raise ValueError('unsupported hook')


def install_hooks():
    subprocess.run(['git', 'config', '--local', 'core.hooksPath', '.githooks'], cwd=ROOT, check=True)
    for p in (ROOT / '.githooks').iterdir():
        p.chmod(0o755)
    print('Hooks installed in this repository only: pre-commit, pre-push, post-commit')
