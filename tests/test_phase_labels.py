import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from voiceqa.phase_labels import matching_label


class PhaseLabelsTests(unittest.TestCase):
    def test_local_rule_ignores_bridge_and_failure_without_counting_worker_events(self):
        adapter=SimpleNamespace(output=Path('RUN'),trace='current',turn_floor=4,
            phase_rules=[{'id':'D-body','role':'search_body','all_of':[],
                'any_of':['小行星','恐龙'],'none_of':['我查','不能确认','没有找到']}])
        def event(text,segment='body',stamp=5):return {'event':'voice.tts_text_changed','trace':'current',
            'received_monotonic_s':stamp,'raw_line':42,'fields':{'source':'worker','segment_id':segment,'text':text}}
        self.assertIsNone(matching_label(adapter,[event('我查查恐龙。')],'search_body'))
        self.assertIsNone(matching_label(adapter,[event('恐龙的原因还不能确认。')],'search_body'))
        match=matching_label(adapter,[event('小行星撞击引发生态灾难。')],'search_body')
        self.assertEqual(match[1]['evidence_kind'],'rule_text_classification')
        self.assertFalse(match[1]['native_role_verified'])
        self.assertIsNone(matching_label(adapter,[event('恐龙的原因。'),event('现在聊别的。','new',6)],'search_body'))

    def test_review_requires_exact_run_trace_segment_and_caption_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'labels.jsonl';output=root/'RUN'
            text='小行星撞击导致生态灾难。'
            event={'event':'voice.tts_text_changed','trace':'current','received_monotonic_s':5,'raw_line':42,
                   'fields':{'source':'worker','segment_id':'segment','text':text}}
            label={'schema_version':1,'run_id':'RUN','trace':'current','segment_id':'segment',
                   'raw_line':42,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'role':'search_body',
                   'reviewer':'session_agent','evidence_kind':'semantic_log_review'}
            adapter=SimpleNamespace(output=output,trace='current',turn_floor=4,phase_review_file=source)
            def write(value):source.write_text(json.dumps(value)+'\n',encoding='utf-8')
            write(label)
            self.assertEqual(matching_label(adapter,[event],'search_body')[0],event)
            for key,value in [('run_id','OTHER'),('trace','old'),('segment_id','other'),('raw_line',41),('text_sha256','bad')]:
                write({**label,key:value});self.assertIsNone(matching_label(adapter,[event],'search_body'))
            write(label);adapter.turn_floor=6
            self.assertIsNone(matching_label(adapter,[event],'search_body'))

    def test_newer_segment_invalidates_old_label_and_partial_last_line_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'labels.jsonl';text='正文内容。'
            first={'event':'voice.tts_text_changed','trace':'current','received_monotonic_s':5,'raw_line':42,
                   'fields':{'source':'worker','segment_id':'first','text':text}}
            second={**first,'raw_line':43,'received_monotonic_s':6,'fields':{**first['fields'],'segment_id':'second'}}
            label={'schema_version':1,'run_id':'RUN','trace':'current','segment_id':'first','raw_line':42,
                'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'role':'search_body',
                'reviewer':'session_agent','evidence_kind':'semantic_log_review'}
            source.write_text(json.dumps(label)+'\n{"unfinished":',encoding='utf-8')
            adapter=SimpleNamespace(output=root/'RUN',trace='current',turn_floor=4,phase_review_file=source)
            self.assertIsNotNone(matching_label(adapter,[first],'search_body'))
            self.assertIsNone(matching_label(adapter,[first,second],'search_body'))
