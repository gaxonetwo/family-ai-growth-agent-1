"""Offline integration tests of the real intent processing components."""
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
import json
import socket
import sqlite3
import unittest
from unittest.mock import Mock, patch

from src.agent.intent.intent_pipeline import IntentPipeline
from src.agent.intent.date_resolver import DateResolver
from src.agent.intent.deepseek_provider import DeepSeekProvider
from src.agent.intent.mock_provider import MockIntentProvider
from src.agent.intent.models import ProviderResponse, IntentStatus
from src.agent.intent.provider import IntentProviderError, ProviderErrorCode
from src.agent.tool_registry import create_default_tool_registry


ENV = {"INTENT_CONFIDENCE_THRESHOLD": "0.8"}
TEXT = "  帮我分析最近7天的用户活跃情况\n"


def response(**updates):
    output = dict(intent_candidates=["activity_analysis"], arguments={},
                  argument_evidence={"date_range": "最近7天"}, date_expression="最近7天",
                  confidence=.95, reason="test")
    output.update(updates)
    return ProviderResponse(output, "mock-intent-model", "stop")


class PipelineTests(unittest.TestCase):
    def pipeline(self, **updates):
        self.provider = MockIntentProvider(response=response(**updates))
        return IntentPipeline(self.provider, environ=ENV)

    def test_complete_flow(self):
        result = self.pipeline().process(TEXT)
        self.assertEqual(result.status, IntentStatus.READY)
        self.assertIsNone(result.error)
        self.assertIsNone(result.clarification_question)
        self.assertEqual(self.provider.call_count, 1)

    def test_four_tools(self):
        cases = [("activation_analysis", "analyze_activation_funnel"),
                 ("retention_analysis", "analyze_retention"),
                 ("activity_analysis", "analyze_user_activity"),
                 ("feedback_analysis", "analyze_feedback_trends")]
        for intent, tool in cases:
            with self.subTest(intent=intent):
                result = self.pipeline(intent_candidates=[intent]).process(TEXT)
                self.assertEqual(result.status, IntentStatus.READY)
                self.assertEqual(result.validation["tool_name"], tool)

    def test_original_text_passed_to_validator(self):
        pipeline = self.pipeline()
        with patch.object(pipeline.validator, "validate", wraps=pipeline.validator.validate) as validate:
            result = pipeline.process(TEXT)
        self.assertEqual(validate.call_args.kwargs["user_input"], TEXT)
        self.assertEqual(self.provider.calls[0]["user_input"], TEXT)
        self.assertEqual(result.user_input, TEXT)

    def test_evidence_verified(self):
        result = self.pipeline().process(TEXT)
        self.assertTrue(result.validation["argument_provenance"]["start_date"]["verified"])

    def test_explicit_reference(self):
        result = self.pipeline().process(TEXT, "2024-03-01")
        self.assertEqual(result.date_resolution["reference_date"], "2024-03-01")
        self.assertEqual(result.validation["arguments"]["start_date"], "2024-02-24")

    def test_config_reference(self):
        result = self.pipeline().process(TEXT)
        self.assertEqual(result.date_resolution["reference_source"], "config:dataset_end_date")
        self.assertEqual(result.date_resolution["reference_date"], "2026-11-13")

    def test_invalid_inputs_stop(self):
        for text in ("", "  ", 123, None):
            with self.subTest(text=text):
                pipeline = self.pipeline()
                with patch.object(pipeline.matcher, "match", side_effect=AssertionError):
                    result = pipeline.process(text)
                self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
                self.assertIsNone(result.draft)
                self.assertIsNone(result.validation)
                self.assertEqual(self.provider.call_count, 0)

    def test_provider_error_propagates_unchanged(self):
        error = IntentProviderError(ProviderErrorCode.REQUEST_TIMEOUT, "timeout", retryable=True)
        pipeline = IntentPipeline(MockIntentProvider(error=error), environ=ENV)
        with patch.object(pipeline.matcher, "match", side_effect=AssertionError):
            with self.assertRaises(IntentProviderError) as raised:
                pipeline.process(TEXT)
        self.assertIs(raised.exception, error)
        self.assertEqual(raised.exception.code, ProviderErrorCode.REQUEST_TIMEOUT)

    def test_invalid_model_output_stops(self):
        pipeline = self.pipeline(confidence="high")
        with patch.object(pipeline.matcher, "match", side_effect=AssertionError):
            result = pipeline.process(TEXT)
        self.assertEqual(result.status, IntentStatus.MODEL_OUTPUT_INVALID)
        self.assertIsNone(result.tool_match)

    def test_multiple_intents_stop(self):
        pipeline = self.pipeline(intent_candidates=["activity_analysis", "retention_analysis"])
        with patch.object(pipeline.date_resolver, "resolve", side_effect=AssertionError):
            result = pipeline.process(TEXT)
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertEqual(len(result.tool_match["candidate_tools"]), 2)
        self.assertTrue(result.clarification_question)
        self.assertIsNone(result.date_resolution)

    def test_missing_tool(self):
        pipeline = self.pipeline()
        pipeline.matcher.registry.unregister_tool("analyze_user_activity")
        with patch.object(pipeline.date_resolver, "resolve", side_effect=AssertionError):
            result = pipeline.process(TEXT)
        self.assertEqual(result.status, IntentStatus.UNSUPPORTED)
        self.assertIsNone(result.validation)

    def test_unknown_date_stops_before_validator(self):
        pipeline = self.pipeline(date_expression="上周")
        with patch.object(pipeline.validator, "validate", side_effect=AssertionError):
            result = pipeline.process("分析上周活跃")
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertIsNone(result.validation)

    def test_invalid_date_stops(self):
        pipeline = self.pipeline()
        with patch.object(pipeline.validator, "validate", side_effect=AssertionError):
            result = pipeline.process(TEXT, "bad")
        self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
        self.assertIsNone(result.validation)

    def test_missing_reference(self):
        pipeline = self.pipeline()
        pipeline.date_resolver = DateResolver(Path(__file__).parent / "absent.yaml")
        result = pipeline.process(TEXT)
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertIn("参考日期", result.clarification_question)

    def test_validator_invalid_parameter(self):
        result = self.pipeline(arguments={"unexpected": 1}).process(TEXT)
        self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
        self.assertTrue(result.validation["validation_errors"])

    def test_conflicts_preserved(self):
        result = self.pipeline(arguments={"start_date": "2026-11-01"}).process(TEXT)
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertTrue(result.validation["conflict_details"])
        self.assertIn("2026-11-01", result.clarification_question)

    def test_repeated_calls_isolated(self):
        pipeline = self.pipeline()
        first = pipeline.process(TEXT, "2026-11-13")
        pipeline.process("", "2024-03-01")
        second = pipeline.process(TEXT, "2024-03-01")
        self.assertEqual(first.validation["arguments"]["end_date"], "2026-11-13")
        self.assertEqual(second.validation["arguments"]["end_date"], "2024-03-01")

    def test_result_immutable_and_serializable(self):
        result = self.pipeline().process(TEXT)
        with self.assertRaises(FrozenInstanceError):
            result.status = IntentStatus.UNSUPPORTED
        with self.assertRaises(TypeError):
            result.validation["argument_provenance"]["start_date"]["verified"] = False
        snapshot = result.to_dict()
        snapshot["draft"]["arguments"]["changed"] = True
        self.assertNotIn("changed", result.draft["arguments"])
        json.dumps(snapshot)

    def test_no_tool_database_network_calls(self):
        with patch("src.agent.tool_executor.ToolExecutor.execute", side_effect=AssertionError), patch.object(sqlite3, "connect", side_effect=AssertionError), patch.object(socket, "create_connection", side_effect=AssertionError):
            registry = create_default_tool_registry()
            for definition in registry.list_tools():
                from dataclasses import replace
                registry.unregister_tool(definition.name)
                registry.register_tool(replace(definition, execute=Mock(side_effect=AssertionError)))
            pipeline = IntentPipeline(MockIntentProvider(response=response()), registry, environ=ENV)
            self.assertEqual(pipeline.process(TEXT).status, IntentStatus.READY)
            for definition in registry.list_tools():
                definition.execute.assert_not_called()

    def test_deepseek_interface_with_fake_sdk_client(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            model="deepseek-flash", choices=[SimpleNamespace(finish_reason="stop",
                message=SimpleNamespace(content=json.dumps(response().output)))])
        provider = DeepSeekProvider(environ={"DEEPSEEK_API_KEY": "test-only", "DEEPSEEK_MODEL": "deepseek-flash"}, client_factory=lambda **kwargs: client)
        with patch.object(socket, "create_connection", side_effect=AssertionError):
            result = IntentPipeline(provider, environ=ENV).process(TEXT)
        self.assertEqual(result.status, IntentStatus.READY)
        client.chat.completions.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
