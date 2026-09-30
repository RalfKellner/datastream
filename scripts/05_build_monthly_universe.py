"""Condense the filtered daily price panel into a small monthly universe for the firm-variable checks.

Reads only the needed columns of US_data_panel_filtered_<p>.feather batch by batch (memory-safe on the
Windows machine) and writes monthly_universe_<p>.parquet next to it:
DSCD | Date (month end) | n_days | last_day | MarketCAP | Close | MTBV | first_price_month | last_price_month |
delisting_date | size_group (1 = smallest, NYSE breakpoints if available).

    uv run python scripts/05_build_monthly_universe.py
    uv run python scripts/05_build_monthly_universe.py --data-path D:/Datastream/PriceData/US/processed --penny 0.2
"""

import argparse
import logging
from pathlib import Path

from datastream.firm_evaluation import build_monthly_universe

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-path", default="D:/Datastream/PriceData/US/processed")
    ap.add_argument("--penny", default="0.2", help="Penny percentile used in 02_filter.py (part of the file name)")
    ap.add_argument("--breakpoints", default="nyse", choices=["nyse", "all"])
    args = ap.parse_args()

    data = Path(args.data_path)
    panel = data / f"US_data_panel_filtered_{args.penny}.feather"
    statics = data / f"statics_filtered_{args.penny}.csv"
    out = data / f"monthly_universe_{args.penny}.parquet"

    logging.info(f"Reading {panel}")
    uni = build_monthly_universe(panel, statics, breakpoints=args.breakpoints)
    uni.to_parquet(out, index=False)
    logging.info(f"{len(uni):,} stock-months, {uni['DSCD'].nunique():,} stocks, "
                 f"{uni['Date'].min():%Y-%m} to {uni['Date'].max():%Y-%m} -> {out}")


if __name__ == "__main__":
    main()
