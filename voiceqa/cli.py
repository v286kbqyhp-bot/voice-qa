from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .adapters import HttpAdapter, NativeAdapter, ReplayAdapter
from .core import Blocked, ROOT, load_case, load_yaml, resolve
from .runner import prepare_assets, record_bundle, run, verify_bundle


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8',errors='backslashreplace')
    parser = argparse.ArgumentParser(description='Portable YAML voice QA and evidence workflow')
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('validate', 'inspect'):
        p = sub.add_parser(command)
        p.add_argument('case', type=Path)
    p = sub.add_parser('run')
    p.add_argument('case', type=Path)
    p.add_argument('--mode', choices=['replay', 'live', 'http'], default='replay')
    p.add_argument('--fixture', type=Path)
    p.add_argument('--config', type=Path)
    p.add_argument('--adapter-url')
    p.add_argument('--output', type=Path, default=None)
    p.add_argument('--verbose', action='store_true', help='Stream progress to stderr while preserving JSON stdout')
    p.add_argument('--prepared-assets',type=Path,help='Reuse this prebuilt TTS manifest; missing/mismatched assets block without synthesis')
    p = sub.add_parser('watch')
    p.add_argument('--root',type=Path,default=None)
    p.add_argument('--run',default='latest')
    p.add_argument('--device-log',action='store_true')
    p=sub.add_parser('prepare')
    p.add_argument('case',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--config',type=Path)
    p=sub.add_parser('library-sync',help='Reuse library speech and generate only missing phrases; never plays audio')
    p.add_argument('inputs',nargs='*',type=Path)
    p.add_argument('--library-root',type=Path,default=ROOT/'speech-library')
    p.add_argument('--config',type=Path)
    p.add_argument('--adapter-url',help='Optional remote synthesis adapter')
    p.add_argument('--provider',default='macos_say')
    p=sub.add_parser('library-prepare',help='Prepare one case entirely from the reusable speech library')
    p.add_argument('case',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--library-root',type=Path,default=ROOT/'speech-library')
    p.add_argument('--config',type=Path)
    p.add_argument('--adapter-url')
    p.add_argument('--provider',default='macos_say')
    p=sub.add_parser('run-suite')
    p.add_argument('suite',type=Path)
    p.add_argument('--mode',choices=['replay','live','http'],default='replay')
    p.add_argument('--fixture',type=Path)
    p.add_argument('--config',type=Path)
    p.add_argument('--adapter-url')
    p.add_argument('--output',type=Path,default=None)
    p.add_argument('--verbose',action='store_true')
    for command in ('verify', 'record', 'record-suite'):
        p = sub.add_parser(command)
        p.add_argument('bundle', type=Path)
    p = sub.add_parser('import-analysis')
    p.add_argument('bundle', type=Path)
    p.add_argument('analysis', type=Path)
    sub.add_parser('gate')
    sub.add_parser('install-hooks')
    p = sub.add_parser('hook')
    p.add_argument('name', choices=['pre-commit', 'pre-push', 'post-commit'])
    p = sub.add_parser('serve')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--config', type=Path)
    p.add_argument('--root', type=Path, default=None)
    p=sub.add_parser('review-prepare',help='Prepare literal server text or independent ASR and the original template for GPT review')
    p.add_argument('bundle',type=Path);p.add_argument('--asr',type=Path);p.add_argument('--template',type=Path,required=True);p.add_argument('--output',type=Path)
    p.add_argument('--evidence-source',choices=['recording_asr','server_text'],default='recording_asr')
    p.add_argument('--timing',type=Path,help='Verified timing sidecar for an older sealed run')
    p=sub.add_parser('review-import',help='Validate GPT reconstruction and render a local source-template receipt')
    p.add_argument('review_dir',type=Path);p.add_argument('assessment',type=Path)
    p.add_argument('--final', action='store_true', help='Refuse final delivery with unresolved required criteria')
    p=sub.add_parser('timing',help='Calculate per-turn delays and acoustic estimates from a sealed run; never plays audio')
    p.add_argument('bundle',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--policy',type=Path,help='Recording SHA and reviewed body_segment_ids per step')
    sub.add_parser('paths')
    sub.add_parser('doctor')
    p = sub.add_parser('synthesize')
    p.add_argument('text')
    p.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command in ('validate', 'inspect'):
            case = load_case(args.case)
            missing = []
            for step in case['steps']:
                try:
                    resolve(step.get('params', {}), {})
                except Blocked as exc:
                    missing.append({'step': step['name'], 'blocker': str(exc)})
            print(json.dumps({'case_id': case['case_id'], 'schema_valid': True, 'unbound': missing}, ensure_ascii=False))
        elif args.command == 'run':
            if args.mode == 'replay':
                if not args.fixture:
                    raise ValueError('replay requires --fixture; fixture events are never live evidence')
                adapter = ReplayAdapter(json.loads(args.fixture.read_text(encoding='utf-8'))['events'])
            elif args.mode == 'http':
                if not args.adapter_url:
                    raise ValueError('--adapter-url required')
                adapter = HttpAdapter(args.adapter_url, os.environ.get('VQA_API_TOKEN'))
            else:
                adapter = NativeAdapter()
            config = load_yaml(args.config) if args.config else (load_yaml(ROOT/'config.local.yaml') if (ROOT/'config.local.yaml').is_file() else {})
            output, result = run(args.case, adapter, args.mode, config, args.output,verbose=args.verbose,prepared_assets=args.prepared_assets)
            print(json.dumps({'bundle': str(output.resolve()), 'execution': result['execution_status'],
                              'product': result['product_status'], 'reason': result['reason']}, ensure_ascii=False))
            return 0 if result['execution_status'] == 'completed' else 2
        elif args.command == 'verify':
            print(json.dumps(verify_bundle(args.bundle), ensure_ascii=False))
        elif args.command=='run-suite':
            from .batch import run_suite
            config=load_yaml(args.config) if args.config else {}
            fixture=json.loads(args.fixture.read_text(encoding='utf-8')) if args.fixture else {}
            if args.mode=='replay' and not args.fixture:raise ValueError('replay suite requires --fixture')
            if args.mode=='http' and not args.adapter_url:raise ValueError('HTTP suite requires --adapter-url')
            def factory(key,role):
                if args.mode=='replay':return ReplayAdapter(fixture.get('cases',{}).get(key,{}).get(role,fixture.get('events',[])))
                if args.mode=='http':return HttpAdapter(args.adapter_url,os.environ.get('VQA_API_TOKEN'))
                return NativeAdapter()
            output,result=run_suite(args.suite,factory,args.mode,config,args.output,None,verbose=args.verbose)
            print(json.dumps({'batch':str(output.resolve()),'cases':[{k:r[k] for k in ['id','status']} for r in result['cases']]},ensure_ascii=False))
            return 2 if any(r['status'] in ('not_passed','blocked','interrupted') for r in result['cases']) else 0
        elif args.command == 'prepare':
            config=load_yaml(args.config) if args.config else {}
            output=prepare_assets(args.case,args.output,NativeAdapter(),config)
            print(json.dumps({'prepared_assets':str(output.resolve()),'playback_started':False},ensure_ascii=False))
        elif args.command in ('library-sync','library-prepare'):
            from .speech_library import SpeechLibrary, sync_cases
            config=load_yaml(args.config) if args.config else {}
            synthesizer=HttpAdapter(args.adapter_url,os.environ.get('VQA_API_TOKEN')) if args.adapter_url else None
            library=SpeechLibrary(args.library_root,synthesizer=synthesizer,provider=args.provider)
            if args.command=='library-sync':
                inputs=args.inputs or [ROOT/'cases']
                print(json.dumps(sync_cases(inputs,library,config),ensure_ascii=False))
            else:
                output=library.prepare_case(args.case,args.output,config)
                print(json.dumps({'prepared_assets':str(output.resolve()),'playback_started':False,'source':'speech_library'},ensure_ascii=False))
        elif args.command == 'watch':
            from .live import watch
            try:
                from .storage import output_path
                watch(output_path(args.root,'runs'),args.run,args.device_log)
            except KeyboardInterrupt:
                pass
        elif args.command == 'record':
            print(record_bundle(args.bundle).resolve())
        elif args.command=='record-suite':
            from .batch import record_suite
            print(record_suite(args.bundle).resolve())
        elif args.command == 'import-analysis':
            from .analysis import import_analysis
            print(import_analysis(args.bundle, args.analysis).resolve())
        elif args.command == 'gate':
            from .gitflow import gate
            gate()
        elif args.command == 'install-hooks':
            from .gitflow import install_hooks
            install_hooks()
        elif args.command == 'hook':
            from .gitflow import hook
            hook(args.name)
        elif args.command == 'serve':
            from .service import AdapterServer
            config = load_yaml(args.config) if args.config else (load_yaml(ROOT/'config.local.yaml') if (ROOT/'config.local.yaml').is_file() else {})
            from .storage import output_path
            server = AdapterServer((args.host, args.port), output_path(args.root,'adapter-service',config), os.environ.get('VQA_API_TOKEN'), config=config)
            print(f'Adapter API listening on {args.host}:{server.server_port}; lease 180s', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        elif args.command == 'synthesize':
            print(json.dumps(NativeAdapter().prepare({'text': args.text}, args.output), ensure_ascii=False))
        elif args.command=='review-prepare':
            from .semantic_review import prepare_review
            print(prepare_review(args.bundle,args.asr,args.template,args.output,evidence_source=args.evidence_source,timing_file=args.timing))
        elif args.command=='review-import':
            from .semantic_review import import_review
            print(import_review(args.review_dir,args.assessment,final=args.final))
        elif args.command=='timing':
            from .timing import export_timing
            policy=json.loads(args.policy.read_text(encoding='utf-8')) if args.policy else None
            print(export_timing(args.bundle,args.output,policy))
        elif args.command=='paths':
            from .storage import data_root
            print(json.dumps({'runtime_root':str(ROOT),'data_root':str(data_root())},ensure_ascii=False))
        elif args.command == 'doctor':
            import sounddevice as sd
            print(json.dumps({'python': sys.version.split()[0], 'audio_devices': list(sd.query_devices()),
                              'adapter': NativeAdapter().capabilities()}, ensure_ascii=False))
    except (Blocked, ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0
