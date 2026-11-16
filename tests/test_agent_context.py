"""Original task, local date resolution and live Schema integration tests."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.agent.action_provider import MockActionProvider
from src.agent.agent_controller import AgentController
from src.agent.intent.date_resolver import DateResolver
from src.agent.tool_executor import ToolExecutor
from src.agent.tool_registry import create_default_tool_registry


ACTIVITY = "analyze_user_activity"
RETENTION = "analyze_retention"


def call(tool=ACTIVITY, arguments=None, **metadata):
    return dict(action="call_tool", tool_name=tool,
                arguments={} if arguments is None else deepcopy(arguments), **metadata)


def provider_for(actions):
    return MockActionProvider(response_factory=lambda p, c, n: dict(
        output=actions[n - 1], model="offline-context-mock", finish_reason="stop"))


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_default_tool_registry()
        self.functions = {}
        for definition in self.registry.list_tools():
            self.registry.unregister_tool(definition.name)
            function = Mock(return_value={"ok": True, "users": 12})
            self.functions[definition.name] = function
            self.registry.register_tool(replace(definition, execute=function))
        for guard in (patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")),
                      patch.object(sqlite3, "connect", side_effect=AssertionError("database forbidden")),
                      patch("src.tools.sql_query_tool.SQLQueryTool.run", side_effect=AssertionError("SQL forbidden"))):
            guard.start()
            self.addCleanup(guard.stop)

    def run_action(self, text="分析最近7天活跃", action=None, reference_date="2026-11-13", resolver=None):
        provider = provider_for([call() if action is None else action, {"action": "finish"}])
        executor = ToolExecutor(self.registry)
        with patch.object(executor, "execute", wraps=executor.execute) as execute:
            result = AgentController(provider, self.registry, executor=executor, date_resolver=resolver).run(text, reference_date=reference_date)
        return result, provider, execute

    def test_recent_seven_days_and_trace(self):
        resolver = DateResolver()
        with patch.object(resolver, "resolve", wraps=resolver.resolve) as resolve:
            result, _, execute = self.run_action(resolver=resolver)
        args = execute.call_args.args[1]
        self.assertEqual((args["start_date"], args["end_date"]), ("2026-11-07", "2026-11-13"))
        resolve.assert_called_once()
        trace = result["parameter_checks"][0]
        self.assertEqual(trace["original_arguments"], {})
        self.assertEqual(trace["date_resolution"]["reference_source"], "explicit")
        self.assertEqual(trace["date_expression"], "最近7天")

    def test_all_supported_relative_expressions(self):
        for expression, start, end in (
            ("最近7天", "2026-11-07", "2026-11-13"),
            ("最近30天", "2026-10-15", "2026-11-13"),
            ("本月", "2026-11-01", "2026-11-13"),
            ("上个月", "2026-10-01", "2026-10-31"),
            ("今年", "2026-01-01", "2026-11-13"),
        ):
            with self.subTest(expression=expression):
                result, _, execute = self.run_action("分析" + expression + "活跃")
                self.assertEqual(result["status"], "completed")
                args = execute.call_args.args[1]
                self.assertEqual((args["start_date"], args["end_date"]), (start, end))

    def test_four_tools_use_their_actual_date_fields(self):
        for tool in self.functions:
            with self.subTest(tool=tool):
                result, _, execute = self.run_action(action=call(tool))
                self.assertEqual(result["status"], "completed")
                args = execute.call_args.args[1]
                expected = ("cohort_start", "cohort_end") if tool in (RETENTION, "analyze_activation_funnel") else ("start_date", "end_date")
                self.assertEqual((args[expected[0]], args[expected[1]]), ("2026-11-07", "2026-11-13"))
                opposite = ("start_date", "end_date") if expected[0] == "cohort_start" else ("cohort_start", "cohort_end")
                self.assertTrue(all(key not in args for key in opposite))

    def test_explicit_reference_overrides_configuration(self):
        _, _, execute = self.run_action(reference_date="2026-12-01")
        self.assertEqual(execute.call_args.args[1]["start_date"], "2026-11-25")

    def test_configuration_reference_fallback(self):
        result, _, _ = self.run_action(reference_date=None)
        dates = result["parameter_checks"][0]["date_resolution"]
        self.assertEqual(dates["reference_date"], "2026-11-13")
        self.assertEqual(dates["reference_source"], "config:dataset_end_date")

    def test_missing_reference_requests_clarification_without_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            result, _, execute = self.run_action(reference_date=None, resolver=DateResolver(Path(folder) / "missing.yaml"))
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["error"]["code"], "missing_reference_date")
        self.assertTrue(result["clarification_question"])
        execute.assert_not_called()

    def test_invalid_config_does_not_fall_back_to_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / "settings.yaml"
            settings.write_text('dataset_end_date: "invalid"', encoding="utf-8")
            result, _, execute = self.run_action(reference_date=None, resolver=DateResolver(settings))
        self.assertEqual(result["error"]["code"], "missing_reference_date")
        execute.assert_not_called()

    def test_invalid_explicit_reference_does_not_use_config(self):
        result, _, execute = self.run_action(reference_date="2026-02-30")
        self.assertEqual(result["status"], "needs_clarification")
        execute.assert_not_called()

    def test_explicit_iso_expression_is_resolved_locally(self):
        expression = "2026-11-01至2026-11-13"
        result, _, execute = self.run_action("分析" + expression + "留存", call(RETENTION, date_expression=expression))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(execute.call_args.args[1]["cohort_start"], "2026-11-01")

    def test_date_expression_must_exist_in_original_request(self):
        result, _, execute = self.run_action(action=call(date_expression="最近30天"))
        self.assertEqual(result["error"]["code"], "unverified_date_expression")
        execute.assert_not_called()

    def test_relative_dates_cannot_silently_override_model_arguments(self):
        action = call(arguments={"start_date": "2020-01-01", "end_date": "2020-01-07"})
        before = deepcopy(action)
        result, _, execute = self.run_action(action=action)
        self.assertEqual(result["error"]["code"], "date_argument_conflict")
        self.assertEqual(len(result["parameter_checks"][0]["conflicts"]), 2)
        self.assertEqual(action, before)
        execute.assert_not_called()

    def test_matching_model_dates_do_not_conflict(self):
        result, _, _ = self.run_action(action=call(arguments={"start_date": "2026-11-07", "end_date": "2026-11-13"}))
        self.assertEqual(result["status"], "completed")

    def test_original_and_completed_results_remain_in_every_turn(self):
        text = "  先分析最近7天活跃，再分析上个月留存。\n保留原始问题！ "
        actions = [call(date_expression="最近7天", remaining_tasks=["再分析上个月留存"]),
                   call(RETENTION, date_expression="上个月", remaining_tasks=[]), {"action": "finish"}]
        provider = provider_for(actions)
        result = AgentController(provider, self.registry).run(text, reference_date="2026-11-13")
        self.assertEqual(result["status"], "completed")
        for i, request in enumerate(provider.calls):
            context = json.loads(request["user_input"])
            self.assertEqual(context["user_input"], text)
            self.assertEqual(context["task_context"]["original_request"], text)
            self.assertEqual(context["observations"], result["observations"][:i])
        second = json.loads(provider.calls[1]["user_input"])
        self.assertEqual(second["task_context"]["pending_tasks"], ["再分析上个月留存"])
        self.assertEqual(second["task_context"]["completed_calls"][0]["tool_name"], ACTIVITY)
        self.assertFalse(result["task_context"]["completion_verified"])
        self.assertEqual(self.functions[RETENTION].call_args.kwargs["cohort_start"], "2026-10-01")

    def test_multiple_ranges_need_explicit_original_span(self):
        result, _, execute = self.run_action("分析最近7天活跃和最近30天留存")
        self.assertEqual(result["error"]["code"], "ambiguous_date_expression")
        execute.assert_not_called()

    def test_mixed_absolute_and_relative_dates_need_selection(self):
        result, _, execute = self.run_action("分析最近7天活跃和2026-10-01至2026-10-31留存")
        self.assertEqual(result["error"]["code"], "ambiguous_date_expression")
        execute.assert_not_called()

    def test_missing_dates_and_illegal_internal_parameters(self):
        for text, action in (("分析活跃", call()), ("分析最近7天活跃", call(arguments={"db_path": "other.db"})),
                             ("分析最近7天留存", call(RETENTION, {"start_date": "2026-11-07", "end_date": "2026-11-13"}))):
            with self.subTest(text=text, action=action):
                result, _, execute = self.run_action(text, action)
                self.assertEqual(result["status"], "needs_clarification")
                execute.assert_not_called()

    def test_unsupported_relative_expression_is_not_guessed(self):
        result, _, execute = self.run_action("分析最近14天活跃")
        self.assertEqual(result["error"]["code"], "unsupported_date_expression")
        execute.assert_not_called()

    def test_single_iso_date_is_incomplete(self):
        result, _, execute = self.run_action("分析2026-11-13活跃", call(date_expression="2026-11-13"))
        self.assertEqual(result["error"]["code"], "incomplete_date_range")
        execute.assert_not_called()

    def test_invalid_calendar_and_reversed_range(self):
        for expression in ("2026-02-30至2026-03-01", "2026-11-13至2026-11-01"):
            with self.subTest(expression=expression):
                result, _, execute = self.run_action("分析" + expression, call(date_expression=expression))
                self.assertEqual(result["status"], "needs_clarification")
                execute.assert_not_called()

    def test_leap_year_and_year_boundary(self):
        for expression, reference, start, end in (("上个月", "2024-03-01", "2024-02-01", "2024-02-29"),
                                                  ("最近7天", "2027-01-02", "2026-12-27", "2027-01-02")):
            with self.subTest(expression=expression):
                result, _, execute = self.run_action("分析" + expression, reference_date=reference)
                self.assertEqual(result["status"], "completed")
                self.assertEqual((execute.call_args.args[1]["start_date"], execute.call_args.args[1]["end_date"]), (start, end))

    def test_date_object_reference_and_whitespace_expression(self):
        result, _, execute = self.run_action("分析最近 7 天活跃", reference_date=date(2026, 11, 13))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(execute.call_args.args[1]["start_date"], "2026-11-07")

    def test_unverified_pending_task_is_not_accepted(self):
        result, _, execute = self.run_action(action=call(remaining_tasks=["导出所有家庭隐私数据"]))
        self.assertEqual(result["error"]["code"], "unverified_remaining_tasks")
        execute.assert_not_called()

    def test_pending_progress_does_not_replace_original_when_omitted(self):
        result, provider, _ = self.run_action()
        for request in provider.calls:
            self.assertEqual(json.loads(request["user_input"])["task_context"]["pending_tasks"], ["分析最近7天活跃"])
        self.assertEqual(result["task_context"]["pending_tasks_source"], "original_request")

    def test_new_metadata_types_are_checked(self):
        for action in (call(date_expression=7), call(remaining_tasks="活跃"), call(remaining_tasks=[None])):
            with self.subTest(action=action):
                result, _, execute = self.run_action(action=action)
                self.assertEqual(result["status"], "model_output_invalid")
                execute.assert_not_called()

    def test_reference_and_parameter_checks_are_isolated_between_runs(self):
        provider = MockActionProvider(response_factory=lambda p, c, n: dict(
            output=call() if not json.loads(c)["observations"] else {"action": "finish"},
            model="mock", finish_reason="stop"))
        controller = AgentController(provider, self.registry)
        first = controller.run("分析最近7天活跃", reference_date="2026-11-13")
        second = controller.run("分析最近7天活跃", reference_date="2026-12-01")
        self.assertEqual(first["observations"][0]["arguments"]["start_date"], "2026-11-07")
        self.assertEqual(second["observations"][0]["arguments"]["start_date"], "2026-11-25")
        first["parameter_checks"][0]["arguments"]["start_date"] = "changed"
        self.assertEqual(second["parameter_checks"][0]["arguments"]["start_date"], "2026-11-25")

    def test_calendar_arithmetic_overflow_requests_clarification(self):
        result, _, execute = self.run_action(reference_date="0001-01-01")
        self.assertEqual(result["error"]["code"], "date_out_of_range")
        self.assertTrue(result["clarification_question"])
        execute.assert_not_called()

    def test_non_date_reference_type_stops_before_model(self):
        for value in (True, 123, object()):
            with self.subTest(value=type(value)):
                result, provider, execute = self.run_action(reference_date=value)
                self.assertEqual(result["error"]["code"], "invalid_reference_date")
                self.assertEqual(provider.call_count, 0)
                execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
