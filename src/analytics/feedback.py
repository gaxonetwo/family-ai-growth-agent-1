"""Read-only feedback trends using the existing local classifier."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any, Protocol

from src.data.feedback_classifier import classify_feedback_text
from src.data.feedback_classifier.rules import RULE_VERSION, CATEGORIES, SENTIMENTS
from src.tools.sql_query_tool import DEFAULT_DB_PATH, SQLQueryTool

SETTINGS_PATH = Path(__file__).resolve().parents[2] / "configs" / "settings.yaml"
BREAKDOWN_FIELDS = {"channel", "acquisition_channel", "plan_type"}
STATUSES = ("classified", "partially_classified", "unknown", "invalid_input")


class QueryRunner(Protocol):
    def run(self, sql: str) -> dict[str, Any]:
        """Execute one guarded read-only query."""


class FeedbackAnalysisError(RuntimeError):
    """Incomplete queries or failed classification must not yield a report."""


def _date(value: str | date | datetime, name: str) -> date:
    if isinstance(value, datetime):
        return (value.astimezone(timezone.utc) if value.tzinfo else value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError(f"{name} must be a valid YYYY-MM-DD date.")


def _configured_coverage() -> tuple[date, date]:
    text = SETTINGS_PATH.read_text(encoding="utf-8")
    end = re.search(r'^\s*dataset_end_date:\s*[\"\']?(\d{4}-\d{2}-\d{2})[\"\']?\s*$', text, re.MULTILINE)
    days = re.search(r"^\s*default_days:\s*(\d+)\s*$", text, re.MULTILINE)
    if not end or not days or int(days.group(1)) < 1:
        raise ValueError("Settings must declare dataset_end_date and positive default_days.")
    through = _date(end.group(1), "dataset_end_date")
    try:
        return through - timedelta(days=int(days.group(1)) - 1), through
    except OverflowError as exc:
        raise ValueError("Configured data range is out of bounds.") from exc


class FeedbackRepository:
    """Fetch stable ID pages and reconcile them with SQL aggregate counts."""

    def __init__(self, runner: QueryRunner) -> None:
        self.runner = runner

    def _query(self, sql: str) -> list[dict[str, Any]]:
        result = self.runner.run(sql)
        if result.get("ok") is not True or result.get("truncated") is not False:
            raise FeedbackAnalysisError("Feedback SQL failed or was truncated; no partial report returned.")
        rows = result.get("rows")
        if not isinstance(rows, list) or result.get("row_count") != len(rows):
            raise FeedbackAnalysisError("Feedback SQL returned inconsistent row metadata.")
        return rows

    def fetch(self, start: date, end: date, page_size: int) -> tuple[list[dict], dict]:
        try:
            end_exclusive = end + timedelta(days=1)
        except OverflowError as exc:
            raise ValueError("end_date cannot exceed the representable date range.") from exc
        # Parsed dates are regenerated here; raw user text is never interpolated.
        date_filter = (
            f"julianday(f.created_at) >= julianday('{start.isoformat()}T00:00:00+00:00') "
            f"AND julianday(f.created_at) < julianday('{end_exclusive.isoformat()}T00:00:00+00:00')"
        )
        count_sql = f"""SELECT COUNT(*) AS total_feedback,
            COUNT(DISTINCT f.feedback_id) AS unique_ids,
            COUNT(DISTINCT f.user_id) AS unique_feedback_users,
            COUNT(DISTINCT f.feedback_text) AS distinct_texts
            FROM feedback AS f WHERE {date_filter}"""
        counts = self._query(count_sql)
        if len(counts) != 1:
            raise FeedbackAnalysisError("Feedback count query must return exactly one row.")
        summary = counts[0]
        if summary["total_feedback"] != summary["unique_ids"]:
            raise FeedbackAnalysisError("Feedback IDs are null or non-unique.")
        records, seen, cursor = [], set(), None
        page_count = 0
        # The initial count bounds the loop, even if a broken runner repeats pages.
        for _ in range(summary["total_feedback"] // page_size + 1):
            cursor_filter = ""
            if cursor is not None:
                escaped = cursor.replace("'", "''")
                cursor_filter = f" AND f.feedback_id COLLATE BINARY > '{escaped}'"
            page = self._query(f"""SELECT f.feedback_id, f.user_id, f.created_at,
                date(f.created_at) AS feedback_date, f.channel, f.feedback_text,
                u.acquisition_channel, u.plan_type,
                CASE WHEN u.user_id IS NULL THEN 1 ELSE 0 END AS missing_user
                FROM feedback AS f LEFT JOIN users AS u ON u.user_id = f.user_id
                WHERE {date_filter}{cursor_filter}
                ORDER BY f.feedback_id COLLATE BINARY LIMIT {page_size}""")
            if not page:
                break
            ids = [row["feedback_id"] for row in page]
            if any(not isinstance(item, str) for item in ids):
                raise FeedbackAnalysisError("Feedback IDs must be non-null strings.")
            if (len(page) > page_size or ids != sorted(ids) or
                    len(set(ids)) != len(ids) or seen.intersection(ids) or
                    (cursor is not None and ids[0] <= cursor)):
                raise FeedbackAnalysisError("Feedback pagination repeated IDs or violated stable ordering.")
            records.extend(page)
            seen.update(ids)
            cursor = ids[-1]
            page_count += 1
            if len(page) < page_size:
                break
        after = self._query(count_sql)
        if after != counts or len(records) != summary["total_feedback"]:
            raise FeedbackAnalysisError("Feedback count changed or pagination is incomplete.")
        actual = (
            len({r["user_id"] for r in records if r["user_id"] is not None}),
            len({r["feedback_text"] for r in records if r["feedback_text"] is not None}),
        )
        if actual != (summary["unique_feedback_users"], summary["distinct_texts"]):
            raise FeedbackAnalysisError("Feedback user/text counts do not reconcile with SQL.")
        return records, {"page_size": page_size, "pages_read": page_count,
                         "records_read": len(records), "count_verified": True}


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _distribution(counts: Counter, labels: tuple[str, ...], total: int) -> list[dict]:
    return [{"name": label, "count": counts[label], "rate": _rate(counts[label], total)}
            for label in labels]


def _summary(records: list[dict], rules: bool) -> dict:
    summary = {
        "total_feedback": len(records),
        "unique_feedback_users": len({r["user_id"] for r in records if r["user_id"] is not None}),
        "distinct_texts": len({r["feedback_text"] for r in records if r["feedback_text"] is not None}),
        "classified_feedback": None, "invalid_input": None,
        "unknown_category": None, "unknown_sentiment": None,
        "valid_unknown_category": None, "valid_unknown_sentiment": None,
        "ambiguous_feedback": None, "conflict_feedback": None,
    }
    if rules:
        results = [r["classification"] for r in records]
        summary.update({
            "classified_feedback": sum(r["classification_status"] == "classified" for r in results),
            "invalid_input": sum(r["classification_status"] == "invalid_input" for r in results),
            "unknown_category": sum(r["category"] == "unknown" for r in results),
            "unknown_sentiment": sum(r["sentiment"] == "unknown" for r in results),
            "valid_unknown_category": sum(r["category"] == "unknown" and r["classification_status"] != "invalid_input" for r in results),
            "valid_unknown_sentiment": sum(r["sentiment"] == "unknown" and r["classification_status"] != "invalid_input" for r in results),
            "ambiguous_feedback": sum(r["is_ambiguous"] for r in results),
            "conflict_feedback": sum(bool(r["conflict_reasons"]) for r in results),
        })
    return summary


def _class_distributions(records: list[dict], enabled: bool) -> dict:
    if not enabled:
        return {"category_distribution": None, "sentiment_distribution": None,
                "classification_status_distribution": None}
    return {
        "category_distribution": _distribution(Counter(r["classification"]["category"] for r in records), CATEGORIES + ("unknown",), len(records)),
        "sentiment_distribution": _distribution(Counter(r["classification"]["sentiment"] for r in records), SENTIMENTS + ("unknown",), len(records)),
        "classification_status_distribution": _distribution(Counter(r["classification"]["classification_status"] for r in records), STATUSES, len(records)),
    }


def analyze_feedback_trends(
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    breakdown_by: str | None = None,
    analysis_mode: str = "rules",
    data_through_date: str | date | datetime | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
    query_runner: QueryRunner | None = None,
    *,
    page_size: int = 100,
    top_n: int = 10,
    data_start_date: str | date | datetime | None = None,
) -> dict[str, Any]:
    """Count UTC feedback and optionally aggregate existing rule classifications.

    Explicit data coverage is an assertion by the caller, not inferred from
    event presence. Any failed/truncated query or classifier exception raises
    rather than returning misleading zeros or a successful partial report.
    """
    start, end = _date(start_date, "start_date"), _date(end_date, "end_date")
    if start > end:
        raise ValueError("start_date cannot be later than end_date.")
    if breakdown_by is not None and breakdown_by not in BREAKDOWN_FIELDS:
        raise ValueError("breakdown_by must be channel, acquisition_channel, plan_type, or None.")
    if analysis_mode not in ("none", "rules"):
        raise ValueError("analysis_mode must be none or rules.")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 200:
        raise ValueError("page_size must be an integer between 1 and 200.")
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError("top_n must be a positive integer.")
    configured_start, configured_end = _configured_coverage()
    coverage_start = configured_start if data_start_date is None else _date(data_start_date, "data_start_date")
    coverage_end = configured_end if data_through_date is None else _date(data_through_date, "data_through_date")
    if coverage_start > coverage_end or start < coverage_start or end > coverage_end:
        raise ValueError("Requested dates must be inside the declared complete data range.")

    runner = query_runner if query_runner is not None else SQLQueryTool(db_path)
    records, pagination = FeedbackRepository(runner).fetch(start, end, page_size)
    rules = analysis_mode == "rules"
    for record in records if rules else ():
        try:
            result = classify_feedback_text(record["feedback_text"])
            if (result["rule_version"] != RULE_VERSION or
                    result["category"] not in CATEGORIES + ("unknown",) or
                    result["sentiment"] not in SENTIMENTS + ("unknown",) or
                    result["classification_status"] not in STATUSES or
                    not isinstance(result["is_ambiguous"], bool) or
                    not isinstance(result["conflict_reasons"], list)):
                raise ValueError("Classifier returned an unsupported result contract.")
            record["classification"] = result
        except Exception as exc:
            raise FeedbackAnalysisError(
                f"Classification failed for feedback_id={record['feedback_id']}; no partial report returned."
            ) from exc

    by_day = defaultdict(list)
    for record in records:
        by_day[record["feedback_date"]].append(record)
    daily, negative_trend = [], []
    day = start
    while day <= end:
        items = by_day[day.isoformat()]
        daily.append({"date": day.isoformat(), "feedback_count": len(items)})
        if rules:
            negative = sum(r["classification"]["sentiment"] == "negative" for r in items)
            negative_trend.append({"date": day.isoformat(), "total_feedback": len(items),
                                   "negative_feedback": negative, "negative_rate": _rate(negative, len(items))})
        day += timedelta(days=1)

    grouped = defaultdict(list)
    if breakdown_by:
        for record in records:
            grouped[record[breakdown_by]].append(record)
    breakdown = [
        {"group_value": value, "summary": _summary(items, rules),
         **_class_distributions(items, rules)}
        for value, items in sorted(grouped.items(), key=lambda pair: (pair[0] is not None, str(pair[0])))
    ]
    text_counts = Counter(r["feedback_text"] for r in records)
    sorted_texts = sorted(text_counts.items(), key=lambda pair: (-pair[1], pair[0] is not None, str(pair[0])))
    missing_users = sum(bool(r["missing_user"]) for r in records)
    return {
        "ok": True, "start_date": start.isoformat(), "end_date": end.isoformat(),
        "data_through_date": coverage_end.isoformat(), "analysis_mode": analysis_mode,
        "coverage": {"data_start_date": coverage_start.isoformat(), "data_through_date": coverage_end.isoformat()},
        "summary": _summary(records, rules), "daily_trend": daily,
        **_class_distributions(records, rules),
        "negative_trend": negative_trend if rules else None,
        "top_texts": [{"feedback_text": text, "count": count, "rate": _rate(count, len(records))}
                      for text, count in sorted_texts[:top_n]],
        "breakdown_by": breakdown_by, "breakdown": breakdown,
        "classification": {"method": "python_rules" if rules else "none", "version": RULE_VERSION if rules else None},
        "pagination": pagination,
        "methodology": {
            "timezone": "UTC", "date_window": "[start_date 00:00, day_after_end_date 00:00)",
            "distribution_denominator": "All feedback records in scope, including invalid_input and unknown",
            "negative_rate_denominator": "All feedback records on that UTC date; 0.0 when no records",
            "group_rate_denominator": "Feedback records within each group",
            "rate_format": "decimal_between_0_and_1_rounded_to_4_places",
            "unknown_counts": "unknown_category/sentiment include invalid_input; valid_unknown_* exclude it",
            "distinct_texts": "Exact original text values; SQL COUNT(DISTINCT) excludes NULL",
            "raw_text_preserved": True, "plan_type_basis": "current_snapshot_not_historical",
            "channel": "Feedback submission channel; distinct from users.acquisition_channel",
            "coverage_basis": "Configuration or explicit caller assertion; internal collection gaps not detected",
            "ground_truth_used": False,
        },
        "warnings": ([f"{missing_users} feedback records have no matching users; user attributes are null."]
                     if missing_users else []),
    }
