"""Condense the filtered daily price panel into the monthly universe (firm-variable checks, baseline panel).

Reads only the needed columns of the filtered daily panel batch by batch and writes
monthly_universe_<p>.parquet next to it: DSCD | Date (month end) | n_days | last_day | MarketCAP | Close |
MTBV | ReturnIndex | first_price_month | last_price_month | delisting_date | size_group (1 = smallest).
Month-end values; ReturnIndex includes the delisting return of the filter script.

US: input US_data_panel_filtered_<p>.feather; size_group with NYSE breakpoints (if available).
EU: input EU_data_panel_filtered_<p>/<COUNTRY>.feather (from scripts/22_filter_prices_eu.py), one country at a time.
    Additional columns: Country, MarketCAP_EUR, ReturnIndex_EUR, size_group_country.
    size_group: quintiles of MarketCAP_EUR across all European stocks of the month;
    size_group_country: quintiles of MarketCAP_EUR within the country (EUR, because a country can contain
    lines quoted in a legacy currency and in euro).

    uv run python scripts/40_build_monthly_universe.py                      # US
    uv run python scripts/40_build_monthly_universe.py --region EU
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from datastream.firm_evaluation import UNIVERSE_VALUE_COLUMNS, build_monthly_universe, size_groups

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)

EU_VALUE_COLUMNS = UNIVERSE_VALUE_COLUMNS + ["MarketCAP_EUR", "ReturnIndex_EUR"]


def eur_size_groups(uni: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Quintiles of MarketCAP_EUR: across Europe and within each country (1 = smallest)."""
    tmp = uni[["DSCD", "Date", "Country", "MarketCAP_EUR"]].rename(columns={"MarketCAP_EUR": "MarketCAP"})
    europe = size_groups(tmp, None, breakpoints="all")
    country = pd.concat([size_groups(d, None, breakpoints="all") for _, d in tmp.groupby("Country")])
    return europe, country.reindex(uni.index)


def build_eu(data: Path, penny: str) -> pd.DataFrame:
    folder = data / f"EU_data_panel_filtered_{penny}"
    statics = data / f"statics_filtered_{penny}.csv"
    files = sorted(folder.glob("*.feather"))
    if not files:
        raise SystemExit(f"No country files in {folder}. Run scripts/22_filter_prices_eu.py first.")
    parts = []
    for f in files:
        logging.info(f"Reading {f.name}")
        u = build_monthly_universe(f, statics, value_columns=EU_VALUE_COLUMNS, breakpoints="all")
        u.insert(2, "Country", f.stem)
        parts.append(u.drop(columns="size_group"))
    uni = pd.concat(parts, ignore_index=True)
    uni["size_group"], uni["size_group_country"] = eur_size_groups(uni)
    return uni


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="US", choices=["US", "EU"])
    ap.add_argument("--data-path", default=None, help="Default: D:/Datastream/PriceData/<region>/processed")
    ap.add_argument("--penny", default="0.25", help="Penny percentile used in the filter script (part of the file name)")
    ap.add_argument("--breakpoints", default="nyse", choices=["nyse", "all"], help="US only")
    args = ap.parse_args()

    data = Path(args.data_path or f"D:/Datastream/PriceData/{args.region}/processed")
    out = data / f"monthly_universe_{args.penny}.parquet"

    if args.region == "EU":
        uni = build_eu(data, args.penny)
    else:
        panel = data / f"US_data_panel_filtered_{args.penny}.feather"
        statics = data / f"statics_filtered_{args.penny}.csv"
        logging.info(f"Reading {panel}")
        uni = build_monthly_universe(panel, statics, breakpoints=args.breakpoints)

    uni.to_parquet(out, index=False)
    logging.info(f"{len(uni):,} stock-months, {uni['DSCD'].nunique():,} stocks, "
                 f"{uni['Date'].min():%Y-%m} to {uni['Date'].max():%Y-%m} -> {out}")


if __name__ == "__main__":
    main()
