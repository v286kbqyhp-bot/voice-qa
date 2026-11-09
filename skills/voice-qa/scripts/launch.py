#!/usr/bin/env python3
"""Locate a compatible checkout and dispatch its existing CLI without a shell."""
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
    contract = json.loads((root/'voiceqa/runtime-contract.json').read_text(encoding='utf-8'))
    return {'runtime_root': str(root), 'python': executable, 'cli_contract': CONTRACT,
            'review_evidence_sources': contract.get('review_evidence_sources', ['recording_asr']),
            'relative_paths_base': str(root), **info}


def windows_cli(root, command):
    # Popen quotes each argument for Windows; execv loses paths containing spaces.
    child = subprocess.Popen(command, cwd=root)
    try:
        return child.wait()
    except KeyboardInterrupt:
        # The child shares our console and receives Ctrl+C too. Let it finalize
        # recordings before forcing an unresponsive process to stop.
        try:
            return child.wait(timeout=15)
        except (KeyboardInterrupt, subprocess.TimeoutExpired):
            child.terminate()
            try:
                child.wait(timeout=3)
            except (KeyboardInterrupt, subprocess.TimeoutExpired):
                child.kill()
                child.wait()
            return 130


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
        command = [executable, str(root / 'qa.py'), *(args or ['--help'])]
        if os.name == 'nt':
            return windows_cli(root, command)
        os.chdir(root)
        os.execv(executable, command)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print('voice-qa launcher: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
