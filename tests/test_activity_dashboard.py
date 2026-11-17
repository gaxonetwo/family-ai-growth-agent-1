from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

from src.ui.activity_dashboard import (
    ACTIVITY_TOOL_NAME,
    ActivityDashboardError,
    build_activity_view_model,
    run_activity_analysis,
)


def _business_result(*, event_records: int = 12) -> dict:
    return {
        "ok": True,
        "tool_name": ACTIVITY_TOOL_NAME,
        "data": {
            "ok": True,
            "start_date": "2026-11-07",
            "end_date": "2026-11-13",
            "as_of_date": "2026-11-13",
            "dau": 8,
            "wau": 24,
            "mau": 42,
            "time_series": [
                {"date": "2026-11-12", "event_records": 5, "dau": 4, "wau": 20, "mau": None},
                {"date": "2026-11-13", "event_records": 7, "dau": 8, "wau": 24, "mau": 42},
            ],
            "summary": {
                "event_records": event_records,
                "unique_active_users": 9,
                "average_dau": 6.0,
                "average_wau": 22.0,
                "average_mau": 42.0,
                "peak_dau": {"date": "2026-11-13", "users": 8},
            },
            "trend_analysis": {"direction": "up", "limitation": "Endpoint comparison only."},
            "coverage": {
                "data_start_date": "2026-08-16",
                "data_through_date": "2026-11-13",
                "wau_available_from": "2026-08-22",
                "mau_available_from": "2026-09-14",
                "incomplete_windows_are_null": True,
            },
            "methodology": {
                "valid_events": ["app_open", "ai_interaction", "feature_use"],
                "dau": "Distinct daily users",
                "wau": "Distinct rolling 7-day users",
                "mau": "Distinct rolling 30-day users",
                "event_records": "Qualifying event rows",
            },
        },
        "error": None,
        "metadata": {"execution_status": "success"},
    }


class FakeExecutor:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    def execute(self, tool_name: str, arguments: dict) -> dict:
        self.calls.append((tool_name, deepcopy(arguments)))
        return deepcopy(self.result)


class ActivityDashboardTests(unittest.TestCase):
    def test_run_uses_registered_tool_name_and_iso_dates(self) -> None:
        executor = FakeExecutor(_business_result())

        result = run_activity_analysis(
            executor, date(2026, 11, 7), date(2026, 11, 13)
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            executor.calls,
            [
                (
                    "analyze_user_activity",
                    {"start_date": "2026-11-07", "end_date": "2026-11-13"},
                )
            ],
        )

    def test_invalid_date_range_does_not_execute(self) -> None:
        executor = FakeExecutor(_business_result())

        with self.assertRaisesRegex(ActivityDashboardError, "开始日期"):
            run_activity_analysis(
                executor, date(2026, 11, 13), date(2026, 11, 7)
            )

        self.assertEqual(executor.calls, [])

    def test_view_model_preserves_kpis_nulls_and_source_result(self) -> None:
        result = _business_result()
        original = deepcopy(result)

        view = build_activity_view_model(result)

        self.assertEqual(view["kpis"], {"dau": 8, "wau": 24, "mau": 42})
        self.assertIsNone(view["rolling_activity"][0]["mau"])
        self.assertFalse(view["is_empty"])
        self.assertEqual(result, original)

    def test_zero_events_is_an_empty_business_period(self) -> None:
        view = build_activity_view_model(_business_result(event_records=0))
        self.assertTrue(view["is_empty"])

    def test_tool_failure_is_shown_as_dashboard_error(self) -> None:
        result = {
            "ok": False,
            "tool_name": ACTIVITY_TOOL_NAME,
            "data": None,
            "error": {"code": "argument_validation_error", "message": "日期无效"},
            "metadata": {"execution_status": "failed"},
        }

        with self.assertRaisesRegex(
            ActivityDashboardError, "日期无效.*argument_validation_error"
        ):
            build_activity_view_model(result)

    def test_invalid_or_incomplete_payload_is_rejected(self) -> None:
        with self.subTest("not a mapping"):
            with self.assertRaises(ActivityDashboardError):
                build_activity_view_model(None)

        invalid = _business_result()
        del invalid["data"]["time_series"][0]["wau"]
        with self.subTest("missing metric"):
            with self.assertRaisesRegex(ActivityDashboardError, "缺少必要指标"):
                build_activity_view_model(invalid)


class StreamlitActivityPageTests(unittest.TestCase):
    APP_PATH = Path(__file__).resolve().parents[1] / "streamlit_app.py"

    def test_initial_page_waits_for_button_without_business_result(self) -> None:
        app = AppTest.from_file(str(self.APP_PATH)).run(timeout=10)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual([title.value for title in app.title], ["用户活跃分析"])
        self.assertEqual([button.label for button in app.button], ["开始分析"])
        self.assertEqual(len(app.metric), 0)
        self.assertTrue(any("不会自动查询数据库" in item.value for item in app.info))

    def test_cached_fake_result_renders_kpis_charts_and_table(self) -> None:
        app = AppTest.from_file(str(self.APP_PATH))
        app.session_state["activity_analysis_result"] = _business_result()
        app.session_state["activity_analysis_parameters"] = {
            "start_date": "2026-10-15",
            "end_date": "2026-11-13",
        }

        app.run(timeout=10)

        self.assertEqual(len(app.exception), 0)
        self.assertEqual(
            [metric.label for metric in app.metric[:3]],
            ["DAU · 日活跃用户", "WAU · 近7日活跃用户", "MAU · 近30日活跃用户"],
        )
        html = "\n".join(item.value for item in app.markdown)
        self.assertGreaterEqual(html.count('class="chart-card"'), 2)
        self.assertIn('class="metric-table"', html)


if __name__ == "__main__":
    unittest.main()

