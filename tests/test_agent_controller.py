"""Bounded decisions with injected responses and real Executor/fake tools."""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.agent.action_provider import MockActionProvider
from src.agent.agent_controller import AgentController
from src.agent.intent.deepseek_provider import DeepSeekProvider
from src.agent.intent.provider import IntentProviderError
from src.agent.tool_executor import ToolExecutor
from src.agent.tool_registry import ToolRegistry, create_default_tool_registry


ACTIVITY = "analyze_user_activity"
RETENTION = "analyze_retention"
DATES = {"start_date": "2026-11-07", "end_date": "2026-11-13"}


def call(name=ACTIVITY, arguments=None):
    return dict(action="call_tool", tool_name=name, arguments=deepcopy(DATES if arguments is None else arguments))


def response(action):
    return dict(output=action, model="offline-action-mock", finish_reason="stop")


def provider_for(actions):
    return MockActionProvider(response_factory=lambda prompt, context, n: response(actions[n - 1]))


class AgentControllerTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_default_tool_registry()
        self.functions = {}
        for definition in self.registry.list_tools():
            self.registry.unregister_tool(definition.name)
            function = Mock(return_value={"ok": True, "users": 12, "unknown": None})
            self.functions[definition.name] = function
            self.registry.register_tool(replace(definition, execute=function))
        self.network = patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden"))
        self.database = patch.object(sqlite3, "connect", side_effect=AssertionError("database forbidden"))
        self.sql = patch("src.tools.sql_query_tool.SQLQueryTool.run", side_effect=AssertionError("SQL forbidden"))
        for guard in (self.network, self.database, self.sql):
            guard.start()
            self.addCleanup(guard.stop)

    def run_actions(self, actions, **kwargs):
        provider = provider_for(actions)
        controller = AgentController(provider, self.registry, **kwargs)
        return controller.run("分析2026-11-07至2026-11-13的活跃和留存"), provider

    def test_single_tool_and_finish(self):
        result, provider = self.run_actions([call(), {"action": "finish"}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["observations"][0]["result"]["data"]["users"], 12)
        self.assertIsNone(result["observations"][0]["result"]["data"]["unknown"])
        self.functions[ACTIVITY].assert_called_once()
        self.assertEqual(provider.call_count, 2)

    def test_two_different_tools_and_real_observations(self):
        actions = [call(), call(RETENTION, {"cohort_start": "2026-08-16", "cohort_end": "2026-10-14"}), {"action": "finish"}]
        result, provider = self.run_actions(actions)
        self.assertEqual([r["tool_name"] for r in result["observations"]], [ACTIVITY, RETENTION])
        self.functions[ACTIVITY].assert_called_once()
        self.functions[RETENTION].assert_called_once()
        second_context = json.loads(provider.calls[1]["user_input"])
        self.assertEqual(second_context["observations"], result["observations"][:1])
        self.assertEqual(len(second_context["tools"]), 4)
        self.assertEqual(second_context["user_input"], result["user_input"])

    def test_tool_order_is_decided_by_provider(self):
        actions = [call(RETENTION, {"cohort_start": "2026-08-16", "cohort_end": "2026-10-14"}), call(), {"action": "finish"}]
        result, _ = self.run_actions(actions)
        self.assertEqual([r["tool_name"] for r in result["observations"]], [RETENTION, ACTIVITY])

    def test_tool_exception_retains_error_and_stops(self):
        self.functions[ACTIVITY].side_effect = ValueError("fake tool failure")
        result, provider = self.run_actions([call(), {"action": "finish"}])
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"][0]["result"]["error"]["code"], "tool_execution_error")
        self.assertIn("fake tool failure", result["observations"][0]["result"]["error"]["message"])
        self.assertEqual(provider.call_count, 1)
        self.functions[ACTIVITY].assert_called_once()

    def test_explicit_business_failure_is_not_success(self):
        self.functions[ACTIVITY].return_value = {"ok": False, "reason": "missing coverage"}
        result, _ = self.run_actions([call()])
        self.assertEqual(result["observations"][0]["result"]["error"]["code"], "business_tool_failed")
        self.assertFalse(result["ok"])

    def test_illegal_tool_cannot_execute(self):
        result, _ = self.run_actions([call("__import__('os').system")])
        self.assertEqual(result["error"]["code"], "tool_not_found")
        self.assertEqual(result["tool_calls"], 0)
        for function in self.functions.values():
            function.assert_not_called()

    def test_schema_invalid_parameters_stop_before_executor(self):
        for arguments in ({}, {**DATES, "sql": "DELETE FROM users"}, {**DATES, "start_date": "not-date"}, {**DATES, "breakdown_by": "secret"}, {**DATES, "start_date": "2026-12-01"}):
            with self.subTest(arguments=arguments):
                executor = ToolExecutor(self.registry)
                with patch.object(executor, "execute", wraps=executor.execute) as execute:
                    result, _ = self.run_actions([call(arguments=arguments)], executor=executor)
                self.assertEqual(result["status"], "needs_clarification")
                self.assertTrue(result["clarification_question"])
                execute.assert_not_called()

    def test_limit_prevents_extra_tool_execution(self):
        result, provider = self.run_actions([call(), call(), call()], max_tool_calls=2)
        self.assertEqual(result["status"], "limit_reached")
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual(self.functions[ACTIVITY].call_count, 2)
        self.assertEqual(provider.call_count, 3)

    def test_finish_permitted_at_limit(self):
        result, _ = self.run_actions([call(), {"action": "finish"}], max_tool_calls=1)
        self.assertEqual(result["status"], "completed")

    def test_active_finish_without_tool_has_no_invented_data(self):
        result, _ = self.run_actions([{"action": "finish"}])
        self.assertTrue(result["ok"])
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["tool_calls"], 0)

    def test_model_cannot_put_unverified_answer_in_finish(self):
        result, _ = self.run_actions([{"action": "finish", "answer": "DAU为9999"}])
        self.assertEqual(result["status"], "model_output_invalid")

    def test_clarification_stops_without_tool(self):
        question = "你希望分析哪段注册日期的留存？"
        result, provider = self.run_actions([{"action": "clarify", "question": question}])
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["clarification_question"], question)
        self.assertEqual(provider.call_count, 1)

    def test_invalid_user_input_never_calls_provider(self):
        provider = provider_for([])
        for value in (None, 42, "", " "):
            self.assertEqual(AgentController(provider, self.registry).run(value)["status"], "invalid_input")
        self.assertEqual(provider.call_count, 0)

    def test_provider_failure_preserves_safe_code(self):
        provider = MockActionProvider(error=IntentProviderError("request_timeout", "secret", retryable=True))
        result = AgentController(provider, self.registry).run("分析活跃")
        self.assertEqual(result["error"]["code"], "request_timeout")
        self.assertNotIn("secret", str(result))

    def test_provider_failure_after_success_keeps_observation(self):
        provider = provider_for([call()])
        result = AgentController(provider, self.registry).run("分析活跃")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["observations"][0]["result"]["ok"], True)

    def test_invalid_response_and_truncation(self):
        for value in (None, {"output": {"action": "finish"}, "model": "fake", "finish_reason": "length"}, response({"action": "call_tool", "tool_name": ACTIVITY, "arguments": []}), response({"action": "exec", "code": "print(1)"})):
            with self.subTest(value=value):
                provider = Mock(generate_action=Mock(return_value=value))
                result = AgentController(provider, self.registry).run("分析活跃")
                self.assertEqual(result["status"], "model_output_invalid")

    def test_empty_registry_is_not_replaced_by_default(self):
        provider = provider_for([])
        result = AgentController(provider, ToolRegistry()).run("分析活跃")
        self.assertEqual(result["error"]["code"], "empty_registry")
        self.assertEqual(provider.call_count, 0)

    def test_read_write_tool_is_blocked(self):
        definition = self.registry.unregister_tool(ACTIVITY)
        self.registry.register_tool(replace(definition, read_only=False))
        result, _ = self.run_actions([call()])
        self.assertEqual(result["error"]["code"], "tool_not_read_only")
        self.functions[ACTIVITY].assert_not_called()

    def test_registry_changes_reflected_in_next_prompt(self):
        def factory(prompt, context, n):
            if n == 1:
                self.registry.unregister_tool(RETENTION)
                return response(call())
            self.assertNotIn(RETENTION, [t["name"] for t in json.loads(context)["tools"]])
            return response(call(RETENTION))
        result = AgentController(MockActionProvider(response_factory=factory), self.registry).run("分析活跃留存")
        self.assertEqual(result["error"]["code"], "tool_not_found")

    def test_schema_defaults_and_input_not_mutated(self):
        action = call()
        before = deepcopy(action)
        result, _ = self.run_actions([action, {"action": "finish"}])
        self.assertEqual(action, before)
        self.assertIsNone(result["observations"][0]["arguments"]["breakdown_by"])

    def test_repeated_runs_are_isolated(self):
        provider = MockActionProvider(response_factory=lambda p, c, n: response(call() if not json.loads(c)["observations"] else {"action": "finish"}))
        controller = AgentController(provider, self.registry)
        first = controller.run("同一任务")
        second = controller.run("同一任务")
        self.assertEqual(first, second)
        first["observations"][0]["result"]["data"]["users"] = 999
        self.assertEqual(second["observations"][0]["result"]["data"]["users"], 12)

    def test_context_limit_has_no_silent_truncation(self):
        result, provider = self.run_actions([], max_context_chars=1)
        self.assertEqual(result["error"]["code"], "context_too_large")
        self.assertEqual(provider.call_count, 0)

    def test_invalid_limits_and_executor_registry(self):
        for limit in (0, True, -1, 21, 1.5):
            with self.assertRaises(ValueError):
                AgentController(provider_for([]), self.registry, max_tool_calls=limit)
        with self.assertRaises(ValueError):
            AgentController(provider_for([]), self.registry, executor=ToolExecutor(ToolRegistry()))

    def test_deepseek_action_reuses_fake_sdk(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(model="deepseek-flash", choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"action":"finish"}'))])
        provider = DeepSeekProvider(environ={"DEEPSEEK_API_KEY": "fake", "DEEPSEEK_MODEL": "deepseek-flash"}, client_factory=lambda **kwargs: client)
        self.assertEqual(AgentController(provider, self.registry).run("查询可用工具")["status"], "completed")
        client.chat.completions.create.assert_called_once()
        self.assertEqual(client.chat.completions.create.call_args.kwargs["response_format"], {"type": "json_object"})

    def test_optional_explainer_failure_does_not_change_execution(self):
        provider = provider_for([call(), {"action": "finish"}])
        provider.generate_report = Mock(side_effect=IntentProviderError("request_timeout", "safe", retryable=True))
        result = AgentController(provider, self.registry, explain_results=True).run("分析活跃")
        self.assertTrue(result["ok"])
        self.assertTrue(result["observations"][0]["result"]["ok"])
        self.assertEqual(result["explanations"][0]["result"]["status"], "failed")
        self.assertNotIn("explanations", json.loads(provider.calls[1]["user_input"]))


if __name__ == "__main__":
    unittest.main()
