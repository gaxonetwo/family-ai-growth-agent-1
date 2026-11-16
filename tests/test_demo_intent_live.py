"""Live CLI wiring tests; all SDK traffic is replaced with a fake client."""
import json
import os
import socket
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.tools import demo_intent as cli
from src.agent.intent.deepseek_provider import DeepSeekProvider
from src.agent.intent.mock_provider import MockIntentProvider
from src.agent.intent.models import IntentStatus
from src.agent.intent.provider import IntentProviderError


ENV = {"DEEPSEEK_API_KEY": "fake-secret-test-only", "DEEPSEEK_MODEL": "deepseek-flash",
       "INTENT_CONFIDENCE_THRESHOLD": "0.8", "INTENT_MODEL_MAX_RETRIES": "0"}


def completion(arguments=None, evidence=None, confidence=.95):
    output = dict(intent_candidates=["retention_analysis"], arguments=arguments or {},
                  argument_evidence=evidence or {}, date_expression="最近7天",
                  confidence=confidence, reason="test")
    return SimpleNamespace(model="deepseek-flash", choices=[SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(content=json.dumps(output)))])


class LiveCLITests(unittest.TestCase):
    def invoke(self, argv, answers, client=None):
        client = client or Mock()
        if not client.chat.completions.create.side_effect:
            client.chat.completions.create.return_value = completion()
        def factory(**kwargs):
            return DeepSeekProvider(client_factory=lambda **options: client, **kwargs)
        with patch.dict(os.environ, ENV, clear=True), patch.object(cli, "DeepSeekProvider", side_effect=factory) as live, patch("builtins.input", side_effect=answers), patch("builtins.print") as printer, patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")), patch.object(sqlite3, "connect", side_effect=AssertionError("database forbidden")), patch("src.agent.tool_executor.ToolExecutor.execute", side_effect=AssertionError("execution forbidden")):
            code = cli.main(argv)
        return code, printer, live, client

    def test_default_uses_mock_and_same_pipeline(self):
        with patch.object(cli, "run_session") as session, patch.object(cli, "DeepSeekProvider", side_effect=AssertionError), patch.dict(os.environ, ENV, clear=True), patch("builtins.print"):
            self.assertEqual(cli.main([]), 0)
            self.assertIsInstance(session.call_args.args[0].parser.provider, MockIntentProvider)
            self.assertIsInstance(session.call_args.args[0], cli.IntentPipeline)

    def test_live_uses_real_provider_and_actual_input(self):
        code, printer, live, client = self.invoke(["--live", "--intent", "feedback_analysis"], ["分析最近7天留存"])
        self.assertEqual(code, 0)
        live.assert_called_once()
        request = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request["messages"][1]["content"], "分析最近7天留存")
        self.assertEqual(request["model"], ENV["DEEPSEEK_MODEL"])
        self.assertIn("analyze_retention", str(printer.call_args_list))

    def test_live_exit_and_blank_do_not_request(self):
        for text in ("quit", "exit", ""):
            with self.subTest(text=text):
                _, _, _, client = self.invoke(["--live"], [text])
                client.chat.completions.create.assert_not_called()

    def test_missing_key_and_model_no_fallback(self):
        for field, code in (("DEEPSEEK_API_KEY", "missing_api_key"), ("DEEPSEEK_MODEL", "missing_model")):
            env = dict(ENV)
            del env[field]
            with self.subTest(field=field), patch.dict(os.environ, env, clear=True), patch("builtins.print") as printer, patch.object(cli, "create_demo_provider", side_effect=AssertionError("fallback forbidden")), patch.object(cli, "run_session") as session:
                self.assertEqual(cli.main(["--live"]), 2)
                self.assertIn(code, str(printer.call_args_list))
                self.assertNotIn(ENV["DEEPSEEK_API_KEY"], str(printer.call_args_list))
                session.assert_not_called()

    def test_live_clarification(self):
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"breakdown_by": "plan_type"}, {"breakdown_by": "按照套餐"}),
            completion({"breakdown_by": "plan_type"}, {"breakdown_by": "用户当前套餐分组"}),
        ]
        _, printer, _, client = self.invoke(["--live"], ["按照套餐分析最近7天留存", "我指的是用户当前套餐分组"], client)
        self.assertEqual(client.chat.completions.create.call_count, 2)
        self.assertIn("参数验证状态：ready", str(printer.call_args_list))
        self.assertNotIn(ENV["DEEPSEEK_API_KEY"], str(printer.call_args_list))

    def test_low_confidence_still_asks(self):
        client = Mock()
        client.chat.completions.create.side_effect = [completion(confidence=.1), completion(confidence=.1)]
        _, printer, _, _ = self.invoke(["--live"], ["分析最近7天留存", "分析留存"], client)
        self.assertIn("一次澄清已用完", str(printer.call_args_list))

    def test_all_provider_errors_safe_in_session(self):
        codes = ["authentication_error", "rate_limit_error", "connection_error", "request_timeout", "invalid_json", "response_truncated", "api_error"]
        for code in codes:
            with self.subTest(code=code):
                client = Mock()
                client.chat.completions.create.side_effect = IntentProviderError(code, ENV["DEEPSEEK_API_KEY"], retryable=False)
                with patch.object(cli, "create_demo_provider", side_effect=AssertionError("fallback forbidden")):
                    _, printer, _, _ = self.invoke(["--live"], ["分析最近7天留存"], client)
                self.assertIn(code, str(printer.call_args_list))
                self.assertNotIn(ENV["DEEPSEEK_API_KEY"], str(printer.call_args_list))

    def test_invalid_json_and_truncation_from_fake_sdk(self):
        for content, finish, expected in (("not json", "stop", "invalid_json"), ("{}", "length", "response_truncated")):
            with self.subTest(expected=expected):
                client = Mock()
                client.chat.completions.create.side_effect = [SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])]
                _, printer, _, _ = self.invoke(["--live"], ["分析最近7天留存"], client)
                self.assertIn(expected, str(printer.call_args_list))


if __name__ == "__main__":
    unittest.main()
