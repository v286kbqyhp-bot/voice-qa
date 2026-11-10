import array
import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
import wave

from voiceqa.core import digest, dump
from voiceqa.semantic_review import delivery_readiness
from voiceqa.timing import build_timing, locate_reference


def wav(path, samples, rate=16000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        stream.writeframes(array.array('h', samples).tobytes())


def text(segment, stamp, value, *, source='worker', final='false', trace='room'):
    return {'event':'voice.tts_text_changed','trace':trace,'received_monotonic_s':stamp,
            'raw_line':round(stamp*100),'fields':{'source':source,'text':value,
                'segment_id':segment,'is_final':final,'replace':'true'}}


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        # Synthetic test waveform with deliberate leading/trailing silence and
        # a driver start offset; these fixtures are never actual device evidence.
        ref=[]
        for i in range(16000):
            t=i/16000
            amplitude=0 if t<.1 or t>.82 else (1500 if t<.3 else 4000 if t<.54 else 2200)
            ref.append(round(amplitude*math.sin(i*.13)))
        mic=[0]*80000
        mic[17280:17280+len(ref)]=ref  # heard file starts at 1.08 s
        for i in range(48000,57000):mic[i]=round(1200*math.sin(i*.17))  # reply at 3 s
        wav(self.root/'tts/question.wav',ref);wav(self.root/'audio/mic_raw.wav',mic)
        (self.root/'cases.yaml').write_text('case_id: timer\nsteps:\n- {name: bind, kind: wait_event, params: {bind_trace: true}}\n- {name: question, kind: audio_play, params: {text: 提问}}\n',encoding='utf-8')
        dump(self.root/'tts/manifest.json',{'question':{'sha256':digest(self.root/'tts/question.wav')}})
        dump(self.root/'run_manifest.json',{'device':{'audio':{'host_monotonic_s':100}}})
        self.steps=[{'step':'bind','kind':'wait_event','execution_status':'completed','actual':{'trace':'room','received_monotonic_s':100.5}},
                    {'step':'question','kind':'audio_play','execution_status':'completed','text':'提问',
                     'actual':{'start_monotonic_s':101,'end_monotonic_s':102.2}}]
        dump(self.root/'results.json',{'mode':'live','steps':self.steps})
        self.events=[text('answer',103.2,'这是正文'),{'event':'worker_speaking','trace':'room',
                     'received_monotonic_s':102.9,'fields':{'speaking':'true'}}]

    def tearDown(self):self.tmp.cleanup()

    def calculate(self, events=None, policy=None):
        events=self.events if events is None else events
        (self.root/'events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events),encoding='utf-8')
        return build_timing(self.root,policy)['rows'][0]

    def test_input_tail_and_driver_offset_are_removed_from_same_recording_delay(self):
        row=self.calculate();estimate=row['acoustic_estimate']
        self.assertAlmostEqual(row['input_recording_match']['end_s'],1.92,delta=.06)
        self.assertAlmostEqual(estimate['response_delay_s'],1.08,delta=.08)
        self.assertEqual(row['reply_text']['host_observed_delay_s'],1.0)
        self.assertEqual(estimate['precision'],'estimate_not_calibrated_T5')

    def test_old_late_final_local_greeting_and_other_room_are_excluded(self):
        events=[text('old',100.8,'旧故事'),text('old',102.4,'旧故事final',final='true'),
                text('greet',102.3,'在呢',source='dynamic_current'),text('answer',102.5,'旧房',trace='other')]+self.events
        row=self.calculate(events)
        self.assertEqual(row['reply_text']['segment_id'],'answer')
        self.assertEqual(row['reply_text']['text'],'这是正文')

    def test_ack_segment_is_excluded_even_if_later_final_is_truncated(self):
        events=[text('ack',102.4,'查查天气'),text('ack',102.6,'我',final='true')]+self.events
        row=self.calculate(events)
        self.assertEqual(row['reply_text']['segment_id'],'answer')
        self.assertAlmostEqual(row['first_worker_text_delay_s'],.2)

    def test_current_question_cannot_borrow_next_room(self):
        self.steps[0]['actual']['received_monotonic_s']=104
        dump(self.root/'results.json',{'mode':'live','steps':self.steps})
        row=self.calculate()
        self.assertIsNone(row['trace']);self.assertNotIn('reply_text',row)

    def test_policy_requires_matching_audio_and_actual_trace_segments(self):
        with self.assertRaisesRegex(ValueError,'different recording'):
            self.calculate(policy={'audio_sha256':'0'*64})
        policy={'audio_sha256':digest(self.root/'audio/mic_raw.wav'),'steps':{'question':{'body_segment_ids':['invented']}}}
        with self.assertRaisesRegex(ValueError,'existing current-input'):
            self.calculate(policy=policy)

    def test_silence_and_uniform_reference_do_not_invent_match(self):
        self.assertIsNone(locate_reference([0]*100,[0]*10,0))
        self.assertIsNone(locate_reference([100]*100,[100]*10,0))

    def test_missing_response_does_not_become_zero_latency(self):
        row=self.calculate([])
        self.assertIsNone(row['acoustic_estimate']);self.assertNotIn('reply_text',row)

    def test_reply_for_previous_question_during_new_playback_is_not_new_reply(self):
        events=[text('late-old',101.5,'上轮答案'),
                {'event':'voice.asr_text_changed','trace':'room','received_monotonic_s':102.1,
                 'fields':{'is_final':'true','text':'本轮问题'}}]+self.events
        row=self.calculate(events)
        self.assertEqual(row['reply_text']['segment_id'],'answer')

    def test_control_step_does_not_turn_late_reply_into_question_latency(self):
        (self.root/'cases.yaml').write_text('steps:\n- {name: bind, kind: wait_event, params: {bind_trace: true}}\n- {name: question, kind: audio_play, params: {timing_role: control}}\n')
        row=self.calculate()
        self.assertEqual(row['role'],'control');self.assertNotIn('reply_text',row)
        self.assertIsNone(row['acoustic_estimate'])


class DeliveryReadinessTests(unittest.TestCase):
    def test_unresolved_items_require_repair_or_discussion_before_final(self):
        req={'criteria':[{'id':'reply'},{'id':'audio'}],'metadata':{}}
        assessment={'criteria':[{'id':'reply','verdict':'pass','reason':'实际文本'},
                                {'id':'audio','verdict':'inconclusive','reason':'缺录音分析'}]}
        result=delivery_readiness(req,assessment)
        self.assertFalse(result['ready']);self.assertEqual(result['unresolved'][0]['id'],'audio')
        req['metadata']['user_waivers']={'audio':{'reason':'本轮只验文本','authorization':'用户明确本轮免验实际声音'}}
        self.assertEqual(delivery_readiness(req,assessment)['functional_result'],'pass')

    def test_failure_is_a_completed_verification_and_waiver_needs_authorization(self):
        req={'criteria':[{'id':'reply'}],'metadata':{}}
        assessment={'criteria':[{'id':'reply','verdict':'fail','reason':'实际答错'}]}
        self.assertTrue(delivery_readiness(req,assessment)['ready'])
        self.assertEqual(delivery_readiness(req,assessment)['functional_result'],'fail')
        req['metadata']['user_waivers']={'reply':{'reason':'自行跳过'}}
        with self.assertRaisesRegex(ValueError,'explicit user authorization'):
            delivery_readiness(req,assessment)

    def test_unknown_fields_cannot_leak_into_final_even_when_function_passes(self):
        req={'criteria':[{'id':'reply'}],'template_fields':['结果','版本'],'metadata':{}}
        assessment={'criteria':[{'id':'reply','verdict':'pass','reason':'回答正确'}],
                    'receipt_fields':{'结果':'PASS','版本':'未核验'}}
        self.assertFalse(delivery_readiness(req,assessment)['ready'])
        req['metadata']['user_field_omissions']={'版本':{'reason':'按约定分工不列出','authorization':'用户明确省略该字段'}}
        self.assertTrue(delivery_readiness(req,assessment)['ready'])

    def test_known_failed_dependency_preserves_unexecuted_branch_without_inventing_pass(self):
        req={'criteria':[{'id':'entry'},{'id':'followup','depends_on':['entry']}],'metadata':{}}
        assessment={'criteria':[{'id':'entry','verdict':'fail','reason':'实际入口流程失败'},
                                {'id':'followup','verdict':'not_run','reason':'入口失败，后续步骤未执行'}]}
        result=delivery_readiness(req,assessment)
        self.assertTrue(result['ready']);self.assertEqual(result['functional_result'],'fail')
        req['criteria'][1]['depends_on']=['unknown']
        self.assertFalse(delivery_readiness(req,assessment)['ready'])
