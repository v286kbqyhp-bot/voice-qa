import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import yaml

from voiceqa.adapters import NativeAdapter, ReplayAdapter, match_event, parse_log
from voiceqa.core import Blocked
from voiceqa.interaction import captions, wait_interaction
from voiceqa.lifecycle import cleanup_budget, session_status
from voiceqa.runner import run


def event(name, stamp, trace='current', **fields):
    return {'event': name, 'received_monotonic_s': stamp, 'trace': trace,
            'fields': fields, 'raw_line': int(stamp*10)}


class InteractionTests(unittest.TestCase):
    def test_explicit_pause_quiet_wait_uses_latest_state_without_consumed_caption_event(self):
        self.clock=[4.0]
        events=[event('started',1),event('worker_speaking',3.1,speaking='false')]
        result=self.wait(self.adapter(events,rms=10),'wait_output',{'require_caption':False,'quiet_s':.12,'timeout_s':1})
        self.assertEqual(result['device_caption_text'],'')
        self.assertFalse(result['formal_turn_or_queue_completion'])
        events.append(event('worker_speaking',3.5,speaking='true'))
        with self.assertRaisesRegex(Blocked,'execution_timeout'):
            self.wait(self.adapter(events,rms=10),'wait_output',{'require_caption':False,'quiet_s':.12,'timeout_s':.2})

    def test_old_bridge_cannot_borrow_a_later_body_speaking_span(self):
        self.clock=[6.0]
        events=[event('started',1),event('worker_speaking',2.1,speaking='true'),
                event('voice.tts_text_changed',2.5,source='worker',segment_id='bridge',text='我查查。',is_final='true'),
                event('worker_speaking',3,speaking='false'),event('worker_speaking',5,speaking='true')]
        for sound in (True,False):
            with self.subTest(require_sound=sound),self.assertRaisesRegex(Blocked,'earlier speaking span'):
                self.wait(self.adapter(events),'wait_speech',{'role':'ack','require_sound':sound,'timeout_s':1})

    def test_caption_only_phase_trigger_still_preserves_its_classification_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.clock=[4.0]
            adapter=self.adapter([event('started',1),event('voice.tts_text_changed',3.0,source='worker',
                segment_id='bridge',text='看看新闻。',is_final='true')],rms=10)
            adapter.output=Path(tmp)
            adapter.phase_rules=[{'id':'news-bridge','role':'search_bridge','any_of':['看看新闻'],'all_of':[],'none_of':[]}]
            result=self.wait(adapter,'wait_speech',{'role':'any','phase_role':'search_bridge','require_sound':False,'timeout_s':1})
            self.assertFalse(result['acoustic_verified'])
            self.assertEqual(result['phase_label']['evidence_kind'],'rule_text_classification')
            self.assertTrue((adapter.output/'phase_labels.jsonl').exists())

    def test_partial_bridge_role_survives_truncation_only_in_the_same_segment(self):
        events=[event('voice.tts_text_changed',2.1,source='worker',segment_id='bridge',text='我查查。',is_final='false'),
                event('voice.tts_text_changed',2.5,source='worker',segment_id='bridge',text='我',is_final='true'),
                event('voice.tts_text_changed',3,source='worker',segment_id='new',text='宝可梦的主角是莉可。',is_final='false')]
        self.assertEqual([e['fields']['text'] for e in captions(events,'current',2,'ack')],['我查查。','我'])
        self.assertEqual([e['fields']['text'] for e in captions(events,'current',2,'body')],['宝可梦的主角是莉可。'])

    def adapter(self, events, rms=200):
        class Audio:
            def levels(inner):
                return {'rms':rms,'updated_monotonic_s':self.clock[0],'history':[], 'errors':[]}
        return SimpleNamespace(logging=True, trace='current', turn_floor=2,
            sound_threshold=100, audio=Audio(), _notify=lambda *a,**kw:None,
            logs=SimpleNamespace(healthy=lambda:None,
                all_events=lambda:[e for e in events if e['received_monotonic_s']<=self.clock[0]]))

    def wait(self, adapter, kind, params):
        with patch('voiceqa.interaction.time.monotonic',side_effect=lambda:self.clock[0]), \
                patch('voiceqa.interaction.time.sleep',side_effect=lambda s:self.clock.__setitem__(0,self.clock[0]+s)):
            return wait_interaction(adapter,kind,params)

    def test_json_caption_and_boolean_fields_remain_literal_and_redacted(self):
        payload={'event':'voice.tts_text_changed','data':{'trace_id':'current','text':'我查查。',
            'source':'worker','is_final':True,'token':'secret'},'ts_ms':123}
        result=parse_log('10-27 16:00:00.000 1 1 I GodotBridgePlugin: emitJsonEvent payload='+json.dumps(payload),4,12)
        self.assertEqual(result['fields']['token'],'[REDACTED]')
        self.assertEqual(result['fields']['text'],'我查查。')
        self.assertTrue(match_event(result,{'event':'voice.tts_text_changed','where':{'is_final':True}}))

    def test_speech_trigger_needs_current_trace_and_acoustic_activity(self):
        self.clock=[3.0]
        events=[event('started',1),event('voice.tts_text_changed',2.1,source='worker',text='恐龙灭绝的原因包括小行星。',is_final='false'),
                event('worker_speaking',2.2,speaking='true')]
        result=self.wait(self.adapter(events),'wait_speech',{'role':'body','delay_s':1,'timeout_s':2})
        self.assertGreaterEqual(result['speech_elapsed_proxy_s'],1)
        self.assertEqual(result['speaker_attribution'],'unverified')
        with self.assertRaisesRegex(Blocked,'execution_timeout'):
            self.wait(self.adapter(events,rms=10),'wait_speech',{'role':'body','timeout_s':.1})
        events[1]['trace']='old'
        with self.assertRaisesRegex(Blocked,'execution_timeout'):
            self.wait(self.adapter(events),'wait_speech',{'role':'body','timeout_s':.1})

    def test_output_quiet_is_only_a_scheduling_proxy(self):
        self.clock=[4.0]
        events=[event('started',1),event('voice.tts_text_changed',3,source='worker',text='这是本轮回答的内容。',is_final='true'),
                event('worker_speaking',3.1,speaking='false')]
        result=self.wait(self.adapter(events,rms=10),'wait_output',{'quiet_s':.12,'timeout_s':1})
        self.assertFalse(result['formal_turn_or_queue_completion'])
        self.assertGreaterEqual(result['quiet_s'],.12)

    def test_weather_waiting_phrase_cannot_satisfy_body_completion(self):
        events=[event('voice.tts_text_changed',3,source='worker',segment_id='ack',text='查查天气。',is_final='true'),
                event('voice.tts_text_changed',5,source='worker',segment_id='answer',text='目标城市今天有小雨。',is_final='true')]
        self.assertEqual([e['fields']['text'] for e in captions(events,'current',2,'body')],['目标城市今天有小雨。'])

    def test_interrupted_story_final_cannot_complete_the_next_question(self):
        self.clock=[3.5]
        events=[event('started',1),
                event('voice.tts_text_changed',2.2,source='worker',segment_id='old',text='旧故事内容',is_final='false'),
                event('voice.asr_text_changed',3,segment_id='question',text='三加五',is_final='true'),
                event('voice.tts_text_changed',3.1,source='worker',segment_id='old',text='旧故事',is_final='true'),
                event('worker_speaking',3.2,speaking='false')]
        with self.assertRaisesRegex(Blocked,'execution_timeout'):
            self.wait(self.adapter(events,rms=10),'wait_output',{'after_input_final':True,'quiet_s':.12,'timeout_s':.3})
        events += [event('voice.tts_text_changed',4,source='worker',segment_id='new',text='八',is_final='true'),
                   event('worker_speaking',4.1,speaking='false')]
        self.clock=[4.2]
        result=self.wait(self.adapter(events,rms=10),'wait_output',{'after_input_final':True,'quiet_s':.12,'timeout_s':1})
        self.assertEqual(result['device_caption_text'],'八')

    def test_input_gate_cannot_be_bypassed_by_disabling_captions(self):
        self.clock=[4.0]
        events=[event('started',1),event('worker_speaking',3,speaking='false')]
        with self.assertRaisesRegex(Blocked,'execution_timeout'):
            self.wait(self.adapter(events,rms=10),'wait_output',{'after_input_final':True,'require_caption':False,'quiet_s':.12,'timeout_s':.3})

    def test_idle_close_ends_wait_immediately_instead_of_waiting_for_timeout(self):
        self.clock=[4.0]
        adapter=self.adapter([event('started',1),event('session_end_requested',3,trace=None,reason='idle_timeout')])
        with self.assertRaisesRegex(Blocked,'session_ended_while_waiting: idle_timeout'):
            self.wait(adapter,'wait_speech',{'timeout_s':40})
        self.assertEqual(self.clock[0],4)

    def test_short_rolling_audio_history_does_not_reset_speech_onset(self):
        self.clock=[3.0]
        events=[event('started',1),event('worker_speaking',2.5,speaking='true'),
                event('voice.tts_text_changed',3.1,source='worker',text='正在讲解恐龙灭绝的原因。',is_final='false')]
        adapter=self.adapter(events)
        class RollingAudio:
            def levels(inner):return {'rms':200,'updated_monotonic_s':self.clock[0],
                'history':[(self.clock[0]-.1,200)],'errors':[]}
        adapter.audio=RollingAudio()
        result=self.wait(adapter,'wait_speech',{'role':'body','delay_s':1,'timeout_s':1.5})
        self.assertGreaterEqual(result['speech_elapsed_proxy_s'],1)

    def test_complete_audio_and_startup_latency_must_fit_before_four_seconds(self):
        countdown=event('voice.idle_countdown',10,timeout_ms='5000',remaining_ms='4500',running='true')
        early=cleanup_budget(countdown,10.1,.5,4,1)
        self.assertTrue(early['can_finish_in_budget'])
        self.assertEqual(early['finish_deadline_monotonic_s'],13.5)
        late=cleanup_budget(countdown,12.5,.5,4,1)
        self.assertFalse(late['can_finish_in_budget'])

    def test_native_cleanup_uses_countdown_and_does_not_play_when_budget_is_late(self):
        from unittest.mock import Mock
        adapter=NativeAdapter(tts=Mock(),audio=Mock())
        adapter.logging=True;adapter.trace='current'
        adapter.logs=SimpleNamespace(healthy=lambda:None,all_events=lambda:[event('started',1),
            event('voice.idle_countdown',10,timeout_ms='5000',remaining_ms='4500',running='true')])
        adapter.play=Mock(return_value={'start_monotonic_s':10.1,'end_monotonic_s':11.6})
        with patch('voiceqa.adapters.time.monotonic',return_value=10.1):
            result=adapter.play_cleanup({'duration_s':.5},{'idle_finish_before_s':4,'startup_budget_s':1})
        self.assertTrue(result['host_playback_finished_in_budget']);adapter.play.assert_called_once()
        adapter.play.reset_mock()
        with patch('voiceqa.adapters.time.monotonic',return_value=12.5):
            result=adapter.play_cleanup({'duration_s':.5},{'idle_finish_before_s':4,'startup_budget_s':1})
        self.assertEqual(result['reason'],'cleanup_deadline_missed');adapter.play.assert_not_called()

    def test_lifecycle_does_not_reuse_old_close_or_another_trace(self):
        events=[event('session_end_requested',.5,trace=None,reason='idle_timeout'),event('ready_next_wake',.7,trace=None),
                event('started',1),event('session_end_requested',2,trace='other',reason='user_session_exit')]
        self.assertEqual(session_status(events,'current')['state'],'active')
        events += [event('session_end_requested',3,trace=None,reason='idle_timeout'),event('ready_next_wake',3.2,trace=None)]
        result=session_status(events,'current')
        self.assertEqual((result['state'],result['reason']),('closed','idle_timeout'))
        events.append(event('started',4,trace='new'))
        self.assertEqual(session_status(events,'current')['state'],'superseded')

    def test_failure_skips_main_steps_but_cleanup_does_not_play_to_closed_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'case.yaml'
            source.write_text(yaml.safe_dump({'case_id':'CLEANUP','title':'conditional cleanup','domain':'smoke','severity':'normal',
                'steps':[{'kind':'wait_event','name':'missing','params':{'event':'missing'}},
                         {'kind':'audio_play','name':'unused','params':{'text':'do not play'}}],
                'cleanup':[{'kind':'audio_play','name':'exit','params':{'text':'不聊了','if_session_active':True}},
                           {'kind':'wait_event','name':'closed','params':{'event':'ready_next_wake','if_session_active':True}}]},allow_unicode=True),encoding='utf-8')
            class ClosedReplay(ReplayAdapter):
                def session_status(inner):return {'state':'closed','reason':'idle_timeout'}
            adapter=ClosedReplay([])
            output,result=run(source,adapter,'replay',output_root=root/'runs')
            self.assertEqual(result['execution_status'],'blocked')
            self.assertEqual(result['steps'][1]['execution_status'],'not_executed')
            self.assertEqual(adapter.played,[])
            self.assertEqual(result['steps'][2]['actual']['lifecycle']['reason'],'idle_timeout')
            timeline=[json.loads(s) for s in (output/'timeline.jsonl').read_text().splitlines()]
            self.assertEqual([s['event'] for s in timeline if s['step']=='unused'],['step_skipped'])

    def test_missing_interaction_transport_blocks_before_capture_even_without_requires(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'case.yaml'
            source.write_text(yaml.safe_dump({'case_id':'NO-TRANSPORT','title':'preflight','domain':'smoke','severity':'normal',
                'steps':[{'kind':'wait_speech','name':'speech','params':{'timeout_s':1}}]}),encoding='utf-8')
            class MissingTransport(ReplayAdapter):
                def capabilities(inner):return {**super().capabilities(),'interaction_wait':True}
            adapter=MissingTransport();adapter.begin=Mock()
            output,result=run(source,adapter,'replay',output_root=root/'runs')
            self.assertIn('capability unavailable',result['reason']);adapter.begin.assert_not_called()


if __name__=='__main__':unittest.main()
