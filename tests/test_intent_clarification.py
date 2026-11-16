"""One-round clarification integration tests with offline providers."""
from copy import deepcopy
from dataclasses import replace
import socket
import sqlite3
import unittest
from unittest.mock import patch

from src.agent.intent.intent_pipeline import IntentPipeline
from src.agent.intent.mock_provider import MockIntentProvider
from src.agent.intent.provider import IntentModelProvider
from src.agent.intent.models import ProviderResponse, IntentStatus


ORIGINAL = "按照套餐分析最近7天活跃情况。"
ANSWER = "是的，我指的是按照用户当前套餐分组。"
ENV = {"INTENT_CONFIDENCE_THRESHOLD": "0.8"}


def response(evidence="按照套餐", **updates):
    output = dict(intent_candidates=["activity_analysis"], arguments={"breakdown_by": "plan_type"},
                  argument_evidence={"breakdown_by": evidence}, date_expression="最近7天",
                  confidence=.95, reason="test")
    output.update(updates)
    return ProviderResponse(output, "mock", "stop")


class ClarificationTests(unittest.TestCase):
    def setup_pipeline(self, second=None):
        self.provider = MockIntentProvider(response_factory=lambda prompt, text, n:
            response() if n == 1 else (second or response("用户当前套餐分组")))
        self.pipeline = IntentPipeline(self.provider, environ=ENV)
        self.previous = self.pipeline.process(ORIGINAL, "2026-11-13")
        self.assertEqual(self.previous.status, IntentStatus.NEEDS_CLARIFICATION)

    def test_normal_confirmation(self):
        self.setup_pipeline()
        result = self.pipeline.continue_with_clarification(self.previous, ANSWER)
        self.assertEqual(result.status, IntentStatus.READY)
        self.assertEqual(result.validation["arguments"]["breakdown_by"], "plan_type")

    def test_original_and_answer_preserved(self):
        self.setup_pipeline()
        result = self.pipeline.continue_with_clarification(self.previous, ANSWER)
        self.assertEqual(result.user_input, ORIGINAL)
        self.assertEqual(result.clarification_answer, ANSWER)
        self.assertEqual(result.previous_result["draft"]["argument_evidence"]["breakdown_by"], "按照套餐")

    def test_actual_text_passed_to_validator(self):
        self.setup_pipeline()
        with patch.object(self.pipeline.validator, "validate", wraps=self.pipeline.validator.validate) as validate:
            result = self.pipeline.continue_with_clarification(self.previous, ANSWER)
        self.assertEqual(validate.call_args.kwargs["user_input"], ORIGINAL + "\n" + ANSWER)
        self.assertTrue(result.validation["argument_provenance"]["breakdown_by"]["verified"])
        self.assertEqual(result.date_resolution["reference_date"], "2026-11-13")

    def test_denial(self):
        self.setup_pipeline()
        result = self.pipeline.continue_with_clarification(self.previous, "不是，我不是这个意思。")
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)

    def test_empty_answer(self):
        self.setup_pipeline()
        for answer in ("", " \n", None, 123):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                self.pipeline.continue_with_clarification(self.previous, answer)
        self.assertEqual(self.provider.call_count, 1)

    def test_changed_argument_is_not_silently_accepted(self):
        self.setup_pipeline(response("acquisition_channel", arguments={"breakdown_by": "acquisition_channel"}))
        result = self.pipeline.continue_with_clarification(self.previous, "改为acquisition_channel")
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertTrue(result.validation["conflict_details"])

    def test_still_unclear(self):
        self.setup_pipeline(response())
        result = self.pipeline.continue_with_clarification(self.previous, "是的")
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertTrue(result.clarification_question)

    def test_second_round_rejected(self):
        self.setup_pipeline(response())
        result = self.pipeline.continue_with_clarification(self.previous, "是的")
        with self.assertRaises(ValueError):
            self.pipeline.continue_with_clarification(result, ANSWER)

    def test_ready_and_unsupported_rejected(self):
        self.setup_pipeline()
        for status in (IntentStatus.READY, IntentStatus.UNSUPPORTED):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.pipeline.continue_with_clarification(replace(self.previous, status=status), ANSWER)

    def test_repeated_calls_are_isolated_and_previous_unchanged(self):
        self.setup_pipeline()
        before = self.previous.to_dict()
        a = self.pipeline.continue_with_clarification(self.previous, ANSWER)
        b = self.pipeline.continue_with_clarification(self.previous, ANSWER)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertEqual(self.previous.to_dict(), before)

    def test_omitted_original_parameters_retained(self):
        self.setup_pipeline(response(arguments={}, argument_evidence={}))
        result = self.pipeline.continue_with_clarification(self.previous, "是的")
        self.assertEqual(result.draft["arguments"]["breakdown_by"], "plan_type")
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)

    def test_fake_provider_and_no_external_actions(self):
        class FakeProvider(IntentModelProvider):
            def generate_intent(self, system_prompt, user_input):
                return response("用户当前套餐分组" if ANSWER in user_input else "按照套餐").to_dict()
        with patch("src.agent.tool_executor.ToolExecutor.execute", side_effect=AssertionError), patch.object(sqlite3, "connect", side_effect=AssertionError), patch.object(socket, "create_connection", side_effect=AssertionError):
            pipeline = IntentPipeline(FakeProvider(), environ=ENV)
            previous = pipeline.process(ORIGINAL)
            self.assertEqual(pipeline.continue_with_clarification(previous, ANSWER).status, IntentStatus.READY)


if __name__ == "__main__":
    unittest.main()
