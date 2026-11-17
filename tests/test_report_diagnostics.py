"""Synthetic regressions; historical Live report bodies were not retained."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from test_result_explainer import report
from src.agent.agent_controller import AgentController
from src.agent.action_provider import MockActionProvider
from src.agent.result_explainer import ResultExplainer, _diagnostic_snapshot
from src.agent.result_formatter import format_success
from src.agent.tool_registry import create_default_tool_registry
from src.agent.tracing import JsonlTracer
from eval import run_eval


class DiagnosticTests(unittest.TestCase):
    def explain(self, output):
        provider = Mock()
        provider.generate_report.return_value = dict(output=output, model='fake', finish_reason='stop')
        return ResultExplainer(provider).explain('分析活跃', 'analyze_user_activity',
            format_success('analyze_user_activity', {'dau':10, 'wau':30, 'mau':80}))

    def test_semantic_template_has_exact_actual_expected(self):
        output = report()
        output['key_findings'][0]['description'] = 'DAU持续增长'
        original = deepcopy(output)
        result = self.explain(output)
        self.assertEqual(result['error']['code'], 'unverified_semantic_statement')
        details = result['error']['details']
        self.assertEqual(details['field'], '/key_findings/0/description')
        self.assertEqual(details['actual'], 'DAU持续增长')
        self.assertEqual(details['expected'], report()['key_findings'][0]['description'])
        self.assertEqual(result['failed_report'], output)
        self.assertIsNone(result['report'])
        self.assertEqual(output, original)

    def test_old_generic_failure_is_now_specific(self):
        for kind, code in [('missing', 'report_field_missing'), ('type', 'report_type_error'),
                           ('path', 'evidence_path_error'), ('value', 'evidence_value_mismatch')]:
            output = report()
            if kind == 'missing':
                del output['summary']
            elif kind == 'type':
                output['summary'] = []
            elif kind == 'path':
                output['key_findings'][0]['evidence'][0]['json_path'] = '/missing'
            else:
                output['key_findings'][0]['evidence'][0]['value'] = 999
            with self.subTest(kind=kind):
                result = self.explain(output)
                self.assertEqual(result['error']['code'], code)
                self.assertTrue(result['error']['details']['field'])
                self.assertIn('actual', result['error']['details'])
                self.assertIn('expected', result['error']['details'])

    def test_snapshot_redacts_limits_and_excludes_thinking(self):
        snapshot = _diagnostic_snapshot(dict(api_key='private', reasoning_content='private thoughts',
                                             text='Authorization: Bearer private-token'))
        encoded = json.dumps(snapshot)
        self.assertNotIn('private', encoded)
        self.assertLessEqual(len(json.dumps(_diagnostic_snapshot({'text':'x'*20000}), ensure_ascii=False)), 8000)

    def test_successful_tool_failed_report_has_separate_delivery_and_trace(self):
        class Provider(MockActionProvider):
            def generate_report(self, prompt, context):
                output = report()
                output['key_findings'][0]['description'] = '构造的错误结论'
                return dict(output=output, model='fake', finish_reason='stop')
        actions = [dict(action='call_tool', tool_name='analyze_user_activity',
                        arguments={}, date_expression='最近7天'),
                   dict(action='finish', remaining_tasks=[])]
        provider = Provider(response_factory=lambda p,c,n:
            dict(output=actions[n-1], model='fake', finish_reason='stop'))
        registry = create_default_tool_registry()
        original = registry.unregister_tool('analyze_user_activity')
        registry.register_tool(replace(original, execute=lambda **kw: {'dau':10, 'wau':30, 'mau':80}))
        with tempfile.TemporaryDirectory() as folder:
            tracer = JsonlTracer(Path(folder)/'trace.jsonl')
            result = AgentController(provider, registry, tracer=tracer, explain_results=True).run(
                '分析最近7天活跃', reference_date='2026-11-13')
            self.assertTrue(result['ok'])  # Existing execution compatibility.
            self.assertEqual(result['execution_status'], 'success')
            self.assertEqual(result['report_status'], 'failed')
            self.assertEqual(result['delivery_status'], 'report_failed')
            self.assertTrue(result['observations'][0]['result']['ok'])
            event = next(e for e in tracer.replay(result['session_id']) if e['event']=='report_result')
            self.assertEqual(event['explanation']['error']['details']['field'], '/key_findings/0/description')
            self.assertIsNotNone(event['explanation']['failed_report'])

    def test_targeted_eval_selects_only_three_original_cases(self):
        selected = ['answer_2', 'answer_4', 'answer_6']
        provider = Mock()
        provider.config.model = 'fake'
        with patch('sys.argv', ['eval', '--case-ids', *selected]), \
             patch.object(run_eval, 'DeepSeekProvider', return_value=provider), \
             patch.object(run_eval, 'AgentController'), \
             patch.object(run_eval, 'evaluate', return_value=[]) as evaluate, \
             patch.object(run_eval, 'save_report') as save:
            self.assertEqual(run_eval.main(), 0)
            self.assertEqual([c['id'] for c in evaluate.call_args.args[0]], selected)
            self.assertEqual(save.call_args.kwargs['selected_ids'], selected)


if __name__ == '__main__':
    unittest.main()
