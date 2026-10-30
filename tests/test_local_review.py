import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voiceqa.core import ROOT, digest
from voiceqa.adapters import ReplayAdapter
from voiceqa.runner import run, seal
from voiceqa.semantic_review import prepare_review, import_review, validate_review
from voiceqa.storage import data_root, outside_checkout
from voiceqa.gitflow import gate


class LocalStorageTests(unittest.TestCase):
    def test_environment_data_root_and_reject_source_tree(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,VOICEQA_DATA_DIR=tmp):
            self.assertEqual(data_root(),Path(tmp).resolve())
            with self.assertRaises(ValueError):outside_checkout(ROOT/'reports/new.md')
            with patch.dict(os.environ,VOICEQA_DATA_DIR=str(ROOT/'data')):
                with self.assertRaises(ValueError):data_root()

    def test_worktree_compatibility_symlink_resolves_external(self):
        if os.name=='nt':self.skipTest('Windows symlink permission differs')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'repo').mkdir();(p/'data').mkdir();(p/'repo/link').symlink_to(p/'data',target_is_directory=True)
            self.assertEqual(outside_checkout(p/'repo/link/file',root=p/'repo'),(p/'data/file').resolve())

    def test_git_gate_rejects_even_minimal_real_result_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'cases').mkdir();(p/'speech-library').mkdir();(p/'records/run').mkdir(parents=True)
            import shutil
            shutil.copyfile(ROOT/'cases/voice-smoke.yaml',p/'cases/example.yaml')
            (p/'speech-library/list.yaml').write_text('schema_version: 1\nassets: []\n',encoding='utf-8')
            (p/'records/run/summary.json').write_text('{}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'local data_root'):gate(p)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        fixture=json.loads((ROOT/'fixtures/workflow.json').read_text(encoding='utf-8'))['events']
        self.bundle,result=run(ROOT/'cases/voice-smoke.yaml',ReplayAdapter(fixture),'replay',output_root=self.root/'runs')
        import wave
        with wave.open(str(self.bundle/'audio/mic_raw.wav'),'wb') as w:
            w.setparams((1,2,16000,0,'NONE','not compressed'));w.writeframes(b'\0\0'*1600)
        seal(self.bundle)
        self.asr=self.root/'asr.json';self.asr.write_text(json.dumps({'contract':'recording-asr-v1','audio_sha256':digest(self.bundle/'audio/mic_raw.wav'),'duration_s':.1,'segments':[{'start_s':0,'end_s':.08,'text':'这是原始错字','speaker':'unknown','confidence':None}]},ensure_ascii=False),encoding='utf-8')
        self.template=self.root/'template.yaml';self.template.write_text('fields: [现场SN, 原始字段]\ncriteria:\n- id: relevant\n  description: 回答应对应问题\n',encoding='utf-8')
        self.review=prepare_review(self.bundle,self.asr,self.template,self.root/'review');request=self.review/'request.json'
        self.assessment={'request_sha256':digest(request),'reviewer':'current GPT Agent','dialogue':[{'segment_ids':['asr-0001'],'speaker':'设备回复','text':'这是推测还原','certainty':'推测还原','rationale':'原字的同音语义推测，不当作逐字实听'}],'criteria':[{'id':'relevant','verdict':'inconclusive','reason':'这是合成fixture，不能实机验收','segment_ids':['asr-0001'],'limitations':['无真实设备']}],'receipt_fields':{'现场SN':'未核','原始字段':'fixture'},'summary':'待真机证据'}
    def tearDown(self):self.tmp.cleanup()

    def test_receipt_preserves_raw_and_inferred_side_by_side_without_resealing_run(self):
        prior=digest(self.bundle/'integrity.json');file=self.root/'assessment.json';file.write_text(json.dumps(self.assessment,ensure_ascii=False),encoding='utf-8')
        receipt=import_review(self.review,file);text=receipt.read_text(encoding='utf-8')
        self.assertIn('这是原始错字',text);self.assertIn('这是推测还原',text);self.assertIn('现场SN：未核',text)
        self.assertEqual(digest(self.bundle/'integrity.json'),prior)
        with self.assertRaises(FileExistsError):import_review(self.review,file)

    def test_unknown_segment_changed_asr_or_wrong_template_is_rejected(self):
        v=copy.deepcopy(self.assessment);v['dialogue'][0]['segment_ids']=['fabricated']
        with self.assertRaisesRegex(ValueError,'unknown ASR'):validate_review(self.review,v)
        v=copy.deepcopy(self.assessment);v['receipt_fields']['added']='not original'
        with self.assertRaisesRegex(ValueError,'source template'):validate_review(self.review,v)
        self.asr.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'ASR changed'):validate_review(self.review,self.assessment)

    def test_synthetic_run_cannot_be_promoted_to_pass_by_gpt_json(self):
        v=copy.deepcopy(self.assessment);v['criteria'][0]['verdict']='pass'
        with self.assertRaisesRegex(ValueError,'PASS requires'):validate_review(self.review,v)

    def test_review_request_rejects_transcript_from_different_recording(self):
        data=json.loads(self.asr.read_text(encoding='utf-8'));data['audio_sha256']='0'*64;self.asr.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'this recording'):prepare_review(self.bundle,self.asr,self.template,self.root/'bad-review')
