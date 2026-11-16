"""Offline report validation and explicit CLI explanation gates."""
from copy import deepcopy
import json
import socket
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.agent.result_explainer import ResultExplainer, resolve_path
from src.agent.report_semantics import report_context
from src.agent.result_formatter import format_success, format_failure
from src.agent.intent.deepseek_provider import DeepSeekProvider
from src.agent.intent.provider import IntentProviderError
from src.tools import demo_intent as cli


def report():
    # Same successful metrics, now with the batch-2 traceable report contract.
    data = {"dau": 10, "wau": 30, "mau": 80}
    context = report_context("analyze_user_activity", data)
    findings = []
    for key, value in data.items():
        path = "/" + key
        label = context["metric_catalog"][path]
        findings.append(dict(title=label, description=f"{label}：{value}。",
                             claim=dict(kind="observed", path=path),
                             evidence=[dict(json_path=path, value=value)]))
    return dict(status="success", summary=findings[0]["description"], key_findings=findings,
                summary_finding_indices=[0], summary_evidence=deepcopy(findings[0]["evidence"]),
                limitations=context["required_limitations"], suggestions=[])


class ExplainerTests(unittest.TestCase):
    def setUp(self):
        self.raw = format_success("analyze_user_activity", {"dau": 10, "wau": 30, "mau": 80})
        self.provider = Mock()
        self.provider.generate_report.return_value = dict(output=report(), model="fake", finish_reason="stop")
        self.explainer = ResultExplainer(self.provider)

    def explain(self):
        return self.explainer.explain("分析活跃", "analyze_user_activity", self.raw)

    def test_success_metrics_and_real_payload(self):
        self.assertEqual(self.explain()["status"], "success")
        prompt, payload = self.provider.generate_report.call_args.args
        self.assertEqual(json.loads(payload)["data"], self.raw["data"])
        self.assertIn("相关性不等于因果", prompt)

    def test_missing_path(self):
        self.provider.generate_report.return_value["output"]["key_findings"][0]["evidence"][0]["json_path"] = "/missing"
        self.assertEqual(self.explain()["status"], "failed")

    def test_wrong_value(self):
        self.provider.generate_report.return_value["output"]["key_findings"][0]["evidence"][0]["value"] = 999
        self.assertEqual(self.explain()["status"], "failed")

    def test_invented_metric(self):
        self.provider.generate_report.return_value["output"]["key_findings"][0]["evidence"].append(dict(json_path="/metrics/revenue", value=100))
        self.assertEqual(self.explain()["status"], "failed")

    def test_invalid_structure(self):
        for output in (None, "invalid json", {}, {**report(), "key_findings": []}):
            self.provider.generate_report.return_value["output"] = output
            self.assertEqual(self.explain()["status"], "failed")

    def test_truncated(self):
        self.provider.generate_report.return_value["finish_reason"] = "length"
        self.assertEqual(self.explain()["error"]["code"], "response_truncated")

    def test_failed_tool_not_sent(self):
        self.raw = format_failure("analyze_user_activity", "fake", "failure")
        self.assertEqual(self.explain()["status"], "failed")
        self.provider.generate_report.assert_not_called()

    def test_original_unchanged(self):
        before = deepcopy(self.raw)
        self.explain()
        self.assertEqual(before, self.raw)

    def test_input_limit_no_truncation(self):
        self.assertEqual(ResultExplainer(self.provider, max_input_chars=5).explain("分析", "analyze_user_activity", self.raw)["error"]["code"], "input_too_large")
        self.provider.generate_report.assert_not_called()

    def test_safe_provider_errors(self):
        for code in ("request_timeout", "invalid_json", "empty_response"):
            self.provider.generate_report.side_effect = IntentProviderError(code, "secret", retryable=False)
            result = self.explain()
            self.assertEqual(result["error"]["code"], code)
            self.assertNotIn("secret", str(result))

    def test_paths_arrays_and_escaped_keys(self):
        self.assertEqual(resolve_path({"a/b": [3]}, "/a~1b/0"), 3)
        self.assertEqual(resolve_path({"a": [{"b": 4}]}, "a[0].b"), 4)

    def test_no_external_calls(self):
        with patch.object(socket, "create_connection", side_effect=AssertionError), patch.object(sqlite3, "connect", side_effect=AssertionError), patch("src.agent.tool_executor.ToolExecutor.execute", side_effect=AssertionError):
            self.assertEqual(self.explain()["status"], "success")

    def test_fake_sdk_report_interface(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(model="deepseek-flash", choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(report())))])
        provider = DeepSeekProvider(environ={"DEEPSEEK_API_KEY": "fake", "DEEPSEEK_MODEL": "deepseek-flash"}, client_factory=lambda **kw: client)
        self.assertEqual(ResultExplainer(provider).explain("分析", "analyze_user_activity", self.raw)["status"], "success")

    def test_fake_sdk_invalid_json(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="not json"))])
        provider = DeepSeekProvider(environ={"DEEPSEEK_API_KEY": "fake", "DEEPSEEK_MODEL": "deepseek-flash"}, client_factory=lambda **kw: client)
        result = ResultExplainer(provider).explain("分析", "analyze_user_activity", self.raw)
        self.assertEqual(result["error"]["code"], "invalid_json")

    def test_unexpected_provider_fault_is_safe(self):
        self.provider.generate_report.side_effect = RuntimeError("secret")
        self.assertEqual(self.explain()["error"]["code"], "report_provider_error")

    def cli_run(self, explain=False, success=True):
        execution = dict(ok=success, execution_status="success" if success else "failed", tool_result=self.raw, error=None)
        pipeline = Mock()
        current = SimpleNamespace(user_input="原始问题")
        with patch.object(cli, "DeepSeekProvider"), patch.object(cli, "IntentPipeline", return_value=pipeline), patch.object(cli, "run_session", return_value=current), patch.object(cli, "ToolExecutor"), patch.object(cli, "SingleToolExecution") as bridge, patch.object(cli, "ResultExplainer") as explainer, patch("builtins.print"):
            bridge.return_value.execute.return_value = execution
            explainer.return_value.explain.return_value = dict(status="failed", error={"code": "fake"})
            code = cli.main(["--live", "--execute"] + (["--explain"] if explain else []))
        return code, explainer, execution

    def test_cli_default_no_explanation(self):
        _, explainer, _ = self.cli_run()
        explainer.assert_not_called()

    def test_cli_explicit_explanation_failure_preserves_success(self):
        code, explainer, execution = self.cli_run(True)
        self.assertEqual(code, 0)
        self.assertTrue(execution["ok"])
        explainer.return_value.explain.assert_called_once_with("原始问题", "analyze_user_activity", self.raw)

    def test_cli_failed_execution_blocks_explanation(self):
        code, explainer, _ = self.cli_run(True, False)
        self.assertEqual(code, 1)
        explainer.assert_not_called()

    def test_cli_explain_without_execute_is_rejected(self):
        with patch.object(cli, "DeepSeekProvider") as provider, patch.object(cli, "run_session") as session, patch("sys.stderr"):
            with self.assertRaises(SystemExit) as error:
                cli.main(["--live", "--explain"])
        self.assertEqual(error.exception.code, 2)
        provider.assert_not_called()
        session.assert_not_called()

    def test_cli_execute_and_explain_success_with_offline_dependencies(self):
        from dataclasses import replace
        from src.agent.intent.intent_pipeline import IntentPipeline
        from src.agent.tool_registry import create_default_tool_registry

        registry = create_default_tool_registry()
        definition = registry.unregister_tool("analyze_user_activity")
        business_data = deepcopy(self.raw["data"])
        function = Mock(return_value=business_data)
        registry.register_tool(replace(definition, execute=function))
        provider = Mock(spec=DeepSeekProvider)
        provider.generate_intent.side_effect = cli.create_demo_provider().generate_intent
        provider.generate_report.return_value = dict(output=report(), model="fake", finish_reason="stop")
        def pipeline_factory(selected_provider, **kwargs):
            return IntentPipeline(selected_provider, registry, **kwargs)
        with patch.object(cli, "DeepSeekProvider", return_value=provider), patch.object(cli, "IntentPipeline", side_effect=pipeline_factory), patch("builtins.input", return_value="分析最近7天活跃"), patch("builtins.print") as printer, patch.object(sqlite3, "connect", side_effect=AssertionError), patch.object(socket, "create_connection", side_effect=AssertionError):
            code = cli.main(["--live", "--execute", "--explain", "--confidence-threshold", "0.8"])
        self.assertEqual(code, 0)
        function.assert_called_once()
        provider.generate_intent.assert_called_once()
        provider.generate_report.assert_called_once()
        output = "\n".join(call.args[0] for call in printer.call_args_list)
        self.assertIn("工具执行状态：success", output)
        explanation = next(call.args[0] for call in printer.call_args_list if call.args[0].startswith("结果解释（"))
        self.assertEqual(json.loads(explanation.split("：", 1)[1])["status"], "success")
        self.assertEqual(business_data, self.raw["data"])


if __name__ == "__main__":
    unittest.main()
