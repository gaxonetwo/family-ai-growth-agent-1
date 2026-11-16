"""Offline tests for deterministic intent-to-tool matching."""
from __future__ import annotations

import sqlite3
import socket
from unittest import mock
import unittest

from src.agent.intent.models import IntentCandidate, IntentDraft, IntentStatus
from src.agent.intent.tool_matcher import (
    INTENT_TOOL_MAP,
    ToolMatcher,
    ToolMatcherError,
)
from src.agent.tool_registry import ToolRegistry, create_default_tool_registry


def _draft(*candidates: IntentCandidate) -> IntentDraft:
    return IntentDraft(
        intent_candidates=tuple(candidates),
        arguments={},
        argument_evidence={},
        date_expression=None,
        confidence=0.9,
        reason="test draft",
    )


class ToolMatcherTests(unittest.TestCase):
    def test_each_intent_matches_its_registered_tool(self):
        matcher = ToolMatcher(create_default_tool_registry())
        expected = {
            IntentCandidate.ACTIVATION_ANALYSIS: "analyze_activation_funnel",
            IntentCandidate.RETENTION_ANALYSIS: "analyze_retention",
            IntentCandidate.ACTIVITY_ANALYSIS: "analyze_user_activity",
            IntentCandidate.FEEDBACK_ANALYSIS: "analyze_feedback_trends",
        }

        for intent, tool_name in expected.items():
            with self.subTest(intent=intent):
                result = matcher.match(_draft(intent))
                self.assertEqual(result.status, IntentStatus.READY)
                self.assertEqual(result.intent, intent)
                self.assertEqual(result.tool_name, tool_name)
                self.assertEqual(result.candidate_tools, (tool_name,))
                self.assertEqual(result.to_dict()["status"], "ready")

        self.assertEqual(dict(INTENT_TOOL_MAP), expected)

    def test_empty_candidates_require_clarification(self):
        result = ToolMatcher(create_default_tool_registry()).match(_draft())
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertIsNone(result.intent)
        self.assertIsNone(result.tool_name)
        self.assertEqual(result.candidate_tools, ())

    def test_multiple_candidates_preserve_all_and_do_not_select(self):
        result = ToolMatcher(create_default_tool_registry()).match(_draft(
            IntentCandidate.ACTIVITY_ANALYSIS,
            IntentCandidate.RETENTION_ANALYSIS,
        ))
        self.assertEqual(result.status, IntentStatus.NEEDS_CLARIFICATION)
        self.assertIsNone(result.intent)
        self.assertIsNone(result.tool_name)
        self.assertEqual(
            set(result.candidate_tools),
            {"analyze_user_activity", "analyze_retention"},
        )

    def test_unregistered_mapped_tool_is_unsupported(self):
        registry = create_default_tool_registry()
        registry.unregister_tool("analyze_feedback_trends")
        result = ToolMatcher(registry).match(_draft(
            IntentCandidate.FEEDBACK_ANALYSIS
        ))
        self.assertEqual(result.status, IntentStatus.UNSUPPORTED)
        self.assertEqual(result.intent, IntentCandidate.FEEDBACK_ANALYSIS)
        self.assertIsNone(result.tool_name)
        self.assertIn("not registered", result.reason)

    def test_invalid_inputs_raise_clear_errors(self):
        matcher = ToolMatcher(create_default_tool_registry())
        with self.assertRaises(TypeError):
            matcher.match(object())  # type: ignore[arg-type]

        invalid = _draft(IntentCandidate.ACTIVITY_ANALYSIS)
        object.__setattr__(invalid, "intent_candidates", ("activity_analysis",))
        with self.assertRaises(ToolMatcherError):
            matcher.match(invalid)

    def test_empty_registry_returns_unsupported_for_known_intent(self):
        result = ToolMatcher(ToolRegistry()).match(_draft(
            IntentCandidate.ACTIVATION_ANALYSIS
        ))
        self.assertEqual(result.status, IntentStatus.UNSUPPORTED)
        self.assertIsNone(result.tool_name)
        self.assertEqual(
            result.candidate_tools,
            ("analyze_activation_funnel",),
        )

    def test_registry_dynamic_change_is_observed(self):
        registry = create_default_tool_registry()
        matcher = ToolMatcher(registry)
        draft = _draft(IntentCandidate.RETENTION_ANALYSIS)

        first = matcher.match(draft)
        registry.unregister_tool("analyze_retention")
        second = matcher.match(draft)

        self.assertEqual(first.status, IntentStatus.READY)
        self.assertEqual(second.status, IntentStatus.UNSUPPORTED)

    def test_candidate_order_does_not_change_result(self):
        matcher = ToolMatcher(create_default_tool_registry())
        first = matcher.match(_draft(
            IntentCandidate.RETENTION_ANALYSIS,
            IntentCandidate.ACTIVITY_ANALYSIS,
        ))
        second = matcher.match(_draft(
            IntentCandidate.ACTIVITY_ANALYSIS,
            IntentCandidate.RETENTION_ANALYSIS,
        ))
        self.assertEqual(first, second)

    def test_duplicate_same_candidate_is_one_effective_intent(self):
        result = ToolMatcher(create_default_tool_registry()).match(_draft(
            IntentCandidate.ACTIVITY_ANALYSIS,
            IntentCandidate.ACTIVITY_ANALYSIS,
        ))
        self.assertEqual(result.status, IntentStatus.READY)
        self.assertEqual(result.tool_name, "analyze_user_activity")

    def test_matching_does_not_execute_access_database_or_use_network(self):
        registry = create_default_tool_registry()
        matcher = ToolMatcher(registry)
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
            result = matcher.match(_draft(IntentCandidate.ACTIVITY_ANALYSIS))

        self.assertEqual(result.status, IntentStatus.READY)
        executor.assert_not_called()
        database.assert_not_called()
        network.assert_not_called()

    def test_matcher_requires_existing_registry(self):
        with self.assertRaises(TypeError):
            ToolMatcher(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
