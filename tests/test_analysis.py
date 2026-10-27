import json
from pathlib import Path
import tempfile
import unittest
import wave

from voiceqa.analysis import validate_analysis
from voiceqa.core import digest


class AnalysisTests(unittest.TestCase):
    def test_audio_hash_and_bounds_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'mic.wav'
            with wave.open(str(p),'wb') as f:
                f.setparams((1,2,16000,0,'NONE','not compressed'))
                f.writeframes(b'\0\0'*16000)
            data={'schema_version':1,'audio_sha256':digest(p),'segments':[
                {'start_s':0,'end_s':0.8,'speaker':'unknown','text':None,'confidence':0.2,'overlap':True}],
                'observations':[],'limitations':['overlap needs review']}
            self.assertEqual(validate_analysis(data,p),data)
            data['segments'][0]['end_s']=2
            with self.assertRaises(ValueError):
                validate_analysis(data,p)
            data['segments']=[]
            data['audio_sha256']='0'*64
            with self.assertRaises(ValueError):
                validate_analysis(data,p)
