"""User activity tests using an isolated temporary SQLite database."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.analytics.activity import analyze_user_activity
from src.tools.sql_query_tool import SQLQueryTool


SCHEMA = """
CREATE TABLE events (
 event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT,
 event_time TEXT NOT NULL, event_name TEXT NOT NULL,
 feature_name TEXT, session_id TEXT
);
"""


class UserActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "activity test.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO events VALUES (?, ?, NULL, ?, ?, NULL, NULL)",
            [
                ("e1", "u1", "2026-01-01T08:00:00+00:00", "app_open"),
                # A second event is a record, but not a second active user.
                ("e2", "u1", "2026-01-01T09:00:00+00:00", "ai_interaction"),
                # Device/system events do not count as activity.
                ("e3", "u2", "2026-01-01T10:00:00+00:00", "device_online"),
                ("e4", "u1", "2026-01-02T08:00:00+00:00", "feature_use"),
                ("e5", "u2", "2026-01-02T09:00:00+00:00", "app_open"),
                ("e6", "u3", "2026-01-07T08:00:00+00:00", "app_open"),
                ("e7", "u1", "2026-01-30T08:00:00+00:00", "app_open"),
                ("e8", "u2", "2026-02-01T08:00:00+00:00", "ai_interaction"),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def analyze(self, start: str = "2026-01-01", end: str = "2026-01-30"):
        return analyze_user_activity(
            start,
            end,
            data_start_date="2026-01-01",
            data_through_date="2026-02-10",
            db_path=self.db_path,
        )

    def test_dau_separates_event_records_from_distinct_users(self) -> None:
        result = self.analyze("2026-01-01", "2026-01-02")
        first, second = result["time_series"]
        self.assertEqual(first["event_records"], 2)
        self.assertEqual(first["dau"], 1)
        self.assertEqual(second["event_records"], 2)
        self.assertEqual(second["dau"], 2)
        self.assertEqual(result["summary"]["event_records"], 4)
        self.assertEqual(result["summary"]["unique_active_users"], 2)

    def test_rolling_wau_and_mau_deduplicate_users(self) -> None:
        result = self.analyze()
        by_date = {row["date"]: row for row in result["time_series"]}
        self.assertEqual(by_date["2026-01-07"]["wau"], 3)
        self.assertEqual(by_date["2026-01-30"]["mau"], 3)
        self.assertEqual(by_date["2026-01-30"]["wau"], 1)

    def test_missing_history_is_null_not_zero(self) -> None:
        result = self.analyze("2026-01-01", "2026-01-07")
        by_date = {row["date"]: row for row in result["time_series"]}
        self.assertIsNone(by_date["2026-01-01"]["wau"])
        self.assertIsNone(by_date["2026-01-07"]["mau"])
        self.assertEqual(by_date["2026-01-07"]["wau"], 3)

    def test_complete_day_without_events_is_zero(self) -> None:
        result = self.analyze("2026-01-04", "2026-01-04")
        row = result["time_series"][0]
        self.assertEqual(row["event_records"], 0)
        self.assertEqual(row["dau"], 0)
        self.assertIsNone(row["wau"])

    def test_complete_empty_windows_are_zero(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM events")
        conn.commit()
        conn.close()
        result = self.analyze("2026-02-10", "2026-02-10")
        row = result["time_series"][0]
        self.assertEqual(row, {
            "date": "2026-02-10", "event_records": 0,
            "dau": 0, "wau": 0, "mau": 0,
        })

    def test_request_outside_coverage_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "complete data range"):
            self.analyze("2025-12-31", "2026-01-02")
        with self.assertRaisesRegex(ValueError, "complete data range"):
            self.analyze("2026-02-10", "2026-02-11")

    def test_invalid_dates_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.analyze("bad-date", "2026-01-02")
        with self.assertRaises(ValueError):
            self.analyze("2026-01-03", "2026-01-02")
        with self.assertRaises(ValueError):
            analyze_user_activity(
                "2026-01-01", "2026-01-02",
                data_start_date="2026-02-01",
                data_through_date="2026-01-01",
                db_path=self.db_path,
            )

    def test_existing_sql_query_tool_can_be_injected(self) -> None:
        result = analyze_user_activity(
            "2026-01-01", "2026-01-02",
            data_start_date="2026-01-01",
            data_through_date="2026-02-10",
            query_runner=SQLQueryTool(self.db_path),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["unique_active_users"], 2)

    def test_project_config_supplies_default_coverage(self) -> None:
        result = analyze_user_activity(
            "2026-11-13", "2026-11-13", db_path=self.db_path
        )
        self.assertEqual(result["coverage"]["data_start_date"], "2026-08-16")
        self.assertEqual(result["coverage"]["data_through_date"], "2026-11-13")

    def test_groups_and_baseline(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY, acquisition_channel TEXT, plan_type TEXT)")
            conn.executemany("INSERT INTO users VALUES (?, ?, ?)", [
                ("u1", "organic", "free"), ("u2", "social", "premium"),
                ("u3", "organic", "free"),
            ])
        conn.close()
        for field in ("acquisition_channel", "plan_type"):
            result = analyze_user_activity(
                "2026-01-01", "2026-01-30", "2026-01-01", "2026-02-10",
                db_path=self.db_path, as_of_date="2026-01-30", breakdown_by=field,
            )
            self.assertEqual(sum(row["mau"] for row in result["breakdown"]), 3)
            self.assertEqual(result["dau"], 1)
            self.assertEqual(result["mau"], 3)

    def test_invalid_group(self) -> None:
        with self.assertRaises(ValueError):
            analyze_user_activity("2026-01-01", "2026-01-02", breakdown_by="bad")


if __name__ == "__main__":
    unittest.main()

