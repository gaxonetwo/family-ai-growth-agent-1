"""Offline evidence and clarification regression tests."""
from dataclasses import replace
import socket
import sqlite3
import unittest
from unittest.mock import patch

from src.agent.intent.intent_validator import IntentValidator
from src.agent.intent.date_resolver import DateResolver
from src.agent.intent.models import IntentDraft, IntentCandidate, IntentStatus
from src.agent.intent.tool_matcher import ToolMatcher
from src.agent.tool_registry import create_default_tool_registry


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_default_tool_registry()
        self.validator = IntentValidator(self.registry, environ={"INTENT_CONFIDENCE_THRESHOLD": "0.8"})
        self.draft = IntentDraft((IntentCandidate.ACTIVITY_ANALYSIS,), {}, {}, "最近7天", .95, "test")
        self.match = ToolMatcher(self.registry).match(self.draft)
        self.dates = DateResolver().resolve(self.draft, self.match, "2026-11-13")
        self.text = "帮我分析最近7天的用户活跃情况"

    def run_validation(self, draft=None, text=None, dates=None):
        return self.validator.validate(draft or self.draft, self.match, dates or self.dates, user_input=text)

    def test_explicit_value_with_original_evidence(self):
        draft = replace(self.draft, arguments={"breakdown_by": "plan_type"}, argument_evidence={"breakdown_by": "plan_type"})
        result = self.run_validation(draft, self.text + "按plan_type")
        self.assertEqual(result.argument_provenance["breakdown_by"]["source"], "user_explicit")

    def test_model_evidence_without_original_is_unverified(self):
        draft = replace(self.draft, arguments={"breakdown_by": "plan_type"}, argument_evidence={"breakdown_by": "按套餐"})
        record = self.run_validation(draft).argument_provenance["breakdown_by"]
        self.assertEqual(record["source"], "model_extracted")
        self.assertFalse(record["verified"])

    def test_dates_have_reference_and_rule(self):
        record = self.run_validation(text=self.text).argument_provenance["start_date"]
        self.assertEqual(record["source"], "date_resolver")
        self.assertTrue(record["verified"])
        self.assertIn("2026-11-13", record["derivation"])
        self.assertIn("recent_7_days", record["derivation"])

    def test_schema_default_is_confirmed(self):
        record = self.run_validation().argument_provenance["breakdown_by"]
        self.assertEqual(record["source"], "schema_default")
        self.assertTrue(record["verified"])

    def test_explicit_none_is_not_schema_default(self):
        draft = replace(self.draft, arguments={"breakdown_by": None})
        self.assertEqual(self.run_validation(draft).argument_provenance["breakdown_by"]["source"], "model_extracted")

    def test_missing_evidence_requires_confirmation_when_text_available(self):
        draft = replace(self.draft, arguments={"breakdown_by": "plan_type"})
        self.assertEqual(self.run_validation(draft, self.text).status, IntentStatus.NEEDS_CLARIFICATION)

    def test_fabricated_evidence_is_not_verified(self):
        draft = replace(self.draft, arguments={"breakdown_by": "plan_type"}, argument_evidence={"breakdown_by": "plan_type"})
        result = self.run_validation(draft, self.text)
        self.assertFalse(result.argument_provenance["breakdown_by"]["verified"])
        self.assertTrue(result.conflict_details)

    def test_evidence_value_mismatch(self):
        draft = replace(self.draft, arguments={"breakdown_by": "plan_type"}, argument_evidence={"breakdown_by": "acquisition_channel"})
        self.assertEqual(self.run_validation(draft, self.text + "acquisition_channel").status, IntentStatus.NEEDS_CLARIFICATION)

    def test_conflict_keeps_both_values(self):
        draft = replace(self.draft, arguments={"start_date": "2026-11-01"})
        result = self.run_validation(draft)
        self.assertEqual(len(result.conflict_details[0]["alternatives"]), 2)

    def test_date_conflict_question_uses_actual_dates(self):
        draft = replace(self.draft, arguments={"start_date": "2026-11-01", "end_date": "2026-11-13"})
        result = self.run_validation(draft)
        self.assertEqual(result.clarification_question, "你希望使用2026-11-01至2026-11-13，还是2026-11-07至2026-11-13？")

    def test_missing_dates_question(self):
        draft = replace(self.draft, date_expression=None)
        dates = DateResolver().resolve(draft, self.match)
        result = self.run_validation(draft, dates=dates)
        self.assertIn("起始日期", result.clarification_question)

    def test_low_confidence_question(self):
        result = self.run_validation(replace(self.draft, confidence=.1))
        self.assertIn("业务问题", result.clarification_question)

    def test_ready_has_no_question(self):
        self.assertIsNone(self.run_validation(text=self.text).clarification_question)

    def test_missing_dates_take_priority(self):
        draft = replace(self.draft, date_expression=None)
        result = self.run_validation(draft, dates=DateResolver().resolve(draft, self.match))
        self.assertEqual(result.missing_fields, ("start_date", "end_date"))
        self.assertEqual(result.clarification_question.count("？"), 1)

    def test_inputs_unchanged(self):
        before = self.draft.to_dict(), self.dates.to_dict()
        self.run_validation(text=self.text)
        self.assertEqual(before, (self.draft.to_dict(), self.dates.to_dict()))

    def test_repeatable(self):
        self.assertEqual(self.run_validation(text=self.text).to_dict(), self.run_validation(text=self.text).to_dict())

    def test_serialized_records_are_copied(self):
        result = self.run_validation()
        serialized = result.to_dict()
        serialized["argument_provenance"]["start_date"]["verified"] = "changed"
        self.assertFalse(result.argument_provenance["start_date"]["verified"])

    def test_no_execution_database_network(self):
        with patch("src.agent.tool_executor.ToolExecutor.execute", side_effect=AssertionError), patch.object(sqlite3, "connect", side_effect=AssertionError), patch.object(socket, "create_connection", side_effect=AssertionError):
            self.assertEqual(self.run_validation(text=self.text).status, IntentStatus.READY)


if __name__ == "__main__":
    unittest.main()
