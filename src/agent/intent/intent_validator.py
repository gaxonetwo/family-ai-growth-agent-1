"""Local validation of a parsed, matched, and date-resolved intent."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date
import math
import os
from types import MappingProxyType
from typing import Any, Mapping

from src.agent.intent.date_resolver import DateResolutionResult
from src.agent.intent.models import IntentDraft, IntentStatus
from src.agent.intent.tool_matcher import ToolMatchResult
from src.agent.tool_registry import ToolNotFoundError, ToolRegistry
from src.agent.tool_schemas import SchemaValidationError
from src.agent.intent.argument_provenance import build_provenance, clarification


CONFIDENCE_THRESHOLD_ENV = "INTENT_CONFIDENCE_THRESHOLD"
_DATE_FIELD_PAIRS = (
    ("cohort_start", "cohort_end"),
    ("start_date", "end_date"),
)


class IntentValidatorConfigurationError(RuntimeError):
    """Required local Validator configuration is missing or invalid."""


@dataclass(frozen=True)
class IntentValidationResult:
    """Stable validation output with basic argument-source traceability."""

    status: IntentStatus
    tool_name: str | None
    arguments: Mapping[str, Any]
    original_arguments: Mapping[str, Any]
    date_arguments: Mapping[str, Any]
    missing_fields: tuple[str, ...]
    validation_errors: tuple[str, ...]
    confidence: float | None
    reason: str
    argument_provenance: Mapping[str, Any] = field(default_factory=dict)
    conflict_details: tuple[dict[str, Any], ...] = ()
    clarification_question: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "arguments",
            MappingProxyType(deepcopy(dict(self.arguments))),
        )
        object.__setattr__(
            self,
            "original_arguments",
            MappingProxyType(deepcopy(dict(self.original_arguments))),
        )
        object.__setattr__(
            self,
            "date_arguments",
            MappingProxyType(deepcopy(dict(self.date_arguments))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "tool_name": self.tool_name,
            "arguments": deepcopy(dict(self.arguments)),
            "original_arguments": deepcopy(dict(self.original_arguments)),
            "date_arguments": deepcopy(dict(self.date_arguments)),
            "missing_fields": list(self.missing_fields),
            "validation_errors": list(self.validation_errors),
            "confidence": self.confidence,
            "reason": self.reason,
            "argument_provenance": deepcopy(dict(self.argument_provenance)),
            "conflict_details": deepcopy(list(self.conflict_details)),
            "clarification_question": self.clarification_question,
        }


class IntentValidator:
    """Validate arguments against the schema held by the live Tool Registry."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry instance.")
        values = os.environ if environ is None else environ
        self.confidence_threshold = _read_confidence_threshold(values)
        self.registry = registry

    def validate(
        self,
        draft: IntentDraft,
        tool_match: ToolMatchResult,
        date_resolution: DateResolutionResult,
        *,
        user_input: str | None = None,
    ) -> IntentValidationResult:
        """Keep legacy calls valid; optionally verify evidence against original text."""
        if user_input is not None and not isinstance(user_input, str):
            raise TypeError("user_input must be a string or None.")
        result = self._validate_parameters(draft, tool_match, date_resolution)
        try:
            schema = self.registry.get_tool(result.tool_name).schema
        except ToolNotFoundError:
            schema = None
        records, conflicts = build_provenance(
            draft, date_resolution, result, schema, user_input
        )
        if conflicts and result.status is IntentStatus.READY:
            result = replace(result, status=IntentStatus.NEEDS_CLARIFICATION,
                             reason="Parameter evidence requires clarification.")
        return replace(result, argument_provenance=records,
                       conflict_details=tuple(conflicts),
                       clarification_question=clarification(result, draft, date_resolution, conflicts))

    def _validate_parameters(
        self,
        draft: IntentDraft,
        tool_match: ToolMatchResult,
        date_resolution: DateResolutionResult,
    ) -> IntentValidationResult:
        _check_contract_types(draft, tool_match, date_resolution)
        original_arguments = deepcopy(draft.arguments)
        date_arguments = deepcopy(dict(date_resolution.date_arguments))
        confidence = _safe_confidence(draft.confidence)

        if tool_match.status is not IntentStatus.READY:
            return _result(
                status=tool_match.status,
                tool_name=tool_match.tool_name,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                confidence=confidence,
                errors=(tool_match.reason,),
                reason="Tool matching is not ready; argument validation was skipped.",
            )

        if date_resolution.status is not IntentStatus.READY:
            errors = tuple(
                item for item in (date_resolution.error, date_resolution.reason) if item
            )
            return _result(
                status=date_resolution.status,
                tool_name=tool_match.tool_name,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                confidence=confidence,
                errors=errors,
                reason="Date resolution is not ready; argument validation was skipped.",
            )

        tool_name = tool_match.tool_name
        if not isinstance(tool_name, str) or not tool_name:
            return _result(
                status=IntentStatus.INVALID_INPUT,
                tool_name=None,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                confidence=confidence,
                errors=("A ready tool match must contain a tool name.",),
                reason="The tool-match contract is invalid.",
            )

        try:
            definition = self.registry.get_tool(tool_name)
        except ToolNotFoundError as exc:
            return _result(
                status=IntentStatus.UNSUPPORTED,
                tool_name=tool_name,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                confidence=confidence,
                errors=(str(exc),),
                reason="The matched tool is no longer registered.",
            )

        confidence_error = _validate_confidence(draft.confidence)
        if confidence_error is not None:
            return _result(
                status=IntentStatus.INVALID_INPUT,
                tool_name=tool_name,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                confidence=confidence,
                errors=(confidence_error,),
                reason="The model confidence value is invalid.",
            )
        assert confidence is not None
        if confidence < self.confidence_threshold:
            return _result(
                status=IntentStatus.NEEDS_CLARIFICATION,
                tool_name=tool_name,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                confidence=confidence,
                errors=(
                    "Model confidence is below the configured threshold "
                    f"({confidence} < {self.confidence_threshold}).",
                ),
                reason="The intent needs clarification because confidence is too low.",
            )

        merged, conflicts = _merge_arguments(original_arguments, date_arguments)
        if conflicts:
            return _result(
                status=IntentStatus.NEEDS_CLARIFICATION,
                tool_name=tool_name,
                arguments=merged,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                confidence=confidence,
                errors=tuple(
                    f"Conflicting values for argument: {field}"
                    for field in conflicts
                ),
                reason="Model and date-resolution arguments conflict.",
            )

        missing_fields = tuple(
            parameter.name
            for parameter in definition.schema.parameters
            if parameter.required and parameter.name not in merged
        )
        try:
            validated = definition.schema.validate(merged)
        except SchemaValidationError as exc:
            return _result(
                status=(
                    IntentStatus.NEEDS_CLARIFICATION
                    if missing_fields else IntentStatus.INVALID_INPUT
                ),
                tool_name=tool_name,
                arguments=merged,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                missing_fields=missing_fields,
                confidence=confidence,
                errors=(str(exc),),
                reason=(
                    "Required tool arguments are missing."
                    if missing_fields
                    else "Tool arguments do not satisfy the registered schema."
                ),
            )

        date_error = _date_range_error(validated)
        if date_error is not None:
            return _result(
                status=IntentStatus.INVALID_INPUT,
                tool_name=tool_name,
                arguments=validated,
                original_arguments=original_arguments,
                date_arguments=date_arguments,
                confidence=confidence,
                errors=(date_error,),
                reason="The validated date range is reversed.",
            )

        return _result(
            status=IntentStatus.READY,
            tool_name=tool_name,
            arguments=validated,
            original_arguments=original_arguments,
            date_arguments=date_arguments,
            confidence=confidence,
            reason="Intent arguments satisfy the registered tool schema.",
        )


def _read_confidence_threshold(values: Mapping[str, str]) -> float:
    raw = values.get(CONFIDENCE_THRESHOLD_ENV, "").strip()
    if not raw:
        raise IntentValidatorConfigurationError(
            f"{CONFIDENCE_THRESHOLD_ENV} must be configured."
        )
    try:
        threshold = float(raw)
    except ValueError as exc:
        raise IntentValidatorConfigurationError(
            f"{CONFIDENCE_THRESHOLD_ENV} must be a number between 0 and 1."
        ) from exc
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise IntentValidatorConfigurationError(
            f"{CONFIDENCE_THRESHOLD_ENV} must be a finite number between 0 and 1."
        )
    return threshold


def _check_contract_types(
    draft: object,
    tool_match: object,
    date_resolution: object,
) -> None:
    if not isinstance(draft, IntentDraft):
        raise TypeError("draft must be an IntentDraft instance.")
    if not isinstance(tool_match, ToolMatchResult):
        raise TypeError("tool_match must be a ToolMatchResult instance.")
    if not isinstance(date_resolution, DateResolutionResult):
        raise TypeError(
            "date_resolution must be a DateResolutionResult instance."
        )


def _validate_confidence(value: object) -> str | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        return "confidence must be a finite number and cannot be a boolean."
    if not 0 <= float(value) <= 1:
        return "confidence must be between 0 and 1."
    return None


def _safe_confidence(value: object) -> float | None:
    return None if _validate_confidence(value) is not None else float(value)


def _merge_arguments(
    original: Mapping[str, Any],
    resolved_dates: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    merged = deepcopy(dict(original))
    conflicts: list[str] = []
    for name, value in resolved_dates.items():
        if name in merged and merged[name] != value:
            conflicts.append(name)
            continue
        merged[name] = deepcopy(value)
    return merged, tuple(sorted(conflicts))


def _date_range_error(arguments: Mapping[str, Any]) -> str | None:
    for start_field, end_field in _DATE_FIELD_PAIRS:
        if start_field not in arguments or end_field not in arguments:
            continue
        start_value = arguments[start_field]
        end_value = arguments[end_field]
        if start_value is None or end_value is None:
            continue
        start = date.fromisoformat(start_value)
        end = date.fromisoformat(end_value)
        if start > end:
            return f"{start_field} cannot be later than {end_field}."
    return None


def _result(
    *,
    status: IntentStatus,
    tool_name: str | None,
    original_arguments: Mapping[str, Any],
    date_arguments: Mapping[str, Any],
    confidence: float | None,
    reason: str,
    arguments: Mapping[str, Any] | None = None,
    missing_fields: tuple[str, ...] = (),
    errors: tuple[str, ...] = (),
) -> IntentValidationResult:
    return IntentValidationResult(
        status=status,
        tool_name=tool_name,
        arguments={} if arguments is None else arguments,
        original_arguments=original_arguments,
        date_arguments=date_arguments,
        missing_fields=missing_fields,
        validation_errors=errors,
        confidence=confidence,
        reason=reason,
    )
