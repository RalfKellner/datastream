"""Step 1 of the firm-variable pipeline: import each variable into its own long panel.

Replaces the loop in the (now legacy) notebook 01_import_transform_to_panel_monthly.ipynb.
Every variable is imported on its own, so adding a new variable only requires importing that variable:
  - time-series variables <root>/<VAR>/        -> Paneldata/variables/<VAR>.parquet  (Date | DSCD | VAR)
  - static variables      <root>/Static/<VAR>/ -> Paneldata/static/<VAR>.parquet     (DSCD | VAR | as_of)
The inventory Paneldata/variable_inventory.csv is refreshed at the end.

Examples (from the repo root):

    # import every variable that is new or whose raw files changed since the last import (default)
    uv run python scripts/30_import_firm_variables.py

    # import (or re-import) specific variables
    uv run python scripts/30_import_firm_variables.py WC02999 WC01001

    # re-import everything
    uv run python scripts/30_import_firm_variables.py --all

    # only refresh and print the inventory
    uv run python scripts/30_import_firm_variables.py --inventory-only

    # other machine / region: --root, or set DS_FIRM_ROOT to the folder that contains US/ and EU/
    uv run python scripts/30_import_firm_variables.py --root ~/Datastream/Firmcharacteristics_Monthly/US
"""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from datastream.preprocessing.firm_data import (
    discover_raw_variables,
    import_variable,
    needs_import,
    resolve_root,
    write_inventory,
)
from datastream.preprocessing.static_data import (
    discover_static_variables,
    import_static_variable,
    static_needs_import,
)

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)

REGISTRY = Path(__file__).resolve().parents[1] / "config" / "firm_variables.csv"

INVENTORY_COLUMNS = ["variable", "name", "type", "category", "panel_state", "n_raw_files", "n_firms_with_data", "n_series",
                     "share_firms_with_data", "first_date", "last_date", "date_convention", "in_merged"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("variables", nargs="*", help="Mnemonics to import (default: all new or changed ones)")
    ap.add_argument("--all", action="store_true", help="Re-import all variables with raw files")
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None, help="Folder with the variable folders (overrides --region)")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="Write the panel even if a file failed or is an unfilled template")
    ap.add_argument("--inventory-only", action="store_true")
    args = ap.parse_args()

    root = resolve_root(args.root, args.region)
    logging.info(f"Firm variable root: {root}")
    if not root.is_dir():
        logging.error(f"{root} does not exist.")
        return 1

    summaries = []
    if not args.inventory_only:
        ts_vars = discover_raw_variables(root)
        st_vars = discover_static_variables(root)
        if args.variables:
            unknown = [v for v in args.variables if v not in ts_vars and v not in st_vars]
            if unknown:
                logging.warning(f"No raw files for: {unknown}")
            todo_ts = [v for v in args.variables if v in ts_vars]
            todo_st = [v for v in args.variables if v in st_vars]
        elif args.all:
            todo_ts, todo_st = ts_vars, st_vars
        else:
            todo_ts = [v for v in ts_vars if needs_import(root, v)]
            todo_st = [v for v in st_vars if static_needs_import(root, v)]
            logging.info(f"New or changed: {len(todo_ts)} of {len(ts_vars)} time-series variables {todo_ts}, "
                         f"{len(todo_st)} of {len(st_vars)} static variables {todo_st}")

        for v in todo_ts:
            summaries.append(import_variable(root, v, allow_incomplete=args.allow_incomplete))
        for v in todo_st:
            summaries.append(import_static_variable(root, v, allow_incomplete=args.allow_incomplete))

    inv = write_inventory(root, REGISTRY)
    with pd.option_context("display.max_rows", 500, "display.width", 200, "display.max_columns", 20):
        cols = [c for c in INVENTORY_COLUMNS if c in inv.columns]
        shown = inv[cols].astype(object)
        print(shown.where(shown.notna(), "").to_string(index=False) if len(inv) else "No variables found.")

    not_written = [s.variable for s in summaries if not s.panel_written]
    if not_written:
        logging.error(f"Not written (see the _reports folders in Paneldata/variables and Paneldata/static): "
                      f"{not_written}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
