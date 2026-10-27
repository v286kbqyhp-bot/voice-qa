from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import shutil
import subprocess
import time
import uuid

from .core import Blocked, FIELDS, ROOT, append, digest, dump, load_case, resolve

CST = timezone(timedelta(hours=8))


def now():
    return datetime.now(CST).isoformat(timespec='seconds')


def source_revision():
    try:
        sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True))
        return {'commit': sha, 'dirty': dirty}
    except subprocess.CalledProcessError:
        return {'commit': None, 'dirty': True}


def run(case_path: Path, adapter, mode: str, config=None, output_root=Path('runs')) -> tuple[Path, dict]:
    case = load_case(case_path)
    config = config or {}
    run_id = datetime.now(CST).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
    output = output_root / run_id
    output.mkdir(parents=True, exist_ok=False)
    for part in ['source', 'steps', 'tts', 'audio', 'logs']:
        (output / part).mkdir()
    shutil.copyfile(case_path, output / 'cases.yaml')
    manifest = {'schema_version': 1, 'run_id': run_id, 'case_id': case['case_id'], 'mode': mode,
                'started_at': now(), 'host_platform': platform.system(),
                'source_sha256': digest(case_path), 'source': case.get('source', {}),
                'runner': source_revision(), 'operator': config.get('operator', '未提供'),
                'capabilities': {}}
    dump(output / 'run_manifest.json', manifest)
    steps = []
    assets = {}
    started = False
    failure = None
    all_steps = case['steps'] + case.get('cleanup', [])
    params_by_name = {}
    try:
        manifest['capabilities'] = adapter.capabilities()
        dump(output / 'run_manifest.json', manifest)
        fixture_adapter = manifest['capabilities'].get('adapter') == 'replay'
        if fixture_adapter != (mode == 'replay'):
            raise Blocked('adapter evidence layer and requested mode do not match')
        if case.get('blockers'):
            raise Blocked('case declares blockers: ' + '; '.join(case['blockers']))
        for condition in case.get('preconditions', []):
            if condition != {'kind': 'check', 'name': 'device_online'}:
                raise Blocked('unsupported precondition; explicit adapter implementation required')
        for step in all_steps:
            params = resolve(step.get('params', {}), config.get('bindings', {}))
            params_by_name[step['name']] = params
            if step['kind'] == 'audio_play':
                if 'file' in params and not Path(params['file']).is_absolute():
                    params['file'] = str((case_path.parent / params['file']).resolve())
                assets[step['name']] = adapter.prepare(params, output / 'tts' / (step['name'] + '.wav'))
        dump(output / 'tts/manifest.json', assets)
        unsupported = set(case.get('artifacts', [])) - {'audio', 'logcat', 'timeline'}
        if unsupported:
            raise Blocked('required artifact capability unavailable: ' + ', '.join(sorted(unsupported)))
        unsupported = set(case.get('requires', {}).get('capabilities', [])) - {'audio_hil'}
        if unsupported:
            raise Blocked('required adapter capability unavailable: ' + ', '.join(sorted(unsupported)))
        cfg = {**config, 'require_device': case.get('requires', {}).get('target', 'real_device') == 'real_device'}
        manifest['device'] = adapter.begin(output, cfg)
        started = True
        dump(output / 'run_manifest.json', manifest)
        for index, step in enumerate(all_steps, 1):
            record = {'step': step['name'], 'kind': step['kind'], 'index': index,
                      'phase': 'cleanup' if index > len(case['steps']) else 'test',
                      'started_at': now(), 'execution_status': 'running', 'product_status': 'needs_review'}
            append(output / 'timeline.jsonl', {**record, 'event': 'step_started'})
            params = params_by_name[step['name']]
            try:
                if step['kind'] == 'audio_play':
                    record['actual'] = adapter.play(assets[step['name']])
                    record['text'] = params.get('text', '预制 WAV；文本未提供')
                    append(output / 'playback.jsonl', {**record, 'asset': 'tts/' + step['name'] + '.wav'})
                elif step['kind'] in ('audio_record', 'sleep'):
                    seconds = params.get('seconds', params.get('max_seconds', 1))
                    if not 0 <= seconds <= 60:
                        raise Blocked('observation duration must be 0..60 seconds')
                    record['actual'] = adapter.observe(seconds)
                elif step['kind'] == 'wait_event':
                    record['actual'] = adapter.wait_event(params)
                elif step['kind'] == 'expect':
                    contract = params.get('contract')
                    if not isinstance(contract, dict) or contract.get('type') != 'log_event':
                        raise Blocked('semantic/physical oracle needs an analyzer or reviewer')
                    record['actual'] = adapter.wait_event({k: v for k, v in contract.items() if k != 'type'})
                    record['product_status'] = 'log_observed_only'
                else:
                    raise Blocked('physical observation capability not available')
                record['execution_status'] = 'completed'
            except Blocked as exc:
                record['execution_status'] = 'blocked'
                record['reason'] = str(exc)
                failure = str(exc)
            except Exception as exc:
                record['execution_status'] = 'error'
                record['reason'] = type(exc).__name__
                failure = record['reason']
            record['ended_at'] = now()
            dump(output / 'steps' / (f'{index:03d}-' + step['name'] + '.json'), record)
            steps.append(record)
            append(output / 'timeline.jsonl', {**record, 'event': 'step_finished'})
            if failure:
                break
    except Blocked as exc:
        failure = str(exc)
    except KeyboardInterrupt:
        failure = 'interrupted_by_operator'
    except Exception as exc:
        failure = 'execution_error: ' + type(exc).__name__
    finally:
        if started:
            try:
                manifest['capture_result'] = adapter.finish()
                audio = manifest['capture_result'].get('audio')
                if audio is not None and not audio.get('valid_capture'):
                    failure = failure or 'capture_invalid: silence, overflow, or stream error; needs investigation'
            except Exception as exc:
                failure = failure or 'capture_finalize_error: ' + type(exc).__name__
        for index in range(len(steps), len(all_steps)):
            step = all_steps[index]
            record = {'step': step['name'], 'kind': step['kind'], 'index': index + 1,
                      'execution_status': 'not_executed', 'product_status': 'needs_review', 'reason': failure}
            steps.append(record)
            dump(output / 'steps' / (f'{index+1:03d}-' + step['name'] + '.json'), record)
        manifest['ended_at'] = now()
        dump(output / 'run_manifest.json', manifest)
    results = {'schema_version': 1, 'run_id': run_id, 'case_id': case['case_id'], 'mode': mode,
               'execution_status': 'blocked' if failure else 'completed',
               'product_status': 'not_applicable' if mode == 'replay' else 'needs_review',
               'evidence_status': 'synthetic_fixture' if mode == 'replay' else
                   'audio_and_logs' if (output / 'audio/mic_raw.wav').exists() and (output / 'logs/device_redacted.log').exists()
                   else 'partial',
               'reason': failure, 'attempt': 1, 'steps': steps}
    dump(output / 'results.json', results)
    dump(output / 'transcript.json', {'status': 'not_analyzed', 'segments': [],
                                     'reason': 'ASR/speaker attribution provider not configured; no inferred speech'})
    render_receipt(output, manifest, results)
    seal(output)
    return output, results


def render_receipt(output, manifest, results):
    spoken = [s.get('text', s['step']) for s in results['steps']
              if s['kind'] == 'audio_play' and s['execution_status'] == 'completed']
    device = manifest.get('device', {})
    device = device.get('metadata', device)
    values = [
        f"{device.get('cloud_sn_suffix', '未核对')} / {device.get('version_name', '未读取')}；来源：本轮运行清单",
        f"{manifest['operator']}；{manifest['mode']}；{manifest['started_at']}—{manifest['ended_at']}",
        f"{results['case_id']}；来源 SHA256：{manifest['source_sha256']}",
        '；'.join(spoken) or '无已完成的播放动作',
        '未生成声学转写，待复听；产品结论：' + results['product_status'],
        '日志仅代表内部观测；字幕、灯光、物理待机未观察',
        '未开展普通设备对照',
        f"独立尝试 1 次；执行状态：{results['execution_status']}；{results.get('reason') or '无执行异常'}",
        '本轮无视频；详见 [证据索引](evidence_index.md) 与 [步骤结果](results.json)',
    ]
    (output / 'receipt.md').write_text('# 自动化回执\n\n' +
        ('**回放验证，未操作真机；不得用于产品验收。**\n\n' if results['mode'] == 'replay' else '') +
        '\n'.join(f'- {key}：{value}' for key, value in zip(FIELDS, values)) + '\n', encoding='utf-8')
    rows = ['# 逐步证据索引', '', '| 步骤 | 执行 | 产品 | 交付 |', '|---|---|---|---|']
    for step in results['steps']:
        file = f"steps/{step['index']:03d}-{step['step']}.json"
        rows.append(f"| {step['step']} | {step['execution_status']} | {step['product_status']} | [JSON]({file}) |")
    for name in ['audio/mic_raw.wav', 'logs/device_redacted.log', 'playback.jsonl', 'events.jsonl', 'timeline.jsonl']:
        if (output / name).exists():
            rows += ['', f'- [{name}]({name})']
    (output / 'evidence_index.md').write_text('\n'.join(rows) + '\n', encoding='utf-8')


def seal(output):
    dump(output / 'integrity.json', {'schema_version': 1, 'files':
         {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob('*'))
          if p.is_file() and p.name != 'integrity.json'}})


def verify_bundle(output):
    from .core import safe_path
    integrity = json.loads((output / 'integrity.json').read_text(encoding='utf-8'))
    for name, sha in integrity['files'].items():
        p = safe_path(output, name)
        if not p.is_file() or digest(p) != sha:
            raise ValueError(f'artifact missing or changed: {name}')
    required = ['run_manifest.json', 'cases.yaml', 'results.json', 'receipt.md', 'evidence_index.md', 'transcript.json']
    if any(name not in integrity['files'] for name in required):
        raise ValueError('incomplete evidence manifest')
    results = json.loads((output / 'results.json').read_text(encoding='utf-8'))
    manifest = json.loads((output / 'run_manifest.json').read_text(encoding='utf-8'))
    case = load_case(output / 'cases.yaml')
    if digest(output / 'cases.yaml') != manifest['source_sha256'] or manifest['run_id'] != results['run_id']:
        raise ValueError('source/run fingerprint mismatch')
    if results['case_id'] != case['case_id'] or results['mode'] not in ('replay', 'live', 'http'):
        raise ValueError('run/case identity mismatch')
    if results['mode'] == 'replay' and results['product_status'] != 'not_applicable':
        raise ValueError('replay cannot claim product acceptance')
    if len(results['steps']) != len(case['steps']) + len(case.get('cleanup', [])):
        raise ValueError('missing step records')
    for index, step in enumerate(results['steps'], 1):
        p = output / f"steps/{index:03d}-{step['step']}.json"
        if not p.exists() or json.loads(p.read_text(encoding='utf-8')) != step:
            raise ValueError('missing or inconsistent per-step deliverable')
    if any(field + '：' not in (output / 'receipt.md').read_text(encoding='utf-8') for field in FIELDS):
        raise ValueError('receipt template fields missing')
    return results


def record_bundle(output, destination=Path('records')):
    """Only fixture bundles are publishable initially; live evidence stays local."""
    results = verify_bundle(output)
    dest = destination / output.name
    dest.mkdir(parents=True, exist_ok=False)
    if results['mode'] == 'replay':
        for p in output.rglob('*'):
            if p.is_file() and p.suffix not in ('.wav', '.log') and p.name != 'integrity.json':
                target = dest / p.relative_to(output)
                target.parent.mkdir(parents=True, exist_ok=True)
                if p.relative_to(output).as_posix() == 'tts/manifest.json':
                    data = json.loads(p.read_text(encoding='utf-8'))
                    dump(target, {key: {k: v for k, v in asset.items() if k != 'path'} for key, asset in data.items()})
                else:
                    shutil.copyfile(p, target)
        seal(dest)
    else:
        # Explicit allowlist: no raw logs, transcripts, speech, full identities or absolute paths.
        summary = {k: results[k] for k in ['schema_version', 'run_id', 'case_id', 'mode',
                                           'execution_status', 'product_status', 'evidence_status']}
        summary['steps'] = [{k: s[k] for k in ['step', 'kind', 'execution_status', 'product_status']}
                            for s in results['steps']]
        for step, original in zip(summary['steps'], results['steps']):
            name = f"steps/{original['index']:03d}-{original['step']}.json"
            step['local_artifact'] = 'runs/' + output.name + '/' + name
            step['artifact_sha256'] = digest(output / name)
        summary['local_bundle_integrity_sha256'] = digest(output / 'integrity.json')
        summary['source_sha256'] = digest(output / 'cases.yaml')
        summary['local_evidence_index'] = 'runs/' + output.name + '/evidence_index.md'
        summary['raw_evidence'] = 'kept_local_not_in_git'
        dump(dest / 'summary.json', summary)
    return dest
