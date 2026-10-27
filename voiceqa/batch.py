"""Sequential case scheduling with one separate diagnostic repeat per failure."""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import uuid

from .core import Blocked, append, digest, dump, load_case, load_yaml, resolve, safe_path
from .runner import CST, now, run, seal, verify_bundle


def load_suite(path):
    data=load_yaml(path)
    if not isinstance(data,dict) or data.get('schema_version')!=1 or not isinstance(data.get('cases'),list) or not data['cases']:
        raise ValueError('suite requires schema_version=1 and ordered cases')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',data.get('suite_id','')):
        raise ValueError('invalid suite_id')
    policy=data.get('policy',{})
    if policy.get('diagnostic_repeats',1)!=1 or policy.get('continue_after_failure',True) is not True:
        raise ValueError('this policy allows exactly one diagnostic repeat and continues independent cases')
    seen=set()
    for case in data['cases']:
        key=case.get('id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key) or key in seen or not isinstance(case.get('file'),str):
            raise ValueError('case ids must be unique and file is required')
        if not set(case.get('depends_on',[]))<=seen:
            raise ValueError('dependencies must name earlier cases')
        if case.get('initial_verdict',{}).get('status') not in (None,'not_passed','pending_review','passed'):
            raise ValueError('unsupported initial verdict')
        if case.get('initial_verdict',{}).get('status')=='not_passed' and not case['initial_verdict'].get('reason'):
            raise ValueError('a recorded failure requires a reason')
        seen.add(key)
    return data


def classify(result):
    if result.get('reason')=='interrupted_by_operator':
        return {'status':'interrupted','reason':'operator stopped the suite'}
    reason=result.get('reason') or ''
    if result['execution_status']!='completed':
        preflight=any(x in reason for x in ['case declares blockers','unresolved binding','prepared asset','capability unavailable','adapter does not declare','unsupported precondition'])
        return {'status':'blocked' if preflight else 'not_passed','reason':reason or 'execution incomplete',
                'failure_layer':'precondition' if preflight else 'execution'}
    if result.get('product_status') in ('fail','not_passed'):
        return {'status':'not_passed','reason':'product oracle did not pass','failure_layer':'product'}
    if result.get('product_status')=='pass':
        return {'status':'passed','reason':'product oracle passed'}
    return {'status':'pending_review','reason':'execution completed; no product verdict is available'}


def review_package(root, key, attempts):
    folder=root/'review'/key
    folder.mkdir(parents=True,exist_ok=True)
    rows=['# 人工复核证据包','', '首次与诊断复测分别保存；复测结果不覆盖首次未通过。','']
    for i,(role,path,result,verdict) in enumerate(attempts,1):
        verify_bundle(path)
        target=folder/f'{i:02d}-{role}'
        shutil.copytree(path,target)
        audio=(target/'audio/mic_raw.wav').exists()
        log=(target/'logs/device_redacted.log').exists()
        rows += [f'## {i}. {role}', '', f'- 判定：{verdict["status"]}；执行：{result["execution_status"]}',
                 f'- [逐步索引]({target.name}/evidence_index.md)', f'- [实际结果]({target.name}/results.json)',
                 f'- 录音：'+(f'[mic_raw.wav]({target.name}/audio/mic_raw.wav)' if audio else '未取得，见执行异常'),
                 f'- 日志：'+(f'[device_redacted.log]({target.name}/logs/device_redacted.log)' if log else '未取得，见执行异常'), '']
    (folder/'review_index.md').write_text('\n'.join(rows),encoding='utf-8')
    return folder.relative_to(root).as_posix()+'/review_index.md'


def run_suite(suite_path, adapter_factory, mode, config=None, output_root=Path('batches'),
              runs_root=Path('runs'), prepared=None, decision=None, verbose=False):
    suite=load_suite(suite_path)
    config=config or {}
    batch_id=datetime.now(CST).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8]
    output=output_root/batch_id
    output.mkdir(parents=True,exist_ok=False)
    shutil.copyfile(suite_path,output/'suite.yaml')
    manifest={'schema_version':1,'batch_id':batch_id,'suite_id':suite['suite_id'],'mode':mode,
              'started_at':now(),'suite_sha256':digest(suite_path),'policy':{
                  'diagnostic_repeats':1,'continue_after_failure':True,'preserve_initial_failure':True}}
    dump(output/'batch_manifest.json',manifest)
    rows=[];by_id={};aborted=False
    def save():
        summary={'schema_version':1,'batch_id':batch_id,'suite_id':suite['suite_id'],'mode':mode,
                 'aborted':aborted,'cases':rows}
        dump(output/'results.json',summary)
        lines=['# 批量测试结果','', '**回放调试，不是产品验收。**' if mode=='replay' else '产品判定与执行状态分别记录。', '',
               '| 用例 | 状态 | 尝试数 | 人工复核 |','|---|---|---|---|']
        labels={'not_passed':'未通过，待人工复核','blocked':'前置阻塞，未通过门禁','pending_review':'已执行，待判定','passed':'通过','skipped_dependency':'前置用例未通过，未执行','interrupted':'用户停止','skipped_cancelled':'未执行（用户停止）'}
        for row in rows:
            link=f"[独立证据包]({row['review_index']})" if row.get('review_index') else '见原因'
            lines.append(f"| {row['id']} | {labels[row['status']]} | {len(row.get('attempts',[]))} | {link} |")
        (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        return summary
    for entry in suite['cases']:
        key=entry['id']
        if aborted:
            row={'id':key,'status':'skipped_cancelled','attempts':[]};rows.append(row);by_id[key]=row;save();continue
        unmet=[dep for dep in entry.get('depends_on',[]) if by_id[dep]['status']!='passed']
        if unmet:
            row={'id':key,'status':'skipped_dependency','reason':'dependencies did not pass: '+','.join(unmet),'attempts':[]}
            rows.append(row);by_id[key]=row;save();continue
        case_path=(suite_path.parent/entry['file']).resolve() if not Path(entry['file']).is_absolute() else Path(entry['file'])
        case=load_case(case_path)
        attempts=[]
        asset_path=entry.get('prepared_assets') or (prepared or {}).get(key)
        try:
            if asset_path:
                asset_path=Path(resolve(asset_path,config.get('bindings',{}))).resolve()
            if mode!='replay' and asset_path is None and not case.get('blockers'):
                raise Blocked('prepared assets required; no runtime synthesis')
        except Blocked as exc:
            row={'id':key,'status':'blocked','reason':str(exc),'attempts':[]}
            rows.append(row);by_id[key]=row;save();continue
        initial_run=entry.get('initial_run')
        if initial_run:
            first_path=Path(resolve(initial_run,config.get('bindings',{}))).resolve()
            first=verify_bundle(first_path)
            if digest(first_path/'cases.yaml')!=digest(case_path) or first['mode']!=mode:
                raise ValueError('initial run does not match the exact case being repeated')
        else:
            first_path,first=run(case_path,adapter_factory(key,'initial'),mode,config,runs_root,verbose,
                                 prepared_assets=asset_path,context={'batch_id':batch_id,'attempt_role':'initial','attempt_index':1})
        first_verdict=entry.get('initial_verdict') or (decision(first_path,first) if decision else classify(first))
        if first_verdict['status'] not in ('passed','not_passed','pending_review','blocked','interrupted'):
            raise ValueError('invalid evaluator verdict')
        attempts.append(('initial',first_path,first,first_verdict))
        append(output/'progress.jsonl',{'time':now(),'case':key,'event':'initial_decided','verdict':first_verdict})
        final_status=first_verdict['status']
        if first.get('reason')=='interrupted_by_operator' or final_status=='interrupted':
            aborted=True;final_status='interrupted'
        elif final_status=='not_passed':
            if mode!='replay' and asset_path is None:
                diagnostic={'role':'diagnostic','status':'blocked','reason':'prepared assets required; no runtime synthesis'}
            else:
                diagnostic_path,diagnostic_result=run(case_path,adapter_factory(key,'diagnostic'),mode,config,runs_root,verbose,
                    prepared_assets=asset_path,context={'batch_id':batch_id,'attempt_role':'diagnostic',
                        'attempt_index':2,'initial_run_id':first['run_id']})
                diagnostic_verdict=decision(diagnostic_path,diagnostic_result) if decision else classify(diagnostic_result)
                attempts.append(('diagnostic',diagnostic_path,diagnostic_result,diagnostic_verdict))
                if diagnostic_result.get('reason')=='interrupted_by_operator':aborted=True
                diagnostic={'role':'diagnostic','status':diagnostic_verdict['status']}
            # A diagnostic pass never changes the original gate failure.
        else:
            diagnostic=None
        row={'id':key,'case_id':case['case_id'],'status':final_status,'reason':first_verdict.get('reason'),
             'attempts':[{'role':role,'run_id':r['run_id'],'execution_status':r['execution_status'],
                          'verdict':v,'bundle_integrity_sha256':digest(p/'integrity.json')}
                         for role,p,r,v in attempts],
             'review_index':review_package(output,key,attempts)}
        if final_status=='not_passed':row['diagnostic']=diagnostic
        rows.append(row);by_id[key]=row;save()
    manifest['ended_at']=now();dump(output/'batch_manifest.json',manifest)
    result=save();seal(output)
    return output,result


def record_suite(output,destination=Path('records')):
    integrity=json.loads((output/'integrity.json').read_text(encoding='utf-8'))
    for name,sha in integrity['files'].items():
        p=safe_path(output,name)
        if not p.is_file() or digest(p)!=sha:raise ValueError('batch evidence changed: '+name)
    data=json.loads((output/'results.json').read_text(encoding='utf-8'))
    summary={'schema_version':1,'batch_id':data['batch_id'],'suite_id':data['suite_id'],'mode':data['mode'],
             'raw_evidence':'kept_local_not_in_git','batch_integrity_sha256':digest(output/'integrity.json'),
             'cases':[{'id':row['id'],'status':row['status'],
                 'initial_verdict':row['attempts'][0]['verdict']['status'] if row.get('attempts') else None,'attempts':[
                 {k:a[k] for k in ('role','run_id','execution_status','bundle_integrity_sha256')} for a in row.get('attempts',[])],
                 'review_index':'batches/'+output.name+'/'+row['review_index'] if row.get('review_index') else None} for row in data['cases']]}
    target=destination/output.name;target.mkdir(parents=True,exist_ok=False)
    dump(target/'suite_summary.json',summary)
    return target
