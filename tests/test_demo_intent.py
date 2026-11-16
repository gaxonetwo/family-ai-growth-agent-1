"""Offline CLI tests using injected terminal streams and real Pipeline."""
import socket
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.tools.demo_intent import create_demo_provider, run_session, main
from src.agent.intent.intent_pipeline import IntentPipeline
from src.agent.intent.models import IntentStatus
from src.agent.intent.mock_provider import MockIntentProvider
from src.agent.intent.provider import IntentProviderError, ProviderErrorCode


class DemoIntentTests(unittest.TestCase):
    def session(self, texts, scenario="ready", intent="activity_analysis", provider=None):
        pipeline = IntentPipeline(provider or create_demo_provider(intent, scenario), environ={"INTENT_CONFIDENCE_THRESHOLD": "0.8"})
        reader, writer = Mock(side_effect=texts), Mock()
        with patch.object(pipeline, "continue_with_clarification", wraps=pipeline.continue_with_clarification) as continuation:
            result = run_session(pipeline, input_fn=reader, output_fn=writer)
        return result, reader, writer, continuation

    def test_ready(self):
        result, reader, writer, continuation = self.session(["分析最近7天活跃"])
        self.assertEqual(result.status, IntentStatus.READY)
        self.assertEqual(reader.call_count, 1)
        continuation.assert_not_called()

    def test_clarification_success(self):
        result, reader, writer, continuation = self.session(["按照套餐分析最近7天活跃", "我指的是用户当前套餐分组"], "clarification")
        self.assertEqual(result.status, IntentStatus.READY)
        self.assertEqual(result.clarification_count, 1)
        self.assertEqual(continuation.call_count, 1)
        self.assertTrue(any("澄清问题" in c.args[0] for c in writer.call_args_list))

    def test_unresolved_only_once_latest_result_returned(self):
        result, reader, writer, continuation = self.session(["按照套餐分析最近7天活跃", "是的"], "unresolved")
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertEqual(result.clarification_count, 1)
        self.assertEqual(reader.call_count, 2)
        self.assertEqual(continuation.call_count, 1)
        self.assertEqual(continuation.call_args.args[0].clarification_count, 0)
        self.assertIn("一次澄清已用完", writer.call_args.args[0])

    def test_exit_and_quit(self):
        for text in ("exit", "quit", " EXIT "):
            with self.subTest(text=text):
                self.assertIsNone(self.session([text])[0])

    def test_empty(self):
        result, reader, writer, continuation = self.session(["  "])
        self.assertIsNone(result)
        continuation.assert_not_called()

    def test_empty_answer(self):
        result, reader, writer, continuation = self.session(["按照套餐分析最近7天活跃", ""], "clarification")
        continuation.assert_not_called()
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)

    def test_exit_during_clarification(self):
        result, reader, writer, continuation = self.session(["按照套餐分析最近7天活跃", "quit"], "clarification")
        continuation.assert_not_called()

    def test_provider_error_safe_display(self):
        error = IntentProviderError(ProviderErrorCode.REQUEST_TIMEOUT, "secret-request-details", retryable=True)
        result, reader, writer, continuation = self.session(["分析最近7天"], provider=MockIntentProvider(error=error))
        output = str(writer.call_args_list)
        self.assertIn("request_timeout", output)
        self.assertNotIn("secret-request-details", output)

    def test_all_four_intents_displayed(self):
        for intent, tool in [("activity_analysis", "analyze_user_activity"), ("activation_analysis", "analyze_activation_funnel"), ("retention_analysis", "analyze_retention"), ("feedback_analysis", "analyze_feedback_trends")]:
            with self.subTest(intent=intent):
                result, reader, writer, continuation = self.session(["分析最近7天"], intent=intent)
                self.assertEqual(result.status, IntentStatus.READY)
                self.assertIn(tool, str(writer.call_args_list))
                self.assertIn(intent, str(writer.call_args_list))

    def test_no_executor_database_network(self):
        with patch("src.agent.tool_executor.ToolExecutor.execute", side_effect=AssertionError), patch.object(sqlite3, "connect", side_effect=AssertionError), patch.object(socket, "create_connection", side_effect=AssertionError):
            self.assertEqual(self.session(["分析最近7天"])[0].status, IntentStatus.READY)

    def test_eof(self):
        pipeline = IntentPipeline(create_demo_provider(), environ={"INTENT_CONFIDENCE_THRESHOLD": "0.8"})
        self.assertIsNone(run_session(pipeline, input_fn=Mock(side_effect=EOFError), output_fn=Mock()))

    def test_main_mock_entry(self):
        with patch("builtins.input", return_value="分析最近7天"), patch("builtins.print") as printer:
            # run_session's injectable input default was bound at import time.
            with patch("src.tools.demo_intent.run_session", return_value=None) as session:
                self.assertEqual(main(["--confidence-threshold", "0.8"]), 0)
                self.assertIsInstance(session.call_args.args[0].parser.provider, MockIntentProvider)


if __name__ == "__main__":
    unittest.main()
