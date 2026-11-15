"""Activation tests using an isolated temporary SQLite database."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.analytics.activation import analyze_activation_funnel
from src.tools.sql_query_tool import SQLQueryTool


SCHEMA = """
CREATE TABLE users (user_id TEXT PRIMARY KEY, signup_at TEXT NOT NULL,
 acquisition_channel TEXT NOT NULL, region TEXT NOT NULL, plan_type TEXT NOT NULL);
CREATE TABLE devices (device_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
 device_type TEXT NOT NULL, bound_at TEXT NOT NULL, current_status TEXT NOT NULL,
 last_seen_at TEXT NOT NULL, firmware_version TEXT NOT NULL);
CREATE TABLE events (event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
 device_id TEXT, event_time TEXT NOT NULL, event_name TEXT NOT NULL,
 feature_name TEXT, session_id TEXT);
"""


class ActivationFunnelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "activation_test.db"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA)
        users = [
            ("complete", "2026-11-01T09:00:00+00:00"),
            ("no_device", "2026-11-01T09:00:00+00:00"),
            ("no_onboarding", "2026-11-01T09:00:00+00:00"),
            ("no_ai", "2026-11-01T09:00:00+00:00"),
            ("late_signup", "2026-11-10T09:00:00+00:00"),
        ]
        conn.executemany(
            "INSERT INTO users VALUES (?, ?, 'organic', 'east', 'free')", users
        )
        devices = [
            ("d1", "complete", "2026-11-02T09:00:00+00:00"),
            ("d2", "no_onboarding", "2026-11-02T09:00:00+00:00"),
            ("d3", "no_ai", "2026-11-02T09:00:00+00:00"),
            ("d4", "late_signup", "2026-11-11T09:00:00+00:00"),
        ]
        conn.executemany(
            "INSERT INTO devices VALUES (?, ?, 'family_hub', ?, 'online', ?, '1.0')",
            [(device, user, bound, bound) for device, user, bound in devices],
        )
        events = [
            ("e1", "complete", "onboarding_complete", "2026-11-03T09:00:00+00:00"),
            ("e2", "complete", "ai_interaction", "2026-11-04T09:00:00+00:00"),
            # The duplicate event must not count the same user twice.
            ("e3", "complete", "ai_interaction", "2026-11-05T09:00:00+00:00"),
            ("e4", "no_ai", "onboarding_complete", "2026-11-03T09:00:00+00:00"),
            ("e5", "late_signup", "onboarding_complete", "2026-11-11T09:00:00+00:00"),
            ("e6", "late_signup", "ai_interaction", "2026-11-12T09:00:00+00:00"),
        ]
        conn.executemany(
            "INSERT INTO events VALUES (?, ?, NULL, ?, ?, NULL, NULL)",
            [(event, user, event_time, name) for event, user, name, event_time in events],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def analyze(self, start: str = "2026-11-01", end: str = "2026-11-10"):
        return analyze_activation_funnel(
            start, end, 7, db_path=self.db_path, dataset_end_date="2026-11-13"
        )

    def test_normal_and_incomplete_user_paths(self) -> None:
        result = self.analyze()
        self.assertEqual(result["counts"], {
            "registered_users": 4,
            "device_bound_users": 3,
            "onboarding_completed_users": 2,
            "first_ai_interaction_users": 1,
        })
        self.assertEqual(result["drop_off_counts"], {
            "registration_to_device_bound": 1,
            "device_bound_to_onboarding_completed": 1,
            "onboarding_completed_to_first_ai_interaction": 1,
        })
        self.assertEqual(result["conversion_rates"], {
            "registration_to_device_bound": 0.75,
            "device_bound_to_onboarding_completed": 0.6667,
            "onboarding_completed_to_first_ai_interaction": 0.5,
        })
        self.assertEqual(
            result["activation"], {"activated_users": 1, "activation_rate": 0.25}
        )

    def test_event_at_observation_end_does_not_count(self) -> None:
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO events VALUES ('late_ai', 'no_ai', NULL, "
            "'2026-11-08T09:00:00+00:00', 'ai_interaction', NULL, NULL)"
        )
        conn.commit()
        conn.close()
        result = self.analyze("2026-11-01", "2026-11-01")
        self.assertEqual(result["activation"]["activated_users"], 1)

    def test_empty_cohort_returns_zero_metrics(self) -> None:
        result = self.analyze("2026-10-01", "2026-10-02")
        self.assertEqual(result["counts"]["registered_users"], 0)
        self.assertTrue(all(value == 0.0 for value in result["conversion_rates"].values()))
        self.assertEqual(
            result["activation"], {"activated_users": 0, "activation_rate": 0.0}
        )

    def _set_complete_path(self, bound, onboarding, ai_times):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM events WHERE user_id = 'complete'")
            conn.execute("UPDATE devices SET bound_at = ? WHERE user_id = 'complete'", (bound,))
            for index, event_time in enumerate(onboarding):
                conn.execute("INSERT INTO events VALUES (?, 'complete', NULL, ?, 'onboarding_complete', NULL, NULL)", (f'o{index}', event_time))
            for index, event_time in enumerate(ai_times):
                conn.execute("INSERT INTO events VALUES (?, 'complete', NULL, ?, 'ai_interaction', NULL, NULL)", (f'a{index}', event_time))
        conn.close()

    def test_ai_before_binding_is_not_activated(self):
        self._set_complete_path('2026-11-03T09:00:00Z', ['2026-11-04T09:00:00Z'], ['2026-11-02T09:00:00Z'])
        self.assertEqual(self.analyze()['activation']['activated_users'], 0)

    def test_onboarding_before_binding_is_not_activated(self):
        self._set_complete_path('2026-11-03T09:00:00Z', ['2026-11-02T09:00:00Z'], ['2026-11-04T09:00:00Z'])
        result = self.analyze()
        self.assertEqual(result['activation']['activated_users'], 0)
        self.assertEqual(result['counts']['onboarding_completed_users'], 1)

    def test_ai_before_onboarding_is_not_activated(self):
        self._set_complete_path('2026-11-02T09:00:00Z', ['2026-11-04T09:00:00Z'], ['2026-11-03T09:00:00Z'])
        self.assertEqual(self.analyze()['activation']['activated_users'], 0)

    def test_correct_sequence_is_activated(self):
        self.assertEqual(self.analyze()['activation']['activated_users'], 1)

    def test_later_events_can_complete_valid_sequence(self):
        self._set_complete_path('2026-11-02T09:00:00Z', ['2026-11-01T10:00:00Z', '2026-11-03T09:00:00Z', '2026-11-03T09:00:00Z'], ['2026-11-01T11:00:00Z', '2026-11-04T09:00:00Z', '2026-11-05T09:00:00Z'])
        self.assertEqual(self.analyze()['activation']['activated_users'], 1)

    def test_binding_outside_window_is_not_activated(self):
        for bound in ('2026-10-31T09:00:00Z', '2026-11-08T09:00:00Z'):
            with self.subTest(bound=bound):
                self._set_complete_path(bound, ['2026-11-03T09:00:00Z'], ['2026-11-04T09:00:00Z'])
                self.assertEqual(self.analyze()['activation']['activated_users'], 0)

    def test_equal_event_times_are_allowed(self):
        self._set_complete_path('2026-11-01T09:00:00Z', ['2026-11-01T09:00:00Z'], ['2026-11-01T09:00:00Z'])
        self.assertEqual(self.analyze()['activation']['activated_users'], 1)

    def test_invalid_parameters_raise_value_error(self) -> None:
        invalid_inputs = [
            ("2026-11-02", "2026-11-01", 7),
            ("not-a-date", "2026-11-01", 7),
            ("2026-11-01", "2026-11-01", 0),
            ("2026-11-01", "2026-11-01", True),
        ]
        for start, end, days in invalid_inputs:
            with self.subTest(start=start, end=end, days=days):
                with self.assertRaises(ValueError):
                    analyze_activation_funnel(
                        start, end, days, db_path=self.db_path,
                        dataset_end_date="2026-11-13"
                    )

    def test_uses_existing_sql_query_tool(self) -> None:
        result = analyze_activation_funnel(
            "2026-11-01", "2026-11-01", 7,
            query_runner=SQLQueryTool(self.db_path), dataset_end_date="2026-11-13"
        )
        self.assertEqual(result["activation"]["activated_users"], 1)


if __name__ == "__main__":
    unittest.main()
