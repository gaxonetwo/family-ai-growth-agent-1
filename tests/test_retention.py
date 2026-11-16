"""Retention tests using independent temporary SQLite data."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.analytics.retention import analyze_retention
from src.tools.sql_query_tool import SQLQueryTool


SCHEMA = """
CREATE TABLE users (
 user_id TEXT PRIMARY KEY, signup_at TEXT NOT NULL,
 acquisition_channel TEXT NOT NULL, region TEXT NOT NULL, plan_type TEXT NOT NULL
);
CREATE TABLE events (
 event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT,
 event_time TEXT NOT NULL, event_name TEXT NOT NULL,
 feature_name TEXT, session_id TEXT
);
"""


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "retention test.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, ?, 'east', ?)",
            [
                ("u1", "2026-11-01T09:00:00+00:00", "organic", "free"),
                ("u2", "2026-11-01T15:00:00+00:00", "organic", "premium"),
                ("u3", "2026-11-02T08:00:00+00:00", "social", "free"),
                ("u4", "2026-11-10T10:00:00+00:00", "paid_ads", "free"),
            ],
        )
        conn.executemany(
            "INSERT INTO events VALUES (?, ?, NULL, ?, ?, NULL, NULL)",
            [
                # Registration-day behavior never counts as D1.
                ("e1", "u1", "2026-11-01T10:00:00+00:00", "app_open"),
                ("e2", "u1", "2026-11-02T09:00:00+00:00", "app_open"),
                # Duplicate valid behavior still counts u1 only once on D1.
                ("e3", "u1", "2026-11-02T10:00:00+00:00", "ai_interaction"),
                ("e4", "u1", "2026-11-08T09:00:00+00:00", "ai_interaction"),
                ("e5", "u1", "2026-12-01T09:00:00+00:00", "app_open"),
                ("e6", "u2", "2026-11-02T16:00:00+00:00", "ai_interaction"),
                # Device/system behavior is not a valid retention event.
                ("e7", "u2", "2026-12-01T16:00:00+00:00", "device_online"),
                ("e8", "u3", "2026-11-03T08:00:00+00:00", "app_open"),
                ("e9", "u3", "2026-11-09T08:00:00+00:00", "app_open"),
                ("e10", "u3", "2026-12-02T08:00:00+00:00", "ai_interaction"),
                ("e11", "u4", "2026-11-11T10:00:00+00:00", "app_open"),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def analyze(self, **kwargs):
        return analyze_retention(
            "2026-11-01",
            "2026-11-10",
            data_through_date="2026-12-02",
            db_path=self.db_path,
            **kwargs,
        )

    def test_overall_exact_day_retention_and_maturity(self) -> None:
        result = self.analyze()
        self.assertTrue(result["ok"])
        self.assertEqual(result["registered_users"], 4)
        self.assertEqual(result["eligible_users"], 3)
        self.assertEqual(result["excluded_users"], 1)
        self.assertEqual(result["metrics"], {
            "D1": {"retained_users": 3, "retention_rate": 1.0},
            "D7": {"retained_users": 2, "retention_rate": 0.6667},
            "D30": {"retained_users": 2, "retention_rate": 0.6667},
        })
        self.assertEqual(result["retention_changes"], [
            {
                "from_day": "D1", "to_day": "D7",
                "retained_user_drop": 1, "retention_rate_drop": 0.3333,
            },
            {
                "from_day": "D7", "to_day": "D30",
                "retained_user_drop": 0, "retention_rate_drop": 0.0,
            },
        ])

    def test_channel_breakdown_uses_same_mature_sample(self) -> None:
        result = self.analyze(breakdown_by="acquisition_channel")
        groups = {row["group_value"]: row for row in result["breakdown"]}
        self.assertEqual(groups["organic"]["eligible_users"], 2)
        self.assertEqual(groups["organic"]["metrics"]["D7"]["retention_rate"], 0.5)
        self.assertEqual(groups["social"]["metrics"]["D30"]["retention_rate"], 1.0)
        self.assertEqual(groups["paid_ads"]["eligible_users"], 0)
        self.assertEqual(groups["paid_ads"]["metrics"]["D1"]["retention_rate"], 0.0)

    def test_plan_breakdown(self) -> None:
        result = self.analyze(breakdown_by="plan_type")
        groups = {row["group_value"]: row for row in result["breakdown"]}
        self.assertEqual(groups["premium"]["metrics"]["D1"]["retained_users"], 1)
        self.assertEqual(groups["premium"]["metrics"]["D7"]["retained_users"], 0)

    def test_requested_max_day_controls_maturity(self) -> None:
        result = self.analyze(retention_days=[1])
        self.assertEqual(result["eligible_users"], 4)
        self.assertEqual(result["metrics"]["D1"]["retained_users"], 4)

    def test_default_data_cutoff_comes_from_project_config(self) -> None:
        result = analyze_retention(
            "2026-11-01", "2026-11-01", [1], db_path=self.db_path
        )
        self.assertEqual(result["data_through_date"], "2026-11-13")

    def test_empty_cohort_has_zero_rates(self) -> None:
        result = analyze_retention(
            "2026-10-01", "2026-10-02", [1, 7], "2026-12-02",
            db_path=self.db_path
        )
        self.assertEqual(result["registered_users"], 0)
        self.assertEqual(result["eligible_users"], 0)
        self.assertEqual(result["metrics"]["D1"]["retention_rate"], 0.0)

    def test_invalid_parameters_are_rejected(self) -> None:
        cases = [
            {"cohort_start": "bad-date", "cohort_end": "2026-11-10"},
            {"cohort_start": "2026-11-11", "cohort_end": "2026-11-10"},
            {"cohort_start": "2026-11-01", "cohort_end": "2026-11-10", "retention_days": []},
            {"cohort_start": "2026-11-01", "cohort_end": "2026-11-10", "retention_days": [1, 1]},
            {"cohort_start": "2026-11-01", "cohort_end": "2026-11-10", "retention_days": [0]},
            {"cohort_start": "2026-11-01", "cohort_end": "2026-11-10", "breakdown_by": "region"},
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    analyze_retention(
                        data_through_date="2026-12-02",
                        db_path=self.db_path,
                        **arguments,
                    )

    def test_existing_sql_query_tool_can_be_injected(self) -> None:
        result = analyze_retention(
            "2026-11-01", "2026-11-10", [1], "2026-12-02",
            query_runner=SQLQueryTool(self.db_path)
        )
        self.assertEqual(result["eligible_users"], 4)


if __name__ == "__main__":
    unittest.main()
