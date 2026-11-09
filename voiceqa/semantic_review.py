"""Labeled server text or recording ASR -> GPT -> local source-template receipt."""
import json
from pathlib import Path
import uuid
import jsonschema

from .core import ROOT, digest, dump, load_case, load_yaml
from .runner import verify_bundle, now
from .storage import data_root, outside_checkout


def prepare_review(bundle,asr_file,template_file,destination=None,*,evidence_source='recording_asr'):
    # Older sealed execution placeholders may truncate a long source template.
    # Verify their immutable evidence; validate the final receipt's own complete
    # source-template fields independently, without rewriting the sealed run.
    bundle=Path(bundle).resolve();results=verify_bundle(bundle,check_receipt_fields=False);manifest=json.loads((bundle/'run_manifest.json').read_text(encoding='utf-8'));case=load_case(bundle/'cases.yaml')
    template=load_yaml(template_file);fields=template.get('fields',[]);criteria=template.get('criteria',[])
    if not fields or len(fields)!=len(set(fields)) or not all(isinstance(x,str) and x.strip() for x in fields):raise ValueError('template requires original unique feedback fields')
    if not criteria or len({c['id'] for c in criteria})!=len(criteria) or not all(c.get('description') for c in criteria):raise ValueError('template requires explicit acceptance criteria with unique ids')
    if evidence_source not in ('recording_asr','server_text'):raise ValueError('unsupported review evidence source')
    if evidence_source=='server_text' and asr_file:raise ValueError('server_text review does not consume --asr')
    asr_file=Path(asr_file).resolve() if asr_file else bundle/'asr/transcript.json';audio=bundle/'audio/mic_raw.wav'
    asr=json.loads(asr_file.read_text(encoding='utf-8')) if evidence_source=='recording_asr' and asr_file.is_file() else {'status':'not_requested' if evidence_source=='server_text' else 'unavailable','segments':[]}
    if asr.get('segments') and (not audio.is_file() or asr.get('audio_sha256')!=digest(audio)):raise ValueError('ASR does not refer to this recording')
    if asr.get('segments') and asr.get('contract')!='recording-asr-v1':raise ValueError('review requires independent recording-asr-v1; device captions are diagnostic only')
    segments=[]
    for index,s in enumerate(asr.get('segments',[]),1):
        if not 0<=s['start_s']<=s['end_s']<=asr['duration_s']+.01:raise ValueError('ASR segment outside recording')
        segments.append({**s,'segment_id':s.get('segment_id',f'asr-{index:04d}')})
    if len({s['segment_id'] for s in segments})!=len(segments):raise ValueError('ASR segment ids must be unique')
    events=[json.loads(x) for x in (bundle/'events.jsonl').read_text(encoding='utf-8').splitlines()] if (bundle/'events.jsonl').exists() else []
    server_text=None
    if evidence_source=='server_text':
        from .server_text import collect_server_text
        server_text=collect_server_text(events,results,case)
        segments=server_text['segments']
    diagnostics=[e for e in events if e['event'] in ('voice.asr_text_changed','voice.tts_text_changed','session_end_requested') and (e.get('fields',{}).get('is_final')=='true' or e['event']=='session_end_requested')]
    target=outside_checkout(destination) if destination else data_root()/'reviews'/bundle.name/uuid.uuid4().hex[:10];target.mkdir(parents=True,exist_ok=False)
    request={'contract':'dialogue-review-request-v1','created_at':now(),'run_id':bundle.name,'case_id':results['case_id'],'bundle':str(bundle),'bundle_integrity_sha256':digest(bundle/'integrity.json'),'audio_file':str(audio),'audio_sha256':digest(audio) if audio.is_file() else None,'asr_file':str(asr_file),'asr_sha256':digest(asr_file) if asr_file.is_file() else None,'raw_asr':asr,'segments':segments,'case':case,'execution':results,'manifest':manifest,'diagnostic_device_text_not_audio_asr':diagnostics,'template_fields':fields,'template_values':template.get('values',{}),'criteria':criteria,'metadata':template.get('metadata',{}),'review_instructions':['Read ASR raw text first. Case/known input and device text are auxiliary context, never overwrite raw ASR.','Correct homophones only in reconstructed dialogue, cite source segment ids and mark inferred/unclear.','Expected device answers cannot fill missing speech. Unclear critical speech => inconclusive.','Judge actual reply semantics against each criterion; device ASR text mismatch alone is not a product failure.','Keep physical actions, trace/task identity and silence/overlap unverified unless their evidence establishes them.']}
    request['evidence_source']=evidence_source
    if evidence_source=='server_text':
        request.update(server_text=server_text,asr_file=None,asr_sha256=None,
                       review_instructions=['Read literal trace-bound server reply text and device input ASR. Neither is independent acoustic ASR.',
                                            'Keep partial/final raw events and native segment ids. Never fill missing text from expected answers.',
                                            'Only explicit reply_semantics/input_recognition criteria may PASS from text alone. Acoustic, physical, human and backend task criteria require their own evidence.',
                                            'Received/final text does not prove spoken words, full playback, speaker timing, continuity or real tool calls.'])
    dump(target/'request.json',request)
    dump(target/'assessment.template.json',{'request_sha256':digest(target/'request.json'),'reviewer':'','dialogue':[],'criteria':[{'id':c['id'],'verdict':'inconclusive','reason':'尚未语义复核','segment_ids':[],'limitations':[]} for c in criteria],'receipt_fields':{f:template.get('values',{}).get(f,'待填') for f in fields},'summary':'尚未语义复核'})
    (target/'NEXT.md').write_text('由当前OpenAI GPT Agent读取request.json，按schema/semantic-review.schema.json写assessment.json，再运行review-import。没有GPT评审时保持pending，不伪造调用。\n',encoding='utf-8')
    return target


def validate_review(folder,assessment):
    folder=Path(folder).resolve();request_file=folder/'request.json';req=json.loads(request_file.read_text(encoding='utf-8'))
    jsonschema.validate(assessment,json.loads((ROOT/'schema/semantic-review.schema.json').read_text(encoding='utf-8')))
    if assessment['request_sha256']!=digest(request_file):raise ValueError('review refers to a different request')
    bundle=Path(req['bundle']);sealed_results=verify_bundle(bundle,check_receipt_fields=False)
    if req['execution']!=sealed_results:raise ValueError('execution differs from sealed run')
    if digest(bundle/'integrity.json')!=req['bundle_integrity_sha256']:raise ValueError('run changed since review preparation')
    if req['audio_sha256'] and digest(Path(req['audio_file']))!=req['audio_sha256']:raise ValueError('recording changed')
    if req['asr_sha256'] and digest(Path(req['asr_file']))!=req['asr_sha256']:raise ValueError('raw ASR changed')
    ids={s['segment_id'] for s in req['segments']}
    for item in assessment['dialogue']+assessment['criteria']:
        if not set(item['segment_ids'])<=ids:raise ValueError('review cites an unknown ASR segment')
    if set(assessment['receipt_fields'])!=set(req['template_fields']):raise ValueError('feedback fields must exactly match the source template')
    if len(assessment['criteria'])!=len(req['criteria']) or {c['id'] for c in assessment['criteria']}!={c['id'] for c in req['criteria']}:raise ValueError('review must cover every original acceptance criterion exactly once')
    capture=req['manifest'].get('capture_result',{}).get('audio',{})
    server_mode=req.get('evidence_source')=='server_text'
    if server_mode:
        from .server_text import collect_server_text
        events=[json.loads(x) for x in (bundle/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        actual=collect_server_text(events,sealed_results,load_case(bundle/'cases.yaml'))
        if actual!=req['server_text'] or actual['segments']!=req['segments']:raise ValueError('server text differs from sealed run events')
        scopes={c['id']:c.get('evidence_scope') for c in req['criteria']}
        sources={s['segment_id']:s['source'] for s in req['segments']}
    for c in assessment['criteria']:
        if c['verdict']!='pass':continue
        if req['execution']['mode']=='replay' or req['execution']['execution_status']!='completed' or not ids or not c['segment_ids']:
            raise ValueError('PASS requires executed live steps and referenced evidence; keep incomplete evidence inconclusive')
        if server_mode:
            required={'reply_semantics':'server_reply_text','input_recognition':'device_input_asr'}.get(scopes[c['id']])
            if not required or not any(sources[s]==required for s in c['segment_ids']):
                raise ValueError('server text PASS requires explicit text scope and corresponding final evidence; acoustic/physical/task/human acceptance remains unverified')
        elif not capture.get('valid_capture'):
            raise ValueError('PASS requires a valid recording and referenced ASR')
    verdicts={c['verdict'] for c in assessment['criteria']}
    overall='fail' if 'fail' in verdicts else 'pass' if verdicts=={'pass'} else 'blocked' if verdicts<={'blocked','not_run'} and 'blocked' in verdicts else 'not_run' if verdicts=={'not_run'} else 'inconclusive'
    return req,overall


def render_receipt(folder,assessment,req,overall):
    text=f"# {req['case_id']} 测试回执\n\n判定：**{overall}**。{assessment['summary']}\n\n测试人：{req['metadata'].get('tester',req['manifest'].get('operator','未提供'))}；语义评审：{assessment['reviewer']}。\n\n"
    for f in req['template_fields']:text+=f"- {f}：{assessment['receipt_fields'][f]}\n"
    server_mode=req.get('evidence_source')=='server_text'
    if server_mode:
        text+='\n## 服务端回复文本与设备输入识别（原文）\n\n本轮未运行独立ASR。下列为当前绑定会话的final文本；partial/final完整事件保留在评审输入包。客户端收到文本不等于扬声器已完整播放，观察时间不是物理首音。\n\n'
        for s in req['segments']:text+=f"- `{s['segment_id']}` [{s['source']} / trace={s['trace']} / segment={s['native_segment_id']}] {s['text']}\n"
        if not req['segments']:text+='未取得有效final文本；应使用独立录音ASR或保留未判。\n'
    else:
        live=Path(req['asr_file']).parent/'live.jsonl'
        mode='实时流final汇总（partial与final JSONL保留）' if live.is_file() else '历史录音文件后处理（非实时）'
        text+=f'\n## 原始录音ASR字幕\n\n来源：{mode}。原文未按预期或设备字幕改写；时间为模型估计。\n\n'
        if live.is_file():text+=f'[完整实时partial/final字幕](<{live}>)\n\n'
        for s in req['segments']:text+=f"- `{s['segment_id']}` [{s['start_s']:.2f}–{s['end_s']:.2f}s] {s['text']}\n"
        if not req['segments']:text+='未取得可用字幕。\n'
    text+='\n## GPT推测／还原对话\n\n这是基于字幕与上下文的语义还原，不冒充逐字实听。\n\n'
    for turn in assessment['dialogue']:text+=f"- **{turn['speaker']}**：{turn['text']} 〔{turn['certainty']}；依据 {', '.join(turn['segment_ids'])}〕\n  - 还原依据：{turn['rationale']}\n"
    text+='\n## 逐项语义判断\n\n'
    for c in assessment['criteria']:text+=f"- **{c['id']} / {c['verdict']}**：{c['reason']}（依据：{', '.join(c['segment_ids']) or '未执行/缺证据'}）\n"+''.join(f"  - 限制：{x}\n" for x in c['limitations'])
    text+=f"\n## 本机证据\n\n- [原录音](<{req['audio_file']}>)\n"
    if req['asr_file']:text+=f"- [ASR原文件](<{req['asr_file']}>)\n"
    text+=f"- [原始运行](<{req['bundle']}>)\n- [评审输入包](<{folder/'request.json'}>)\n\n服务端回复文本、设备输入ASR与独立录音ASR分别标注，不互相冒充。\n"
    return text


def import_review(folder,assessment_file):
    folder=outside_checkout(folder);assessment_file=outside_checkout(assessment_file);assessment=json.loads(assessment_file.read_text(encoding='utf-8'));req,overall=validate_review(folder,assessment)
    if (folder/'review.json').exists() or (folder/'receipt.md').exists():raise FileExistsError('preserve earlier semantic review; prepare a new review to revise it')
    dump(folder/'review.json',assessment);(folder/'receipt.md').write_text(render_receipt(folder,assessment,req,overall),encoding='utf-8')
    dump(folder/'review-integrity.json',{'request_sha256':digest(folder/'request.json'),'review_sha256':digest(folder/'review.json'),'receipt_sha256':digest(folder/'receipt.md'),'overall':overall,'created_at':now()})
    return folder/'receipt.md'
