"""Build the baseline monthly panel: filtered price universe + Worldscope variables, point in time.

Output: <firm root>/Paneldata/baseline/<name>.parquet with a JSON sidecar (parameters, cleaning counts, column
names) and <name>_coverage_by_year.csv. See src/datastream/panel_builder.py for the design.

Columns use the readable names from the `name` column of config/firm_variables.csv (e.g. total_assets,
common_equity, total_assets_prev) and for the price data (market_cap, price, mtbv, return_index); the JSON
sidecar maps them back to the Datastream mnemonics. --mnemonics keeps the mnemonics.

Prerequisites: monthly_universe_<p>.parquet from scripts/05_build_monthly_universe.py (rebuild it once: it now
contains ReturnIndex) and the Worldscope variables imported with scripts/03_import_firm_variables.py.

    uv run python scripts/06_build_baseline_panel.py                       # rolling, lag 3, max age 18
    uv run python scripts/06_build_baseline_panel.py --convention ff       # Fama-French June timing
    uv run python scripts/06_build_baseline_panel.py --lag 5 --name baseline_lag5
"""

import argparse
import logging
from pathlib import Path

import pandas as pd

from datastream.panel_builder import BaselineConfig, build_baseline, write_baseline
from datastream.preprocessing.firm_data import VARIABLES_SUBDIR, read_registry, resolve_root

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)

REGISTRY = Path(__file__).resolve().parents[1] / "config" / "firm_variables.csv"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default=None, help="Output name (default: baseline_<convention>_<penny>)")
    ap.add_argument("--convention", default="rolling", choices=["rolling", "ff"])
    ap.add_argument("--lag", type=int, default=3, help="rolling: months after the report month")
    ap.add_argument("--max-age", type=int, default=None, help="months after the report month (18 / 24)")
    ap.add_argument("--vars", nargs="+", default=None,
                    help="Variables (default: all imported time-series variables with source Worldscope)")
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None)
    ap.add_argument("--price-path", default="D:/Datastream/PriceData/US/processed")
    ap.add_argument("--penny", default="0.2")
    ap.add_argument("--no-unit-cleaning", action="store_true")
    ap.add_argument("--mnemonics", action="store_true",
                    help="Keep Datastream mnemonics as column names (default: readable names from the registry)")
    ap.add_argument("--no-statics", action="store_true", help="Do not join company name, ISIN, ticker, TRBC")
    args = ap.parse_args()

    root = resolve_root(args.root, args.region)
    uni = pd.read_parquet(Path(args.price_path) / f"monthly_universe_{args.penny}.parquet")
    reg = read_registry(REGISTRY)
    available = {p.stem for p in (root / VARIABLES_SUBDIR).glob("*.parquet")}
    if args.vars:
        variables = args.vars
    else:
        ws = reg[(reg.get("source", "") == "Worldscope") & (reg.get("type", "") == "timeseries")]["variable"]
        variables = [v for v in ws if v in available]
    missing = [v for v in variables if v not in available]
    if missing:
        raise SystemExit(f"Not imported: {missing}")
    signs = dict(zip(reg["variable"], reg.get("sign", "")))

    cfg = BaselineConfig(variables=variables, convention=args.convention, lag_months=args.lag,
                         max_age_months=args.max_age, signs=signs,
                         clean_unit_errors=not args.no_unit_cleaning)
    logging.info(f"Building baseline ({cfg.convention}, lag {cfg.lag_months}, max age {cfg.resolved_max_age()}) "
                 f"with {len(variables)} variables: {variables}")
    statics_file = Path(args.price_path) / f"statics_filtered_{args.penny}.csv"
    statics = None if args.no_statics or not statics_file.exists() else pd.read_csv(statics_file, dtype=str)
    from datastream.naming import check_names
    check_names(reg)
    panel, info = build_baseline(root, uni, cfg, registry=None if args.mnemonics else reg, statics=statics)
    name = args.name or f"baseline_{args.convention}_{args.penny}"
    path = write_baseline(root, name, panel, info)
    meta = info["meta"]
    logging.info(f"{meta['n_rows']:,} stock-months, {meta['n_stocks']:,} stocks, {meta['first_month']} to "
                 f"{meta['last_month']}; {meta['share_with_fundamentals']:.1%} with fundamentals, median age "
                 f"{meta['median_fund_age_months']:.0f} months -> {path}")
    logging.info(f"Cleaning: {meta['cleaning']}")


if __name__ == "__main__":
    main()
