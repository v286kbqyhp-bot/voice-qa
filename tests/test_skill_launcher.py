import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / 'skills/voice-qa/scripts/launch.py'
SPEC = importlib.util.spec_from_file_location('voice_qa_launcher', LAUNCH)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class SkillLauncherTests(unittest.TestCase):
    def environment(self, **overrides):
        env = {k:v for k,v in os.environ.items() if k not in ('VOICEQA_REPO', 'VOICEQA_PYTHON')}
        return {**env, **overrides}

    def invoke(self, args, cwd, env=None, launcher=LAUNCH):
        return subprocess.run([sys.executable, str(launcher), *args], cwd=cwd,
            env=env or self.environment(), capture_output=True, text=True, encoding='utf-8', timeout=15)

    def test_repository_launcher_locates_runtime_from_unrelated_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=self.invoke(['--runtime-info'],tmp)
            self.assertEqual(result.returncode,0,result.stderr)
            info=json.loads(result.stdout)
            self.assertEqual(Path(info['runtime_root']),ROOT)
            self.assertEqual(info['cli_contract'],'voiceqa-cli-v2')
            self.assertIn('server_text',info['review_evidence_sources'])
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_copied_skill_uses_explicit_checkout_and_its_relative_case_paths(self):
        with tempfile.TemporaryDirectory(prefix='installed skill ') as tmp:
            path=Path(tmp)/'voice-qa/scripts/launch.py';path.parent.mkdir(parents=True)
            shutil.copyfile(LAUNCH,path)
            result=self.invoke(['validate','cases/voice-smoke.yaml'],tmp,
                self.environment(VOICEQA_REPO=str(ROOT),VOICEQA_PYTHON=sys.executable),path)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue(json.loads(result.stdout)['schema_valid'])

    def test_copied_skill_without_runtime_never_guesses_from_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'voice-qa/scripts/launch.py';path.parent.mkdir(parents=True)
            shutil.copyfile(LAUNCH,path)
            result=self.invoke(['--runtime-info'],ROOT,launcher=path)
            self.assertEqual(result.returncode,2)
            self.assertIn('VOICEQA_REPO',result.stderr)

    def test_explicit_bad_runtime_is_not_silently_replaced_with_local_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'voiceqa').mkdir()
            (root/'voiceqa/runtime-contract.json').write_text(json.dumps({'cli_contract':'future-v99'}),encoding='utf-8')
            result=self.invoke(['--help'],ROOT,self.environment(VOICEQA_REPO=str(root)))
            self.assertEqual(result.returncode,2)
            self.assertIn('contract mismatch',result.stderr)

    def test_dispatch_preserves_arguments_exit_status_and_runtime_cwd(self):
        with tempfile.TemporaryDirectory(prefix='runtime with spaces ') as tmp:
            root=Path(tmp);(root/'voiceqa').mkdir()
            shutil.copyfile(ROOT/'voiceqa/runtime-contract.json',root/'voiceqa/runtime-contract.json')
            for name in ('AGENTS.md','README.md','requirements-lock.txt','voiceqa/cli.py'):
                (root/name).write_text('',encoding='utf-8')
            (root/'qa.py').write_text('import os,sys,json\nprint(json.dumps({"args":sys.argv[1:],"cwd":os.getcwd()}))\nraise SystemExit(7)\n',encoding='utf-8')
            args=['inspect','路径 with spaces.yaml','literal;$(unchanged)']
            result=self.invoke(args,ROOT,self.environment(VOICEQA_REPO=str(root),VOICEQA_PYTHON=sys.executable))
            self.assertEqual(result.returncode,7,result.stderr)
            data=json.loads(result.stdout)
            self.assertEqual(data['args'],args)
            self.assertEqual(Path(data['cwd']).resolve(),root.resolve())

    def test_missing_python_override_fails_without_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=self.invoke(['--runtime-info'],tmp,self.environment(VOICEQA_PYTHON=str(Path(tmp)/'missing-python')))
            self.assertEqual(result.returncode,2)
            self.assertIn('VOICEQA_PYTHON',result.stderr)

    def test_windows_interrupt_allows_cli_to_finalize_before_exit(self):
        child=Mock();child.wait.side_effect=[KeyboardInterrupt,130]
        with patch.object(launcher.subprocess,'Popen',return_value=child):
            self.assertEqual(launcher.windows_cli(ROOT,[sys.executable,'qa.py']),130)
        self.assertEqual(child.wait.call_args_list,[call(),call(timeout=15)])
        child.terminate.assert_not_called();child.kill.assert_not_called()

    def test_windows_interrupt_stops_unresponsive_cli_after_cleanup_budget(self):
        child=Mock();child.wait.side_effect=[KeyboardInterrupt,subprocess.TimeoutExpired('qa.py',15),subprocess.TimeoutExpired('qa.py',3),1]
        with patch.object(launcher.subprocess,'Popen',return_value=child):
            self.assertEqual(launcher.windows_cli(ROOT,[sys.executable,'qa.py']),130)
        self.assertEqual(child.method_calls,[call.wait(),call.wait(timeout=15),call.terminate(),call.wait(timeout=3),call.kill(),call.wait()])

    def test_platform_entrypoint_can_probe_without_starting_device_work(self):
        wrapper=LAUNCH.parent/('voice-qa.cmd' if os.name=='nt' else 'voice-qa')
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run([str(wrapper),'--runtime-info'],cwd=tmp,
                env=self.environment(VOICEQA_PYTHON=sys.executable),capture_output=True,
                text=True,encoding='utf-8',timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['cli_contract'],'voiceqa-cli-v2')
