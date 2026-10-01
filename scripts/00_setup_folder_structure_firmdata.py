"""Create the folder and empty Excel templates for new monthly firm variables.

One template <VAR>_<nn>.xlsx per Datastream list is created in <root>/<VAR>/. Existing files are never
overwritten (the earlier version replaced already downloaded files with empty workbooks when it was run
again for the same variable). Templates that are never filled are reported as 'empty_template' by
scripts/03_import_firm_variables.py.

    uv run python scripts/00_setup_folder_structure_firmdata.py WC04601 WC03501
    uv run python scripts/00_setup_folder_structure_firmdata.py WC04601 --n-lists 39 --region US
"""

import argparse
import os
from pathlib import Path

from openpyxl import Workbook

from datastream.preprocessing.firm_data import resolve_root


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("variables", nargs="+")
    ap.add_argument("--n-lists", type=int, default=39, help="Number of Datastream lists (US: 39)")
    ap.add_argument("--digits", type=int, default=2, help="Zero padding of the list number in file names")
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None)
    args = ap.parse_args()

    root = resolve_root(args.root, args.region)
    for var in args.variables:
        folder = Path(root) / var
        os.makedirs(folder, exist_ok=True)
        created, skipped = 0, 0
        for i in range(1, args.n_lists + 1):
            path = folder / f"{var}_{i:0{args.digits}d}.xlsx"
            if path.exists():
                skipped += 1
                continue
            Workbook().save(path)
            created += 1
        print(f"{var}: {created} template(s) created, {skipped} existing file(s) left untouched in {folder}")


if __name__ == "__main__":
    main()
