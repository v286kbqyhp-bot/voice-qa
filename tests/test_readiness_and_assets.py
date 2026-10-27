import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import wave

from voiceqa.adapters import NativeAdapter, match_event, wav_info
from voiceqa.core import dump
from voiceqa.runner import prepare_assets, run


class ReadinessAndAssetTests(unittest.TestCase):
    def test_fallback_requires_explicit_opt_in_and_current_trace(self):
        event={'event':'input_ready_fallback','trace':'current'}
        self.assertFalse(match_event(event,{'event':'input_ready'}))
        params={'event':'input_ready','alternative_events':['input_ready_fallback'],'same_trace':True}
        self.assertFalse(match_event(event,params,'old'))
        self.assertTrue(match_event(event,params,'current'))

    def test_cached_only_run_never_synthesizes_and_missing_asset_blocks_before_capture(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audio=root/'prebuilt.wav'
            with wave.open(str(audio),'wb') as f:
                f.setparams((1,2,16000,0,'NONE','not compressed'));f.writeframes(b'\1\0'*1600)
            case={'case_id':'CACHE','title':'cache test','domain':'smoke','severity':'normal',
                  'requires':{'target':'host'},'steps':[{'kind':'audio_play','name':'say','params':{'text':'预生成话术'}}]}
            source=root/'case.yaml';source.write_text(yaml.safe_dump(case,allow_unicode=True),encoding='utf-8')
            manifest=root/'manifest.json';dump(manifest,{'say':{**wav_info(audio),'text':'预生成话术'}})
            tts=Mock();backend=Mock()
            backend.backend_name='test_audio'
            backend.start.return_value={};backend.play.return_value={};backend.stop.return_value={'valid_capture':True}
            p,result=run(source,NativeAdapter(tts=tts,audio=backend),'live',output_root=root/'runs',prepared_assets=manifest)
            self.assertEqual(result['execution_status'],'completed');tts.synthesize.assert_not_called()
            backend.play.assert_called_once()
            backend.reset_mock();dump(manifest,{})
            p,result=run(source,NativeAdapter(tts=tts,audio=backend),'live',output_root=root/'runs',prepared_assets=manifest)
            self.assertEqual(result['execution_status'],'blocked')
            backend.start.assert_not_called();tts.synthesize.assert_not_called()

    def test_prepare_creates_assets_without_play_or_capture(self):
        import yaml
        from voiceqa.adapters import ReplayAdapter
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'case.yaml'
            source.write_text(yaml.safe_dump({'case_id':'PREPARE','title':'prepare','domain':'smoke','severity':'normal',
                'steps':[{'kind':'audio_play','name':'say','params':{'text':'hello'}}]}),encoding='utf-8')
            adapter=ReplayAdapter();adapter.begin=Mock(side_effect=AssertionError('capture must not start'))
            manifest=prepare_assets(source,root/'assets',adapter)
            self.assertTrue(manifest.exists());adapter.begin.assert_not_called();self.assertEqual(adapter.played,[])
