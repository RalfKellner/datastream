"""Step 2 (optional) of the firm-variable pipeline: merge variable panels into one multi-variable panel.

Replaces the (now legacy) notebook 02_monthly_firm_panel_data.ipynb. Writes
Paneldata/merged/<name>.parquet with columns DSCD | Date | <VAR1> | <VAR2> | ... and a sidecar
<name>.json that records the variables and the version of each variable panel used.

By default, dates are aligned to the calendar month end (the convention of the monthly returns from the
price panel). Use --no-align to keep the raw Datastream dates; then all variables must share one date
convention.

Examples:

    uv run python scripts/31_merge_firm_variables.py --name all_variables --all
    uv run python scripts/31_merge_firm_variables.py --name balance_sheet --vars WC02999 WC02003 WC03255
    uv run python scripts/31_merge_firm_variables.py --name core --vars-file config/sets/core.txt
"""

import argparse
import logging
import sys
from pathlib import Path

from datastream.preprocessing.firm_data import (
    VARIABLES_SUBDIR,
    merge_variables,
    resolve_root,
    write_inventory,
    write_merged,
)

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)

REGISTRY = Path(__file__).resolve().parents[1] / "config" / "firm_variables.csv"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="Name of the merged dataset (file name without extension)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--vars", nargs="+", help="Mnemonics to merge")
    g.add_argument("--vars-file", help="Text file with one mnemonic per line ('#' starts a comment)")
    g.add_argument("--all", action="store_true", help="All imported variable panels")
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None)
    ap.add_argument("--no-align", action="store_true", help="Keep raw dates instead of month end")
    ap.add_argument("--names", action="store_true",
                    help="Readable column names from config/firm_variables.csv (mapping stored in the JSON sidecar)")
    args = ap.parse_args()

    root = resolve_root(args.root, args.region)
    if args.all:
        variables = sorted(p.stem for p in (root / VARIABLES_SUBDIR).glob("*.parquet"))
    elif args.vars_file:
        lines = Path(args.vars_file).read_text(encoding="utf-8").splitlines()
        variables = [l.split("#")[0].strip() for l in lines if l.split("#")[0].strip()]
    else:
        variables = args.vars

    logging.info(f"Merging {len(variables)} variables: {variables}")
    merged, meta = merge_variables(root, variables, align_month_end=not args.no_align)
    if args.names:
        from datastream.naming import check_names, output_mapping
        from datastream.preprocessing.firm_data import read_registry
        reg = read_registry(REGISTRY)
        check_names(reg)
        names = output_mapping(merged.columns, reg, include_price=False)
        merged = merged.rename(columns=names)
        meta["column_names"] = names
    path = write_merged(root, args.name, merged, meta)
    logging.info(f"{meta['n_rows']:,} firm-months, {meta['n_firms']:,} firms, "
                 f"{meta['first_date']} to {meta['last_date']} -> {path}")
    write_inventory(root, REGISTRY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
