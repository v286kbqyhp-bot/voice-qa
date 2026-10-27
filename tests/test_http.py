import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import jsonschema

from voiceqa.adapters import HttpAdapter, ReplayAdapter
from voiceqa.core import Blocked, ROOT, load_yaml
from voiceqa.runner import run, verify_bundle
from voiceqa.service import AdapterServer


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        events = json.loads((ROOT / 'fixtures/workflow.json').read_text(encoding='utf-8'))['events']
        self.server = AdapterServer(('127.0.0.1', 0), Path(self.temp.name)/'server', 'test-secret',
                                    adapter_factory=lambda: ReplayAdapter(events))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.client = HttpAdapter(self.url, 'test-secret')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_http_contract_end_to_end(self):
        # A remote fixture adapter remains replay, regardless of transport.
        p, result = run(ROOT/'cases/voice-smoke.yaml', self.client, 'replay', output_root=Path(self.temp.name)/'runs')
        self.assertEqual(result['execution_status'], 'completed')
        verify_bundle(p)

    def test_missing_auth_denied(self):
        with self.assertRaises(Blocked):
            HttpAdapter(self.url).capabilities()
        self.assertFalse(self.server.sessions)

    def test_openapi_matches_actual_response_and_rejects_ambiguous_asset(self):
        schemas = load_yaml(ROOT/'docs/openapi.yaml')['components']['schemas']
        response = self.client.capabilities()
        jsonschema.validate(response, schemas['Capabilities'])
        response = self.client.request('POST', '/v1/assets', {'text':'fixture'})
        jsonschema.validate(response, schemas['Asset'])
        with self.assertRaises(Blocked):
            self.client.request('POST', '/v1/assets', {'text':'fixture','wav_base64':response['wav_base64']})

    def test_busy_and_idempotent_stop(self):
        session = self.client.request('POST', '/v1/sessions', {'config': {}})['session_id']
        with self.assertRaises(Blocked):
            self.client.request('POST', '/v1/sessions', {'config': {}})
        first = self.client.request('POST', f'/v1/sessions/{session}/stop', {})
        second = self.client.request('POST', f'/v1/sessions/{session}/stop', {})
        self.assertEqual(first, second)

    def test_play_request_id_is_at_most_once(self):
        asset = self.client.request('POST', '/v1/assets', {'text':'fixture'})
        sid = self.client.request('POST', '/v1/sessions', {'config': {}})['session_id']
        params = {'asset_id':asset['asset_id'], 'request_id':'same-play'}
        first = self.client.request('POST', f'/v1/sessions/{sid}/play', params)
        second = self.client.request('POST', f'/v1/sessions/{sid}/play', params)
        self.assertEqual(first, second)
        self.assertEqual(len(self.server.sessions[sid]['adapter'].played), 1)

    def test_analyzer_not_configured_is_explicit_blocker(self):
        sid = self.client.request('POST', '/v1/sessions', {'config': {}})['session_id']
        self.client.request('POST', f'/v1/sessions/{sid}/stop', {})
        with self.assertRaisesRegex(Blocked, 'not configured'):
            self.client.request('POST', f'/v1/sessions/{sid}/analyze', {})

    def test_stop_failure_preserves_partial_result_and_closes_session(self):
        sid = self.client.request('POST', '/v1/sessions', {'config': {}})['session_id']
        def failed_finish():
            raise Blocked('driver unavailable')
        self.server.sessions[sid]['adapter'].finish = failed_finish
        result = self.client.request('POST', f'/v1/sessions/{sid}/stop', {})
        self.assertFalse(result['audio']['valid_capture'])
        self.assertEqual(result['capture_error'], 'Blocked')
        self.assertTrue(self.server.sessions[sid]['stopped'])

    def test_host_paths_are_not_accepted(self):
        with self.assertRaises(Blocked):
            self.client.request('POST', '/v1/assets', {'file': '/etc/passwd'})

    def test_host_adb_command_cannot_be_overridden_by_client(self):
        with self.assertRaises(Blocked):
            self.client.request('POST', '/v1/sessions', {'config': {'adb': '/arbitrary/command'}})

    def test_unknown_session_is_not_found(self):
        with self.assertRaises(Blocked):
            self.client.request('POST', '/v1/sessions/' + 'a'*32 + '/stop', {})

    def test_non_loopback_requires_token(self):
        with self.assertRaises(ValueError):
            AdapterServer(('0.0.0.0', 0), Path(self.temp.name)/'other')


if __name__ == '__main__':
    unittest.main()
