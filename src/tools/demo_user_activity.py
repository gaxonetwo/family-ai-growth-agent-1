"""Run DAU, WAU, and MAU analysis against the configured database."""
from __future__ import annotations

import argparse
from pprint import pprint

from src.analytics.activity import analyze_user_activity, _read_data_coverage


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze DAU, WAU, and MAU.")
    parser.add_argument("start_date", nargs="?", help="Inclusive date, YYYY-MM-DD")
    parser.add_argument("end_date", nargs="?", help="Inclusive date, YYYY-MM-DD")
    parser.add_argument("--data-start-date", help="Complete data start date")
    parser.add_argument("--data-through-date", help="Complete data end date")
    args = parser.parse_args()
    coverage_start, coverage_end = _read_data_coverage()

    result = analyze_user_activity(
        args.start_date or coverage_end.isoformat(),
        args.end_date or coverage_end.isoformat(),
        args.data_start_date,
        args.data_through_date,
    )
    pprint(result, sort_dicts=False)
    for field in ("acquisition_channel", "plan_type"):
        grouped = analyze_user_activity(
            args.start_date or coverage_end.isoformat(),
            args.end_date or coverage_end.isoformat(),
            args.data_start_date, args.data_through_date, breakdown_by=field,
        )
        pprint({field: grouped["breakdown"]}, sort_dicts=False)


if __name__ == "__main__":
    main()

