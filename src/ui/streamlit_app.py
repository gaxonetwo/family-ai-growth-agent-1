"""Streamlit entry implementation for the Family AI Analytics dashboard."""
from __future__ import annotations

from datetime import timedelta
from html import escape
from typing import Any

import streamlit as st

from src.agent.tool_executor import ToolExecutor
from src.agent.tool_registry import create_default_tool_registry
from src.analytics.activity import _read_data_coverage
from src.ui.activity_dashboard import (
    ActivityDashboardError,
    build_activity_view_model,
    run_activity_analysis,
)


RESULT_STATE_KEY = "activity_analysis_result"
PARAMETERS_STATE_KEY = "activity_analysis_parameters"


def _inject_style() -> None:
    st.markdown(
        """
        <style>
        .stApp { background: #f5f7fb; }
        [data-testid="stSidebar"] { background: #ffffff; border-right: 1px solid #e7ebf2; }
        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid #e6ebf2;
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 2px 10px rgba(20, 45, 90, 0.04);
        }
        [data-testid="stMetricValue"] { color: #1769e0; }
        .dashboard-card {
            background: #ffffff;
            border: 1px solid #e6ebf2;
            border-radius: 12px;
            padding: 18px 20px;
            margin: 8px 0 18px 0;
        }
        .page-kicker { color: #1769e0; font-weight: 700; letter-spacing: .04em; }
        .muted { color: #667085; }
        .chart-card {
            background: #ffffff; border: 1px solid #e6ebf2; border-radius: 12px;
            padding: 12px 16px 8px; margin-bottom: 20px;
        }
        .chart-card svg { width: 100%; height: auto; min-height: 240px; }
        .chart-legend { display: flex; gap: 18px; color: #5e6878; font-size: 13px; }
        .chart-legend span { display: inline-flex; align-items: center; gap: 6px; }
        .chart-legend i { width: 18px; height: 3px; border-radius: 2px; display: inline-block; }
        .table-wrap { overflow-x: auto; }
        .metric-table { width: 100%; border-collapse: collapse; background: #ffffff; }
        .metric-table th, .metric-table td {
            padding: 10px 12px; border-bottom: 1px solid #edf0f5; text-align: right;
        }
        .metric-table th { background: #f8faff; color: #445064; font-weight: 600; }
        .metric-table th:first-child, .metric-table td:first-child { text-align: left; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _format_users(value: Any) -> str:
    return "不可用" if value is None else f"{int(value):,}"


def _format_number(value: Any) -> str:
    if value is None:
        return "不可用"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{int(value):,}"


def _line_chart_html(
    rows: list[dict[str, Any]],
    series: tuple[tuple[str, str, str], ...],
) -> str | None:
    """Build a dependency-free SVG line chart for non-negative user counts."""
    available = [
        float(row[key])
        for row in rows
        for key, _, _ in series
        if row.get(key) is not None
    ]
    if not rows or not available:
        return None

    width, height = 900, 300
    left, right, top, bottom = 52, 18, 22, 42
    plot_width = width - left - right
    plot_height = height - top - bottom
    maximum = max(available) or 1.0

    def point(index: int, value: float) -> tuple[float, float]:
        x = left + plot_width * index / max(len(rows) - 1, 1)
        y = top + plot_height * (1 - value / maximum)
        return x, y

    chart_parts = [
        '<div class="chart-card">',
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="活跃趋势折线图">',
    ]
    for tick in range(5):
        tick_value = maximum * (4 - tick) / 4
        y = top + plot_height * tick / 4
        chart_parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" '
            'stroke="#e8edf5" stroke-width="1" />'
        )
        chart_parts.append(
            f'<text x="{left-8}" y="{y+4:.1f}" text-anchor="end" '
            f'fill="#7b8494" font-size="11">{tick_value:,.0f}</text>'
        )

    for key, _, color in series:
        segments: list[list[str]] = []
        current: list[str] = []
        for index, row in enumerate(rows):
            value = row.get(key)
            if value is None:
                if current:
                    segments.append(current)
                    current = []
                continue
            x, y = point(index, float(value))
            current.append(f"{x:.1f},{y:.1f}")
        if current:
            segments.append(current)
        for segment in segments:
            points = " ".join(segment)
            chart_parts.append(
                f'<polyline points="{points}" fill="none" stroke="{color}" '
                'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />'
            )

    first_date = escape(str(rows[0].get("date", "")))
    last_date = escape(str(rows[-1].get("date", "")))
    chart_parts.extend(
        [
            f'<text x="{left}" y="{height-12}" fill="#7b8494" font-size="12">{first_date}</text>',
            f'<text x="{width-right}" y="{height-12}" text-anchor="end" fill="#7b8494" '
            f'font-size="12">{last_date}</text>',
            "</svg>",
            '<div class="chart-legend">',
            *[
                f'<span><i style="background:{color}"></i>{escape(label)}</span>'
                for _, label, color in series
            ],
            "</div></div>",
        ]
    )
    return "".join(chart_parts)


def _detail_table_html(rows: list[dict[str, Any]]) -> str:
    def display(value: Any) -> str:
        return "—" if value is None else escape(str(value))

    body = "".join(
        "<tr>"
        f"<td>{display(row.get('date'))}</td>"
        f"<td>{display(row.get('dau'))}</td>"
        f"<td>{display(row.get('wau'))}</td>"
        f"<td>{display(row.get('mau'))}</td>"
        f"<td>{display(row.get('event_records'))}</td>"
        "</tr>"
        for row in rows
    )
    return (
        '<div class="table-wrap"><table class="metric-table">'
        "<thead><tr><th>日期</th><th>DAU</th><th>WAU</th><th>MAU</th>"
        "<th>有效事件记录</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def _render_activity_result(view: dict[str, Any]) -> None:
    st.caption(
        f"统计区间：{view['start_date']} 至 {view['end_date']}（UTC，含首尾日期） · "
        f"指标基准日：{view['as_of_date']}"
    )

    kpi_columns = st.columns(3)
    kpi_columns[0].metric("DAU · 日活跃用户", _format_users(view["kpis"]["dau"]))
    kpi_columns[1].metric("WAU · 近7日活跃用户", _format_users(view["kpis"]["wau"]))
    kpi_columns[2].metric("MAU · 近30日活跃用户", _format_users(view["kpis"]["mau"]))

    if view["is_empty"]:
        st.info("所选完整数据区间内没有符合口径的有效使用事件。")

    st.subheader("每日 DAU 趋势")
    dau_chart = _line_chart_html(
        view["daily_dau"], (("dau", "DAU", "#1769e0"),)
    )
    if dau_chart is None:
        st.info("暂无每日 DAU 趋势数据。")
    else:
        st.markdown(dau_chart, unsafe_allow_html=True)

    st.subheader("WAU / MAU 滚动趋势")
    rolling_chart = _line_chart_html(
        view["rolling_activity"],
        (("wau", "WAU", "#1769e0"), ("mau", "MAU", "#72a7f7")),
    )
    if rolling_chart is None:
        st.info("当前数据覆盖不足以形成完整的 WAU 或 MAU 窗口。缺失值不会显示为零。")
    else:
        st.markdown(rolling_chart, unsafe_allow_html=True)

    with st.expander("查看每日指标明细", expanded=False):
        st.markdown(_detail_table_html(view["time_series"]), unsafe_allow_html=True)

    st.subheader("关键统计摘要")
    summary = view["summary"]
    summary_columns = st.columns(4)
    summary_columns[0].metric("区间独立活跃用户", _format_number(summary.get("unique_active_users")))
    summary_columns[1].metric("有效事件记录", _format_number(summary.get("event_records")))
    summary_columns[2].metric("平均 DAU", _format_number(summary.get("average_dau")))
    peak_dau = summary.get("peak_dau") or {}
    summary_columns[3].metric(
        "峰值 DAU",
        _format_number(peak_dau.get("users")),
        help=f"峰值日期：{peak_dau.get('date', '不可用')}",
    )

    with st.expander("数据口径及限制", expanded=False):
        methodology = view["methodology"]
        coverage = view["coverage"]
        st.markdown(
            f"""
            - **有效使用事件**：{', '.join(methodology.get('valid_events', []))}
            - **DAU**：{methodology.get('dau', '未提供')}
            - **WAU**：{methodology.get('wau', '未提供')}
            - **MAU**：{methodology.get('mau', '未提供')}
            - **事件记录数**：{methodology.get('event_records', '未提供')}
            - **完整数据覆盖**：{coverage.get('data_start_date')} 至 {coverage.get('data_through_date')}（UTC）
            - WAU 从 {coverage.get('wau_available_from')} 起可用；MAU 从 {coverage.get('mau_available_from')} 起可用。
            - 当前页面展示的是独立用户指标；事件记录数不能当作活跃用户数。
            - 首尾变化只能描述端点差异，不能证明持续趋势或因果关系。
            """
        )


def main() -> None:
    st.set_page_config(
        page_title="Family AI Analytics",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_style()

    with st.sidebar:
        st.markdown("## Family AI Analytics")
        st.caption("运营分析中心")
        selected_page = st.radio("导航", ["用户活跃分析"], label_visibility="collapsed")
        st.divider()
        st.caption("当前版本：本地只读分析")

    if selected_page != "用户活跃分析":
        return

    st.markdown('<div class="page-kicker">FAMILY AI · BUSINESS INTELLIGENCE</div>', unsafe_allow_html=True)
    st.title("用户活跃分析")
    st.markdown(
        '<p class="muted">查看有效使用事件产生的 DAU、滚动 WAU、滚动 MAU 及其变化。</p>',
        unsafe_allow_html=True,
    )

    coverage_start, coverage_end = _read_data_coverage()
    default_start = max(coverage_start, coverage_end - timedelta(days=29))

    with st.container(border=True):
        filter_columns = st.columns([1, 1, 1.2])
        start_date = filter_columns[0].date_input(
            "开始日期",
            value=default_start,
            min_value=coverage_start,
            max_value=coverage_end,
        )
        end_date = filter_columns[1].date_input(
            "结束日期",
            value=coverage_end,
            min_value=coverage_start,
            max_value=coverage_end,
        )
        filter_columns[2].caption(
            f"完整数据范围：{coverage_start.isoformat()} 至 {coverage_end.isoformat()}（UTC）"
        )
        analyze_clicked = st.button("开始分析", type="primary", use_container_width=False)

    if analyze_clicked:
        try:
            if start_date > end_date:
                raise ActivityDashboardError("开始日期不能晚于结束日期。")
            registry = create_default_tool_registry()
            executor = ToolExecutor(registry)
            with st.spinner("正在读取只读业务数据并计算活跃指标……"):
                result = run_activity_analysis(executor, start_date, end_date)
            st.session_state[RESULT_STATE_KEY] = result
            st.session_state[PARAMETERS_STATE_KEY] = {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }
        except ActivityDashboardError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"页面无法完成本次分析：{exc}")

    cached_result = st.session_state.get(RESULT_STATE_KEY)
    if cached_result is None:
        st.info("请选择日期范围并点击“开始分析”。页面刷新不会自动查询数据库。")
        return

    cached_parameters = st.session_state.get(PARAMETERS_STATE_KEY, {})
    if cached_parameters != {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }:
        st.warning("日期选择已变化；当前仍展示上一次分析结果。点击“开始分析”后更新。")

    try:
        view = build_activity_view_model(cached_result)
    except ActivityDashboardError as exc:
        st.error(str(exc))
        return
    _render_activity_result(view)


if __name__ == "__main__":
    main()

