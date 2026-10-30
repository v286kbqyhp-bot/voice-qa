import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from voiceqa.adapters import ReplayAdapter
from voiceqa.core import ROOT
from voiceqa.runner import run,verify_bundle
from voiceqa.streaming_asr import pcm_header


class AsrPipelineTests(unittest.TestCase):
    def test_growing_wav_header_and_format_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'audio.wav'
            with p.open('wb',buffering=0) as raw:
                with wave.open(raw,'wb') as w:
                    w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\0\0'*160)
                    offset,rate=pcm_header(p);self.assertEqual(rate,16000)
                    with p.open('rb') as f:f.seek(offset);self.assertEqual(len(f.read()),320)
                    w.writeframes(b'\0\0'*160)
                    with p.open('rb') as f:f.seek(offset);self.assertEqual(len(f.read()),640)
            with wave.open(str(p),'wb') as w:w.setparams((2,2,16000,0,'NONE','not compressed'));w.writeframes(b'\0'*640)
            with self.assertRaisesRegex(ValueError,'mono PCM16'):pcm_header(p)

    def test_asr_started_before_first_input_and_finished_after_capture_closes(self):
        calls=[]
        class FixtureNative(ReplayAdapter):
            def capabilities(self):return {'adapter':'native','audio_capture':True,'audio_play':True}
            def begin(self,output,config):
                calls.append('capture_start')
                with wave.open(str(output/'audio/mic_raw.wav'),'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\1\0'*1600)
                return {'fixture_only':True}
            def play(self,asset):calls.append('play');return super().play(asset)
            def finish(self):calls.append('capture_stop');return {'audio':{'valid_capture':True}}
        class FakeAsr:
            def __init__(self,*args):calls.append('asr_start')
            def finish(self):calls.append('asr_finish');return {'status':'completed'}
        fixture=json.loads((ROOT/'fixtures/workflow.json').read_text())['events']
        with tempfile.TemporaryDirectory() as tmp,patch('voiceqa.streaming_asr.LiveAsrProcess',FakeAsr):
            p,result=run(ROOT/'cases/voice-smoke.yaml',FixtureNative(fixture),'live',{'asr':{'live':True}},Path(tmp)/'runs')
            verify_bundle(p);self.assertEqual(result['execution_status'],'completed')
            self.assertLess(calls.index('capture_start'),calls.index('asr_start'));self.assertLess(calls.index('asr_start'),calls.index('play'));self.assertLess(calls.index('capture_stop'),calls.index('asr_finish'))
            self.assertEqual(result['product_status'],'needs_review')

    def test_asr_initialization_error_closes_capture_without_playing_inputs(self):
        calls=[]
        class FixtureNative(ReplayAdapter):
            def capabilities(self):return {'adapter':'native'}
            def begin(self,*args):calls.append('capture_start');return {}
            def finish(self):calls.append('capture_stop');return {}
        with tempfile.TemporaryDirectory() as tmp,patch('voiceqa.streaming_asr.LiveAsrProcess',side_effect=ValueError('model unavailable')):
            adapter=FixtureNative();p,result=run(ROOT/'cases/voice-smoke.yaml',adapter,'live',{'asr':{'live':True}},Path(tmp)/'runs')
            self.assertEqual(adapter.played,[]);self.assertEqual(calls,['capture_start','capture_stop']);self.assertEqual(result['execution_status'],'blocked');verify_bundle(p)
