"""Run feedback trends against the current worktree's read-only database."""
from __future__ import annotations

import argparse
import json

from src.analytics.feedback import analyze_feedback_trends, _configured_coverage


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze feedback counts, topics and sentiment.")
    parser.add_argument("start_date", nargs="?", help="Inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("end_date", nargs="?", help="Inclusive UTC date, YYYY-MM-DD")
    parser.add_argument("--breakdown-by", choices=["channel", "acquisition_channel", "plan_type"])
    parser.add_argument("--analysis-mode", choices=["rules", "none"], default="rules")
    parser.add_argument("--data-start-date")
    parser.add_argument("--data-through-date")
    parser.add_argument("--top-n", type=int, default=10)
    args = parser.parse_args()
    start, end = _configured_coverage()
    result = analyze_feedback_trends(
        args.start_date or start.isoformat(), args.end_date or end.isoformat(),
        breakdown_by=args.breakdown_by, analysis_mode=args.analysis_mode,
        data_through_date=args.data_through_date, data_start_date=args.data_start_date,
        top_n=args.top_n,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
