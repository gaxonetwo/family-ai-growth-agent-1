"""Offline tests for deterministic local date resolution."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
import socket
from unittest import mock
import unittest

from src.agent.intent.date_resolver import DateResolver
from src.agent.intent.models import IntentCandidate, IntentDraft, IntentStatus
from src.agent.intent.tool_matcher import ToolMatchResult


ROOT = Path(__file__).resolve().parents[1]


def _draft(
    expression: str | None,
    arguments: dict | None = None,
) -> IntentDraft:
    return IntentDraft(
        intent_candidates=(IntentCandidate.ACTIVITY_ANALYSIS,),
        arguments={} if arguments is None else arguments,
        argument_evidence={},
        date_expression=expression,
        confidence=0.9,
        reason="test draft",
    )


def _match(tool_name: str, status: IntentStatus = IntentStatus.READY):
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


class DateResolverTests(unittest.TestCase):
    def test_recent_7_and_30_days_are_inclusive(self):
        resolver = DateResolver()
        cases = (
            ("最近7天", "2026-11-07", "2026-11-13"),
            ("最近30天", "2026-10-15", "2026-11-13"),
        )
        for expression, start, end in cases:
            with self.subTest(expression=expression):
                result = resolver.resolve(
                    _draft(expression),
                    _match("analyze_user_activity"),
                    reference_date="2026-11-13",
                )
                self.assertEqual(result.status, IntentStatus.READY)
                self.assertEqual(
                    dict(result.date_arguments),
                    {"start_date": start, "end_date": end},
                )

    def test_current_month_previous_month_and_current_year(self):
        resolver = DateResolver()
        cases = (
            ("本月", "2026-11-01", "2026-11-13"),
            ("上个月", "2026-10-01", "2026-10-31"),
            ("今年", "2026-01-01", "2026-11-13"),
        )
        for expression, start, end in cases:
            with self.subTest(expression=expression):
                result = resolver.resolve(
                    _draft(expression),
                    _match("analyze_feedback_trends"),
                    reference_date=date(2026, 11, 13),
                )
                self.assertEqual(
                    dict(result.date_arguments),
                    {"start_date": start, "end_date": end},
                )

    def test_all_four_tools_use_fixed_date_field_mapping(self):
        resolver = DateResolver()
        cases = {
            "analyze_activation_funnel": ("cohort_start", "cohort_end"),
            "analyze_retention": ("cohort_start", "cohort_end"),
            "analyze_user_activity": ("start_date", "end_date"),
            "analyze_feedback_trends": ("start_date", "end_date"),
        }
        for tool_name, fields in cases.items():
            with self.subTest(tool_name=tool_name):
                result = resolver.resolve(
                    _draft("最近7天"),
                    _match(tool_name),
                    reference_date="2026-11-13",
                )
                self.assertEqual(tuple(result.date_arguments), fields)

    def test_explicit_reference_date_has_priority_over_config(self):
        result = DateResolver().resolve(
            _draft("最近7天"),
            _match("analyze_user_activity"),
            reference_date="2024-03-01",
        )
        self.assertEqual(result.reference_date, "2024-03-01")
        self.assertEqual(result.reference_source, "explicit")
        self.assertEqual(result.date_arguments["end_date"], "2024-03-01")

    def test_configured_dataset_end_date_is_fallback(self):
        result = DateResolver(ROOT / "configs" / "settings.yaml").resolve(
            _draft("最近7天"),
            _match("analyze_user_activity"),
        )
        self.assertEqual(result.status, IntentStatus.READY)
        self.assertEqual(result.reference_date, "2026-11-13")
        self.assertEqual(result.reference_source, "config:dataset_end_date")

    def test_relative_expression_without_reference_needs_clarification(self):
        resolver = DateResolver(ROOT / "configs" / "missing-settings.yaml")
        result = resolver.resolve(
            _draft("最近7天"),
            _match("analyze_user_activity"),
        )
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertEqual(result.error, "missing_reference_date")
        self.assertEqual(dict(result.date_arguments), {})

    def test_no_date_is_not_invented(self):
        result = DateResolver().resolve(
            _draft(None),
            _match("analyze_retention"),
        )
        self.assertEqual(result.status, IntentStatus.READY)
        self.assertEqual(dict(result.date_arguments), {})
        self.assertEqual(result.resolution_basis, "no_date_expression")

    def test_explicit_range_in_expression_and_arguments(self):
        resolver = DateResolver()
        expression_result = resolver.resolve(
            _draft("2026-10-01 至 2026-10-31"),
            _match("analyze_feedback_trends"),
        )
        arguments_result = resolver.resolve(
            _draft(None, {
                "cohort_start": "2026-08-01",
                "cohort_end": "2026-08-31",
            }),
            _match("analyze_activation_funnel"),
        )
        self.assertEqual(
            dict(expression_result.date_arguments),
            {"start_date": "2026-10-01", "end_date": "2026-10-31"},
        )
        self.assertEqual(
            dict(arguments_result.date_arguments),
            {"cohort_start": "2026-08-01", "cohort_end": "2026-08-31"},
        )

    def test_invalid_date_format_and_calendar_date(self):
        resolver = DateResolver()
        for expression, error in (
            ("2026/10/01 至 2026/10/31", "invalid_date_format"),
            ("2026-02-30 至 2026-03-01", "invalid_calendar_date"),
        ):
            with self.subTest(expression=expression):
                result = resolver.resolve(
                    _draft(expression),
                    _match("analyze_user_activity"),
                )
                self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
                self.assertEqual(result.error, error)

    def test_start_after_end_is_invalid(self):
        result = DateResolver().resolve(
            _draft("2026-11-13 到 2026-11-01"),
            _match("analyze_user_activity"),
        )
        self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
        self.assertEqual(result.error, "start_after_end")

    def test_leap_year_and_cross_year(self):
        resolver = DateResolver()
        leap = resolver.resolve(
            _draft("上个月"),
            _match("analyze_user_activity"),
            reference_date="2024-03-15",
        )
        cross_year = resolver.resolve(
            _draft("上个月"),
            _match("analyze_user_activity"),
            reference_date="2026-01-15",
        )
        self.assertEqual(leap.date_arguments["end_date"], "2024-02-29")
        self.assertEqual(
            dict(cross_year.date_arguments),
            {"start_date": "2025-12-01", "end_date": "2025-12-31"},
        )

    def test_single_date_and_unsupported_expression_need_clarification(self):
        resolver = DateResolver()
        single = resolver.resolve(
            _draft("2026-11-13"),
            _match("analyze_user_activity"),
        )
        unsupported = resolver.resolve(
            _draft("上周"),
            _match("analyze_user_activity"),
        )
        self.assertEqual(single.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertEqual(single.error, "incomplete_date_range")
        self.assertEqual(unsupported.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertEqual(unsupported.error, "unsupported_date_expression")

    def test_non_ready_tool_match_is_not_resolved(self):
        result = DateResolver().resolve(
            _draft("最近7天"),
            _match(
                "analyze_user_activity",
                status=IntentStatus.NEEDS_CLARIFICATION,
            ),
            reference_date="2026-11-13",
        )
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertEqual(dict(result.date_arguments), {})

    def test_repeated_calls_and_results_are_isolated(self):
        resolver = DateResolver()
        first = resolver.resolve(
            _draft("最近7天"),
            _match("analyze_user_activity"),
            reference_date="2026-11-13",
        )
        second = resolver.resolve(
            _draft("本月"),
            _match("analyze_user_activity"),
            reference_date="2026-10-20",
        )
        self.assertEqual(first.date_arguments["start_date"], "2026-11-07")
        self.assertEqual(second.date_arguments["start_date"], "2026-10-01")
        with self.assertRaises(TypeError):
            first.date_arguments["start_date"] = "changed"  # type: ignore[index]

    def test_invalid_reference_date_does_not_fall_back_to_config(self):
        result = DateResolver().resolve(
            _draft("最近7天"),
            _match("analyze_user_activity"),
            reference_date="bad-date",
        )
        self.assertEqual(result.status, IntentStatus.INVALID_INPUT)
        self.assertEqual(result.error, "invalid_date_format")

    def test_resolution_does_not_execute_access_database_or_use_network(self):
        resolver = DateResolver()
        draft = _draft("最近7天")
        original = draft.to_dict()
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
            result = resolver.resolve(
                draft,
                _match("analyze_user_activity"),
                reference_date="2026-11-13",
            )

        self.assertEqual(result.status, IntentStatus.READY)
        self.assertEqual(draft.to_dict(), original)
        executor.assert_not_called()
        database.assert_not_called()
        network.assert_not_called()

    def test_invalid_contract_types_raise(self):
        resolver = DateResolver()
        with self.assertRaises(TypeError):
            resolver.resolve(object(), _match("analyze_user_activity"))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            resolver.resolve(_draft(None), object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
