"""Offline checks: production controller, trace isolation and eval grading."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from eval.run_eval import grade
from src.agent.action_provider import MockActionProvider
from src.agent.agent_controller import AgentController
from src.agent.tracing import JsonlTracer
from src.agent.tool_registry import create_default_tool_registry


class TraceEvalTests(unittest.TestCase):
    def test_real_executor_trace_has_raw_validated_result_and_timing(self):
        registry = create_default_tool_registry()
        definition = registry.unregister_tool('analyze_user_activity')
        calls = []
        def fake(**kwargs):
            calls.append(kwargs)
            return {'ok': True, 'dau': 7}
        registry.register_tool(replace(definition, execute=fake))
        actions = [dict(action='call_tool', tool_name=definition.name,
                        arguments={'start_date':'最近7天','end_date':'最近7天'},
                        date_expression='最近7天'), dict(action='finish', remaining_tasks=[])]
        provider = MockActionProvider(response_factory=lambda p,c,n:
            dict(output=actions[n-1], model='fake', finish_reason='stop'))
        with tempfile.TemporaryDirectory() as directory:
            tracer = JsonlTracer(Path(directory)/'trace.jsonl')
            result = AgentController(provider, registry, tracer=tracer).run(
                '分析最近7天活跃', reference_date='2026-11-13')
            event = next(e for e in tracer.replay(result['session_id']) if e['event']=='tool_result')
            self.assertEqual(len(calls), 1)
            self.assertEqual(event['original_arguments']['start_date'], '最近7天')
            self.assertEqual(event['validated_arguments']['start_date'], '2026-11-07')
            self.assertEqual(event['result']['data']['dau'], 7)
            self.assertEqual(event['step'], 1)
            self.assertGreaterEqual(event['elapsed_ms'], 0)

    def test_trace_replays_clarification_in_same_session(self):
        actions = [dict(action='clarify', question='请补充日期'),
                   dict(action='finish', remaining_tasks=[])]
        provider = MockActionProvider(response_factory=lambda p, c, n:
            dict(output=actions[n-1], model='fake', finish_reason='stop'))
        with tempfile.TemporaryDirectory() as directory:
            tracer = JsonlTracer(Path(directory)/'trace.jsonl')
            agent = AgentController(provider, tracer=tracer)
            first = agent.run('分析活跃')
            original = deepcopy(first)
            final = agent.continue_with_clarification(first, '最近7天')
            self.assertEqual(first, original)
            self.assertEqual(first['session_id'], final['session_id'])
            events = tracer.replay(first['session_id'])
            self.assertEqual([e['event'] for e in events],
                             ['start', 'decision', 'end', 'resume', 'decision', 'end'])
            self.assertEqual(events[3]['clarification_history'][0]['answer'], '最近7天')
            self.assertEqual(tracer.replay('other'), [])

    def test_broken_logger_does_not_break_agent(self):
        class Broken:
            def record(self, *args, **kwargs):
                raise OSError('disk unavailable')
        provider = MockActionProvider(response=dict(output=dict(action='finish'),
                                                    model='fake', finish_reason='stop'))
        self.assertEqual(AgentController(provider, tracer=Broken()).run('结束')['status'], 'completed')

    def test_redaction_and_torn_line(self):
        with tempfile.TemporaryDirectory() as directory:
            tracer = JsonlTracer(Path(directory)/'events')
            self.assertTrue(tracer.record('one', 1, 'test', api_key='secret-value', text='sk-1234567890'))
            with tracer.path.open('a', encoding='utf-8') as handle:
                handle.write('{broken\n')
            self.assertNotIn('secret-value', tracer.path.read_text())
            self.assertEqual(len(tracer.replay('one')), 1)
            self.assertFalse(JsonlTracer(Path(directory)).record('one', 1, 'test'))

    def test_refusal_zero_tools_and_reason(self):
        provider = MockActionProvider(response=dict(output=dict(action='refuse', reason='只读分析不支持删除'),
                                                    model='fake', finish_reason='stop'))
        result = AgentController(provider).run('删除用户')
        self.assertTrue(grade(dict(category='refusal'), result)[0])
        self.assertEqual(result['tool_calls'], 0)
        self.assertIn('删除', result['clarification_question'])

    def test_24_balanced_cases(self):
        cases = json.loads((Path(__file__).resolve().parents[1]/'eval/cases.json').read_text(encoding='utf-8'))
        self.assertEqual(len({c['id'] for c in cases}), 24)
        for category in ('answerable', 'clarification', 'refusal'):
            self.assertEqual(sum(c['category']==category for c in cases), 8)

    def test_text_only_success_is_not_eval_pass(self):
        case = dict(category='answerable', tool='analyze_user_activity')
        self.assertFalse(grade(case, dict(status='completed', observations=[], summary='成功'))[0])
        self.assertFalse(grade(dict(category='refusal'), dict(status='completed', observations=[]))[0])


if __name__ == '__main__':
    unittest.main()
