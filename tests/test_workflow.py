import copy
import json
from pathlib import Path
import tempfile
import unittest

from voiceqa.adapters import AdbEvents, ReplayAdapter, match_event, parse_log, redact
from voiceqa.core import Blocked, ROOT, load_case, load_yaml, resolve, safe_path
from voiceqa.runner import record_bundle, run, verify_bundle


class WorkflowTests(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT / 'fixtures/workflow.json').read_text())['events']

    def test_legacy_attachment_preserved_and_unbound(self):
        data = load_case(ROOT / 'sources/legacy/AI-CMD-001-WAKE.yaml')
        self.assertEqual(data['case_id'], 'AI-CMD-001-WAKE')
        with self.assertRaisesRegex(Blocked, 'unresolved'):
            resolve(data['steps'], {})

    def test_yaml_rejects_duplicate_keys_and_python_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'unsafe.yaml'
            p.write_text('case_id: a\ncase_id: b\n')
            with self.assertRaises(ValueError):
                load_yaml(p)
            p.write_text('!!python/object/apply:os.system [echo should-not-execute]')
            with self.assertRaises(Exception):
                load_yaml(p)

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            safe_path(Path('/tmp/artifacts'), '../outside')

    def test_binding_is_whole_value_no_expression_eval(self):
        self.assertEqual(resolve('{{asset.x}}', {'asset.x': 'a.wav'}), 'a.wav')
        with self.assertRaises(Blocked):
            resolve('prefix {{asset.x}}', {'asset.x': 'a.wav'})

    def test_old_delayed_wrong_trace_and_duplicates_do_not_trigger(self):
        adapter = ReplayAdapter(self.fixture())
        adapter.now = 0.1
        ready = adapter.wait_event({'event': 'input_ready', 'bind_trace': True})
        self.assertEqual(ready['id'], 'ready')
        reply = adapter.wait_event({'event': 'worker_speaking', 'same_trace': True, 'where': {'speaking': 'true'}})
        self.assertEqual(reply['id'], 'answer-start')
        with self.assertRaises(Blocked):
            adapter.wait_event({'event': 'worker_speaking', 'same_trace': True,
                                'where': {'speaking': 'true'}, 'timeout_s': 1})

    def test_matching_requires_trace_binding(self):
        self.assertFalse(match_event({'event': 'x', 'trace': None}, {'event': 'x', 'same_trace': True}))

    def test_log_parser_preserves_layer_without_claiming_acoustic_evidence(self):
        e = parse_log('10-27 14:00:01.123 123 456 I P003VoiceRealCanary: state=input_ready trace=abc', 5.2, 4)
        self.assertEqual(e['event'], 'input_ready')
        self.assertEqual(e['evidence_layer'], 'device_log')
        self.assertEqual(e['raw_line'], 4)
        self.assertNotIn('audible', e)

    def test_secret_redaction(self):
        text = redact('token=abc password=def Authorization: Bearer xyz')
        self.assertNotIn('abc', text)
        self.assertNotIn('def', text)
        self.assertNotIn('xyz', text)

    def test_adb_preflight_quotes_remote_date_and_uses_active_version(self):
        import io
        from unittest.mock import patch
        def read(command, **kwargs):
            if command[-1] == 'devices':
                return 'List of devices attached\ntransport-test\tdevice\n'
            if command[-1] == "date '+%m-%d %H:%M:%S'":
                return '10-27 14:00:00\n'
            if 'dumpsys' in command:
                return 'codePath=/data/app/pkg\nversionCode=319\nversionName=1.2.2-canary.305\ncodePath=/system/priv-app/pkg\nversionCode=12\nversionName=1.2.1\n'
            if 'path' in command:
                return 'package:/data/app/pkg/base.apk\n'
            self.fail('unexpected or incorrectly quoted ADB query')
        class Process:
            stdout = io.StringIO('')
            stderr = io.StringIO('')
            def terminate(self): pass
            def wait(self, timeout): return 0
        with tempfile.TemporaryDirectory() as tmp:
            with patch('voiceqa.adapters.subprocess.check_output', side_effect=read), patch(
                    'voiceqa.adapters.subprocess.Popen', return_value=Process()):
                source = AdbEvents()
                meta = source.start(Path(tmp), {'adb':'adb'})
                source.stop()
                self.assertEqual(meta['version_code'], '319')
                self.assertNotIn('transport-test', json.dumps(meta))

    def test_end_to_end_replay_and_record_has_all_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, result = run(ROOT / 'cases/voice-smoke.yaml', ReplayAdapter(self.fixture()), 'replay', output_root=Path(tmp)/'runs')
            self.assertEqual(result['execution_status'], 'completed')
            self.assertEqual(result['product_status'], 'not_applicable')
            self.assertEqual(len(result['steps']), 8)
            verify_bundle(p)
            recorded = record_bundle(p, Path(tmp)/'records')
            verify_bundle(recorded)
            self.assertEqual(len(list((recorded/'steps').glob('*.json'))), 8)

    def test_missing_trigger_stops_dependents_and_preserves_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, result = run(ROOT / 'cases/voice-smoke.yaml', ReplayAdapter(), 'replay', output_root=Path(tmp))
            self.assertEqual(result['execution_status'], 'blocked')
            self.assertEqual(result['steps'][1]['execution_status'], 'blocked')
            self.assertEqual(result['steps'][2]['execution_status'], 'not_executed')
            self.assertEqual(len(result['steps']), 8)
            self.assertNotEqual(result['product_status'], 'fail')

    def test_unbound_legacy_case_never_plays(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ReplayAdapter()
            p, result = run(ROOT / 'cases/legacy-barge-unbound.yaml', adapter, 'replay', output_root=Path(tmp))
            self.assertEqual(len(adapter.played), 0)
            self.assertIn('unresolved', result['reason'])

    def test_changed_evidence_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, result = run(ROOT / 'cases/voice-smoke.yaml', ReplayAdapter(self.fixture()), 'replay', output_root=Path(tmp))
            (p/'results.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'changed'):
                verify_bundle(p)

    def test_retry_runs_have_different_directories_and_keep_first_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, a = run(ROOT / 'cases/voice-smoke.yaml', ReplayAdapter(), 'replay', output_root=Path(tmp))
            second, b = run(ROOT / 'cases/voice-smoke.yaml', ReplayAdapter(self.fixture()), 'replay', output_root=Path(tmp))
            self.assertNotEqual(first, second)
            self.assertEqual(a['execution_status'], 'blocked')
            self.assertEqual(b['execution_status'], 'completed')
            verify_bundle(first)

    def test_live_record_only_exports_allowlisted_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, result = run(ROOT / 'cases/voice-smoke.yaml', ReplayAdapter(self.fixture()), 'live', output_root=Path(tmp)/'runs')
            (p/'logs/secret.log').write_text('private text')
            recorded = record_bundle(p, Path(tmp)/'records')
            self.assertEqual([x.name for x in recorded.iterdir()], ['summary.json'])
            self.assertNotIn('private text', (recorded/'summary.json').read_text())


if __name__ == '__main__':
    unittest.main()
