from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import platform
import shutil
import subprocess
import time
import uuid

from .core import Blocked, FIELDS, ROOT, append, digest, dump, load_case, receipt_fields, resolve

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


def run(case_path: Path, adapter, mode: str, config=None, output_root=Path('runs'), verbose=False, prepared_assets=None, context=None) -> tuple[Path, dict]:
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
                'capabilities': {},'context':context or {}}
    dump(output / 'run_manifest.json', manifest)
    from .live import LiveLog
    log=LiveLog(output,verbose)
    log.emit('run_started',run_id=run_id,case_id=case['case_id'],mode=mode,
             context=context or {},note='回放数据不是设备证据' if mode=='replay' else '产品判定仍需独立证据')
    dump(output_root/'active_run.json',{'run_id':run_id})
    if hasattr(adapter,'set_observer'):
        adapter.set_observer(log.emit)
    steps = []
    assets = {}
    started = False
    failure = None
    all_steps = case['steps'] + case.get('cleanup', [])
    params_by_name = {}
    try:
        manifest['capabilities'] = adapter.capabilities()
        log.emit('capabilities',capabilities=manifest['capabilities'])
        dump(output / 'run_manifest.json', manifest)
        fixture_adapter = manifest['capabilities'].get('adapter') == 'replay'
        if fixture_adapter != (mode == 'replay'):
            raise Blocked('adapter evidence layer and requested mode do not match')
        if case.get('blockers'):
            raise Blocked('case declares blockers: ' + '; '.join(case['blockers']))
        if any(s['kind']=='device_action' for s in all_steps) and (
                not manifest['capabilities'].get('device_ui_exit') or
                not callable(getattr(type(adapter),'device_action',None))):
            raise Blocked('device_ui_exit capability unavailable')
        if any(s['kind'] in ('wait_speech','wait_output') for s in all_steps) and (
                not manifest['capabilities'].get('interaction_wait') or
                not callable(getattr(type(adapter),'wait_interaction',None))):
            raise Blocked('interaction_wait capability unavailable')
        if any(s.get('params',{}).get('phase_role') for s in all_steps) and not (config.get('phase_review_file') or config.get('phase_rules')):
            raise Blocked('external phase review capability unavailable')
        prepared=json.loads(prepared_assets.read_text(encoding='utf-8')) if prepared_assets else None
        if prepared_assets:
            manifest['prepared_assets_sha256']=digest(prepared_assets)
        for condition in case.get('preconditions', []):
            if condition != {'kind': 'check', 'name': 'device_online'}:
                raise Blocked('unsupported precondition; explicit adapter implementation required')
        for step in all_steps:
            params = resolve(step.get('params', {}), config.get('bindings', {}))
            params_by_name[step['name']] = params
            if step['kind'] == 'audio_play':
                if prepared is not None:
                    asset=prepared.get(step['name'])
                    if not asset or not Path(asset.get('path','')).is_file():
                        raise Blocked('prepared asset missing: '+step['name'])
                    if digest(Path(asset['path'])) != asset['sha256'] or asset.get('text') != params.get('text'):
                        raise Blocked('prepared asset text/hash mismatch: '+step['name'])
                    params={**params,'file':str(Path(asset['path']).resolve())}
                    params_by_name[step['name']]=params
                log.emit('asset_loading' if params.get('file') else 'asset_preparing',
                         step=step['name'],text=params.get('text'),file=params.get('file'))
                if 'file' in params and not Path(params['file']).is_absolute():
                    params['file'] = str((case_path.parent / params['file']).resolve())
                assets[step['name']] = adapter.prepare(params, output / 'tts' / (step['name'] + '.wav'))
                log.emit('asset_ready',step=step['name'],duration_s=assets[step['name']]['duration_s'],sha256=assets[step['name']]['sha256'])
        dump(output / 'tts/manifest.json', assets)
        for params in params_by_name.values():
            if params.get('alternative_events') and not manifest['capabilities'].get('event_alternatives'):
                raise Blocked('adapter does not declare event_alternatives support')
            if params.get('if_session_active') and not callable(getattr(type(adapter),'session_status',None)):
                raise Blocked('session lifecycle capability unavailable')
            if params.get('idle_finish_before_s') and not callable(getattr(type(adapter),'play_cleanup',None)):
                raise Blocked('cleanup deadline capability unavailable')
        unsupported = set(case.get('artifacts', [])) - {'audio', 'logcat', 'timeline'}
        if unsupported:
            raise Blocked('required artifact capability unavailable: ' + ', '.join(sorted(unsupported)))
        unsupported = set(case.get('requires', {}).get('capabilities', [])) - {'audio_hil','interaction_wait','device_ui_exit'}
        if unsupported:
            raise Blocked('required adapter capability unavailable: ' + ', '.join(sorted(unsupported)))
        for optional in ('interaction_wait','device_ui_exit'):
            if optional in case.get('requires',{}).get('capabilities',[]) and not manifest['capabilities'].get(optional):
                raise Blocked(optional+' capability unavailable')
        cfg = {**config, 'require_device': case.get('requires', {}).get('target', 'real_device') == 'real_device'}
        log.emit('capture_starting',require_device=cfg['require_device'])
        manifest['device'] = adapter.begin(output, cfg)
        started = True
        log.emit('capture_started')
        dump(output / 'run_manifest.json', manifest)
        for index, step in enumerate(all_steps, 1):
            log.step=step['name']
            record = {'step': step['name'], 'kind': step['kind'], 'index': index,
                      'phase': 'cleanup' if index > len(case['steps']) else 'test',
                      'started_at': now(), 'execution_status': 'running', 'product_status': 'needs_review'}
            params = params_by_name[step['name']]
            if failure and record['phase']!='cleanup':
                record.update(execution_status='not_executed',reason=failure)
                dump(output/'steps'/(f'{index:03d}-'+step['name']+'.json'),record)
                steps.append(record)
                log.emit('step_finished',execution_status='not_executed',reason=failure)
                append(output/'timeline.jsonl',{**record,'event':'step_skipped'})
                continue
            append(output / 'timeline.jsonl', {**record, 'event': 'step_started'})
            log.emit('step_started',index=index,kind=step['kind'],params=params)
            try:
                lifecycle = adapter.session_status() if params.get('if_session_active') else None
                if lifecycle and lifecycle['state']=='unknown':
                    raise Blocked('cannot condition cleanup on an unbound session')
                skip_cleanup = lifecycle and (lifecycle['state'] in ('closed','superseded') or
                    step['kind'] in ('audio_play','device_action') and lifecycle['state']=='ending')
                if skip_cleanup:
                    record['actual']={'action':'no_op','playback_started':False,'lifecycle':lifecycle,
                                      'reason':'session already ending or closed; redundant cleanup skipped'}
                    log.emit('cleanup_skipped',lifecycle=lifecycle)
                elif step['kind'] == 'audio_play':
                    record['actual'] = (adapter.play_cleanup(assets[step['name']],params) if params.get('idle_finish_before_s')
                                        else adapter.play(assets[step['name']]))
                    if record['actual'].get('playback_started') is not False:
                        record['text'] = params.get('text', '预制 WAV；文本未提供')
                        append(output / 'playback.jsonl', {**record, 'asset': 'tts/' + step['name'] + '.wav'})
                elif step['kind'] in ('audio_record', 'sleep'):
                    seconds = params.get('seconds', params.get('max_seconds', 1))
                    if not 0 <= seconds <= 60:
                        raise Blocked('observation duration must be 0..60 seconds')
                    record['actual'] = adapter.observe(seconds)
                elif step['kind'] == 'wait_event':
                    record['actual'] = adapter.wait_event(params)
                elif step['kind'] in ('wait_speech','wait_output'):
                    record['actual']=adapter.wait_interaction(step['kind'],params)
                elif step['kind']=='device_action':
                    record['actual']=adapter.device_action(params)
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
                failure = failure or str(exc)
            except Exception as exc:
                record['execution_status'] = 'error'
                record['reason'] = type(exc).__name__
                failure = failure or record['reason']
            record['ended_at'] = now()
            dump(output / 'steps' / (f'{index:03d}-' + step['name'] + '.json'), record)
            steps.append(record)
            log.emit('step_finished',execution_status=record['execution_status'],product_status=record['product_status'],
                     reason=record.get('reason'),actual=record.get('actual'))
            append(output / 'timeline.jsonl', {**record, 'event': 'step_finished'})
    except Blocked as exc:
        failure = str(exc)
        log.emit('execution_blocked',reason=failure)
    except KeyboardInterrupt:
        failure = 'interrupted_by_operator'
        log.emit('execution_blocked',reason=failure)
    except Exception as exc:
        failure = 'execution_error: ' + type(exc).__name__
        log.emit('execution_blocked',reason=failure)
    finally:
        if started:
            try:
                log.emit('capture_stopping')
                manifest['capture_result'] = adapter.finish()
                log.emit('capture_stopped',quality=manifest['capture_result'])
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
               'reason': failure, 'attempt': (context or {}).get('attempt_index',1), 'steps': steps}
    dump(output / 'results.json', results)
    dump(output / 'transcript.json', {'status': 'not_analyzed', 'segments': [],
                                     'reason': 'ASR/speaker attribution provider not configured; no inferred speech'})
    render_receipt(output, manifest, results)
    log.step=None
    log.emit('run_finished',execution_status=results['execution_status'],product_status=results['product_status'],reason=failure)
    seal(output)
    return output, results


def prepare_assets(case_path, output, adapter, config=None):
    """Create every speech asset without starting capture or playing anything."""
    case=load_case(case_path)
    output=output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    assets={}
    for step in case['steps']+case.get('cleanup',[]):
        if step['kind']!='audio_play':
            continue
        params=resolve(step.get('params',{}),(config or {}).get('bindings',{}))
        if params.get('file') and not Path(params['file']).is_absolute():
            params['file']=str((case_path.parent/params['file']).resolve())
        assets[step['name']]=adapter.prepare(params,output/(step['name']+'.wav'))
    dump(output/'manifest.json',assets)
    dump(output/'preparation.json',{'case_id':case['case_id'],'case_sha256':digest(case_path),
                                   'prepared_at':now(),'manifest_sha256':digest(output/'manifest.json'),
                                   'playback_started':False,'capture_started':False})
    return output/'manifest.json'


def render_receipt(output, manifest, results):
    spoken = [s.get('text', s['step']) for s in results['steps']
              if s['kind'] == 'audio_play' and s['execution_status'] == 'completed' and s.get('actual',{}).get('playback_started') is not False]
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
    case=load_case(output/'cases.yaml')
    fields=receipt_fields(case)
    if fields != FIELDS:
        values=[device.get('cloud_sn_suffix','未核对'),
                device.get('version_name','未读取')+'；本轮未截图',
                manifest['started_at']+'—'+manifest['ended_at'],
                results['case_id']+'；'+case.get('source',{}).get('scope','见原用例范围'),
                '；'.join(spoken) or '无已完成的播放动作',
                '未自动转写或人工复听；请结合原始录音，不以日志补写实际听到的内容',
                '物理首音等待与首词完整性尚未判定；采集时长不作为响应延迟',
                '待按本项用例核对；本轮执行状态：'+results['execution_status'],
                '本轮无截图/视频；[证据索引](evidence_index.md)',
                '待测试人从“通过 / 部分通过 / 未通过 / 未测试”选择；框架不自动代填产品结论']
    (output / 'receipt.md').write_text('# 自动化回执\n\n' +
        ('**回放验证，未操作真机；不得用于产品验收。**\n\n' if results['mode'] == 'replay' else '') +
        '\n'.join(f'- {key}：{value}' for key, value in zip(fields, values)) + '\n', encoding='utf-8')
    rows = ['# 逐步证据索引', '', '| 步骤 | 执行 | 产品 | 交付 |', '|---|---|---|---|']
    for step in results['steps']:
        file = f"steps/{step['index']:03d}-{step['step']}.json"
        rows.append(f"| {step['step']} | {step['execution_status']} | {step['product_status']} | [JSON]({file}) |")
    for name in ['progress.jsonl', 'audio/mic_raw.wav', 'logs/device_redacted.log', 'playback.jsonl', 'events.jsonl', 'timeline.jsonl']:
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
    if any(field + '：' not in (output / 'receipt.md').read_text(encoding='utf-8') for field in receipt_fields(case)):
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
