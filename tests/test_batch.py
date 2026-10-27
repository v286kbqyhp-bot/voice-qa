import json
from pathlib import Path
import tempfile
import unittest

import yaml

from voiceqa.adapters import ReplayAdapter
from voiceqa.batch import classify, load_suite, record_suite, run_suite
from voiceqa.core import ROOT


class BatchTests(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT/'fixtures/workflow.json').read_text(encoding='utf-8'))['events']

    def source(self,root,entries,policy=None):
        p=root/'suite.yaml'
        p.write_text(yaml.safe_dump({'schema_version':1,'suite_id':'FAILURE-POLICY',
            'policy':policy or {'diagnostic_repeats':1,'continue_after_failure':True},'cases':entries}),encoding='utf-8')
        return p

    def entry(self,key,**extras):
        return {'id':key,'file':str(ROOT/'cases/voice-smoke.yaml'),**extras}

    def test_fail_repeat_pass_preserves_failure_and_continues_independent_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            source=self.source(root,[self.entry('first'),self.entry('next'),self.entry('dependent',depends_on=['first'])])
            def factory(key,role):
                calls.append((key,role))
                return ReplayAdapter([] if (key,role)==('first','initial') else self.fixture())
            def oracle(path,result):
                return {'status':'passed' if result['execution_status']=='completed' else 'not_passed',
                        'reason':'fixture oracle only, not product acceptance'}
            output,result=run_suite(source,factory,'replay',output_root=root/'batches',runs_root=root/'runs',decision=oracle)
            self.assertEqual(calls,[('first','initial'),('first','diagnostic'),('next','initial')])
            self.assertEqual([x['status'] for x in result['cases']],['not_passed','passed','skipped_dependency'])
            first=result['cases'][0]
            self.assertEqual(first['attempts'][0]['verdict']['status'],'not_passed')
            self.assertEqual(first['attempts'][1]['verdict']['status'],'passed')
            self.assertNotEqual(first['attempts'][0]['run_id'],first['attempts'][1]['run_id'])
            self.assertTrue((output/first['review_index']).exists())
            self.assertTrue((output/'review/first/01-initial/results.json').exists())
            self.assertTrue((output/'review/first/02-diagnostic/results.json').exists())
            manifest=json.loads((output/'review/first/02-diagnostic/run_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['context']['attempt_role'],'diagnostic')
            recorded=record_suite(output,root/'records')
            self.assertEqual([p.name for p in recorded.iterdir()],['suite_summary.json'])
            summary=json.loads((recorded/'suite_summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['cases'][0]['status'],'not_passed')

    def test_failure_repeats_only_once_even_if_repeat_also_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[];source=self.source(root,[self.entry('first')])
            def factory(key,role):calls.append(role);return ReplayAdapter([])
            output,result=run_suite(source,factory,'replay',output_root=root/'batches',runs_root=root/'runs')
            self.assertEqual(calls,['initial','diagnostic'])
            self.assertEqual(result['cases'][0]['status'],'not_passed')

    def test_completed_without_oracle_stays_pending_and_does_not_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[];source=self.source(root,[self.entry('first')])
            def factory(key,role):calls.append(role);return ReplayAdapter(self.fixture())
            output,result=run_suite(source,factory,'replay',output_root=root/'batches',runs_root=root/'runs')
            self.assertEqual(calls,['initial'])
            self.assertEqual(result['cases'][0]['status'],'pending_review')

    def test_precondition_block_is_not_a_product_failure_or_blind_retry(self):
        verdict=classify({'execution_status':'blocked','reason':'case declares blockers: missing oracle'})
        self.assertEqual(verdict['status'],'blocked')

    def test_invalid_unbounded_retry_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=self.source(Path(tmp),[self.entry('first')],{'diagnostic_repeats':3})
            with self.assertRaises(ValueError):load_suite(p)

    def test_operator_stop_prevents_retry_and_next_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[];source=self.source(root,[self.entry('first'),self.entry('next')])
            class Interrupted(ReplayAdapter):
                def play(self,asset):raise KeyboardInterrupt()
            def factory(key,role):calls.append((key,role));return Interrupted()
            output,result=run_suite(source,factory,'replay',output_root=root/'batches',runs_root=root/'runs')
            self.assertEqual(calls,[('first','initial')])
            self.assertTrue(result['aborted'])
            self.assertEqual(result['cases'][1]['status'],'skipped_cancelled')
