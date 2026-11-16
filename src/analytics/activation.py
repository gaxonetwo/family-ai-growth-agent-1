"""Activation funnel analysis for Family AI users.

The repository builds the SQL query; the public analysis function validates
business inputs and turns the aggregate counts into funnel metrics.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Protocol

from src.tools.sql_query_tool import DEFAULT_DB_PATH, SQLQueryTool


ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "configs" / "settings.yaml"


class QueryRunner(Protocol):
    """Small interface needed by the activation repository."""

    def run(self, sql: str) -> dict[str, Any]:
        """Execute a read-only SQL query."""


class ActivationRepository:
    """Read activation-stage counts from the approved business tables."""

    def __init__(self, query_runner: QueryRunner) -> None:
        self.query_runner = query_runner

    def fetch_counts(
        self,
        cohort_start: datetime,
        cohort_end_exclusive: datetime,
        observation_days: int,
        data_end_exclusive: datetime,
    ) -> dict[str, int]:
        # Disjoint signup-day batches bound each query's work without changing
        # user-level denominators or weakening SQLQueryTool's execution limit.
        if cohort_end_exclusive - cohort_start > timedelta(days=1):
            totals = {}
            batch_start = cohort_start
            while batch_start < cohort_end_exclusive:
                batch_end = min(batch_start + timedelta(days=1), cohort_end_exclusive)
                batch = self.fetch_counts(
                    batch_start, batch_end, observation_days, data_end_exclusive
                )
                for key, value in batch.items():
                    totals[key] = totals.get(key, 0) + value
                batch_start = batch_end
            return totals
        # Values below are validated and converted to ISO strings before they
        # are inserted. SQLQueryTool deliberately exposes a simple SQL-string
        # interface, so no untrusted text reaches this query.
        start = cohort_start.isoformat()
        end = cohort_end_exclusive.isoformat()
        data_end = data_end_exclusive.isoformat()
        window_modifier = f"+{observation_days} days"

        sql = f"""
        WITH eligible_users AS (
            SELECT
                u.user_id,
                u.signup_at,
                datetime(u.signup_at, '{window_modifier}') AS observation_end
            FROM users AS u
            WHERE datetime(u.signup_at) >= datetime('{start}')
              AND datetime(u.signup_at) < datetime('{end}')
              AND datetime(u.signup_at, '{window_modifier}') <= datetime('{data_end}')
        ),
        bound_users AS MATERIALIZED (
            SELECT e.*, (
                SELECT MIN(julianday(d.bound_at)) FROM devices AS d
                WHERE d.user_id = e.user_id
                  AND julianday(d.bound_at) >= julianday(e.signup_at)
                  AND julianday(d.bound_at) < julianday(e.observation_end)
            ) AS bound_time FROM eligible_users AS e
        ),
        onboarded_users AS MATERIALIZED (
            SELECT b.*, (
                SELECT MIN(julianday(o.event_time)) FROM events AS o
                WHERE o.user_id = b.user_id
                  AND o.event_name = 'onboarding_complete'
                  AND julianday(o.event_time) >= b.bound_time
                  AND julianday(o.event_time) < julianday(b.observation_end)
            ) AS onboarding_time FROM bound_users AS b
        ),
        user_flags AS (
            SELECT
                e.user_id,
                CASE WHEN e.bound_time IS NOT NULL THEN 1 ELSE 0 END AS has_bound,
                CASE WHEN e.onboarding_time IS NOT NULL THEN 1 ELSE 0 END AS has_onboarding,
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM events AS a
                    WHERE a.user_id = e.user_id
                      AND a.event_name = 'ai_interaction'
                      AND julianday(a.event_time) >= e.onboarding_time
                      AND julianday(a.event_time) < julianday(e.observation_end)
                ) THEN 1 ELSE 0 END AS has_ai
            FROM onboarded_users AS e
        )
        SELECT
            COUNT(*) AS registered_users,
            COALESCE(SUM(has_bound), 0) AS device_bound_users,
            COALESCE(SUM(
                CASE WHEN has_bound = 1 AND has_onboarding = 1 THEN 1 ELSE 0 END
            ), 0) AS onboarding_completed_users,
            COALESCE(SUM(
                CASE
                    WHEN has_bound = 1 AND has_onboarding = 1 AND has_ai = 1
                    THEN 1 ELSE 0
                END
            ), 0) AS first_ai_interaction_users
        FROM user_flags
        """
        result = self.query_runner.run(sql)
        if result["row_count"] != 1:
            raise RuntimeError("Activation query did not return exactly one aggregate row.")
        return {key: int(value) for key, value in result["rows"][0].items()}


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
    """Read the one required setting without adding a YAML dependency."""
    if not settings_path.exists():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")
    text = settings_path.read_text(encoding="utf-8")
    match = re.search(r'^\s*dataset_end_date:\s*["\']?(\d{4}-\d{2}-\d{2})["\']?\s*$', text, re.MULTILINE)
    if not match:
        raise ValueError("configs/settings.yaml is missing a valid dataset_end_date.")
    return date.fromisoformat(match.group(1))


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def analyze_activation_funnel(
    cohort_start: str | date | datetime,
    cohort_end: str | date | datetime,
    observation_days: int = 7,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    dataset_end_date: str | date | datetime | None = None,
    query_runner: QueryRunner | None = None,
) -> dict[str, Any]:
    """Analyze the cumulative activation funnel for a signup cohort.

    ``cohort_start`` and ``cohort_end`` are inclusive UTC calendar dates.
    Only users whose full observation window fits inside the dataset are
    counted. Optional keyword arguments support isolated tests and alternate
    database locations without changing the three business inputs.
    """
    start_date = _as_date(cohort_start, "cohort_start")
    end_date = _as_date(cohort_end, "cohort_end")
    if start_date > end_date:
        raise ValueError("cohort_start cannot be later than cohort_end.")
    if isinstance(observation_days, bool) or not isinstance(observation_days, int):
        raise ValueError("observation_days must be a positive integer.")
    if observation_days <= 0:
        raise ValueError("observation_days must be a positive integer.")

    cutoff_date = _as_date(
        dataset_end_date if dataset_end_date is not None else _read_dataset_end_date(),
        "dataset_end_date",
    )
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_exclusive = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
    data_end_exclusive = datetime.combine(
        cutoff_date + timedelta(days=1), time.min, tzinfo=timezone.utc
    )

    runner = query_runner or SQLQueryTool(db_path)
    counts = ActivationRepository(runner).fetch_counts(
        start_at, end_exclusive, observation_days, data_end_exclusive
    )
    registered = counts["registered_users"]
    bound = counts["device_bound_users"]
    onboarded = counts["onboarding_completed_users"]
    activated = counts["first_ai_interaction_users"]

    return {
        "cohort": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "observation_days": observation_days,
            "dataset_end_date": cutoff_date.isoformat(),
        },
        "counts": counts,
        "conversion_rates": {
            "registration_to_device_bound": _rate(bound, registered),
            "device_bound_to_onboarding_completed": _rate(onboarded, bound),
            "onboarding_completed_to_first_ai_interaction": _rate(activated, onboarded),
        },
        "drop_off_counts": {
            "registration_to_device_bound": registered - bound,
            "device_bound_to_onboarding_completed": bound - onboarded,
            "onboarding_completed_to_first_ai_interaction": onboarded - activated,
        },
        "activation": {
            "activated_users": activated,
            "activation_rate": _rate(activated, registered),
        },
    }

