"""Exact-day user retention analysis for Family AI."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
import re
from typing import Any, Protocol

from src.tools.sql_query_tool import DEFAULT_DB_PATH, SQLQueryTool


ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "configs" / "settings.yaml"
ALLOWED_BREAKDOWNS = {"acquisition_channel", "plan_type"}
VALID_EVENT_NAMES = ("app_open", "ai_interaction")


class QueryRunner(Protocol):
    """The read-only query interface required by this analysis."""

    def run(self, sql: str) -> dict[str, Any]:
        """Execute one read-only SQL query."""


class RetentionRepository:
    """Query mature cohort sizes and exact-day retention counts."""

    def __init__(self, query_runner: QueryRunner) -> None:
        self.query_runner = query_runner

    def fetch_summary(
        self,
        cohort_start: date,
        cohort_end: date,
        retention_days: tuple[int, ...],
        data_through_date: date,
        breakdown_by: str | None = None,
    ) -> list[dict[str, Any]]:
        if breakdown_by is not None and breakdown_by not in ALLOWED_BREAKDOWNS:
            raise ValueError(
                "breakdown_by must be acquisition_channel, plan_type, or None."
            )

        max_day = max(retention_days)
        day_flags = ",\n".join(
            f"""CASE WHEN c.is_eligible = 1 AND EXISTS (
                    SELECT 1
                    FROM events AS e
                    WHERE e.user_id = c.user_id
                      AND e.event_name IN ('app_open', 'ai_interaction')
                      AND datetime(e.event_time) > datetime(c.signup_at)
                      AND date(e.event_time) = date(c.signup_at, '+{day} days')
                ) THEN 1 ELSE 0 END AS retained_d{day}"""
            for day in retention_days
        )
        retained_sums = ",\n".join(
            f"COALESCE(SUM(retained_d{day}), 0) AS retained_d{day}_users"
            for day in retention_days
        )

        if breakdown_by is None:
            group_select = "'overall' AS group_value"
            group_by = ""
        else:
            # Safe because breakdown_by was checked against a fixed whitelist.
            group_select = f"c.{breakdown_by} AS group_value"
            group_by = f"GROUP BY c.{breakdown_by} ORDER BY c.{breakdown_by}"

        sql = f"""
        WITH cohort_users AS (
            SELECT
                u.user_id,
                u.signup_at,
                u.acquisition_channel,
                u.plan_type,
                CASE
                    WHEN date(u.signup_at, '+{max_day} days')
                         <= date('{data_through_date.isoformat()}')
                    THEN 1 ELSE 0
                END AS is_eligible
            FROM users AS u
            WHERE date(u.signup_at) >= date('{cohort_start.isoformat()}')
              AND date(u.signup_at) <= date('{cohort_end.isoformat()}')
        ),
        user_flags AS (
            SELECT
                c.*,
                {day_flags}
            FROM cohort_users AS c
        )
        SELECT
            {group_select},
            COUNT(*) AS registered_users,
            COALESCE(SUM(is_eligible), 0) AS eligible_users,
            COUNT(*) - COALESCE(SUM(is_eligible), 0) AS excluded_users,
            {retained_sums}
        FROM user_flags AS c
        {group_by}
        """
        result = self.query_runner.run(sql)
        return result["rows"]


def _as_date(value: str | date | datetime, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a YYYY-MM-DD date.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid YYYY-MM-DD date.") from exc


def _read_dataset_end_date(settings_path: Path = SETTINGS_PATH) -> date:
    if not settings_path.exists():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")
    text = settings_path.read_text(encoding="utf-8")
    match = re.search(
        r'^\s*dataset_end_date:\s*["\']?(\d{4}-\d{2}-\d{2})["\']?\s*$',
        text,
        re.MULTILINE,
    )
    if not match:
        raise ValueError("configs/settings.yaml is missing a valid dataset_end_date.")
    return date.fromisoformat(match.group(1))


def _validate_retention_days(retention_days: Sequence[int]) -> tuple[int, ...]:
    if isinstance(retention_days, (str, bytes)) or not isinstance(
        retention_days, Sequence
    ):
        raise ValueError("retention_days must be a non-empty sequence of positive integers.")
    days = tuple(retention_days)
    if not days:
        raise ValueError("retention_days cannot be empty.")
    if any(isinstance(day, bool) or not isinstance(day, int) or day <= 0 for day in days):
        raise ValueError("retention_days must contain only positive integers.")
    if len(set(days)) != len(days):
        raise ValueError("retention_days cannot contain duplicates.")
    return tuple(sorted(days))


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _build_metrics(
    row: dict[str, Any], retention_days: tuple[int, ...]
) -> dict[str, dict[str, int | float]]:
    eligible = int(row["eligible_users"])
    return {
        f"D{day}": {
            "retained_users": int(row[f"retained_d{day}_users"]),
            "retention_rate": _rate(int(row[f"retained_d{day}_users"]), eligible),
        }
        for day in retention_days
    }


def _build_changes(
    metrics: dict[str, dict[str, int | float]], retention_days: tuple[int, ...]
) -> list[dict[str, int | float | str]]:
    """Compare consecutive requested retention checkpoints."""
    changes = []
    for earlier, later in zip(retention_days, retention_days[1:]):
        earlier_metric = metrics[f"D{earlier}"]
        later_metric = metrics[f"D{later}"]
        changes.append({
            "from_day": f"D{earlier}",
            "to_day": f"D{later}",
            "retained_user_drop": int(earlier_metric["retained_users"])
            - int(later_metric["retained_users"]),
            "retention_rate_drop": round(
                float(earlier_metric["retention_rate"])
                - float(later_metric["retention_rate"]),
                4,
            ),
        })
    return changes


def analyze_retention(
    cohort_start: str | date | datetime,
    cohort_end: str | date | datetime,
    retention_days: Sequence[int] = (1, 7, 30),
    data_through_date: str | date | datetime | None = None,
    breakdown_by: str | None = None,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    query_runner: QueryRunner | None = None,
) -> dict[str, Any]:
    """Calculate exact-day retention using one shared mature-user denominator."""
    start_date = _as_date(cohort_start, "cohort_start")
    end_date = _as_date(cohort_end, "cohort_end")
    if start_date > end_date:
        raise ValueError("cohort_start cannot be later than cohort_end.")

    days = _validate_retention_days(retention_days)
    through_date = _as_date(
        data_through_date if data_through_date is not None else _read_dataset_end_date(),
        "data_through_date",
    )
    if through_date < start_date:
        raise ValueError("data_through_date cannot be earlier than cohort_start.")
    if breakdown_by is not None and breakdown_by not in ALLOWED_BREAKDOWNS:
        raise ValueError(
            "breakdown_by must be acquisition_channel, plan_type, or None."
        )

    runner = query_runner or SQLQueryTool(db_path)
    repository = RetentionRepository(runner)
    overall_rows = repository.fetch_summary(
        start_date, end_date, days, through_date
    )
    if len(overall_rows) != 1:
        raise RuntimeError("Retention query did not return exactly one overall row.")
    overall = overall_rows[0]

    breakdown: list[dict[str, Any]] = []
    if breakdown_by is not None:
        group_rows = repository.fetch_summary(
            start_date, end_date, days, through_date, breakdown_by
        )
        for row in group_rows:
            group_metrics = _build_metrics(row, days)
            breakdown.append({
                "group_value": row["group_value"],
                "registered_users": int(row["registered_users"]),
                "eligible_users": int(row["eligible_users"]),
                "excluded_users": int(row["excluded_users"]),
                "metrics": group_metrics,
                "retention_changes": _build_changes(group_metrics, days),
            })

    overall_metrics = _build_metrics(overall, days)

    return {
        "ok": True,
        "cohort_start": start_date.isoformat(),
        "cohort_end": end_date.isoformat(),
        "data_through_date": through_date.isoformat(),
        "retention_days": list(days),
        "registered_users": int(overall["registered_users"]),
        "eligible_users": int(overall["eligible_users"]),
        "excluded_users": int(overall["excluded_users"]),
        "metrics": overall_metrics,
        "retention_changes": _build_changes(overall_metrics, days),
        "breakdown_by": breakdown_by,
        "breakdown": breakdown,
        "methodology": {
            "type": "exact_day_retention",
            "valid_events": list(VALID_EVENT_NAMES),
            "denominator": "Users mature through the maximum requested retention day",
            "date_basis": "UTC calendar date derived from users.signup_at and events.event_time",
            "rate_format": "decimal_between_0_and_1",
        },
    }

