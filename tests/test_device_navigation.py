from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from voiceqa.adapters import AdbEvents, ReplayAdapter
from voiceqa.core import Blocked, load_case
from voiceqa.runner import run


class NavigationTests(unittest.TestCase):
    def test_conditional_home_cleanup_skips_a_closed_room_without_cancelling_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'case.yaml'
            case.write_text(yaml.safe_dump({'case_id':'NAV-CLEANUP','title':'quiet cleanup','domain':'test','severity':'normal',
                'steps':[{'kind':'sleep','name':'observe','params':{'seconds':0}}],
                'cleanup':[{'kind':'device_action','name':'home','params':{'action':'exit_voice','if_session_active':True}}]}))
            class TestAdapter(ReplayAdapter):
                def capabilities(self):return {**super().capabilities(),'device_ui_exit':True}
                def session_status(self):return {'state':'closed','reason':'idle_timeout'}
                def device_action(self,params):raise AssertionError('closed room must not receive navigation')
            adapter=TestAdapter();_,result=run(case,adapter,'replay',output_root=root/'runs')
            self.assertEqual(result['execution_status'],'completed')
            self.assertEqual(result['steps'][-1]['actual']['action'],'no_op')
            self.assertEqual(adapter.played,[])

    def test_exit_requires_active_room_and_launcher_foreground_before_sending_back(self):
        source=AdbEvents()
        source.adb='adb';source.serial='transport';source.package='com.example.launcher'
        source.all_events=lambda:[]
        with patch('voiceqa.adapters.subprocess.run') as send:
            with self.assertRaisesRegex(Blocked,'active'):
                source.exit_voice('current')
            send.assert_not_called()
        source.all_events=lambda:[{'event':'started','trace':'current','received_monotonic_s':1,'fields':{}}]
        with patch('voiceqa.adapters.subprocess.check_output',return_value='mResumedActivity: com.other/app'), \
                patch('voiceqa.adapters.subprocess.run') as send:
            with self.assertRaisesRegex(Blocked,'foreground'):
                source.exit_voice('current')
            send.assert_not_called()

    def test_exit_saves_both_screenshots_and_does_not_claim_product_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=AdbEvents();source.adb='adb';source.serial='transport';source.package='com.example.launcher';source.output=Path(tmp)
            source.all_events=lambda:[{'event':'started','trace':'current','received_monotonic_s':1,'fields':{}}]
            with patch('voiceqa.adapters.subprocess.check_output',side_effect=[
                    'topResumedActivity: com.example.launcher/Home',b'before-png',b'after-png']), \
                    patch('voiceqa.adapters.subprocess.run') as send:
                result=source.exit_voice('current')
            self.assertTrue(result['command_completed'])
            self.assertFalse(result['product_exit_verified'])
            self.assertEqual(set(result['screenshots']),{'before','after'})
            for item in result['screenshots'].values():self.assertTrue((source.output/item['file']).is_file())
            self.assertEqual(send.call_args.args[0],['adb','-s','transport','shell','input','keyevent','4'])

    def test_missing_navigation_capability_blocks_before_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'case.yaml'
            case.write_text(yaml.safe_dump({'case_id':'NAV','title':'exit','domain':'test','severity':'normal',
                'steps':[{'kind':'device_action','name':'exit','params':{'action':'exit_voice'}}]}))
            adapter=ReplayAdapter();adapter.begin=Mock()
            _,result=run(case,adapter,'replay',output_root=root/'runs')
            self.assertIn('device_ui_exit capability unavailable',result['reason']);adapter.begin.assert_not_called()
            data=yaml.safe_load(case.read_text());data['steps'][0]['params']['action']='arbitrary_shell'
            case.write_text(yaml.safe_dump(data))
            with self.assertRaisesRegex(ValueError,'only supports exit_voice'):load_case(case)

    def test_declared_navigation_requirement_allows_implemented_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);case=root/'case.yaml'
            case.write_text(yaml.safe_dump({'case_id':'NAV','title':'exit','domain':'test','severity':'normal',
                'requires':{'capabilities':['device_ui_exit']},
                'steps':[{'kind':'device_action','name':'exit','params':{'action':'exit_voice'}}]}))
            class TestAdapter(ReplayAdapter):
                def capabilities(self):return {'adapter':'test','device_ui_exit':True}
                def device_action(self,params):return {'fixture':True,'product_exit_verified':False}
            adapter=TestAdapter();adapter.begin=Mock(return_value={})
            _,result=run(case,adapter,'live',output_root=root/'runs')
            adapter.begin.assert_called_once()
            self.assertEqual(result['execution_status'],'completed')


if __name__=='__main__':unittest.main()
