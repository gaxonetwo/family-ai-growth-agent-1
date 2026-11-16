"""Offline end-to-end acceptance tests for the real single-Agent components."""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.agent.action_provider import MockActionProvider
from src.agent.agent_controller import AgentController
from src.agent.report_semantics import multi_report_context, report_context
from src.agent.result_explainer import resolve_path
from src.agent.tool_registry import create_default_tool_registry


ACTIVITY = "analyze_user_activity"
RETENTION = "analyze_retention"
REFERENCE_DATE = "2026-11-13"
TASK = "先分析最近7天的用户活跃情况，再分析上个月注册用户的留存情况，最后综合分析这两个结果。"

ACTIVITY_DATA = {
    "ok": True,
    "dau": 108,
    "summary": {"average_dau": 105.5},
    "methodology": {"dau": "UTC daily distinct users"},
}
RETENTION_DATA = {
    "ok": True,
    "eligible_users": 500,
    "metrics": {"D7": {"retention_rate": 0.24, "retained_users": 120}},
    "methodology": {"retention": "exact-day retention"},
}


def activity_action(arguments=None):
    return {
        "action": "call_tool",
        "tool_name": ACTIVITY,
        "arguments": deepcopy(arguments or {
            "start_date": "最近7天",
            "end_date": "最近7天",
        }),
        "date_expression": "最近7天",
    }


def retention_action():
    return {
        "action": "call_tool",
        "tool_name": RETENTION,
        "arguments": {
            "cohort_start": "上个月",
            "cohort_end": "上个月",
        },
        "date_expression": "上个月",
    }


def observed_report(tool_name, data):
    path = "/dau" if tool_name == ACTIVITY else "/metrics/D7/retention_rate"
    context = report_context(tool_name, data)
    label = context["metric_catalog"][path]
    value = resolve_path(data, path)
    description = f"{label}：{json.dumps(value, ensure_ascii=False)}。"
    evidence = [{"json_path": path, "value": value}]
    return {
        "status": "success",
        "summary": description,
        "summary_finding_indices": [0],
        "summary_evidence": deepcopy(evidence),
        "key_findings": [{
            "title": label,
            "description": description,
            "claim": {"kind": "observed", "path": path},
            "evidence": evidence,
        }],
        "limitations": context["required_limitations"],
        "suggestions": [],
    }


def combined_report(tool_results, context):
    aggregate = {"tool_results": tool_results}
    paths = [
        "/tool_results/0/data/dau",
        "/tool_results/1/data/metrics/D7/retention_rate",
    ]
    labels = [context["metric_catalog"][path] for path in paths]
    values = [resolve_path(aggregate, path) for path in paths]
    description = (
        f"跨工具并列观察：{labels[0]}为"
        f"{json.dumps(values[0], ensure_ascii=False)}；"
        f"{labels[1]}为{json.dumps(values[1], ensure_ascii=False)}。"
        "这些指标口径不同，仅表示本次结果同时出现，不代表相关性或因果关系。"
    )
    evidence = [
        {"json_path": path, "value": value}
        for path, value in zip(paths, values)
    ]
    return {
        "status": "success",
        "summary": description,
        "summary_finding_indices": [0],
        "summary_evidence": deepcopy(evidence),
        "key_findings": [{
            "title": "跨工具指标并列观察（不代表相关或因果）",
            "description": description,
            "claim": {"kind": "cross_tool_observation", "paths": paths},
            "evidence": evidence,
        }],
        "limitations": context["required_limitations"],
        "suggestions": [],
    }


class AcceptanceProvider(MockActionProvider):
    """Separate deterministic action and report channels; never uses a network."""

    def __init__(self, actions, *, invalid_activity_report=False):
        self.report_calls = []
        self.invalid_activity_report = invalid_activity_report
        super().__init__(response_factory=lambda prompt, context, number: {
            "output": deepcopy(actions[number - 1]),
            "model": "offline-acceptance-action",
            "finish_reason": "stop",
        })

    def generate_report(self, system_prompt, user_input):
        payload = json.loads(user_input)
        self.report_calls.append({
            "system_prompt": system_prompt,
            "payload": deepcopy(payload),
        })
        if "tool_results" in payload:
            output = combined_report(payload["tool_results"], payload["report_context"])
        else:
            output = observed_report(payload["tool_name"], payload["data"])
            if self.invalid_activity_report and payload["tool_name"] == ACTIVITY:
                del output["key_findings"][0]["claim"]
        return {
            "output": output,
            "model": "offline-acceptance-report",
            "finish_reason": "stop",
        }


class AgentEndToEndAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_default_tool_registry()
        self.functions = {}
        data = {ACTIVITY: ACTIVITY_DATA, RETENTION: RETENTION_DATA}
        for definition in self.registry.list_tools():
            self.registry.unregister_tool(definition.name)
            function = Mock(return_value=deepcopy(
                data.get(definition.name, {"ok": True, "count": 1})))
            self.functions[definition.name] = function
            self.registry.register_tool(replace(definition, execute=function))
        for guard in (
            patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")),
            patch.object(sqlite3, "connect", side_effect=AssertionError("database forbidden")),
            patch("src.tools.sql_query_tool.SQLQueryTool.run", side_effect=AssertionError("SQL forbidden")),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def run_agent(self, actions, text=TASK, provider_options=None, **kwargs):
        provider = AcceptanceProvider(actions, **(provider_options or {}))
        result = AgentController(provider, self.registry, **kwargs).run(
            text, reference_date=REFERENCE_DATE)
        return result, provider

    def test_single_tool_task_executes_once_with_standardized_dates(self):
        result, provider = self.run_agent(
            [activity_action(), {"action": "finish", "remaining_tasks": []}],
            text="分析最近7天的用户活跃情况。",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual([item["tool_name"] for item in result["observations"]],
                         [ACTIVITY])
        self.functions[ACTIVITY].assert_called_once_with(
            start_date="2026-11-07", end_date="2026-11-13",
            data_start_date=None, data_through_date=None, as_of_date=None,
            breakdown_by=None,
        )
        self.assertEqual(result["observations"][0]["result"]["data"]["dau"], 108)
        self.assertTrue(result["task_context"]["completion_verified"])
        self.assertEqual(provider.call_count, 2)

    def test_two_tools_execute_in_order_and_second_turn_contains_first_observation(self):
        first = activity_action()
        first["remaining_tasks"] = [
            {
                "summary": "分析上个月注册用户的留存情况",
                "evidence": "再分析上个月注册用户的留存情况",
            },
            {
                "summary": "综合分析活跃和留存两个结果",
                "evidence": "最后综合分析这两个结果",
            },
        ]
        second = retention_action()
        second["remaining_tasks"] = [{
            "summary": "综合分析活跃和留存两个结果",
            "evidence": "最后综合分析这两个结果",
        }]
        result, provider = self.run_agent(
            [first, second,
             {"action": "finish", "remaining_tasks": []}])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual([item["tool_name"] for item in result["observations"]],
                         [ACTIVITY, RETENTION])
        first_arguments = result["observations"][0]["arguments"]
        second_arguments = result["observations"][1]["arguments"]
        self.assertEqual((first_arguments["start_date"], first_arguments["end_date"]),
                         ("2026-11-07", "2026-11-13"))
        self.assertEqual((second_arguments["cohort_start"], second_arguments["cohort_end"]),
                         ("2026-10-01", "2026-10-31"))
        self.assertEqual(
            result["parameter_checks"][0]["normalized_date_placeholders"],
            ["start_date", "end_date"],
        )
        self.assertEqual(
            result["parameter_checks"][1]["normalized_date_placeholders"],
            ["cohort_start", "cohort_end"],
        )
        second_context = json.loads(provider.calls[1]["user_input"])
        self.assertEqual(len(second_context["observations"]), 1)
        self.assertEqual(second_context["observations"][0]["tool_name"], ACTIVITY)
        self.assertEqual(second_context["observations"][0]["result"]["data"]["dau"], 108)
        self.assertEqual(second_context["task_context"]["pending_tasks"], [
            "再分析上个月注册用户的留存情况",
            "最后综合分析这两个结果",
        ])
        self.assertEqual(second_context["task_context"]["pending_task_summaries"][0], {
            "summary": "分析上个月注册用户的留存情况",
            "evidence": "再分析上个月注册用户的留存情况",
        })
        third_context = json.loads(provider.calls[2]["user_input"])
        self.assertEqual(third_context["task_context"]["pending_tasks"],
                         ["最后综合分析这两个结果"])
        self.functions[ACTIVITY].assert_called_once()
        self.functions[RETENTION].assert_called_once()
        self.assertEqual(result["task_context"]["pending_tasks"], [])
        self.assertTrue(result["task_context"]["completion_verified"])

    def test_model_derived_iso_dates_are_replaced_by_local_relative_dates(self):
        model_dates = activity_action({
            "start_date": "2026-11-06",
            "end_date": "2026-11-13",
        })
        result, provider = self.run_agent(
            [model_dates, {"action": "finish", "remaining_tasks": []}],
            text="分析最近7天的用户活跃情况。",
        )
        self.assertEqual(result["status"], "completed")
        self.functions[ACTIVITY].assert_called_once()
        arguments = result["observations"][0]["arguments"]
        self.assertEqual((arguments["start_date"], arguments["end_date"]),
                         ("2026-11-07", "2026-11-13"))
        self.assertEqual(provider.call_count, 2)

    def test_user_explicit_iso_conflict_stops_before_execution(self):
        conflict = activity_action({
            "start_date": "2026-10-01",
            "end_date": "2026-10-07",
        })
        text = "分析最近7天活跃，用户明确日期为2026-10-01至2026-10-07。"
        result, provider = self.run_agent([conflict], text=text)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["error"]["code"], "date_argument_conflict")
        self.assertEqual(result["tool_calls"], 0)
        self.functions[ACTIVITY].assert_not_called()
        self.assertEqual(provider.call_count, 1)

    def test_rewritten_pending_task_without_original_evidence_is_rejected(self):
        action = activity_action()
        action["remaining_tasks"] = ["综合分析活跃和留存两个结果"]
        result, provider = self.run_agent([action])
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["error"]["code"], "unverified_remaining_tasks")
        self.assertEqual(result["tool_calls"], 0)
        self.functions[ACTIVITY].assert_not_called()
        self.assertEqual(provider.call_count, 1)

    def test_tool_failure_stops_and_preserves_previous_success(self):
        self.functions[RETENTION].side_effect = RuntimeError("offline retention failure")
        result, provider = self.run_agent([activity_action(), retention_action()])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["tool_calls"], 2)
        self.assertTrue(result["observations"][0]["result"]["ok"])
        self.assertEqual(result["observations"][0]["result"]["data"]["dau"], 108)
        self.assertFalse(result["observations"][1]["result"]["ok"])
        self.assertEqual(result["observations"][1]["result"]["error"]["code"],
                         "tool_execution_error")
        self.functions[ACTIVITY].assert_called_once()
        self.functions[RETENTION].assert_called_once()
        self.assertEqual(provider.call_count, 2)

    def test_final_combined_report_uses_real_fake_tool_results_and_passes_validation(self):
        result, provider = self.run_agent(
            [activity_action(), retention_action(),
             {"action": "finish", "remaining_tasks": []}],
            explain_results=True,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["tool_calls"], 2)
        self.assertEqual(len(result["explanations"]), 2)
        self.assertTrue(all(item["result"]["status"] == "success"
                            for item in result["explanations"]))
        combined = result["combined_explanation"]
        self.assertEqual(combined["status"], "success")
        self.assertEqual(combined["model"], "offline-acceptance-report")
        self.assertEqual(combined["report"]["summary_evidence"], [
            {"json_path": "/tool_results/0/data/dau", "value": 108},
            {"json_path": "/tool_results/1/data/metrics/D7/retention_rate",
             "value": 0.24},
        ])
        self.assertIn("不代表相关性或因果关系", combined["report"]["summary"])
        self.assertEqual(len(provider.report_calls), 3)
        self.assertEqual([item["tool_name"] for item in provider.report_calls[2]["payload"]["tool_results"]],
                         [ACTIVITY, RETENTION])
        self.functions[ACTIVITY].assert_called_once()
        self.functions[RETENTION].assert_called_once()

    def test_invalid_single_report_does_not_contaminate_valid_combined_report(self):
        result, provider = self.run_agent(
            [activity_action(), retention_action(),
             {"action": "finish", "remaining_tasks": []}],
            provider_options={"invalid_activity_report": True},
            explain_results=True,
        )
        self.assertEqual(result["explanations"][0]["result"]["error"]["code"],
                         "unsupported_semantic_claim")
        self.assertEqual(result["explanations"][1]["result"]["status"], "success")
        self.assertEqual(result["combined_explanation"]["status"], "success")
        self.assertEqual(result["combined_explanation"]["report"]["summary_evidence"][0],
                         {"json_path": "/tool_results/0/data/dau", "value": 108})
        self.assertEqual(len(provider.report_calls), 3)

    def test_finish_cannot_verify_model_declared_pending_task(self):
        first = activity_action()
        first["remaining_tasks"] = [{
            "summary": "分析上个月注册用户的留存情况",
            "evidence": "再分析上个月注册用户的留存情况",
        }]
        result, provider = self.run_agent(
            [first, {"action": "finish"}], explain_results=True)
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["error"]["code"], "unfinished_tasks")
        self.assertFalse(result["task_context"]["completion_verified"])
        self.assertEqual(result["task_context"]["pending_tasks"],
                         ["再分析上个月注册用户的留存情况"])
        self.assertEqual(result["task_context"]["pending_task_summaries"], [{
            "summary": "分析上个月注册用户的留存情况",
            "evidence": "再分析上个月注册用户的留存情况",
        }])
        self.assertIsNone(result["combined_explanation"])
        self.functions[ACTIVITY].assert_called_once()
        self.functions[RETENTION].assert_not_called()
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(len(provider.report_calls), 1)


if __name__ == "__main__":
    unittest.main()
