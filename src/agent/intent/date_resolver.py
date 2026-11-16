"""Local, deterministic date conversion for matched business tools.

The resolver never uses the computer clock. Relative expressions require an
explicit reference date or the configured synthetic-data cutoff date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from src.agent.intent.models import IntentDraft, IntentStatus
from src.agent.intent.tool_matcher import ToolMatchResult


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SETTINGS_PATH = ROOT / "configs" / "settings.yaml"

TOOL_DATE_FIELDS: Mapping[str, tuple[str, str]] = MappingProxyType({
    "analyze_activation_funnel": ("cohort_start", "cohort_end"),
    "analyze_retention": ("cohort_start", "cohort_end"),
    "analyze_user_activity": ("start_date", "end_date"),
    "analyze_feedback_trends": ("start_date", "end_date"),
})

_ISO_DATE = r"\d{4}-\d{2}-\d{2}"
_CONFIGURED_END_DATE = re.compile(
    r'^\s*dataset_end_date:\s*["\']?(\d{4}-\d{2}-\d{2})["\']?\s*$',
    re.MULTILINE,
)


@dataclass(frozen=True)
class DateResolutionResult:
    """A date-only resolution outcome; no business tool has been executed."""

    status: IntentStatus
    date_arguments: Mapping[str, str]
    reference_date: str | None
    reference_source: str | None
    resolution_basis: str | None
    reason: str
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "date_arguments",
            MappingProxyType(dict(self.date_arguments)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "date_arguments": dict(self.date_arguments),
            "reference_date": self.reference_date,
            "reference_source": self.reference_source,
            "resolution_basis": self.resolution_basis,
            "reason": self.reason,
            "error": self.error,
        }


class DateResolver:
    """Resolve supported date phrases after a tool has been matched."""

    def __init__(self, settings_path: str | Path = DEFAULT_SETTINGS_PATH) -> None:
        self.settings_path = Path(settings_path)

    def resolve(
        self,
        draft: IntentDraft,
        tool_match: ToolMatchResult,
        reference_date: str | date | datetime | None = None,
    ) -> DateResolutionResult:
        if not isinstance(draft, IntentDraft):
            raise TypeError("draft must be an IntentDraft instance.")
        if not isinstance(tool_match, ToolMatchResult):
            raise TypeError("tool_match must be a ToolMatchResult instance.")

        if tool_match.status is not IntentStatus.READY:
            return DateResolutionResult(
                status=tool_match.status,
                date_arguments={},
                reference_date=None,
                reference_source=None,
                resolution_basis=None,
                reason=(
                    "Date resolution requires a ready tool match. "
                    + tool_match.reason
                ),
            )

        fields = TOOL_DATE_FIELDS.get(tool_match.tool_name or "")
        if fields is None:
            return DateResolutionResult(
                status=IntentStatus.UNSUPPORTED,
                date_arguments={},
                reference_date=None,
                reference_source=None,
                resolution_basis=None,
                reason="The matched tool has no supported date-field mapping.",
                error="unsupported_tool_date_mapping",
            )

        expression = draft.date_expression
        if expression is not None and not isinstance(expression, str):
            return _invalid_result(
                "date_expression must be a string or null.",
                "invalid_date_expression_type",
            )
        if expression is None or not expression.strip():
            return self._resolve_expected_arguments(draft.arguments, fields)

        expression = expression.strip()
        explicit_dates = re.findall(_ISO_DATE, expression)
        if explicit_dates:
            if len(explicit_dates) == 1:
                return DateResolutionResult(
                    status=IntentStatus.NEEDS_CLARIFICATION,
                    date_arguments={},
                    reference_date=None,
                    reference_source=None,
                    resolution_basis="explicit_date:incomplete",
                    reason="Only one date was provided; both start and end are required.",
                    error="incomplete_date_range",
                )
            if len(explicit_dates) != 2:
                return DateResolutionResult(
                    status=IntentStatus.NEEDS_CLARIFICATION,
                    date_arguments={},
                    reference_date=None,
                    reference_source=None,
                    resolution_basis="explicit_date:ambiguous",
                    reason="More than two dates were provided; the range is ambiguous.",
                    error="ambiguous_date_range",
                )
            return _explicit_range_result(
                explicit_dates[0],
                explicit_dates[1],
                fields,
                "explicit_range:date_expression",
            )

        compact_expression = re.sub(r"\s+", "", expression)
        relative_kind = _relative_expression_kind(compact_expression)
        if relative_kind is None:
            if re.search(r"\d{4}[-/年]\d{1,2}", expression):
                return _invalid_result(
                    "Explicit dates must use YYYY-MM-DD format.",
                    "invalid_date_format",
                )
            return DateResolutionResult(
                status=IntentStatus.NEEDS_CLARIFICATION,
                date_arguments={},
                reference_date=None,
                reference_source=None,
                resolution_basis="unsupported_expression",
                reason=f"Unsupported date expression: {expression}",
                error="unsupported_date_expression",
            )

        reference, source, reference_error = self._reference_date(reference_date)
        if reference_error is not None:
            return reference_error
        if reference is None:
            return DateResolutionResult(
                status=IntentStatus.NEEDS_CLARIFICATION,
                date_arguments={},
                reference_date=None,
                reference_source=None,
                resolution_basis=f"relative:{relative_kind}",
                reason=(
                    "A reliable reference_date is required for this relative "
                    "date expression."
                ),
                error="missing_reference_date",
            )

        start, end = _relative_range(relative_kind, reference)
        return _success_result(
            start,
            end,
            fields,
            reference,
            source,
            f"relative:{relative_kind}",
        )

    def _resolve_expected_arguments(
        self,
        arguments: object,
        fields: tuple[str, str],
    ) -> DateResolutionResult:
        if not isinstance(arguments, dict):
            return _invalid_result(
                "draft.arguments must be a dictionary.",
                "invalid_arguments_type",
            )
        start_field, end_field = fields
        has_start = start_field in arguments and arguments[start_field] is not None
        has_end = end_field in arguments and arguments[end_field] is not None
        if not has_start and not has_end:
            return DateResolutionResult(
                status=IntentStatus.READY,
                date_arguments={},
                reference_date=None,
                reference_source=None,
                resolution_basis="no_date_expression",
                reason=(
                    "No date was supplied; required-date validation is deferred "
                    "to IntentValidator."
                ),
            )
        if not has_start or not has_end:
            return DateResolutionResult(
                status=IntentStatus.NEEDS_CLARIFICATION,
                date_arguments={},
                reference_date=None,
                reference_source=None,
                resolution_basis="explicit_range:arguments_incomplete",
                reason="Both mapped start and end date arguments are required.",
                error="incomplete_date_range",
            )
        return _explicit_range_result(
            arguments[start_field],
            arguments[end_field],
            fields,
            "explicit_range:arguments",
        )

    def _reference_date(
        self,
        value: str | date | datetime | None,
    ) -> tuple[date | None, str | None, DateResolutionResult | None]:
        if value is not None:
            parsed, error = _parse_date_value(value, "reference_date")
            if error is not None:
                return None, None, error
            return parsed, "explicit", None

        configured = _read_configured_reference_date(self.settings_path)
        if configured is None:
            return None, None, None
        return configured, "config:dataset_end_date", None


def _relative_expression_kind(expression: str) -> str | None:
    return {
        "最近7天": "recent_7_days",
        "最近30天": "recent_30_days",
        "本月": "current_month",
        "上个月": "previous_month",
        "今年": "current_year",
    }.get(expression)


def _relative_range(kind: str, reference: date) -> tuple[date, date]:
    if kind == "recent_7_days":
        return reference - timedelta(days=6), reference
    if kind == "recent_30_days":
        return reference - timedelta(days=29), reference
    if kind == "current_month":
        return reference.replace(day=1), reference
    if kind == "previous_month":
        previous_last = reference.replace(day=1) - timedelta(days=1)
        return previous_last.replace(day=1), previous_last
    if kind == "current_year":
        return reference.replace(month=1, day=1), reference
    raise RuntimeError(f"Unsupported relative-date rule: {kind}")


def _explicit_range_result(
    start_value: object,
    end_value: object,
    fields: tuple[str, str],
    basis: str,
) -> DateResolutionResult:
    start, start_error = _parse_date_value(start_value, fields[0])
    if start_error is not None:
        return start_error
    end, end_error = _parse_date_value(end_value, fields[1])
    if end_error is not None:
        return end_error
    assert start is not None and end is not None
    if start > end:
        return _invalid_result(
            "The start date cannot be later than the end date.",
            "start_after_end",
            basis,
        )
    return _success_result(start, end, fields, None, None, basis)


def _success_result(
    start: date,
    end: date,
    fields: tuple[str, str],
    reference: date | None,
    reference_source: str | None,
    basis: str,
) -> DateResolutionResult:
    return DateResolutionResult(
        status=IntentStatus.READY,
        date_arguments={
            fields[0]: start.isoformat(),
            fields[1]: end.isoformat(),
        },
        reference_date=reference.isoformat() if reference is not None else None,
        reference_source=reference_source,
        resolution_basis=basis,
        reason="Date range resolved with inclusive start and end dates.",
    )


def _parse_date_value(
    value: object,
    field_name: str,
) -> tuple[date | None, DateResolutionResult | None]:
    if isinstance(value, datetime):
        return value.date(), None
    if isinstance(value, date):
        return value, None
    if not isinstance(value, str) or not re.fullmatch(_ISO_DATE, value):
        return None, _invalid_result(
            f"{field_name} must use YYYY-MM-DD format.",
            "invalid_date_format",
        )
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, _invalid_result(
            f"{field_name} must be a valid calendar date.",
            "invalid_calendar_date",
        )


def _invalid_result(
    reason: str,
    error: str,
    basis: str | None = None,
) -> DateResolutionResult:
    return DateResolutionResult(
        status=IntentStatus.INVALID_INPUT,
        date_arguments={},
        reference_date=None,
        reference_source=None,
        resolution_basis=basis,
        reason=reason,
        error=error,
    )


def _read_configured_reference_date(settings_path: Path) -> date | None:
    try:
        text = settings_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _CONFIGURED_END_DATE.search(text)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None
