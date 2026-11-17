"""Presentation helpers for the Streamlit user-activity dashboard.

This module deliberately has no Streamlit dependency.  It adapts the stable
ToolExecutor response into a small view model, which keeps UI tests offline
and prevents the dashboard from bypassing the registered tool boundary.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any, Protocol


ACTIVITY_TOOL_NAME = "analyze_user_activity"


class Executor(Protocol):
    """The subset of ToolExecutor used by the dashboard."""

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute one registered tool."""


class ActivityDashboardError(ValueError):
    """Raised when an execution response cannot be displayed safely."""


def run_activity_analysis(
    executor: Executor,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    """Execute the registered activity tool with inclusive UTC dates."""
    if not isinstance(start_date, date) or not isinstance(end_date, date):
        raise ActivityDashboardError("开始日期和结束日期必须是有效日期。")
    if start_date > end_date:
        raise ActivityDashboardError("开始日期不能晚于结束日期。")

    return executor.execute(
        ACTIVITY_TOOL_NAME,
        {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )


def build_activity_view_model(execution_result: object) -> dict[str, Any]:
    """Validate and copy a ToolExecutor result for presentation.

    ``None`` values remain ``None`` so incomplete WAU/MAU windows are never
    displayed as zero activity.  The caller's result is not modified.
    """
    if not isinstance(execution_result, dict):
        raise ActivityDashboardError("工具返回了无法识别的结果。")
    if execution_result.get("ok") is not True:
        error = execution_result.get("error")
        if isinstance(error, dict):
            message = error.get("message") or "用户活跃分析执行失败。"
            code = error.get("code") or "tool_error"
            raise ActivityDashboardError(f"{message}（错误码：{code}）")
        raise ActivityDashboardError("用户活跃分析执行失败。")

    data = execution_result.get("data")
    if not isinstance(data, dict) or data.get("ok") is not True:
        raise ActivityDashboardError("工具未返回有效的活跃分析数据。")

    time_series = data.get("time_series")
    summary = data.get("summary")
    coverage = data.get("coverage")
    methodology = data.get("methodology")
    if not isinstance(time_series, list):
        raise ActivityDashboardError("活跃趋势数据格式无效。")
    if not all(isinstance(row, dict) for row in time_series):
        raise ActivityDashboardError("活跃趋势包含无效记录。")
    if not isinstance(summary, dict):
        raise ActivityDashboardError("活跃摘要数据格式无效。")
    if not isinstance(coverage, dict) or not isinstance(methodology, dict):
        raise ActivityDashboardError("数据覆盖范围或统计口径缺失。")

    required_row_fields = {"date", "dau", "wau", "mau"}
    if any(not required_row_fields.issubset(row) for row in time_series):
        raise ActivityDashboardError("活跃趋势缺少必要指标。")

    copied_series = deepcopy(time_series)
    event_records = summary.get("event_records")
    return {
        "start_date": data.get("start_date"),
        "end_date": data.get("end_date"),
        "as_of_date": data.get("as_of_date"),
        "kpis": {
            "dau": data.get("dau"),
            "wau": data.get("wau"),
            "mau": data.get("mau"),
        },
        "time_series": copied_series,
        "daily_dau": [
            {"date": row["date"], "dau": row["dau"]} for row in copied_series
        ],
        "rolling_activity": [
            {"date": row["date"], "wau": row["wau"], "mau": row["mau"]}
            for row in copied_series
        ],
        "summary": deepcopy(summary),
        "trend_analysis": deepcopy(data.get("trend_analysis") or {}),
        "coverage": deepcopy(coverage),
        "methodology": deepcopy(methodology),
        "is_empty": event_records == 0,
    }

