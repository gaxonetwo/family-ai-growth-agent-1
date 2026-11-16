"""Offline tests for local intent argument validation."""
from __future__ import annotations

from copy import deepcopy
import math
import sqlite3
import socket
from unittest import mock
import unittest

from src.agent.intent.date_resolver import DateResolutionResult
from src.agent.intent.intent_validator import (
    IntentValidator,
    IntentValidatorConfigurationError,
)
from src.agent.intent.models import IntentCandidate, IntentDraft, IntentStatus
from src.agent.intent.tool_matcher import ToolMatchResult
from src.agent.tool_registry import create_default_tool_registry
from src.agent.tool_schemas import ToolSchema


ENV = {"INTENT_CONFIDENCE_THRESHOLD": "0.8"}


def _draft(arguments=None, confidence=0.9):
    return IntentDraft(
        intent_candidates=(IntentCandidate.ACTIVITY_ANALYSIS,),
        arguments={} if arguments is None else arguments,
        argument_evidence={},
        date_expression=None,
        confidence=confidence,
        reason="test draft",
    )


def _match(tool_name, status=IntentStatus.READY):
    return ToolMatchResult(
        status=status,
        intent=(
            IntentCandidate.ACTIVITY_ANALYSIS
            if status is IntentStatus.READY else None
        ),
        tool_name=tool_name if status is IntentStatus.READY else None,
        candidate_tools=(tool_name,),
        reason="test match",
    )


def _dates(arguments=None, status=IntentStatus.READY, error=None):
    return DateResolutionResult(
        status=status,
        date_arguments={} if arguments is None else arguments,
        reference_date=None,
        reference_source=None,
        resolution_basis="test",
        reason="test dates",
        error=error,
    )


class IntentValidatorTests(unittest.TestCase):
    def setUp(self):
        self.registry = create_default_tool_registry()
        self.validator = IntentValidator(self.registry, environ=ENV)

    def test_four_tools_validate_against_their_registered_schemas(self):
        cases = (
            (
                "analyze_activation_funnel",
                {"cohort_start": "2026-08-01", "cohort_end": "2026-08-31"},
                {"observation_days": 7, "dataset_end_date": None},
            ),
            (
                "analyze_retention",
                {"cohort_start": "2026-08-01", "cohort_end": "2026-08-31"},
                {"retention_days": [1, 7, 30], "breakdown_by": None},
            ),
            (
                "analyze_user_activity",
                {"start_date": "2026-11-01", "end_date": "2026-11-13"},
                {"as_of_date": None, "breakdown_by": None},
            ),
            (
                "analyze_feedback_trends",
                {"start_date": "2026-11-01", "end_date": "2026-11-13"},
                {"analysis_mode": "rules", "page_size": 100, "top_n": 10},
            ),
        )
        for tool_name, date_arguments, expected_defaults in cases:
            with self.subTest(tool_name=tool_name):
                result = self.validator.validate(
                    _draft(),
                    _match(tool_name),
                    _dates(date_arguments),
                )
                self.assertEqual(result.status, IntentStatus.READY)
                for name, value in expected_defaults.items():
                    self.assertEqual(result.arguments[name], value)

    def test_missing_required_fields_need_clarification(self):
        result = self.validator.validate(
            _draft(),
            _match("analyze_user_activity"),
            _dates(),
        )
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertEqual(result.missing_fields, ("start_date", "end_date"))
        self.assertIn("Missing required", result.validation_errors[0])

    def test_wrong_type_is_invalid(self):
        result = self.validator.validate(
            _draft({"observation_days": "7"}),
            _match("analyze_activation_funnel"),
            _dates({
                "cohort_start": "2026-08-01",
                "cohort_end": "2026-08-31",
            }),
        )
        self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
        self.assertIn("JSON type integer", result.validation_errors[0])

    def test_extra_argument_and_invalid_enum_are_rejected(self):
        cases = (
            {"unexpected": True},
            {"breakdown_by": "unsupported_group"},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                result = self.validator.validate(
                    _draft(arguments),
                    _match("analyze_user_activity"),
                    _dates({
                        "start_date": "2026-11-01",
                        "end_date": "2026-11-13",
                    }),
                )
                self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
                self.assertTrue(result.validation_errors)

    def test_model_and_date_arguments_are_merged(self):
        result = self.validator.validate(
            _draft({"breakdown_by": "plan_type"}),
            _match("analyze_user_activity"),
            _dates({
                "start_date": "2026-11-01",
                "end_date": "2026-11-13",
            }),
        )
        self.assertEqual(result.status, IntentStatus.READY)
        self.assertEqual(result.arguments["breakdown_by"], "plan_type")
        self.assertEqual(result.arguments["start_date"], "2026-11-01")
        self.assertEqual(
            dict(result.original_arguments),
            {"breakdown_by": "plan_type"},
        )
        self.assertEqual(result.date_arguments["end_date"], "2026-11-13")

    def test_conflicting_date_argument_needs_clarification(self):
        result = self.validator.validate(
            _draft({"start_date": "2026-10-01"}),
            _match("analyze_user_activity"),
            _dates({
                "start_date": "2026-11-01",
                "end_date": "2026-11-13",
            }),
        )
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertIn("start_date", result.validation_errors[0])
        self.assertEqual(result.arguments["start_date"], "2026-10-01")

    def test_reversed_date_range_is_invalid(self):
        result = self.validator.validate(
            _draft(),
            _match("analyze_retention"),
            _dates({
                "cohort_start": "2026-11-13",
                "cohort_end": "2026-11-01",
            }),
        )
        self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
        self.assertIn("cannot be later", result.validation_errors[0])

    def test_invalid_confidence_values_are_rejected(self):
        for confidence in (True, float("nan"), -0.1, 1.1, "0.9"):
            with self.subTest(confidence=confidence):
                result = self.validator.validate(
                    _draft(confidence=confidence),
                    _match("analyze_user_activity"),
                    _dates({
                        "start_date": "2026-11-01",
                        "end_date": "2026-11-13",
                    }),
                )
                self.assertEqual(result.status, IntentStatus.INVALID_INPUT)

    def test_low_confidence_needs_clarification(self):
        result = self.validator.validate(
            _draft(confidence=0.79),
            _match("analyze_user_activity"),
            _dates({
                "start_date": "2026-11-01",
                "end_date": "2026-11-13",
            }),
        )
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertIn("0.79 < 0.8", result.validation_errors[0])

    def test_schema_defaults_are_returned_in_new_result(self):
        result = self.validator.validate(
            _draft(),
            _match("analyze_feedback_trends"),
            _dates({
                "start_date": "2026-11-01",
                "end_date": "2026-11-13",
            }),
        )
        self.assertEqual(result.arguments["analysis_mode"], "rules")
        self.assertEqual(result.arguments["page_size"], 100)
        self.assertEqual(result.arguments["top_n"], 10)

    def test_tool_removed_after_matching_is_unsupported(self):
        self.registry.unregister_tool("analyze_user_activity")
        result = self.validator.validate(
            _draft(),
            _match("analyze_user_activity"),
            _dates({
                "start_date": "2026-11-01",
                "end_date": "2026-11-13",
            }),
        )
        self.assertEqual(result.status, IntentStatus.UNSUPPORTED)
        self.assertIn("not registered", result.validation_errors[0])

    def test_non_ready_tool_match_skips_schema_validation(self):
        with mock.patch.object(
            ToolSchema,
            "validate",
            side_effect=AssertionError("schema validation forbidden"),
        ) as schema_validate:
            result = self.validator.validate(
                _draft(),
                _match(
                    "analyze_user_activity",
                    status=IntentStatus.NEEDS_CLARIFICATION,
                ),
                _dates(),
            )
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        schema_validate.assert_not_called()

    def test_non_ready_date_result_skips_schema_validation(self):
        with mock.patch.object(
            ToolSchema,
            "validate",
            side_effect=AssertionError("schema validation forbidden"),
        ) as schema_validate:
            result = self.validator.validate(
                _draft(),
                _match("analyze_user_activity"),
                _dates(
                    status=IntentStatus.INVALID_INPUT,
                    error="invalid_date_format",
                ),
            )
        self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
        self.assertIn("invalid_date_format", result.validation_errors)
        schema_validate.assert_not_called()

    def test_inputs_are_not_modified(self):
        arguments = {"retention_days": [1, 7, 30]}
        draft = _draft(arguments)
        dates = _dates({
            "cohort_start": "2026-08-01",
            "cohort_end": "2026-08-31",
        })
        original_draft = draft.to_dict()
        original_dates = dates.to_dict()

        result = self.validator.validate(
            draft,
            _match("analyze_retention"),
            dates,
        )
        result.to_dict()["arguments"]["retention_days"].append(60)

        self.assertEqual(draft.to_dict(), original_draft)
        self.assertEqual(dates.to_dict(), original_dates)

    def test_does_not_execute_access_database_or_use_network(self):
        with (
            mock.patch(
                "src.agent.tool_executor.ToolExecutor.execute",
                side_effect=AssertionError("executor forbidden"),
            ) as executor,
            mock.patch.object(
                sqlite3,
                "connect",
                side_effect=AssertionError("database forbidden"),
            ) as database,
            mock.patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("network forbidden"),
            ) as network,
        ):
            result = self.validator.validate(
                _draft(),
                _match("analyze_user_activity"),
                _dates({
                    "start_date": "2026-11-01",
                    "end_date": "2026-11-13",
                }),
            )
        self.assertEqual(result.status, IntentStatus.READY)
        executor.assert_not_called()
        database.assert_not_called()
        network.assert_not_called()

    def test_configuration_and_contract_errors_are_explicit(self):
        for values in ({}, {"INTENT_CONFIDENCE_THRESHOLD": "bad"},
                       {"INTENT_CONFIDENCE_THRESHOLD": "1.1"}):
            with self.subTest(values=values):
                with self.assertRaises(IntentValidatorConfigurationError):
                    IntentValidator(self.registry, environ=values)

        with self.assertRaises(TypeError):
            IntentValidator(object(), environ=ENV)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            self.validator.validate(object(), _match("x"), _dates())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            self.validator.validate(_draft(), object(), _dates())  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            self.validator.validate(_draft(), _match("x"), object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
