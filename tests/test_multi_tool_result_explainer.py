"""Offline multi-tool report synthesis and Agent integration tests."""
from copy import deepcopy
from dataclasses import replace
import json
import socket
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.agent.action_provider import MockActionProvider
from src.agent.agent_controller import AgentController
from src.agent.report_semantics import multi_report_context, SUGGESTIONS
from src.agent.result_explainer import ResultExplainer, resolve_path
from src.agent.result_formatter import format_failure, format_success
from src.agent.tool_registry import create_default_tool_registry


ACTIVITY = "analyze_user_activity"
RETENTION = "analyze_retention"
ACTIVITY_DATA = {
    "dau": 108,
    "methodology": {"dau": "UTC daily distinct users"},
}
RETENTION_DATA = {
    "eligible_users": 500,
    "metrics": {"D7": {"retention_rate": 0.24, "retained_users": 120}},
    "methodology": {"retention": "exact-day retention"},
}


def observation(call_index, tool_name, data, arguments=None):
    return {
        "call_index": call_index,
        "tool_name": tool_name,
        "arguments": deepcopy(arguments or {}),
        "result": format_success(tool_name, deepcopy(data)),
    }


def canonical_results(observations):
    return [
        {
            "call_index": item["call_index"],
            "tool_name": item["tool_name"],
            "arguments": deepcopy(item["arguments"]),
            "data": deepcopy(item["result"]["data"]),
        }
        for item in observations
    ]


def valid_multi_report(observations):
    tool_results = canonical_results(observations)
    data = {"tool_results": tool_results}
    context = multi_report_context(tool_results)
    paths = [
        "/tool_results/0/data/dau",
        "/tool_results/1/data/metrics/D7/retention_rate",
    ]
    labels = [context["metric_catalog"][path] for path in paths]
    values = [resolve_path(data, path) for path in paths]
    description = (
        f"跨工具并列观察：{labels[0]}为{values[0]}；{labels[1]}为{values[1]}。"
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
        "suggestions": [{
            "text": SUGGESTIONS[2],
            "based_on_finding_indices": [0],
        }],
    }


class MultiToolResultExplainerTests(unittest.TestCase):
    def setUp(self):
        self.observations = [
            observation(1, ACTIVITY, ACTIVITY_DATA),
            observation(2, RETENTION, RETENTION_DATA),
        ]
        self.provider = Mock()
        self.provider.generate_report.return_value = {
            "output": valid_multi_report(self.observations),
            "model": "offline-report-mock",
            "finish_reason": "stop",
        }
        self.explainer = ResultExplainer(self.provider)
        for guard in (
            patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")),
            patch.object(sqlite3, "connect", side_effect=AssertionError("database forbidden")),
            patch("src.agent.tool_executor.ToolExecutor.execute", side_effect=AssertionError("executor forbidden")),
        ):
            guard.start()
            self.addCleanup(guard.stop)

    def explain(self):
        return self.explainer.explain_multiple("综合分析活跃和留存", self.observations)

    def test_two_tools_generate_one_traceable_report(self):
        before = deepcopy(self.observations)
        result = self.explain()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["report"]["summary_finding_indices"], [0])
        self.assertEqual(len(result["report"]["key_findings"][0]["evidence"]), 2)
        self.assertEqual(self.observations, before)
        prompt, payload = self.provider.generate_report.call_args.args
        decoded = json.loads(payload)
        self.assertIn("不代表相关性或因果关系", prompt)
        self.assertEqual([item["tool_name"] for item in decoded["tool_results"]],
                         [ACTIVITY, RETENTION])

    def test_wrong_evidence_path_or_value_is_rejected(self):
        for change, expected in (
            (("json_path", "/tool_results/0/data/missing"),
             "multi_evidence_path_not_found"),
            (("value", 999), "multi_evidence_mismatch"),
        ):
            with self.subTest(change=change):
                report = valid_multi_report(self.observations)
                report["key_findings"][0]["evidence"][0][change[0]] = change[1]
                self.provider.generate_report.return_value["output"] = report
                result = self.explain()
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error"]["code"], expected)

    def test_causal_interpretation_with_real_values_is_rejected(self):
        report = valid_multi_report(self.observations)
        report["key_findings"][0]["description"] = "DAU为108导致D7留存率达到0.24。"
        self.provider.generate_report.return_value["output"] = report
        result = self.explain()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "unverified_semantic_statement")

    def test_suggestion_requires_a_verified_finding(self):
        report = valid_multi_report(self.observations)
        report["suggestions"][0]["based_on_finding_indices"] = []
        self.provider.generate_report.return_value["output"] = report
        result = self.explain()
        self.assertEqual(result["error"]["code"], "suggestion_missing_evidence")

    def test_failed_tool_blocks_report_generation(self):
        observations = deepcopy(self.observations)
        observations[1]["result"] = format_failure(RETENTION, "fake", "failed")
        result = self.explainer.explain_multiple("综合分析", observations)
        self.assertEqual(result["error"]["code"], "tool_execution_not_successful")
        self.provider.generate_report.assert_not_called()

    def test_insufficient_results_distinct_tools_and_metrics(self):
        cases = (
            ([self.observations[0]], "insufficient_successful_results"),
            ([self.observations[0], observation(2, ACTIVITY, ACTIVITY_DATA)],
             "insufficient_distinct_tools"),
            ([observation(1, ACTIVITY, {"methodology": {}}),
              observation(2, RETENTION, {"methodology": {}})],
             "insufficient_metric_data"),
        )
        for observations, code in cases:
            with self.subTest(code=code):
                self.provider.reset_mock()
                result = self.explainer.explain_multiple("综合分析", observations)
                self.assertEqual(result["error"]["code"], code)
                self.provider.generate_report.assert_not_called()


class AgentCombinedReportIntegrationTests(unittest.TestCase):
    def test_completed_agent_exposes_combined_report_without_changing_raw_results(self):
        registry = create_default_tool_registry()
        functions = {}
        business_data = {ACTIVITY: ACTIVITY_DATA, RETENTION: RETENTION_DATA}
        for definition in registry.list_tools():
            registry.unregister_tool(definition.name)
            function = Mock(return_value=deepcopy(business_data.get(
                definition.name, {"ok": True, "count": 1})))
            functions[definition.name] = function
            registry.register_tool(replace(definition, execute=function))
        actions = [
            {"action": "call_tool", "tool_name": ACTIVITY,
             "arguments": {"start_date": "2026-11-07", "end_date": "2026-11-13"}},
            {"action": "call_tool", "tool_name": RETENTION,
             "arguments": {"cohort_start": "2026-08-16", "cohort_end": "2026-10-14"}},
            {"action": "finish", "remaining_tasks": []},
        ]
        provider = MockActionProvider(response_factory=lambda prompt, context, number: {
            "output": actions[number - 1], "model": "offline-action-mock",
            "finish_reason": "stop",
        })
        fake_explainer = Mock()
        fake_explainer.explain.return_value = {"status": "success", "report": {"single": True}}
        fake_explainer.explain_multiple.return_value = {
            "status": "success", "report": {"combined": True}, "error": None,
        }
        with patch("src.agent.agent_controller.ResultExplainer", return_value=fake_explainer), \
                patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")), \
                patch.object(sqlite3, "connect", side_effect=AssertionError("database forbidden")):
            result = AgentController(provider, registry, explain_results=True).run(
                "综合分析活跃和留存")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["task_context"]["completion_verified"])
        self.assertEqual(result["combined_explanation"]["report"], {"combined": True})
        fake_explainer.explain_multiple.assert_called_once_with(
            "综合分析活跃和留存", result["observations"])
        self.assertEqual(len(result["observations"]), 2)
        functions[ACTIVITY].assert_called_once()
        functions[RETENTION].assert_called_once()


if __name__ == "__main__":
    unittest.main()
