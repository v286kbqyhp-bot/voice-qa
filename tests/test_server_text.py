import copy
import json
from pathlib import Path
import tempfile
import unittest

from voiceqa.adapters import ReplayAdapter
from voiceqa.core import ROOT, digest, dump
from voiceqa.runner import run, seal
from voiceqa.semantic_review import prepare_review, import_review, validate_review
from voiceqa.server_text import collect_server_text


def caption(trace, segment, text, *, stamp=4, final=True, source='worker', event='voice.tts_text_changed'):
    return {'event': event, 'trace': trace, 'received_monotonic_s': stamp,
            'raw_line': int(stamp * 10), 'event_id': f'e-{stamp}',
            'fields': {'segment_id': segment, 'text': text, 'source': source,
                       'is_final': str(final).lower(), 'replace': 'true'}}


class ServerTextTests(unittest.TestCase):
    def collect(self, events):
        return collect_server_text(events,
            {'steps': [{'step': 'bind', 'execution_status': 'completed',
                        'actual': {'trace': 'current', 'received_monotonic_s': 2}}]},
            {'steps': [{'name': 'bind', 'kind': 'wait_event', 'params': {'bind_trace': True}}]})

    def test_current_final_snapshots_preserve_raw_partials_without_old_room_or_local_greeting(self):
        events = [caption('old', 's', '旧房答案'),
                  caption('current', 's', '绑定前', stamp=1),
                  caption('current', 'wake', '来啦', source='dynamic_current'),
                  caption('current', 's', '答案是', final=False),
                  caption('current', 's', '答案是八', stamp=5),
                  caption('current', 's', '八', stamp=6),
                  caption('current', 'q', '三加五', stamp=3, event='voice.asr_text_changed')]
        result = self.collect(events)
        self.assertEqual([s['text'] for s in result['segments']], ['三加五', '八'])
        self.assertEqual([s['source'] for s in result['segments']], ['device_input_asr', 'server_reply_text'])
        self.assertEqual(len(result['raw_events']), 4)
        self.assertFalse(any('start_s' in s for s in result['segments']))

    def test_partial_only_and_unknown_delta_cannot_become_final_evidence(self):
        delta = caption('current', 's', '八')
        delta['fields']['replace'] = 'false'
        self.assertEqual(self.collect([caption('current', 'p', '未完成', final=False), delta])['segments'], [])

    def test_unbound_capture_cannot_be_promoted_to_server_dialogue(self):
        with self.assertRaisesRegex(ValueError, 'executed trace binding'):
            collect_server_text([], {'steps': []}, {'steps': []})


class ServerReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        fixture = json.loads((ROOT/'fixtures/workflow.json').read_text(encoding='utf-8'))['events']
        self.bundle, result = run(ROOT/'cases/voice-smoke.yaml', ReplayAdapter(fixture), 'replay', output_root=self.root/'runs')
        trace = next(s['actual']['trace'] for s in result['steps'] if s['step']=='ready')
        stamp = next(s['actual']['at_s'] for s in result['steps'] if s['step']=='ready')
        # Synthetic live-shaped bundle for validating evidence gates, never device evidence.
        result['mode'] = 'live'
        result['steps'][1]['actual']['received_monotonic_s'] = stamp
        dump(self.bundle/'results.json', result)
        for step in result['steps']:
            dump(self.bundle/'steps'/f"{step['index']:03d}-{step['step']}.json", step)
        events = [caption(trace, 'q', '三加五', stamp=stamp+1, event='voice.asr_text_changed'),
                  caption(trace, 'a', '八', stamp=stamp+2)]
        (self.bundle/'events.jsonl').write_text(''.join(json.dumps(e, ensure_ascii=False)+'\n' for e in events), encoding='utf-8')
        seal(self.bundle)
        self.template = self.root/'template.yaml'
        self.template.write_text('fields: [原字段]\ncriteria:\n- id: reply\n  description: 答案文本\n  evidence_scope: reply_semantics\n- id: audible\n  description: 实际扬声器完整发声\n  evidence_scope: acoustic\n', encoding='utf-8')
        self.folder = prepare_review(self.bundle, None, self.template, self.root/'review', evidence_source='server_text')
        self.req = json.loads((self.folder/'request.json').read_text(encoding='utf-8'))
        self.assessment = {'request_sha256': digest(self.folder/'request.json'), 'reviewer': 'fixture reviewer',
            'summary': 'fixture text only', 'receipt_fields': {'原字段': '未真人验收'},
            'dialogue': [{'segment_ids': ['text-0002'], 'speaker': '设备回复', 'text': '八',
                          'certainty': '服务端原文', 'rationale': 'fixture literal reply'}],
            'criteria': [{'id': 'reply', 'verdict': 'pass', 'reason': 'fixture semantics', 'segment_ids': ['text-0002'], 'limitations': []},
                         {'id': 'audible', 'verdict': 'inconclusive', 'reason': 'not heard', 'segment_ids': [], 'limitations': []}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_explicit_text_semantics_pass_without_fabricating_asr_or_acoustic_pass(self):
        original = digest(self.bundle/'integrity.json')
        assessment = self.root/'assessment.json'
        dump(assessment, self.assessment)
        receipt = import_review(self.folder, assessment).read_text(encoding='utf-8')
        self.assertIn('本轮未运行独立ASR', receipt)
        self.assertNotIn('## 原始录音ASR字幕', receipt)
        self.assertIn('audible / inconclusive', receipt)
        self.assertEqual(digest(self.bundle/'integrity.json'), original)

    def test_text_cannot_sign_acoustic_criterion_or_cite_input_as_reply(self):
        a = copy.deepcopy(self.assessment)
        a['criteria'][1].update(verdict='pass', segment_ids=['text-0002'])
        with self.assertRaisesRegex(ValueError, 'explicit text scope'):
            validate_review(self.folder, a)
        a = copy.deepcopy(self.assessment)
        a['criteria'][0]['segment_ids'] = ['text-0001']
        with self.assertRaisesRegex(ValueError, 'explicit text scope'):
            validate_review(self.folder, a)

    def test_changed_request_cannot_inject_reply_text(self):
        self.req['segments'][1]['text'] = 'fabricated'
        dump(self.folder/'request.json', self.req)
        a = copy.deepcopy(self.assessment)
        a['request_sha256'] = digest(self.folder/'request.json')
        with self.assertRaisesRegex(ValueError, 'sealed run events'):
            validate_review(self.folder, a)

    def test_text_mode_does_not_accept_asr_argument(self):
        with self.assertRaisesRegex(ValueError, 'does not consume'):
            prepare_review(self.bundle, self.root/'old-asr.json', self.template, self.root/'other', evidence_source='server_text')

    def test_final_delivery_cannot_export_unresolved_required_criterion(self):
        assessment=self.root/'final-assessment.json';dump(assessment,self.assessment)
        with self.assertRaisesRegex(ValueError,'resolution or discussion'):
            import_review(self.folder,assessment,final=True)
        self.assertFalse((self.folder/'receipt.md').exists())
