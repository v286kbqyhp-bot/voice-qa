import array
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import wave
import threading

from voiceqa.adapters import PcmAudio


class CaptureShutdownTests(unittest.TestCase):
    def test_recording_header_is_readable_before_driver_shutdown(self):
        audio=PcmAudio(player=Mock())
        stream=Mock()
        def make_stream(**kwargs):
            stream.start.side_effect=lambda:kwargs['callback'](array.array('h',[100,-100]*100).tobytes(),200,None,None)
            return stream
        sd=SimpleNamespace(RawInputStream=make_stream,query_devices=lambda *a:{'name':'test'},CallbackStop=RuntimeError)
        with tempfile.TemporaryDirectory() as tmp,patch.dict('sys.modules',sounddevice=sd):
            target=Path(tmp)/'mic.wav';audio.start(str(target),{'sample_rate':16000})
            deadline=time.monotonic()+1
            while not audio.frames and time.monotonic()<deadline:time.sleep(.005)
            with wave.open(str(target),'rb') as f:self.assertEqual(f.getnframes(),200)
            result=audio.stop()
            self.assertTrue(result['valid_capture']);stream.abort.assert_called_once();stream.close.assert_called_once()

    def test_driver_timeout_still_closes_wav_and_marks_capture_invalid(self):
        audio=PcmAudio(player=Mock());audio.capture_stopping=threading.Event()
        audio.stream=Mock();audio.blocks=Mock();audio.writer=Mock();audio.writer.is_alive.return_value=False
        audio.wave=Mock();audio.frames=200;audio.peak=100;audio.rate=16000;audio.errors=[]
        closing=Mock();closing.is_alive.return_value=True
        with patch('voiceqa.adapters.threading.Thread',return_value=closing):result=audio.stop()
        closing.join.assert_called_once_with(timeout=2)
        audio.wave.close.assert_called_once()
        self.assertFalse(result['valid_capture'])
        self.assertIn('capture_driver_shutdown_timeout',result['errors'])

    def test_finished_callback_avoids_racing_abort_with_callback_stop(self):
        audio=PcmAudio(player=Mock());audio.capture_stopping=threading.Event();audio.capture_finished=threading.Event()
        audio.capture_finished.set();audio.stream=Mock();audio.blocks=Mock();audio.writer=Mock();audio.writer.is_alive.return_value=False
        audio.wave=Mock();audio.frames=200;audio.peak=100;audio.rate=16000;audio.errors=[]
        result=audio.stop()
        audio.stream.abort.assert_not_called();audio.stream.close.assert_called_once()
        self.assertTrue(result['valid_capture'])


if __name__=='__main__':unittest.main()
