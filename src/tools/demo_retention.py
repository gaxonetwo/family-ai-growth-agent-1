"""Run exact-day retention analysis against the configured demo database."""
from __future__ import annotations

import argparse
from pprint import pprint

from src.analytics.retention import ALLOWED_BREAKDOWNS, analyze_retention


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze exact-day user retention.")
    parser.add_argument("cohort_start", help="Inclusive signup date, YYYY-MM-DD")
    parser.add_argument("cohort_end", help="Inclusive signup date, YYYY-MM-DD")
    parser.add_argument("--retention-days", nargs="+", type=int, default=[1, 7, 30])
    parser.add_argument("--data-through-date", help="Complete data date, YYYY-MM-DD")
    parser.add_argument("--breakdown-by", choices=sorted(ALLOWED_BREAKDOWNS))
    args = parser.parse_args()

    result = analyze_retention(
        args.cohort_start,
        args.cohort_end,
        args.retention_days,
        args.data_through_date,
        args.breakdown_by,
    )
    pprint(result, sort_dicts=False)


if __name__ == "__main__":
    main()

