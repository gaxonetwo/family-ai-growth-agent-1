"""Offline clarification recovery tests for the bounded single-agent loop."""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.agent.action_provider import MockActionProvider
from src.agent.agent_controller import AgentController
from src.agent.tool_executor import ToolExecutor
from src.agent.tool_registry import create_default_tool_registry


ACTIVITY = "analyze_user_activity"
RETENTION = "analyze_retention"
ACTIVITY_DATES = {"start_date": "2026-11-07", "end_date": "2026-11-13"}
RETENTION_DATES = {"cohort_start": "2026-11-07", "cohort_end": "2026-11-13"}


def response(action):
    return {"output": action, "model": "offline-recovery-mock", "finish_reason": "stop"}


def call(tool=ACTIVITY, arguments=None, **extra):
    return {"action": "call_tool", "tool_name": tool,
            "arguments": deepcopy(arguments or {}), **extra}


def provider_for(actions):
    return MockActionProvider(
        response_factory=lambda prompt, context, number: response(actions[number - 1]))


class AgentClarificationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_default_tool_registry()
        self.functions = {}
        for definition in self.registry.list_tools():
            self.registry.unregister_tool(definition.name)
            function = Mock(return_value={"ok": True, "marker": definition.name})
            self.functions[definition.name] = function
            self.registry.register_tool(replace(definition, execute=function))
        for guard in (
            patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")),
            patch.object(sqlite3, "connect", side_effect=AssertionError("database forbidden")),
            patch("src.tools.sql_query_tool.SQLQueryTool.run", side_effect=AssertionError("SQL forbidden")),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def controller(self, actions):
        provider = provider_for(actions)
        executor = ToolExecutor(self.registry)
        return AgentController(provider, self.registry, executor=executor), provider, executor

    def test_clarification_answer_restores_original_task_and_resolves_date(self):
        actions = [
            {"action": "clarify", "question": "你希望分析哪段日期？"},
            call(date_expression="最近7天"),
            {"action": "finish"},
        ]
        controller, provider, _ = self.controller(actions)
        original = "请分析用户活跃情况，日期还没有确定。"
        first = controller.run(original, reference_date="2026-11-13")
        result = controller.continue_with_clarification(first, "请使用最近7天。")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["user_input"], original)
        self.assertEqual(result["clarification_count"], 1)
        self.assertEqual(result["clarification_history"][0]["answer"], "请使用最近7天。")
        self.assertEqual(result["observations"][0]["arguments"]["start_date"], "2026-11-07")
        restored_context = json.loads(provider.calls[1]["user_input"])
        self.assertEqual(restored_context["task_context"]["original_request"], original)
        self.assertEqual(restored_context["user_messages"], [original, "请使用最近7天。"])

    def test_successful_observation_is_preserved_and_duplicate_is_not_executed(self):
        question = "是否还需要留存分析？"
        actions = [
            call(arguments=ACTIVITY_DATES),
            {"action": "clarify", "question": question},
            call(arguments=ACTIVITY_DATES),
            call(RETENTION, RETENTION_DATES),
            {"action": "finish"},
        ]
        controller, _, executor = self.controller(actions)
        with patch.object(executor, "execute", wraps=executor.execute) as execute:
            first = controller.run("分析活跃和留存，日期为2026-11-07至2026-11-13")
            first_snapshot = deepcopy(first)
            result = controller.continue_with_clarification(first, "是的，请继续分析留存。")

        self.assertEqual(first, first_snapshot)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["tool_name"] for item in result["observations"]],
                         [ACTIVITY, RETENTION])
        self.functions[ACTIVITY].assert_called_once()
        self.functions[RETENTION].assert_called_once()
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(result["reused_calls"][0]["tool_name"], ACTIVITY)
        self.assertEqual(result["reused_calls"][0]["original_call_index"], 1)

    def test_recovered_arguments_still_use_live_schema(self):
        actions = [
            {"action": "clarify", "question": "请补充日期。"},
            call(arguments={**ACTIVITY_DATES, "sql": "DELETE FROM users"}),
        ]
        controller, _, executor = self.controller(actions)
        first = controller.run("分析活跃")
        with patch.object(executor, "execute", wraps=executor.execute) as execute:
            result = controller.continue_with_clarification(
                first, "日期是2026-11-07至2026-11-13。")
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["error"]["code"], "argument_validation_error")
        execute.assert_not_called()

    def test_two_clarification_rounds_can_finish(self):
        actions = [
            {"action": "clarify", "question": "请说明日期。"},
            {"action": "clarify", "question": "请确认是否按渠道分组。"},
            call(arguments={**ACTIVITY_DATES, "breakdown_by": "acquisition_channel"}),
            {"action": "finish"},
        ]
        controller, _, _ = self.controller(actions)
        first = controller.run("分析活跃")
        second = controller.continue_with_clarification(
            first, "日期为2026-11-07至2026-11-13。")
        result = controller.continue_with_clarification(second, "是，按获客渠道分组。")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification_count"], 2)
        self.assertEqual(len(result["clarification_history"]), 2)

    def test_third_clarification_round_is_rejected_without_provider_call(self):
        actions = [
            {"action": "clarify", "question": "问题一？"},
            {"action": "clarify", "question": "问题二？"},
            {"action": "clarify", "question": "问题三？"},
        ]
        controller, provider, _ = self.controller(actions)
        first = controller.run("分析活跃")
        second = controller.continue_with_clarification(first, "回答一")
        third = controller.continue_with_clarification(second, "回答二")
        rejected = controller.continue_with_clarification(third, "回答三")
        self.assertEqual(rejected["status"], "clarification_limit_reached")
        self.assertEqual(rejected["error"]["code"], "clarification_limit_reached")
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(rejected["clarification_count"], 2)

    def test_empty_answer_and_non_clarification_result_cannot_resume(self):
        controller, provider, _ = self.controller([
            {"action": "clarify", "question": "请补充日期。"},
        ])
        first = controller.run("分析活跃")
        before = deepcopy(first)
        for answer in (None, "", "   "):
            with self.subTest(answer=answer):
                result = controller.continue_with_clarification(first, answer)
                self.assertEqual(result["error"]["code"], "invalid_clarification_answer")
                self.assertEqual(result["clarification_count"], 0)
        self.assertEqual(first, before)
        self.assertEqual(provider.call_count, 1)

        finished_controller, _, _ = self.controller([{"action": "finish"}])
        finished = finished_controller.run("不需要调用工具")
        invalid = finished_controller.continue_with_clarification(finished, "补充")
        self.assertEqual(invalid["status"], "invalid_input")
        self.assertEqual(invalid["error"]["code"], "clarification_not_allowed")

        malformed = deepcopy(first)
        malformed["task_context"]["original_request"] = "被篡改的任务"
        rejected = controller.continue_with_clarification(malformed, "补充")
        self.assertEqual(rejected["status"], "invalid_input")
        self.assertEqual(rejected["error"]["code"], "invalid_previous_result")
        self.assertEqual(provider.call_count, 1)

    def test_repeated_completed_call_cannot_spin_or_execute(self):
        actions = [
            call(arguments=ACTIVITY_DATES),
            {"action": "clarify", "question": "请确认。"},
            call(arguments=ACTIVITY_DATES),
            call(arguments=ACTIVITY_DATES),
        ]
        controller, _, _ = self.controller(actions)
        first = controller.run("分析2026-11-07至2026-11-13活跃")
        result = controller.continue_with_clarification(first, "确认。")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "repeated_completed_tool_call")
        self.functions[ACTIVITY].assert_called_once()


if __name__ == "__main__":
    unittest.main()
