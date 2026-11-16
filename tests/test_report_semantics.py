"""Offline adversarial examples: true citations do not prove prose conclusions."""
from copy import deepcopy
import json
import socket
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.agent.result_explainer import ResultExplainer
from src.agent.result_formatter import format_success
from src.agent.report_semantics import report_context, SUGGESTIONS


def observed_report(data, tool="analyze_user_activity", path="/dau"):
    from src.agent.result_explainer import resolve_path
    context = report_context(tool, data)
    label = context["metric_catalog"][path]
    value = resolve_path(data, path)
    evidence = [dict(json_path=path, value=value)]
    description = f"{label}：{json.dumps(value, ensure_ascii=False)}。"
    return dict(status="success", summary=description, summary_finding_indices=[0],
                summary_evidence=deepcopy(evidence),
                key_findings=[dict(title=label, description=description,
                                   claim=dict(kind="observed", path=path), evidence=evidence)],
                limitations=context["required_limitations"], suggestions=[SUGGESTIONS[0]])


class ReportSemanticTests(unittest.TestCase):
    def setUp(self):
        self.data = dict(dau=108, wau=300, mau=800, start_date="2026-11-07", end_date="2026-11-13",
                         summary=dict(average_dau=105, peak_dau=dict(date="2026-11-11", users=110)),
                         time_series=[dict(date="2026-11-13", event_records=900, dau=108)],
                         trend_analysis=dict(first_dau=101, last_dau=108, change_rate=0.0693,
                                             limitation="Endpoint comparison only"),
                         methodology=dict(dau="UTC daily distinct users", wau="rolling 7 days", mau="rolling 30 days"))
        self.tool = "analyze_user_activity"
        self.report = observed_report(self.data)

    def run_report(self):
        provider = Mock()
        provider.generate_report.return_value = dict(output=deepcopy(self.report), model="fake", finish_reason="stop")
        raw = format_success(self.tool, self.data)
        before = deepcopy(raw)
        with patch.object(socket, "create_connection", side_effect=AssertionError), patch.object(sqlite3, "connect", side_effect=AssertionError), patch("src.agent.tool_executor.ToolExecutor.execute", side_effect=AssertionError):
            result = ResultExplainer(provider).explain("分析业务", self.tool, raw)
        self.assertEqual(raw, before)
        self.assertTrue(raw["ok"])
        provider.generate_report.assert_called_once()
        return result

    def endpoint(self):
        evidence = [dict(json_path="/trend_analysis/" + k, value=self.data["trend_analysis"][k])
                    for k in ("first_dau", "last_dau", "change_rate")]
        evidence += [dict(json_path="/" + k, value=self.data[k]) for k in ("start_date", "end_date")]
        description = "周期首尾比较（2026-11-07至2026-11-13）：最后一天DAU相对第一天变化率为0.0693（小数比例，不代表持续趋势或因果）。"
        self.report.update(summary=description, summary_evidence=deepcopy(evidence), key_findings=[dict(
            title="DAU首尾变化", description=description, evidence=evidence,
            claim=dict(kind="endpoint_change", formula="(last-first)/first", value=0.0693))])

    def test_valid_endpoint_comparison(self):
        self.endpoint()
        self.assertEqual(self.run_report()["status"], "success")

    def test_endpoint_not_sustained_growth(self):
        self.endpoint()
        self.report["key_findings"][0]["description"] = "DAU持续增长，说明运营效果明显。"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_causation_with_true_evidence(self):
        self.report["key_findings"][0]["description"] = "DAU为108，证明投放导致用户增长。"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_dau_is_not_retention(self):
        self.report["key_findings"][0]["title"] = "用户留存率"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_wau_not_mau(self):
        self.report = observed_report(self.data, path="/wau")
        self.report["key_findings"][0]["description"] = "MAU（含当日的滚动30日独立活跃用户数）：300。"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_event_records_not_users(self):
        self.report = observed_report(self.data, path="/time_series/0/event_records")
        self.report["key_findings"][0]["description"] = "活跃用户数：900。"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_average_not_peak(self):
        self.report = observed_report(self.data, path="/summary/average_dau")
        self.report["key_findings"][0]["title"] = "DAU峰值"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_summary_requires_evidence(self):
        del self.report["summary_evidence"]
        self.assertEqual(self.run_report()["status"], "failed")

    def test_summary_true_evidence_cannot_support_new_conclusion(self):
        self.report["summary"] = "留存率大幅提高，运营效果显著。"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_suggestion_cannot_assert_hypothesis(self):
        self.report["suggestions"] = ["建议继续推广，因为推广已经提高留存率。"]
        self.assertEqual(self.run_report()["status"], "failed")

    def test_legitimate_observation_and_direction(self):
        self.assertEqual(self.run_report()["status"], "success")

    def test_raw_business_success_survives_semantic_failure(self):
        self.report["summary"] = "invented"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_wrong_formula(self):
        self.endpoint()
        self.report["key_findings"][0]["claim"]["formula"] = "last/first"
        self.assertEqual(self.run_report()["error"]["code"], "calculation_mismatch")

    def test_inconsistent_result_with_real_citations(self):
        self.data["trend_analysis"]["change_rate"] = 0.9
        self.endpoint()
        self.report["key_findings"][0]["claim"]["value"] = 0.9
        self.assertEqual(self.run_report()["error"]["code"], "calculation_mismatch")

    def test_zero_denominator(self):
        self.data["trend_analysis"]["first_dau"] = 0
        self.endpoint()
        self.assertEqual(self.run_report()["status"], "failed")

    def test_cannot_drop_methodology(self):
        self.report["limitations"] = []
        self.assertEqual(self.run_report()["status"], "failed")

    def test_null_window_not_zero(self):
        self.data["mau"] = None
        self.report = observed_report(self.data, path="/mau")
        self.assertEqual(self.run_report()["status"], "success")
        self.report["key_findings"][0]["description"] = "MAU：0。"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_all_four_tool_definitions(self):
        for tool, data, path in (
            ("analyze_activation_funnel", {"activation": {"activation_rate": 0.5}}, "/activation/activation_rate"),
            ("analyze_retention", {"metrics": {"D7": {"retention_rate": 0.2}}}, "/metrics/D7/retention_rate"),
            ("analyze_feedback_trends", {"sentiment_distribution": [{"name": "unknown", "count": 2, "rate": 0.1}]}, "/sentiment_distribution/0/rate"),
        ):
            with self.subTest(tool=tool):
                self.tool, self.data = tool, data
                self.report = observed_report(data, tool, path)
                self.assertEqual(self.run_report()["status"], "success")

    def test_unsupported_claim_is_not_assumed_verified(self):
        self.report["key_findings"][0]["claim"] = dict(kind="causal_effect")
        self.assertEqual(self.run_report()["error"]["code"], "unsupported_semantic_claim")

    def test_single_day_cannot_claim_change(self):
        self.data["start_date"] = self.data["end_date"]
        self.endpoint()
        self.assertEqual(self.run_report()["status"], "failed")

    def test_summary_value_tampering(self):
        self.report["summary_evidence"][0]["value"] = 999
        self.assertEqual(self.run_report()["error"]["code"], "summary_evidence_mismatch")

    def test_cannot_add_unvalidated_prose_field(self):
        self.report["business_conclusion"] = "投放带来了增长"
        self.assertEqual(self.run_report()["status"], "failed")

    def test_missing_claim_requires_conservative_failure(self):
        del self.report["key_findings"][0]["claim"]
        self.assertEqual(self.run_report()["error"]["code"], "unsupported_semantic_claim")

    def test_peak_and_average_correct_reports(self):
        for path in ("/summary/average_dau", "/summary/peak_dau/users", "/wau", "/mau", "/time_series/0/event_records"):
            with self.subTest(path=path):
                self.report = observed_report(self.data, path=path)
                self.assertEqual(self.run_report()["status"], "success")

    def test_negative_change_is_not_bounded_to_zero_one(self):
        self.data["trend_analysis"].update(first_dau=100, last_dau=50, change_rate=-0.5)
        self.endpoint()
        finding = self.report["key_findings"][0]
        finding["claim"]["value"] = -0.5
        finding["description"] = finding["description"].replace("0.0693", "-0.5")
        self.report["summary"] = finding["description"]
        self.assertEqual(self.run_report()["status"], "success")


if __name__ == "__main__":
    unittest.main()
