"""Foundational data structures for intent recognition.

This module contains data only.  It does not call a model, match a tool, or
execute business analysis.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any


class IntentCandidate(str, Enum):
    """Business intents supported by the first intent-recognition release."""

    ACTIVATION_ANALYSIS = "activation_analysis"
    RETENTION_ANALYSIS = "retention_analysis"
    ACTIVITY_ANALYSIS = "activity_analysis"
    FEEDBACK_ANALYSIS = "feedback_analysis"


class IntentStatus(str, Enum):
    """Locally determined business-task states.

    Provider communication failures are deliberately not represented here;
    they are raised as ``IntentProviderError`` instances instead.
    """

    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNSUPPORTED = "unsupported"
    INVALID_INPUT = "invalid_input"
    MODEL_OUTPUT_INVALID = "model_output_invalid"


@dataclass(frozen=True)
class IntentDraft:
    """A model-produced draft that later batches will validate locally."""

    intent_candidates: tuple[IntentCandidate, ...]
    arguments: dict[str, Any]
    argument_evidence: dict[str, str]
    date_expression: str | None
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_candidates": [candidate.value for candidate in self.intent_candidates],
            "arguments": deepcopy(self.arguments),
            "argument_evidence": deepcopy(self.argument_evidence),
            "date_expression": self.date_expression,
            "confidence": self.confidence,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ProviderResponse:
    """Parsed JSON output plus transport metadata from a model provider."""

    output: dict[str, Any]
    model: str
    finish_reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "output": deepcopy(self.output),
            "model": self.model,
            "finish_reason": self.finish_reason,
        }


@dataclass(frozen=True)
class IntentError:
    """Safe error information that may be shown to a caller."""

    code: str
    message: str
    retryable: bool

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
