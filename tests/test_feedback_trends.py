"""Feedback trends tests against isolated temporary SQLite databases."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.analytics.feedback import analyze_feedback_trends, FeedbackAnalysisError
from src.tools.sql_query_tool import SQLQueryTool, SQLQueryError

SCHEMA = Path(__file__).resolve().parents[1] / "database" / "schema.sql"


class RecordingRunner:
    def __init__(self, db_path):
        self.tool = SQLQueryTool(db_path)
        self.queries = []

    def run(self, sql):
        self.queries.append(sql)
        return self.tool.run(sql)


class FeedbackTrendsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db = Path(self.temp_dir.name) / "feedback tests.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(SCHEMA.read_text(encoding="utf-8"))
            conn.executemany("INSERT INTO users VALUES (?, ?, ?, 'east', ?)", [
                ("u1", "2026-09-01T00:00:00+00:00", "organic", "free"),
                ("u2", "2026-09-01T00:00:00+00:00", "social", "premium"),
                ("u3", "2026-09-01T00:00:00+00:00", "referral", "free"),
            ])
            conn.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?)", [
                ("f000", "u1", "2026-10-31T23:59:59+00:00", "in_app", "不计入开始前"),
                ("f001", "u1", "2026-11-01T00:00:00+00:00", "in_app", "设备连接失败。"),
                ("f002", "u1", "2026-11-01T12:00:00+00:00", "email", "整体体验不错。"),
                ("f003", "u2", "2026-11-02T00:00:00+00:00", "survey", "xyz"),
                ("f004", "u2", "2026-11-02T23:59:59+00:00", "survey", " \t "),
                ("f005", "u3", "2026-11-03T23:59:59+00:00", "email", "希望优化应用运行流畅度。"),
                ("f006", "u3", "2026-11-04T00:00:00+00:00", "email", "不计入结束后"),
            ])
        conn.close()

    def analyze(self, **kwargs):
        options = {"start_date": "2026-11-01", "end_date": "2026-11-03", "db_path": self.db}
        options.update(kwargs)
        return analyze_feedback_trends(**options)

    def add_feedback(self, rows):
        with sqlite3.connect(self.db) as conn:
            conn.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?)", rows)
        conn.close()

    def test_normal_summary_and_all_distributions(self):
        result = self.analyze()
        summary = result["summary"]
        self.assertTrue(result["ok"])
        self.assertEqual(summary["total_feedback"], 5)
        self.assertEqual(summary["unique_feedback_users"], 3)
        self.assertEqual(summary["distinct_texts"], 5)
        self.assertEqual(summary["classified_feedback"], 3)
        self.assertEqual(sum(item["count"] for item in result["category_distribution"]), 5)
        self.assertEqual(sum(item["count"] for item in result["sentiment_distribution"]), 5)
        self.assertEqual(sum(item["count"] for item in result["classification_status_distribution"]), 5)
        self.assertEqual(result["classification"]["version"], "1.0.0")

    def test_daily_negative_count_and_rate(self):
        result = self.analyze()
        self.assertEqual(result["daily_trend"], [
            {"date": "2026-11-01", "feedback_count": 2},
            {"date": "2026-11-02", "feedback_count": 2},
            {"date": "2026-11-03", "feedback_count": 1},
        ])
        self.assertEqual(result["negative_trend"][0], {
            "date": "2026-11-01", "total_feedback": 2,
            "negative_feedback": 1, "negative_rate": 0.5,
        })
        self.assertEqual(result["negative_trend"][1]["negative_rate"], 0.0)

    def test_unknown_and_invalid_are_separate_and_not_neutral(self):
        result = self.analyze()
        summary = result["summary"]
        self.assertEqual(summary["invalid_input"], 1)
        self.assertEqual(summary["unknown_category"], 2)
        self.assertEqual(summary["unknown_sentiment"], 2)
        self.assertEqual(summary["valid_unknown_category"], 1)
        self.assertEqual(summary["valid_unknown_sentiment"], 1)
        sentiments = {r["name"]: r for r in result["sentiment_distribution"]}
        self.assertEqual(sentiments["neutral"]["count"], 1)
        self.assertEqual(sentiments["unknown"]["rate"], 0.4)
        self.assertEqual(summary["ambiguous_feedback"], 1)

    def test_conflicts_and_partial_classification(self):
        self.add_feedback([("g1", "u1", "2026-11-01T01:00:00Z", "email", "设备掉线而且页面加载很慢。")])
        result = self.analyze()
        self.assertEqual(result["summary"]["conflict_feedback"], 1)
        statuses = {r["name"]: r["count"] for r in result["classification_status_distribution"]}
        self.assertEqual(statuses["partially_classified"], 1)

    def test_duplicate_text_is_not_a_duplicate_feedback(self):
        self.add_feedback([("g1", "u1", "2026-11-02T01:00:00Z", "in_app", "设备连接失败。")])
        result = self.analyze()
        self.assertEqual(result["summary"]["total_feedback"], 6)
        self.assertEqual(result["summary"]["unique_feedback_users"], 3)
        self.assertEqual(result["summary"]["distinct_texts"], 5)
        self.assertEqual(result["top_texts"][0]["count"], 2)
        self.assertEqual(result["top_texts"][0]["feedback_text"], "设备连接失败。")

    def test_empty_period_and_complete_zero_days(self):
        result = self.analyze(start_date="2026-10-01", end_date="2026-10-03")
        self.assertEqual(result["summary"]["total_feedback"], 0)
        self.assertEqual(len(result["daily_trend"]), 3)
        self.assertTrue(all(r["feedback_count"] == 0 for r in result["daily_trend"]))
        self.assertTrue(all(r["negative_rate"] == 0.0 for r in result["negative_trend"]))
        self.assertEqual(result["top_texts"], [])
        self.assertTrue(all(r["rate"] == 0.0 for r in result["category_distribution"]))

    def test_none_mode_never_calls_classifier_and_returns_null_analysis(self):
        with patch("src.analytics.feedback.classify_feedback_text", side_effect=AssertionError("must not run")) as classifier:
            result = self.analyze(analysis_mode="none", breakdown_by="channel")
        classifier.assert_not_called()
        self.assertEqual(result["summary"]["total_feedback"], 5)
        self.assertEqual(result["summary"]["distinct_texts"], 5)
        for key in ("category_distribution", "sentiment_distribution", "negative_trend", "classification_status_distribution"):
            self.assertIsNone(result[key])
        self.assertIsNone(result["summary"]["invalid_input"])
        self.assertIsNone(result["classification"]["version"])
        self.assertIsNone(result["breakdown"][0]["category_distribution"])

    def test_date_window_contains_start_and_excludes_next_midnight(self):
        result = self.analyze(analysis_mode="none")
        texts = {r["feedback_text"] for r in result["top_texts"]}
        self.assertIn("设备连接失败。", texts)
        self.assertIn("希望优化应用运行流畅度。", texts)
        self.assertNotIn("不计入开始前", texts)
        self.assertNotIn("不计入结束后", texts)

    def test_offsets_are_converted_to_utc_before_filter_and_daily_grouping(self):
        self.add_feedback([
            ("g1", "u1", "2026-11-02T07:59:59+08:00", "email", "偏移时区内"),
            ("g2", "u1", "2026-11-01T07:59:59+08:00", "email", "偏移时区外"),
        ])
        result = self.analyze(start_date="2026-11-01", end_date="2026-11-01", analysis_mode="none")
        self.assertEqual(result["summary"]["total_feedback"], 3)
        self.assertEqual(result["daily_trend"][0]["feedback_count"], 3)

    def test_datetime_parameters_use_utc(self):
        result = self.analyze(start_date=datetime(2026, 11, 2, 1, tzinfo=timezone(timedelta(hours=8))),
                              end_date="2026-11-01", analysis_mode="none")
        self.assertEqual(result["start_date"], "2026-11-01")

    def test_submission_channel_groups(self):
        result = self.analyze(breakdown_by="channel")
        groups = {r["group_value"]: r for r in result["breakdown"]}
        self.assertEqual({k: v["summary"]["total_feedback"] for k, v in groups.items()},
                         {"in_app": 1, "email": 2, "survey": 2})
        self.assertEqual(groups["survey"]["summary"]["invalid_input"], 1)

    def test_acquisition_channel_groups_are_user_attributes(self):
        result = self.analyze(breakdown_by="acquisition_channel")
        groups = {r["group_value"]: r["summary"]["total_feedback"] for r in result["breakdown"]}
        self.assertEqual(groups, {"organic": 2, "social": 2, "referral": 1})
        self.assertNotIn("email", groups)

    def test_current_plan_groups_and_group_rate_denominator(self):
        result = self.analyze(breakdown_by="plan_type")
        groups = {r["group_value"]: r for r in result["breakdown"]}
        self.assertEqual(groups["free"]["summary"]["total_feedback"], 3)
        positive = next(r for r in groups["free"]["sentiment_distribution"] if r["name"] == "positive")
        self.assertEqual(positive["rate"], 0.3333)
        self.assertEqual(result["methodology"]["plan_type_basis"], "current_snapshot_not_historical")

    def test_missing_user_is_not_dropped_by_join(self):
        # Separate legacy/corrupt-data fixture: insert without enabling FK checks.
        self.add_feedback([("g1", "missing", "2026-11-01T01:00:00Z", "email", "设备连接失败。")])
        result = self.analyze(breakdown_by="plan_type")
        self.assertEqual(result["summary"]["total_feedback"], 6)
        self.assertTrue(result["warnings"])
        self.assertEqual(result["breakdown"][0]["group_value"], None)

    def test_pagination_over_200_rows_same_timestamp(self):
        self.add_feedback([(f"g{i:04d}", "u1", "2026-11-01T01:00:00Z", "email", "设备连接失败。") for i in range(251)])
        runner = RecordingRunner(self.db)
        result = self.analyze(query_runner=runner, page_size=100)
        self.assertEqual(result["summary"]["total_feedback"], 256)
        self.assertEqual(result["pagination"]["records_read"], 256)
        self.assertEqual(result["pagination"]["pages_read"], 3)
        self.assertTrue(any("feedback_id COLLATE BINARY >" in q for q in runner.queries))

    def test_exact_page_size_multiple_terminates(self):
        result = self.analyze(page_size=1)
        self.assertEqual(result["pagination"]["records_read"], 5)
        self.assertEqual(result["pagination"]["pages_read"], 5)

    def test_quoted_cursor_is_escaped(self):
        self.add_feedback([("q'1", "u1", "2026-11-01T01:00:00Z", "email", "设备连接失败。"),
                           ("q'2", "u1", "2026-11-01T01:00:00Z", "email", "设备连接失败。")])
        self.assertEqual(self.analyze(page_size=1)["summary"]["total_feedback"], 7)

    def test_actual_sql_tool_truncation_is_rejected(self):
        with self.assertRaisesRegex(FeedbackAnalysisError, "truncated"):
            self.analyze(query_runner=SQLQueryTool(self.db, max_rows=2))

    def test_count_query_truncation_is_rejected(self):
        runner = RecordingRunner(self.db)
        original = runner.run
        def truncated(sql):
            result = original(sql)
            result["truncated"] = True
            return result
        runner.run = truncated
        with self.assertRaisesRegex(FeedbackAnalysisError, "truncated"):
            self.analyze(query_runner=runner)

    def test_missing_rows_detected_against_count(self):
        runner = RecordingRunner(self.db)
        original = runner.run
        def incomplete(sql):
            result = original(sql)
            if "SELECT f.feedback_id" in sql:
                result["rows"] = result["rows"][:-1]
                result["row_count"] -= 1
            return result
        runner.run = incomplete
        with self.assertRaisesRegex(FeedbackAnalysisError, "incomplete"):
            self.analyze(query_runner=runner)

    def test_duplicate_page_ids_rejected(self):
        runner = RecordingRunner(self.db)
        original = runner.run
        def duplicate(sql):
            result = original(sql)
            if "SELECT f.feedback_id" in sql:
                result["rows"][1] = result["rows"][0].copy()
            return result
        runner.run = duplicate
        with self.assertRaisesRegex(FeedbackAnalysisError, "repeated IDs"):
            self.analyze(query_runner=runner)

    def test_count_change_during_pagination_is_rejected(self):
        runner = RecordingRunner(self.db)
        original = runner.run
        counter = 0
        def changed(sql):
            nonlocal counter
            result = original(sql)
            if "COUNT(*) AS total_feedback" in sql:
                counter += 1
                if counter == 2:
                    result["rows"][0]["total_feedback"] += 1
            return result
        runner.run = changed
        with self.assertRaisesRegex(FeedbackAnalysisError, "count changed"):
            self.analyze(query_runner=runner)

    def test_classifier_exception_is_not_silently_unknown(self):
        with patch("src.analytics.feedback.classify_feedback_text", side_effect=RuntimeError("classifier down")):
            with self.assertRaisesRegex(FeedbackAnalysisError, "feedback_id=f001") as raised:
                self.analyze()
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)

    def test_invalid_classifier_result_is_rejected(self):
        with patch("src.analytics.feedback.classify_feedback_text", return_value={}):
            with self.assertRaisesRegex(FeedbackAnalysisError, "Classification failed"):
                self.analyze()

    def test_sql_error_propagates(self):
        runner = RecordingRunner(self.db)
        with patch.object(runner, "run", side_effect=SQLQueryError("blocked")):
            with self.assertRaises(SQLQueryError):
                self.analyze(query_runner=runner)

    def test_invalid_parameters(self):
        cases = [
            {"start_date": "2026-02-30"}, {"start_date": "bad"}, {"start_date": None},
            {"start_date": "2026-11-04"}, {"end_date": "2026-11-14"},
            {"start_date": "2026-08-15"}, {"breakdown_by": "region"},
            {"breakdown_by": "channel; DROP TABLE users"}, {"analysis_mode": "llm"},
            {"page_size": 0}, {"page_size": 201}, {"page_size": True},
            {"top_n": 0}, {"top_n": True}, {"data_through_date": "bad"},
            {"data_through_date": "2026-10-31"},
        ]
        for options in cases:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    self.analyze(**options)

    def test_explicit_coverage_for_alternate_dataset(self):
        result = self.analyze(data_start_date="2026-10-01", data_through_date="2026-11-30")
        self.assertEqual(result["coverage"]["data_start_date"], "2026-10-01")

    def test_raw_text_and_database_unchanged_and_connections_readonly(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        connect = sqlite3.connect
        with patch("src.tools.sql_query_tool.sqlite3.connect", wraps=connect) as spy:
            result = self.analyze()
        self.assertTrue(all("mode=ro" in call.args[0] and call.kwargs["uri"] for call in spy.call_args_list))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertIn(" \t ", [r["feedback_text"] for r in result["top_texts"]])

    def test_top_n_and_result_json(self):
        result = self.analyze(top_n=2)
        self.assertEqual(len(result["top_texts"]), 2)
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)


if __name__ == "__main__":
    unittest.main()
