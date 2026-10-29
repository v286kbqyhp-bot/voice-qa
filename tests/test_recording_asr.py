from pathlib import Path
import tempfile
import unittest
import wave

from voiceqa.recording_asr import inspect_recording,validate_destination,fingerprint


class RecordingAsrContractTests(unittest.TestCase):
    def test_reading_retains_audio_fingerprint_and_rejects_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio=Path(tmp)/'mic.wav'
            with wave.open(str(audio),'wb') as w:w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\x01\x00'*1600)
            before=fingerprint(audio);info,payload=inspect_recording(audio)
            self.assertEqual(info['audio_sha256'],before);self.assertEqual(len(payload),3200)
            self.assertEqual(info['duration_s'],.1);self.assertEqual(fingerprint(audio),before)
            audio.write_bytes(audio.read_bytes()[:-20])
            with self.assertRaisesRegex(ValueError,'truncated'):inspect_recording(audio)

    def test_analysis_cannot_modify_sealed_evidence_or_previous_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);bundle=root/'run';(bundle/'audio').mkdir(parents=True)
            audio=bundle/'audio/mic.wav';audio.touch();(bundle/'run_manifest.json').write_text('{}');(bundle/'integrity.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'outside'):validate_destination(audio,bundle/'asr.json')
            output=root/'asr.json';self.assertEqual(validate_destination(audio,output),output.resolve())
            output.write_text('{}')
            with self.assertRaises(FileExistsError):validate_destination(audio,output)


if __name__=='__main__':unittest.main()
