"""Remove firm variables from one region's pipeline (raw files, imported panels, import logs, inventory).

The inventory (Paneldata/variable_inventory.csv) lists every variable that appears in the registry
(config/firm_variables.csv), in a raw folder, as an imported panel or in an import log. To drop a variable,
remove it from the registry and run this script for each region. Nothing is deleted: files are moved to
<root>/_dropped/ (skipped by the import and the inventory), and the variable's rows are removed from the
import logs (a copy of each log is kept in _dropped/).

    uv run python scripts/90_drop_firm_variables.py ENERO52V EPS --region US --dry-run
    uv run python scripts/90_drop_firm_variables.py ENERO52V EPS --region US
    uv run python scripts/90_drop_firm_variables.py ENERO52V EPS --region EU

Merged datasets (Paneldata/merged/) that contain a dropped variable are reported, not changed: rebuild them
with scripts/31_merge_firm_variables.py.
"""
import argparse
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from datastream.preprocessing import static_data as sd
from datastream.preprocessing.firm_data import (IMPORT_LOG_NAME, MERGED_SUBDIR, VARIABLES_SUBDIR, resolve_root,
                                                variable_panel_path, write_inventory)

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)
REGISTRY = Path(__file__).resolve().parents[1] / "config" / "firm_variables.csv"


def move(src: Path, root: Path, dry: bool):
    if not src.exists():
        return False
    dst = root / "_dropped" / src.relative_to(root)
    if dst.exists():
        dst = dst.with_name(f"{dst.name}_{datetime.now():%Y%m%d%H%M%S}")
    logging.info(f"{'[dry-run] ' if dry else ''}move {src.relative_to(root)} -> {dst.relative_to(root)}")
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    return True


def drop_log_rows(log_path: Path, variables: set[str], root: Path, dry: bool):
    if not log_path.exists():
        return
    log = pd.read_csv(log_path)
    hit = log["variable"].astype(str).isin(variables)
    if not hit.any():
        return
    logging.info(f"{'[dry-run] ' if dry else ''}remove {int(hit.sum())} row(s) from {log_path.relative_to(root)}")
    if not dry:
        backup = root / "_dropped" / log_path.relative_to(root)
        backup = backup.with_name(f"{backup.stem}_{datetime.now():%Y%m%d%H%M%S}{backup.suffix}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(log_path, backup)
        log[~hit].to_csv(log_path, index=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("variables", nargs="+")
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(resolve_root(args.root, args.region))
    variables = set(args.variables)

    reg = pd.read_csv(REGISTRY)
    still = sorted(variables & set(reg["variable"].astype(str)))
    if still:
        logging.warning(f"Still in {REGISTRY.name} (remove them there, otherwise they stay in the inventory "
                        f"as registered but missing): {still}")

    for v in sorted(variables):
        found = False
        found |= move(root / v, root, args.dry_run)                                  # time-series raw files
        found |= move(variable_panel_path(root, v), root, args.dry_run)              # time-series panel
        found |= move(root / sd.STATIC_RAW_SUBDIR / v, root, args.dry_run)           # static raw files
        found |= move(sd.static_panel_path(root, v), root, args.dry_run)             # static panel
        if not found:
            logging.info(f"{v}: no files in {root}")
    drop_log_rows(root / VARIABLES_SUBDIR / IMPORT_LOG_NAME, variables, root, args.dry_run)
    drop_log_rows(root / sd.STATIC_PANEL_SUBDIR / IMPORT_LOG_NAME, variables, root, args.dry_run)

    merged_dir = root / MERGED_SUBDIR
    for js in sorted(merged_dir.glob("*.json")) if merged_dir.is_dir() else []:
        used = variables & set(json.loads(js.read_text(encoding="utf-8")).get("variables", []))
        if used:
            logging.warning(f"Merged dataset {js.stem} contains {sorted(used)}: rebuild it with 31_merge_firm_variables.py")

    if not args.dry_run:
        inv = write_inventory(root, REGISTRY)
        left = sorted(variables & set(inv["variable"].astype(str))) if len(inv) else []
        if left:
            logging.warning(f"Still in the inventory: {left}")
        else:
            logging.info("Inventory refreshed; dropped variables no longer listed.")


if __name__ == "__main__":
    main()
