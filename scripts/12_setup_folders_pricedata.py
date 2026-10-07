"""Create the list folders and empty Excel templates for the daily price data download.

<root>/<nn>/<VAR>_<nn>.xlsx for every LSEG list nn = 01 ... n. Existing files are never overwritten (the
earlier version replaced already downloaded files with empty workbooks when it was run again).

    uv run python scripts/12_setup_folders_pricedata.py --region US --n-lists 39
    uv run python scripts/12_setup_folders_pricedata.py --region EU --n-lists 36

Variables (Datastream request in the request table):
  US: AF MTBV MV P PH PL PO RI STATIC UP VO
  EU: the same in local currency plus MV_EU, RI_EU, UP_EU (~E, EUR at daily market rates)
PA/PB (bid/ask) are not used by the pipeline (spreads are estimated from OHLC); add them with --extra PA PB.
"""
import argparse
from pathlib import Path

from openpyxl import Workbook

VARIABLES = {
    "US": ["AF", "MTBV", "MV", "P", "PH", "PL", "PO", "RI", "STATIC", "UP", "VO"],
    "EU": ["AF", "MTBV", "MV", "MV_EU", "P", "PH", "PL", "PO", "RI", "RI_EU", "STATIC", "UP", "UP_EU", "VO"],
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", required=True, choices=sorted(VARIABLES))
    ap.add_argument("--n-lists", type=int, required=True, help="Number of LSEG lists (US: 39, EU: 36)")
    ap.add_argument("--root", default=None, help="Default: D:/Datastream/PriceData/<region>")
    ap.add_argument("--extra", nargs="*", default=[], help="Additional variables, e.g. PA PB")
    args = ap.parse_args()

    root = Path(args.root or f"D:/Datastream/PriceData/{args.region}")
    names = VARIABLES[args.region] + [v for v in args.extra if v not in VARIABLES[args.region]]
    created = skipped = 0
    for i in range(1, args.n_lists + 1):
        folder = root / f"{i:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        for name in names:
            path = folder / f"{name}_{i:02d}.xlsx"
            if path.exists():
                skipped += 1
                continue
            Workbook().save(path)
            created += 1
    print(f"{created} template(s) created, {skipped} existing file(s) left untouched in {root} "
          f"({args.n_lists} lists x {len(names)} variables: {' '.join(names)})")


if __name__ == "__main__":
    main()
