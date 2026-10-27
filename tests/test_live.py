import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from voiceqa.adapters import ReplayAdapter, parse_log
from voiceqa.core import ROOT, R19_FIELDS, dump
from voiceqa.live import FileTail, format_event, scrub, select_run, watch
from voiceqa.runner import run, verify_bundle


class LiveTests(unittest.TestCase):
    def test_partial_utf8_line_is_not_lost_or_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'log'
            tail=FileTail()
            raw='中文事件\n'.encode()
            p.write_bytes(raw[:2]);self.assertEqual(tail.poll(p),[])
            with p.open('ab') as f:f.write(raw[2:])
            self.assertEqual(tail.poll(p),['中文事件'])
            self.assertEqual(tail.poll(p),[])

    def test_sensitive_fields_and_terminal_controls_are_not_displayed(self):
        value=scrub({'token':'test-value','nested':{'text':'Bearer test-secret'}})
        self.assertNotIn('test-value',json.dumps(value))
        self.assertNotIn('test-secret',json.dumps(value))
        text=format_event({'event':'step_started','details':{'text':'\x1b[2J'}})
        self.assertNotIn('\x1b',text)
        event=parse_log('10-27 15:00:00.000 1 2 I P003VoiceRealCanary: state=x token=abc',0,1)
        self.assertEqual(event['fields']['token'],'[REDACTED]')

    def test_pointer_selects_new_run_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'a').mkdir();(root/'z').mkdir()
            dump(root/'active_run.json',{'run_id':'a'})
            self.assertEqual(select_run(root),(root/'a').resolve())
            with self.assertRaises(ValueError):select_run(root,'../outside')

    def test_progress_contains_wait_match_rejection_and_finished_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            events=json.loads((ROOT/'fixtures/workflow.json').read_text(encoding='utf-8'))['events']
            p,r=run(ROOT/'cases/voice-smoke.yaml',ReplayAdapter(events),'replay',output_root=Path(tmp))
            entries=[json.loads(x) for x in (p/'progress.jsonl').read_text(encoding='utf-8').splitlines()]
            names={x['event'] for x in entries}
            self.assertTrue({'wait_started','event_matched','event_rejected','run_finished'}<=names)
            verify_bundle(p)

    def test_watcher_follows_active_run_without_starting_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=root/'demo';p.mkdir()
            dump(p/'run_manifest.json',{'case_id':'DEMO','mode':'replay'})
            dump(root/'active_run.json',{'run_id':'demo'})
            (p/'progress.jsonl').write_text(json.dumps({'event':'wait_started','step':'ready','details':{'expected':'input_ready'}})+'\n')
            stream=io.StringIO();stop=threading.Event()
            t=threading.Thread(target=watch,args=(root,),kwargs={'stream':stream,'stop':stop,'interval':0.01})
            t.start()
            deadline=time.monotonic()+2
            while 'input_ready' not in stream.getvalue() and time.monotonic()<deadline:time.sleep(0.01)
            stop.set();t.join(2)
            self.assertIn('mode=replay',stream.getvalue())
            self.assertIn('input_ready',stream.getvalue())

    def test_r19_receipt_preserves_its_own_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,r=run(ROOT/'cases/r19-A-first-turn-debug.yaml',ReplayAdapter(),'replay',output_root=Path(tmp))
            receipt=(p/'receipt.md').read_text(encoding='utf-8')
            self.assertTrue(all(field+'：' in receipt for field in R19_FIELDS))
            self.assertIn('待测试人',receipt)
            verify_bundle(p)
