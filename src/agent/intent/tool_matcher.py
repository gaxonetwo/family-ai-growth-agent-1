"""Deterministic mapping from parsed business intents to registered tools.

This module only selects a tool name.  It does not validate business
arguments, resolve dates, or execute the selected tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from src.agent.intent.models import IntentCandidate, IntentDraft, IntentStatus
from src.agent.tool_registry import ToolNotFoundError, ToolRegistry


INTENT_TOOL_MAP: Mapping[IntentCandidate, str] = MappingProxyType({
    IntentCandidate.ACTIVATION_ANALYSIS: "analyze_activation_funnel",
    IntentCandidate.RETENTION_ANALYSIS: "analyze_retention",
    IntentCandidate.ACTIVITY_ANALYSIS: "analyze_user_activity",
    IntentCandidate.FEEDBACK_ANALYSIS: "analyze_feedback_trends",
})


class ToolMatcherError(ValueError):
    """The matcher received an invalid draft or has an invalid mapping."""


@dataclass(frozen=True)
class ToolMatchResult:
    """One deterministic tool-selection outcome without tool execution."""

    status: IntentStatus
    intent: IntentCandidate | None
    tool_name: str | None
    candidate_tools: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "intent": self.intent.value if self.intent is not None else None,
            "tool_name": self.tool_name,
            "candidate_tools": list(self.candidate_tools),
            "reason": self.reason,
        }


class ToolMatcher:
    """Match only approved intent enums against an injected ToolRegistry."""

    def __init__(self, registry: ToolRegistry) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry instance.")
        self.registry = registry

    def match(self, draft: IntentDraft) -> ToolMatchResult:
        """Return a selection outcome while never invoking a tool callable."""
        candidates = _validated_candidates(draft)
        if not candidates:
            return ToolMatchResult(
                status=IntentStatus.NEEDS_CLARIFICATION,
                intent=None,
                tool_name=None,
                candidate_tools=(),
                reason="No intent candidate was provided; clarification is required.",
            )

        candidate_tools = tuple(INTENT_TOOL_MAP[candidate] for candidate in candidates)
        unavailable_tools = _find_unavailable_tools(self.registry, candidate_tools)
        if unavailable_tools:
            return ToolMatchResult(
                status=IntentStatus.UNSUPPORTED,
                intent=candidates[0] if len(candidates) == 1 else None,
                tool_name=None,
                candidate_tools=candidate_tools,
                reason=(
                    "Mapped tool(s) are not registered: "
                    + ", ".join(unavailable_tools)
                ),
            )

        if len(candidates) > 1:
            return ToolMatchResult(
                status=IntentStatus.NEEDS_CLARIFICATION,
                intent=None,
                tool_name=None,
                candidate_tools=candidate_tools,
                reason="Multiple intent candidates remain; clarification is required.",
            )

        return ToolMatchResult(
            status=IntentStatus.READY,
            intent=candidates[0],
            tool_name=candidate_tools[0],
            candidate_tools=candidate_tools,
            reason="Exactly one intent maps to a registered tool.",
        )


def _validated_candidates(draft: IntentDraft) -> tuple[IntentCandidate, ...]:
    if not isinstance(draft, IntentDraft):
        raise TypeError("draft must be an IntentDraft instance.")

    candidates = draft.intent_candidates
    if not isinstance(candidates, tuple):
        raise ToolMatcherError("draft.intent_candidates must be a tuple.")
    if any(not isinstance(candidate, IntentCandidate) for candidate in candidates):
        raise ToolMatcherError(
            "Every draft intent candidate must be an IntentCandidate."
        )

    unique_candidates = set(candidates)
    missing_mappings = [
        candidate.value
        for candidate in unique_candidates
        if candidate not in INTENT_TOOL_MAP
    ]
    if missing_mappings:
        raise ToolMatcherError(
            "No tool mapping exists for intent(s): "
            + ", ".join(sorted(missing_mappings))
        )

    return tuple(sorted(unique_candidates, key=lambda item: item.value))


def _find_unavailable_tools(
    registry: ToolRegistry,
    tool_names: tuple[str, ...],
) -> tuple[str, ...]:
    unavailable: list[str] = []
    for tool_name in tool_names:
        try:
            registry.get_tool(tool_name)
        except ToolNotFoundError:
            unavailable.append(tool_name)
    return tuple(sorted(unavailable))
