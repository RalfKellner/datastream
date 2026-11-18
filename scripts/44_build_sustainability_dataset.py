"""Sustainable-finance dataset: emissions panel + TRBC classification + firm controls (see
src/datastream/sustainability_dataset.py).

Prerequisites: emissions panel (43_build_emissions_panel.py), rolling baseline panel (41_build_baseline_panel.py),
statics_filtered_<p>.csv.

Output: <firm root>/Paneldata/sustainability/<name>.parquet (+ .json, _summary_by_year.csv)

    uv run python scripts/44_build_sustainability_dataset.py                    # sustainability_lag12_0.25
    uv run python scripts/44_build_sustainability_dataset.py --all-rows         # keep stock-months without emissions
    uv run python scripts/44_build_sustainability_dataset.py --emissions emissions_lag6_0.25 --name sustainability_lag6_0.25
"""

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from datastream.preprocessing.firm_data import PANEL_SUBDIR, resolve_root
from datastream.sustainability_dataset import build_sustainability_dataset

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None)
    ap.add_argument("--price-path", default=None, help="Default: D:/Datastream/PriceData/<region>/processed")
    ap.add_argument("--penny", default="0.25")
    ap.add_argument("--emissions", default=None, help="emissions panel name (default emissions_lag12_<penny>)")
    ap.add_argument("--baseline", default=None, help="baseline panel name (default baseline_rolling_<penny>)")
    ap.add_argument("--min-industry-firms", type=int, default=5)
    ap.add_argument("--all-rows", action="store_true", help="keep stock-months without emission data")
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    root = resolve_root(args.root, args.region)
    price_path = Path(args.price_path or f"D:/Datastream/PriceData/{args.region}/processed")
    em_name = args.emissions or f"emissions_lag12_{args.penny}"
    bl_name = args.baseline or f"baseline_rolling_{args.penny}"
    em = pd.read_parquet(root / PANEL_SUBDIR / "emissions" / f"{em_name}.parquet")
    bl_path = root / PANEL_SUBDIR / "baseline" / f"{bl_name}.parquet"
    bl = pd.read_parquet(bl_path) if bl_path.exists() else None
    if bl is None:
        logging.warning(f"{bl_path.name} not found: no firm controls")
    st_path = price_path / f"statics_filtered_{args.penny}.csv"
    st = pd.read_csv(st_path, dtype=str) if st_path.exists() else None

    d, info = build_sustainability_dataset(em, bl, st, args.min_industry_firms, emissions_only=not args.all_rows)
    name = args.name or em_name.replace("emissions", "sustainability")
    out = root / PANEL_SUBDIR / "sustainability"
    out.mkdir(parents=True, exist_ok=True)
    d.to_parquet(out / f"{name}.parquet", index=False)
    info["summary_by_year"].to_csv(out / f"{name}_summary_by_year.csv")
    meta = {**info["meta"], "inputs": {"emissions": em_name, "baseline": bl_name if bl is not None else None,
                                       "statics": st_path.name if st is not None else None}}
    with open(out / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)

    logging.info(f"{meta['rows']:,} stock-months, {meta['stocks']:,} stocks, {meta['first_month']} to "
                 f"{meta['last_month']} -> {out / (name + '.parquet')}")
    logging.info(f"estimated only: {meta['share_rows_estimated']:.1%} of rows; financials: "
                 f"{meta['share_rows_financial'] or 0:.1%}; without TRBC: {meta['share_rows_no_trbc'] or 0:.1%}; "
                 f"with fundamentals: {meta['share_rows_with_fundamentals'] or 0:.1%}")
    with pd.option_context("display.width", 220, "display.max_columns", 20, "display.float_format", "{:,.3f}".format):
        print("\nDecember cross-sections (stocks with emissions):")
        print(info["summary_by_year"].to_string())
        print(f"\n{len(d.columns)} columns: {', '.join(d.columns)}")


if __name__ == "__main__":
    main()
