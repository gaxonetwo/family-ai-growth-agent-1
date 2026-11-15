"""Run activation funnel analysis against the configured demo database."""
from __future__ import annotations

import argparse
from pprint import pprint

from src.analytics.activation import analyze_activation_funnel


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze the user activation funnel.")
    parser.add_argument("cohort_start", help="Inclusive signup date, YYYY-MM-DD")
    parser.add_argument("cohort_end", help="Inclusive signup date, YYYY-MM-DD")
    parser.add_argument("--observation-days", type=int, default=7)
    args = parser.parse_args()

    result = analyze_activation_funnel(
        args.cohort_start,
        args.cohort_end,
        args.observation_days,
    )
    pprint(result, sort_dicts=False)


if __name__ == "__main__":
    main()

