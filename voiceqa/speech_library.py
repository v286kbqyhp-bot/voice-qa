"""Versioned, reusable synthetic speech assets; never records or plays audio."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import array
import json
import os
from pathlib import Path
import platform
import shutil
import time
import uuid
import wave

import yaml

from .core import Blocked, ROOT, digest, dump, load_case, load_yaml, resolve, safe_path

DEFAULT_PROVIDER = 'macos_say'


def profile(text, voice='Tingting', rate=175, locale='zh-CN', provider=DEFAULT_PROVIDER):
    if not isinstance(text, str) or not text:
        raise ValueError('speech text must be a nonempty string')
    if not isinstance(voice, str) or not voice or type(rate) is not int or not 1 <= rate <= 500:
        raise ValueError('voice and rate 1..500 are required')
    if not isinstance(locale,str) or not locale or not isinstance(provider,str) or not provider:
        raise ValueError('locale and provider must be nonempty strings')
    return {'text': text, 'voice': voice, 'rate': rate, 'locale': locale, 'provider': provider}


def asset_id(spec):
    canonical=json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return 'tts-' + hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]


def read_index(root):
    path=Path(root)/'list.yaml'
    if not path.exists():
        return {'schema_version': 1, 'assets': []}
    data=load_yaml(path)
    if not isinstance(data, dict) or data.get('schema_version') != 1 or not isinstance(data.get('assets'), list):
        raise ValueError('speech-library/list.yaml requires schema_version=1 and assets')
    return data


def speech_info(path):
    from .adapters import wav_info
    info=wav_info(path)
    with wave.open(str(path),'rb') as f:
        nonzero=False
        while block:=f.readframes(4096):
            if any(array.array('h',block)):
                nonzero=True
                break
    if not nonzero:
        raise ValueError('speech library cannot contain an all-zero placeholder WAV')
    return info


def validate_library(root=ROOT/'speech-library'):
    """Return allowlisted, verified WAV paths. Used by Git delivery gates."""
    root=Path(root).resolve()
    data=read_index(root)
    accepted=set();seen=set()
    for item in data['assets']:
        if not isinstance(item,dict) or item.get('origin') != 'generated_tts':
            raise ValueError('only generated_tts assets are allowed in the speech library')
        spec=profile(**{k:item[k] for k in ('text','voice','rate','locale','provider')})
        identifier=asset_id(spec)
        if item.get('id') != identifier or identifier in seen:
            raise ValueError('speech asset id/profile mismatch or duplicate')
        seen.add(identifier)
        if item.get('file') != f'clips/{identifier}.wav':
            raise ValueError('speech asset must use its content profile id under clips/')
        path=safe_path(root,item['file'])
        if not path.is_file() or (root/item['file']).is_symlink() or (root/'clips').is_symlink():
            raise ValueError('speech asset missing or linked: '+item['file'])
        actual=speech_info(path)
        if actual['sha256'] != item.get('sha256') or path.stat().st_size != item.get('bytes'):
            raise ValueError('speech asset fingerprint changed: '+identifier)
        if actual['sample_rate'] != item.get('sample_rate') or actual['channels'] != item.get('channels'):
            raise ValueError('speech asset format metadata mismatch: '+identifier)
        if item.get('encoding') != 'PCM16_WAV' or abs(actual['duration_s']-item.get('duration_s',-1)) > 1/actual['sample_rate']:
            raise ValueError('speech asset encoding/duration metadata mismatch: '+identifier)
        accepted.add(path)
    clips=root/'clips'
    if clips.exists():
        for path in clips.rglob('*'):
            if path.is_file() and path.resolve() not in accepted:
                raise ValueError('unlisted speech asset: '+path.name)
    return accepted


class SpeechLibrary:
    def __init__(self, root=ROOT/'speech-library', synthesizer=None, runs_root=...,
                 locale='zh-CN', provider=DEFAULT_PROVIDER):
        self.root=Path(root).resolve()
        self.synthesizer=synthesizer
        if runs_root is ...:
            from .storage import data_root
            runs_root=data_root()/'runs'
        self.runs_root=Path(runs_root).resolve() if runs_root is not None else None
        self.locale=locale
        self.provider=provider

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True,exist_ok=True)
        lock=self.root/'.write-lock'
        deadline=time.monotonic()+60
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise Blocked('speech library write lock is busy') from None
                time.sleep(0.05)
        try:
            yield
        finally:
            lock.rmdir()

    def _prior_asset(self, spec):
        if self.runs_root is None or spec['provider'] != DEFAULT_PROVIDER or spec['locale'] != 'zh-CN':
            return None
        if not self.runs_root.exists():
            return None
        for manifest in sorted(self.runs_root.glob('*/tts/manifest.json')):
            try:
                entries=json.loads(manifest.read_text(encoding='utf-8'))
                run=json.loads((manifest.parent.parent/'run_manifest.json').read_text(encoding='utf-8'))
                case=load_yaml(manifest.parent.parent/'cases.yaml')
                if (run.get('host_platform') != 'Darwin' or run.get('mode') != 'live'
                        or run.get('capabilities',{}).get('adapter') != 'native'):
                    continue
                steps={s['name']:s.get('params',{}) for s in case.get('steps',[])+case.get('cleanup',[])
                       if s.get('kind')=='audio_play'}
                for name,item in entries.items():
                    params=steps.get(name,{})
                    if item.get('synthetic_fixture') is not False or item.get('text') != spec['text']:
                        continue
                    if params.get('text') != spec['text']:
                        continue
                    if params.get('voice','Tingting') != spec['voice'] or params.get('rate',175) != spec['rate']:
                        continue
                    if 'file' in params:
                        continue  # Prebuilt external inputs do not establish a TTS voice profile.
                    path=Path(item['path']).resolve()
                    if path.parent != manifest.parent.resolve() or not path.is_file() or path.suffix.lower() != '.wav':
                        continue
                    if digest(path) != item['sha256']:
                        continue
                    return path, {'kind':'verified_prior_tts_asset','run_id':manifest.parent.parent.name,
                                  'asset_manifest_sha256':digest(manifest),
                                  'profile_source':'same-text local macOS preparation with explicit/default voice and rate'}
            except (OSError,ValueError,KeyError,TypeError):
                continue
        return None

    def ensure(self, text, voice='Tingting', rate=175):
        from .adapters import NativeAdapter
        spec=profile(text,voice,rate,self.locale,self.provider)
        identifier=asset_id(spec)
        with self._locked():
            index=read_index(self.root)
            existing=next((x for x in index['assets'] if x.get('id')==identifier),None)
            if existing:
                if any(existing.get(k)!=v for k,v in spec.items()) or existing.get('origin')!='generated_tts':
                    raise ValueError('speech library profile mismatch')
                path=safe_path(self.root,existing['file'])
                if existing['file']!=f'clips/{identifier}.wav' or (self.root/existing['file']).is_symlink() or not path.is_file() or digest(path)!=existing['sha256']:
                    raise Blocked('listed speech asset is missing or changed; restore the verified library asset')
                return {**existing, **speech_info(path), 'library_id':identifier, 'reused':True}
            if (self.root/'clips').is_symlink():
                raise Blocked('speech library clips directory must not be a symlink')
            (self.root/'clips').mkdir(exist_ok=True)
            temporary=self.root/'clips'/('.'+uuid.uuid4().hex+'.wav')
            target=self.root/'clips'/(identifier+'.wav')
            provenance={'kind':'generated_now'}
            try:
                prior=self._prior_asset(spec)
                if prior:
                    source,provenance=prior
                    shutil.copyfile(source,temporary)
                else:
                    if self.synthesizer is None and self.provider==DEFAULT_PROVIDER and platform.system()!='Darwin':
                        raise Blocked('macos_say library miss: use the Mac/HTTP synthesis adapter; cached WAVs work on every platform')
                    adapter=self.synthesizer or NativeAdapter()
                    adapter.prepare({'text':text,'voice':voice,'rate':rate},temporary)
                info=speech_info(temporary)
                os.replace(temporary,target)
                entry={**spec,'id':identifier,'file':f'clips/{identifier}.wav','origin':'generated_tts',
                       'encoding':'PCM16_WAV','sample_rate':info['sample_rate'],'channels':info['channels'],
                       'duration_s':info['duration_s'],'sha256':info['sha256'],'bytes':target.stat().st_size,
                       'created_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
                       'provenance':provenance}
                index['assets'].append(entry)
                index['assets'].sort(key=lambda x:x['id'])
                staged=self.root/('.list-'+uuid.uuid4().hex+'.yaml')
                staged.write_text(yaml.safe_dump(index,allow_unicode=True,sort_keys=False),encoding='utf-8')
                os.replace(staged,self.root/'list.yaml')
                return {**entry,**speech_info(target),'library_id':identifier,'reused':bool(prior)}
            finally:
                if temporary.exists():temporary.unlink()

    def prepare_case(self, case_path, output, config=None):
        case_path=Path(case_path)
        case=load_case(case_path)
        from .storage import outside_checkout
        output=outside_checkout(output)
        output.mkdir(parents=True,exist_ok=False)
        assets={}
        for step in case['steps']+case.get('cleanup',[]):
            if step['kind']!='audio_play':continue
            params=resolve(step.get('params',{}),(config or {}).get('bindings',{}))
            if not params.get('text'):
                raise Blocked('library preparation requires exact text for '+step['name'])
            assets[step['name']]=self.ensure(params['text'],params.get('voice','Tingting'),params.get('rate',175))
        dump(output/'manifest.json',assets)
        dump(output/'preparation.json',{'case_id':case['case_id'],'case_sha256':digest(case_path),
             'source':'versioned_speech_library','manifest_sha256':digest(output/'manifest.json'),
             'playback_started':False,'capture_started':False})
        return output/'manifest.json'


def prepare_case(case_path, output, config=None):
    return SpeechLibrary().prepare_case(case_path,output,config)


def case_files(inputs):
    """Expand case files, folders or ordered suite/catalog references without execution."""
    output=[];seen=set()
    def add(path):
        path=Path(path).resolve()
        if path in seen:return
        seen.add(path)
        if path.is_dir():
            for child in sorted(path.rglob('*.yaml')):add(child)
            return
        data=load_yaml(path)
        if isinstance(data,dict) and 'cases' in data and 'steps' not in data:
            for entry in data['cases']:
                target=Path(entry['file'])
                if not target.is_absolute():
                    local=(path.parent/target).resolve()
                    target=local if local.exists() else (ROOT/target).resolve()
                add(target)
            return
        load_case(path)
        output.append(path)
    for path in inputs:add(path)
    return output


def sync_cases(inputs, library=None, config=None):
    library=library or SpeechLibrary()
    cases=case_files(inputs)
    before={x['id'] for x in read_index(library.root)['assets']}
    selected=set()
    for path in cases:
        case=load_case(path)
        for step in case['steps']+case.get('cleanup',[]):
            if step['kind']!='audio_play':continue
            params=resolve(step.get('params',{}),(config or {}).get('bindings',{}))
            if not params.get('text'):
                raise Blocked('exact text required for speech library import: '+step['name'])
            item=library.ensure(params['text'],params.get('voice','Tingting'),params.get('rate',175))
            selected.add(item['id'])
    verified=validate_library(library.root)
    after={x['id'] for x in read_index(library.root)['assets']}
    return {'case_count':len(cases),'selected_assets':len(selected),'added_assets':len(after-before),
            'library_assets':len(after),'verified_wav_files':len(verified),
            'list':str(library.root/'list.yaml'),'playback_started':False,'capture_started':False}
