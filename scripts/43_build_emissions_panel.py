"""Monthly point-in-time emissions panel on the filtered price universe (sustainable-finance module).

Emission values (Refinitiv ESG: ENERDP023/024/025/123) are dated by their fiscal year end (monthly WC05350,
fallback report month + 11) and used from fiscal year end + --lag months (default 12) for at most --max-age
months after fiscal year end (default 24). Adds reported/estimated totals, an estimate flag, emission
intensities (per million of sales of the same fiscal year) and emission growth. See src/datastream/emissions_panel.py.

The output keeps prices, market cap and returns of the universe, so it is the price-emissions merge; join the
baseline panel on (DSCD, Date) if fundamentals are needed.

Output: <firm root>/Paneldata/emissions/<name>.parquet (+ .json, _coverage_by_year.csv, _stock_counts.csv)

    uv run python scripts/43_build_emissions_panel.py                         # emissions_lag12_0.25
    uv run python scripts/43_build_emissions_panel.py --lag 6 --name emissions_lag6_0.25
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from datastream.emissions_panel import EmissionsConfig, build_emissions_panel, write_emissions_panel
from datastream.preprocessing.firm_data import resolve_root

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None)
    ap.add_argument("--price-path", default=None, help="Default: D:/Datastream/PriceData/<region>/processed")
    ap.add_argument("--penny", default="0.25")
    ap.add_argument("--lag", type=int, default=12, help="months after fiscal year end (default 12)")
    ap.add_argument("--max-age", type=int, default=24, help="months after fiscal year end (default 24)")
    ap.add_argument("--name", default=None, help="Default: emissions_lag<lag>_<penny>")
    args = ap.parse_args()

    root = resolve_root(args.root, args.region)
    price_path = Path(args.price_path or f"D:/Datastream/PriceData/{args.region}/processed")
    uni = pd.read_parquet(price_path / f"monthly_universe_{args.penny}.parquet")
    uni["DSCD"] = uni["DSCD"].astype(str)
    cfg = EmissionsConfig(lag_months=args.lag, max_age_months=args.max_age)
    logging.info(f"Emissions panel: lag {cfg.lag_months}, max age {cfg.max_age_months} months after fiscal year end")
    panel, info = build_emissions_panel(root, uni, cfg)
    name = args.name or f"emissions_lag{args.lag}_{args.penny}"
    path = write_emissions_panel(root, name, panel, info)
    meta = info["meta"]
    logging.info(f"{meta['n_rows']:,} stock-months, {meta['n_stocks']:,} stocks, {meta['first_month']} to "
                 f"{meta['last_month']} -> {path}")
    logging.info(f"Fiscal year end: {meta['fiscal_year_end']}")
    logging.info(f"Cleaning: {meta['cleaning']}")
    logging.info(f"With emissions: {meta['share_with_emissions']:.1%} of stock-months; estimated only: "
                 f"{meta['share_estimated_where_available']:.1%}; intensity available: "
                 f"{meta['share_intensity_where_emissions']:.1%}; median age {meta['median_em_age_months']:.0f} months")
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.float_format", "{:,.3f}".format):
        print("\nStocks with prices and emissions (from 2002):")
        print(info["stock_counts"].to_string())
        print("\nCoverage, December cross-sections:")
        print(info["coverage_by_year"].loc[2000:].to_string())


if __name__ == "__main__":
    main()
