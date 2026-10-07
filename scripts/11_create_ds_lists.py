"""Split a DSCD file into batches of 1,000 codes for the LSEG (Datastream) lists.

Writes one Excel sheet with one column per list (header = list name, e.g. L#EU001). Copy each column into a
new Datastream list with the same name. Replaces the notebook scripts/legacy/00_create_batched_ds_lists.ipynb.

    uv run python scripts/11_create_ds_lists.py --input D:/Datastream/EU_lists/EU_DSCD.csv --prefix EU --digits 3
    uv run python scripts/11_create_ds_lists.py --input D:/Datastream/US_DSCD_Raw.xlsx --sheet DSCD --prefix US --digits 2

Output (default): <input folder>/<prefix>_DSCD_batched.xlsx. Codes are kept as text (no Excel number
conversion of codes like 2866E4).
"""
import argparse
from pathlib import Path

import pandas as pd


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="CSV or Excel file with a DSCD column")
    ap.add_argument("--sheet", default=0, help="Excel sheet (if --input is an Excel file)")
    ap.add_argument("--prefix", required=True, help="List name prefix, e.g. EU -> L#EU001")
    ap.add_argument("--digits", type=int, default=3, help="Zero padding of the list number")
    ap.add_argument("--batch", type=int, default=1000)
    ap.add_argument("--output", default=None)
    args = ap.parse_args()

    src = Path(args.input)
    if src.suffix.lower() == ".csv":
        df = pd.read_csv(src, dtype=str)
    else:
        df = pd.read_excel(src, sheet_name=args.sheet, dtype=str)
    codes = df["DSCD"].astype(str).str.strip()
    codes = codes[codes.ne("") & codes.ne("nan")].drop_duplicates().tolist()

    cols = {f"L#{args.prefix}{i // args.batch + 1:0{args.digits}d}": pd.Series(codes[i:i + args.batch])
            for i in range(0, len(codes), args.batch)}
    out = Path(args.output) if args.output else src.with_name(f"{args.prefix}_DSCD_batched.xlsx")
    with pd.ExcelWriter(out, engine="openpyxl") as xw:
        pd.DataFrame(cols).to_excel(xw, index=False)
        for cell in xw.sheets["Sheet1"].iter_rows(min_row=2):
            for c in cell:
                c.number_format = "@"   # text format, so Excel does not turn codes into numbers
    print(f"{len(codes)} codes in {len(cols)} lists ({next(iter(cols))} ... {list(cols)[-1]}) -> {out}")


if __name__ == "__main__":
    main()
