#!/usr/bin/env python3
"""Locate a compatible checkout, then replace this process with its existing CLI."""
import json
import os
from pathlib import Path
import subprocess
import sys

CONTRACT = 'voiceqa-cli-v2'


def locate_runtime():
    configured = os.environ.get('VOICEQA_REPO')
    root = Path(configured).expanduser() if configured else Path(__file__).resolve().parents[3]
    if not root.is_absolute():
        raise ValueError('VOICEQA_REPO must be an absolute checkout path')
    root = root.resolve()
    marker = root / 'voiceqa/runtime-contract.json'
    if not marker.is_file():
        raise ValueError('Compatible runtime not found; set VOICEQA_REPO to the voice-qa checkout')
    contract = json.loads(marker.read_text(encoding='utf-8'))
    if (not isinstance(contract, dict) or contract.get('schema_version') != 1 or contract.get('cli_contract') != CONTRACT
            or contract.get('entrypoint') != 'qa.py' or contract.get('minimum_python') != [3, 11]):
        raise ValueError('Runtime contract mismatch; use the skill and runtime from a compatible release')
    for name in ('qa.py', 'voiceqa/cli.py', 'AGENTS.md', 'README.md', 'requirements-lock.txt'):
        if not (root / name).is_file():
            raise ValueError('Incomplete runtime checkout: ' + name)
    return root


def select_python(root):
    configured = os.environ.get('VOICEQA_PYTHON')
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute() or not candidate.is_file():
            raise ValueError('VOICEQA_PYTHON must name an existing absolute Python executable')
        return str(candidate)
    relative = '.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python'
    candidate = root / relative
    # Do not resolve a virtualenv Python symlink: that loses virtualenv identity.
    return str(candidate) if candidate.is_file() else sys.executable


def describe(root, executable):
    probe = subprocess.run([executable, '-c',
        'import importlib.util,json,sys; print(json.dumps({"version":list(sys.version_info[:2]),'
        '"dependencies_ready":all(importlib.util.find_spec(n) is not None for n in ("yaml","jsonschema"))}))'],
        cwd=root, capture_output=True, text=True, timeout=10, check=True)
    info = json.loads(probe.stdout)
    if info['version'] < [3, 11]:
        raise ValueError('Runtime requires Python 3.11 or newer')
    return {'runtime_root': str(root), 'python': executable, 'cli_contract': CONTRACT,
            'relative_paths_base': str(root), **info}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        root = locate_runtime()
        executable = select_python(root)
        info = describe(root, executable)
        if args == ['--runtime-info']:
            print(json.dumps(info, ensure_ascii=True, indent=2))
            return 0
        if not info['dependencies_ready']:
            raise ValueError('Runtime dependencies missing; follow README.md and requirements-lock.txt in runtime_root')
        os.chdir(root)
        # Keep Ctrl+C and the CLI's capture-finalization semantics; no extra shell.
        os.execv(executable, [executable, str(root / 'qa.py'), *(args or ['--help'])])
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print('voice-qa launcher: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
