"""DAU, rolling WAU, and rolling MAU analysis for Family AI."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Any, Protocol

from src.tools.sql_query_tool import DEFAULT_DB_PATH, SQLQueryTool


ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "configs" / "settings.yaml"
VALID_EVENT_NAMES = ("app_open", "ai_interaction", "feature_use")


class QueryRunner(Protocol):
    """The read-only query interface required by activity analysis."""

    def run(self, sql: str) -> dict[str, Any]:
        """Execute one read-only SQL query."""


class ActivityRepository:
    """Read daily event and distinct-user activity from SQLite."""

    def __init__(self, query_runner: QueryRunner) -> None:
        self.query_runner = query_runner

    def fetch_daily_metrics(
        self,
        start_date: date,
        end_date: date,
        data_start_date: date,
    ) -> list[dict[str, Any]]:
        event_names = ", ".join(f"'{name}'" for name in VALID_EVENT_NAMES)
        rows = []
        activity_date = start_date
        while activity_date <= end_date:
            next_date = activity_date + timedelta(days=1)
            wau_start = activity_date - timedelta(days=6)
            mau_start = activity_date - timedelta(days=29)
            query_start = max(mau_start, data_start_date)
            sql = f"""
            SELECT
                '{activity_date.isoformat()}' AS activity_date,
                COALESCE(SUM(CASE
                    WHEN event_time >= '{activity_date.isoformat()}T00:00:00+00:00'
                     AND event_time < '{next_date.isoformat()}T00:00:00+00:00'
                    THEN 1 ELSE 0 END
                ), 0) AS event_records,
                COUNT(DISTINCT CASE
                    WHEN event_time >= '{activity_date.isoformat()}T00:00:00+00:00'
                     AND event_time < '{next_date.isoformat()}T00:00:00+00:00'
                    THEN user_id END
                ) AS dau,
                COUNT(DISTINCT CASE
                    WHEN event_time >= '{wau_start.isoformat()}T00:00:00+00:00'
                    THEN user_id END
                ) AS wau,
                COUNT(DISTINCT user_id) AS mau
            FROM events
            WHERE event_name IN ({event_names})
              AND event_time >= '{query_start.isoformat()}T00:00:00+00:00'
              AND event_time < '{next_date.isoformat()}T00:00:00+00:00'
            """
            result = self.query_runner.run(sql)
            if result["row_count"] != 1:
                raise RuntimeError("Daily activity query did not return one row.")
            row = result["rows"][0]
            if wau_start < data_start_date:
                row["wau"] = None
            if mau_start < data_start_date:
                row["mau"] = None
            rows.append(row)
            activity_date = next_date
        return rows

    def fetch_period_summary(
        self, start_date: date, end_date: date
    ) -> dict[str, int]:
        event_names = ", ".join(f"'{name}'" for name in VALID_EVENT_NAMES)
        sql = f"""
        SELECT
            COUNT(*) AS event_records,
            COUNT(DISTINCT user_id) AS unique_active_users
        FROM events
        WHERE event_name IN ({event_names})
          AND date(event_time) >= date('{start_date.isoformat()}')
          AND date(event_time) <= date('{end_date.isoformat()}')
        """
        result = self.query_runner.run(sql)
        if result["row_count"] != 1:
            raise RuntimeError("Activity summary query did not return one row.")
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


def _read_data_coverage(settings_path: Path = SETTINGS_PATH) -> tuple[date, date]:
    """Read the configured complete data range without adding YAML dependency."""
    if not settings_path.exists():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")
    text = settings_path.read_text(encoding="utf-8")
    end_match = re.search(
        r'^\s*dataset_end_date:\s*["\']?(\d{4}-\d{2}-\d{2})["\']?\s*$',
        text,
        re.MULTILINE,
    )
    days_match = re.search(
        r"^\s*default_days:\s*(\d+)\s*$", text, re.MULTILINE
    )
    if not end_match or not days_match:
        raise ValueError(
            "configs/settings.yaml must define dataset_end_date and default_days."
        )
    data_end = date.fromisoformat(end_match.group(1))
    default_days = int(days_match.group(1))
    if default_days <= 0:
        raise ValueError("configs/settings.yaml default_days must be positive.")
    return data_end - timedelta(days=default_days - 1), data_end


def _average(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _peak(
    time_series: list[dict[str, Any]], metric: str
) -> dict[str, str | int] | None:
    available = [row for row in time_series if row[metric] is not None]
    if not available:
        return None
    peak_row = max(available, key=lambda row: row[metric])
    return {"date": peak_row["date"], "users": peak_row[metric]}


def analyze_user_activity(
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    data_start_date: str | date | datetime | None = None,
    data_through_date: str | date | datetime | None = None,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    query_runner: QueryRunner | None = None,
    as_of_date: str | date | datetime | None = None,
    breakdown_by: str | None = None,
) -> dict[str, Any]:
    """Analyze daily active users and rolling 7/30-day active users.

    Dates are inclusive UTC calendar dates. A missing historical window is
    represented by ``None``; zero is reserved for a complete window with no
    qualifying activity.
    """
    requested_start = _as_date(start_date, "start_date")
    requested_end = _as_date(end_date, "end_date")
    baseline = _as_date(as_of_date, "as_of_date") if as_of_date is not None else requested_end
    if breakdown_by not in (None, "acquisition_channel", "plan_type"):
        raise ValueError("breakdown_by must be acquisition_channel, plan_type, or None.")
    if requested_start > requested_end:
        raise ValueError("start_date cannot be later than end_date.")

    configured_start, configured_end = _read_data_coverage()
    coverage_start = _as_date(
        data_start_date if data_start_date is not None else configured_start,
        "data_start_date",
    )
    coverage_end = _as_date(
        data_through_date if data_through_date is not None else configured_end,
        "data_through_date",
    )
    if coverage_start > coverage_end:
        raise ValueError("data_start_date cannot be later than data_through_date.")
    if requested_start < coverage_start or requested_end > coverage_end:
        raise ValueError(
            "Requested dates must be inside the declared complete data range "
            f"{coverage_start.isoformat()} to {coverage_end.isoformat()}."
        )
    if not coverage_start <= baseline <= coverage_end:
        raise ValueError("as_of_date must be inside the complete data range.")

    runner = query_runner or SQLQueryTool(db_path)
    repository = ActivityRepository(runner)
    rows = repository.fetch_daily_metrics(
        requested_start, requested_end, coverage_start
    )
    expected_days = (requested_end - requested_start).days + 1
    if len(rows) != expected_days:
        raise RuntimeError("Activity query returned an incomplete calendar series.")

    time_series = [
        {
            "date": row["activity_date"],
            "event_records": int(row["event_records"]),
            "dau": int(row["dau"]),
            "wau": None if row["wau"] is None else int(row["wau"]),
            "mau": None if row["mau"] is None else int(row["mau"]),
        }
        for row in rows
    ]
    period = repository.fetch_period_summary(requested_start, requested_end)
    baseline_row = repository.fetch_daily_metrics(baseline, baseline, coverage_start)[0]
    breakdown = []
    if breakdown_by is not None:
        # Field names are whitelist-validated; dates are parsed date objects.
        windows = {"dau": 0, "wau": 6, "mau": 29}
        expressions = []
        for metric, offset in windows.items():
            window_start = baseline - timedelta(days=offset)
            if window_start < coverage_start:
                expressions.append(f"NULL AS {metric}")
            else:
                expressions.append(
                    f"COUNT(DISTINCT CASE WHEN date(e.event_time) >= '{window_start.isoformat()}' "
                    f"THEN u.user_id END) AS {metric}"
                )
        sql = f"""SELECT u.{breakdown_by} AS group_value, {', '.join(expressions)}
            FROM users AS u LEFT JOIN events AS e ON e.user_id = u.user_id
             AND e.event_name IN ('app_open', 'ai_interaction', 'feature_use')
             AND date(e.event_time) >= '{max(coverage_start, baseline - timedelta(days=29)).isoformat()}'
             AND date(e.event_time) < '{(baseline + timedelta(days=1)).isoformat()}'
            GROUP BY u.{breakdown_by} ORDER BY u.{breakdown_by}"""
        group_result = runner.run(sql)
        if group_result.get("truncated"):
            raise RuntimeError("Activity groups were truncated.")
        breakdown = group_result["rows"]
    first_dau, last_dau = time_series[0]["dau"], time_series[-1]["dau"]
    change = last_dau - first_dau
    metric_values = {
        metric: [int(row[metric]) for row in time_series if row[metric] is not None]
        for metric in ("dau", "wau", "mau")
    }

    return {
        "ok": True,
        "as_of_date": baseline.isoformat(),
        "data_through_date": coverage_end.isoformat(),
        "dau": baseline_row["dau"],
        "wau": baseline_row["wau"],
        "mau": baseline_row["mau"],
        "daily_trend": [{"date": row["date"], "dau": row["dau"]} for row in time_series],
        "breakdown_by": breakdown_by,
        "breakdown": breakdown,
        "trend_analysis": {
            "first_dau": first_dau, "last_dau": last_dau,
            "absolute_change": change,
            "change_rate": round(change / first_dau, 4) if first_dau else None,
            "direction": "insufficient_data" if len(time_series) < 2 else (
                "up" if change > 0 else "down" if change < 0 else "unchanged"
            ),
            "limitation": "Endpoint comparison only; does not establish a sustained trend or cause.",
        },
        "start_date": requested_start.isoformat(),
        "end_date": requested_end.isoformat(),
        "coverage": {
            "data_start_date": coverage_start.isoformat(),
            "data_through_date": coverage_end.isoformat(),
            "requested_period_complete": True,
            "wau_available_from": (coverage_start + timedelta(days=6)).isoformat(),
            "mau_available_from": (coverage_start + timedelta(days=29)).isoformat(),
            "incomplete_windows_are_null": True,
        },
        "summary": {
            **period,
            "average_dau": _average(metric_values["dau"]),
            "average_wau": _average(metric_values["wau"]),
            "average_mau": _average(metric_values["mau"]),
            "peak_dau": _peak(time_series, "dau"),
            "peak_wau": _peak(time_series, "wau"),
            "peak_mau": _peak(time_series, "mau"),
        },
        "time_series": time_series,
        "methodology": {
            "plan_type_limitation": "users.plan_type is current plan, not historical plan.",
            "valid_events": list(VALID_EVENT_NAMES),
            "dau": "Distinct users active on one UTC calendar date",
            "wau": "Distinct users active in the current date and previous 6 dates",
            "mau": "Distinct users active in the current date and previous 29 dates",
            "event_records": "Number of qualifying event rows before user deduplication",
        },
    }

