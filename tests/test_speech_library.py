import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

import yaml

from voiceqa.adapters import wav_info
from voiceqa.core import Blocked, dump
from voiceqa.speech_library import SpeechLibrary, asset_id, profile, read_index, sync_cases, validate_library


class FileSynthesizer:
    def __init__(self):
        self.calls=[]

    def prepare(self, params, output):
        self.calls.append(dict(params))
        amplitude=int(hashlib.sha256(json.dumps(params,sort_keys=True).encode()).hexdigest()[:4],16)%500+1
        with wave.open(str(output),'wb') as f:
            f.setparams((1,2,16000,0,'NONE','not compressed'))
            f.writeframes(amplitude.to_bytes(2,'little',signed=True)*1600)
        return {**wav_info(output),'text':params['text'],'synthetic_fixture':False}


class SpeechLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.synth=FileSynthesizer()
        self.library=SpeechLibrary(self.root/'speech-library',self.synth,runs_root=None,provider='test_tts')

    def tearDown(self):
        self.temp.cleanup()

    def write_case(self, path, text='我喜欢皮卡丘。', identifier='LIBRARY-TEST'):
        data={'schema_version':1,'case_id':identifier,'title':'library fixture','domain':'smoke','severity':'normal',
              'steps':[{'kind':'audio_play','name':'say','params':{'text':text}}]}
        path.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
        return path

    def test_same_exact_profile_reuses_file_without_synthesizing_again(self):
        first=self.library.ensure('我喜欢皮卡丘。')
        second=self.library.ensure('我喜欢皮卡丘。')
        self.assertEqual(len(self.synth.calls),1)
        self.assertEqual(first['sha256'],second['sha256'])
        self.assertTrue(second['reused'])
        self.assertEqual(len(read_index(self.library.root)['assets']),1)
        self.assertEqual(validate_library(self.library.root),{Path(first['path'])})

    def test_text_punctuation_voice_and_rate_each_have_independent_identity(self):
        assets=[self.library.ensure('你好。'),self.library.ensure('你好'),self.library.ensure('你好。',rate=180),
                self.library.ensure('你好。',voice='AnotherVoice')]
        self.assertEqual(len({x['id'] for x in assets}),4)
        self.assertEqual(len(self.synth.calls),4)
        self.assertEqual(len(validate_library(self.library.root)),4)

    def test_silent_placeholder_cannot_be_added_as_generated_speech(self):
        class SilentGenerator:
            def prepare(self,params,output):
                with wave.open(str(output),'wb') as f:
                    f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\0\0'*1600)
        library=SpeechLibrary(self.library.root,SilentGenerator(),runs_root=None)
        with self.assertRaisesRegex(ValueError,'all-zero'):library.ensure('hello')
        self.assertFalse((self.library.root/'list.yaml').exists())

    def test_changed_asset_fails_closed_without_automatic_resynthesis(self):
        item=self.library.ensure('hello')
        with Path(item['path']).open('ab') as f:f.write(b'changed')
        with self.assertRaises(Blocked):self.library.ensure('hello')
        with self.assertRaises(ValueError):validate_library(self.library.root)
        self.assertEqual(len(self.synth.calls),1)

    def test_unlisted_wave_and_non_generated_origin_are_rejected(self):
        item=self.library.ensure('hello')
        rogue=self.library.root/'clips/mic_raw.wav'
        shutil.copyfile(item['path'],rogue)
        with self.assertRaisesRegex(ValueError,'unlisted'):validate_library(self.library.root)
        rogue.unlink()
        index=read_index(self.library.root)
        index['assets'][0]['origin']='microphone_recording'
        (self.library.root/'list.yaml').write_text(yaml.safe_dump(index),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'generated_tts'):validate_library(self.library.root)

    def test_index_cannot_point_outside_library(self):
        self.library.ensure('hello')
        index=read_index(self.library.root)
        index['assets'][0]['file']='../../mic_raw.wav'
        (self.library.root/'list.yaml').write_text(yaml.safe_dump(index),encoding='utf-8')
        with self.assertRaises(ValueError):validate_library(self.library.root)

    def test_prepare_manifest_points_to_library_without_capture_or_playback(self):
        source=self.write_case(self.root/'case.yaml')
        manifest=self.library.prepare_case(source,self.root/'prepared')
        entries=json.loads(manifest.read_text(encoding='utf-8'))
        self.assertEqual(set(entries),{'say'})
        self.assertTrue(Path(entries['say']['path']).is_absolute())
        self.assertEqual(Path(entries['say']['path']).parent,self.library.root/'clips')
        metadata=json.loads((manifest.parent/'preparation.json').read_text(encoding='utf-8'))
        self.assertFalse(metadata['playback_started'])
        self.assertFalse(metadata['capture_started'])
        self.assertEqual(len(self.synth.calls),1)

    def prior_run(self, mode='live', synthetic=False, outside_tts=False):
        run=self.root/'runs/run-1';(run/'tts').mkdir(parents=True)
        source=self.write_case(run/'cases.yaml',text='hello')
        audio=(run/'audio/mic_raw.wav') if outside_tts else run/'tts/say.wav'
        audio.parent.mkdir(exist_ok=True)
        self.synth.prepare({'text':'hello'},audio)
        dump(run/'tts/manifest.json',{'say':{**wav_info(audio),'text':'hello','synthetic_fixture':synthetic}})
        dump(run/'run_manifest.json',{'host_platform':'Darwin','mode':mode,'capabilities':{'adapter':'native'}})
        self.synth.calls=[]
        return run

    def test_verified_real_prior_tts_is_imported_without_generation(self):
        self.prior_run()
        library=SpeechLibrary(self.library.root,self.synth,runs_root=self.root/'runs')
        item=library.ensure('hello')
        self.assertEqual(self.synth.calls,[])
        self.assertEqual(item['provenance']['kind'],'verified_prior_tts_asset')
        self.assertEqual(len(validate_library(library.root)),1)

    def test_replay_fixture_and_microphone_path_are_never_imported(self):
        for mode,synthetic,outside in [('replay',False,False),('live',True,False),('live',False,True)]:
            with self.subTest(mode=mode,synthetic=synthetic,outside=outside):
                if (self.root/'runs').exists():shutil.rmtree(self.root/'runs')
                if self.library.root.exists():shutil.rmtree(self.library.root)
                self.prior_run(mode,synthetic,outside)
                library=SpeechLibrary(self.library.root,self.synth,runs_root=self.root/'runs')
                item=library.ensure('hello')
                self.assertEqual(len(self.synth.calls),1)
                self.assertEqual(item['provenance']['kind'],'generated_now')

    def test_historical_source_case_text_must_match_manifest(self):
        run=self.prior_run()
        case=yaml.safe_load((run/'cases.yaml').read_text(encoding='utf-8'))
        case['steps'][0]['params']['text']='different text'
        (run/'cases.yaml').write_text(yaml.safe_dump(case),encoding='utf-8')
        library=SpeechLibrary(self.library.root,self.synth,runs_root=self.root/'runs')
        self.assertEqual(library.ensure('hello')['provenance']['kind'],'generated_now')
        self.assertEqual(len(self.synth.calls),1)

    def test_cached_speech_works_on_windows_without_a_synthesis_backend(self):
        expected=self.library.ensure('hello')
        library=SpeechLibrary(self.library.root,runs_root=None,provider='test_tts')
        with patch('voiceqa.speech_library.platform.system',return_value='Windows'), \
                patch('voiceqa.adapters.NativeAdapter',side_effect=AssertionError('must not initialize synthesis')):
            actual=library.ensure('hello')
        self.assertEqual(actual['sha256'],expected['sha256'])
        self.assertTrue(actual['reused'])

    def test_windows_library_miss_requires_matching_remote_or_mac_provider(self):
        library=SpeechLibrary(self.library.root,runs_root=None)
        with patch('voiceqa.speech_library.platform.system',return_value='Windows'):
            with self.assertRaisesRegex(Blocked,'Mac/HTTP'):library.ensure('missing')

    def test_sync_deduplicates_phrases_across_cases_and_follows_suite_files(self):
        cases=self.root/'cases';cases.mkdir()
        self.write_case(cases/'one.yaml',text='shared',identifier='ONE')
        self.write_case(cases/'two.yaml',text='shared',identifier='TWO')
        suite=self.root/'suite.yaml'
        suite.write_text(yaml.safe_dump({'cases':[{'file':'cases/one.yaml'},{'file':'cases/two.yaml'}]}),encoding='utf-8')
        result=sync_cases([suite],self.library)
        self.assertEqual(result['case_count'],2)
        self.assertEqual(result['selected_assets'],1)
        self.assertEqual(result['added_assets'],1)
        self.assertEqual(len(self.synth.calls),1)


if __name__=='__main__':unittest.main()
