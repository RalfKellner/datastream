"""Aggregate the downloaded static files and cross-check them against the list file EU_DSCD_batched.xlsx.

Manual list creation in Datastream is error-prone (lost codes, a list pasted into the wrong
folder, Excel turning codes like '2866E4' into numbers). This script:

  1. reads <root>/<NN>/STATIC_<NN>.xlsx for every list folder and writes statics.csv
     (with the source folder in column 'list_folder'),
  2. compares all DSCDs in EU_DSCD_batched.xlsx with those in the static files
     (missing / unexpected / duplicated codes),
  3. compares every folder with the list it should contain (column L#EU<NN> <-> folder <NN>),
  4. summarises coverage of the static fields and, if EU_DSCD.csv is given, adds the
     Navigator country to the results and checks GEOGN against it.

Usage:
  python scripts/20_check_statics.py --root D:/Datastream/PriceData/EU
      --batched D:/Datastream/EU_DSCD_batched.xlsx [--dscd D:/Datastream/EU_lists/EU_DSCD.csv]

Outputs go to <root>/processed/: statics.csv and static_checks/*.csv
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

SCI = re.compile(r"^\d+E\d+$", re.IGNORECASE)  # codes Excel may read as scientific numbers


def norm_dscd(x) -> str | None:
    """Normalise a DSCD to Datastream's 6-character string (restores lost leading zeros)."""
    if pd.isna(x):
        return None
    s = str(x).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit() and len(s) < 6:
        s = s.zfill(6)
    return s or None


def folder_number(p: Path) -> int | None:
    m = re.search(r"(\d+)$", p.name)
    return int(m.group(1)) if m else None


def load_statics(root: Path) -> pd.DataFrame:
    frames = []
    folders = sorted([p for p in root.iterdir() if p.is_dir() and folder_number(p) is not None],
                     key=folder_number)
    for f in folders:
        files = list(f.glob("STATIC_*.xlsx"))
        if not files:
            print(f"  ! no STATIC file in folder {f.name}")
            continue
        df = pd.read_excel(files[0], dtype=str, engine="openpyxl")
        if "DSCD" not in df.columns:
            raise ValueError(f"{files[0]} has no 'DSCD' column; columns: {list(df.columns)[:10]}")
        df.insert(0, "list_folder", folder_number(f))
        frames.append(df)
        print(f"  folder {f.name}: {len(df):>5} rows")
    st = pd.concat(frames, ignore_index=True)
    st["DSCD"] = st["DSCD"].map(norm_dscd)
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path, help="EU price-data root with list folders 01, 02, ...")
    ap.add_argument("--batched", required=True, type=Path, help="EU_DSCD_batched.xlsx (list definitions)")
    ap.add_argument("--dscd", type=Path, help="optional EU_DSCD.csv (adds Navigator country, GEOGN check)")
    a = ap.parse_args()
    out = a.root / "processed"
    chk = out / "static_checks"
    chk.mkdir(parents=True, exist_ok=True)

    print("Reading static files ...")
    st = load_statics(a.root)
    st.to_csv(out / "statics.csv", index=False)

    # reference = every code in the list file, with the list it belongs to
    b = pd.read_excel(a.batched, dtype=str)
    ref = (b.melt(var_name="list", value_name="DSCD").dropna(subset=["DSCD"]))
    ref["DSCD"] = ref["DSCD"].map(norm_dscd)
    ref = ref.dropna(subset=["DSCD"])
    dup_lists = ref[ref["DSCD"].duplicated(keep=False)]
    if a.dscd:
        nav = pd.read_csv(a.dscd, dtype=str)
        nav["DSCD"] = nav["DSCD"].map(norm_dscd)
        ref = ref.merge(nav[["DSCD", "Country"]].drop_duplicates("DSCD"), on="DSCD", how="left")
        not_in_lists = set(nav["DSCD"].dropna()) - set(ref["DSCD"])
        print(f"EU_DSCD.csv codes not in any list: {len(not_in_lists)}")
        pd.Series(sorted(not_in_lists), name="DSCD").to_csv(chk / "not_in_batched_lists.csv", index=False)
    else:
        ref["Country"] = pd.NA

    # rows Datastream could not resolve: DSCD present but every other field empty or an error string
    fields = [c for c in st.columns if c not in ("list_folder", "DSCD")]
    vals = st[fields].astype(str)
    is_err = vals.apply(lambda s: s.str.startswith("$$ER") | s.isin(["nan", "NA", "None", ""]))
    st_valid = st[~is_err.all(axis=1)]
    unresolved = st[is_err.all(axis=1)]

    s_ref, s_st = set(ref["DSCD"]), set(st_valid["DSCD"].dropna())
    missing = ref[~ref["DSCD"].isin(s_st)]
    unexpected = st_valid[~st_valid["DSCD"].isin(s_ref)]
    dup = st_valid[st_valid["DSCD"].duplicated(keep=False)].sort_values("DSCD")

    print("\n=== DSCD intersection ===")
    print(f"list file (batched):      {len(s_ref):>6} codes  ({len(ref)} entries, {dup_lists['DSCD'].nunique()} codes in >1 list)")
    print(f"static files (resolved):  {len(s_st):>6} codes  ({len(st)} rows, {len(unresolved)} unresolved/error rows)")
    print(f"in both:                  {len(s_ref & s_st):>6}")
    print(f"missing in statics:       {len(missing):>6}")
    print(f"unexpected in statics:    {len(unexpected):>6}")
    print(f"duplicated in statics:    {dup['DSCD'].nunique():>6}")
    sci_ref = ref[ref["DSCD"].str.match(SCI)]
    if len(sci_ref):
        n_lost = (~sci_ref["DSCD"].isin(s_st)).sum()
        print(f"codes like '1234E5' (Excel number risk): {len(sci_ref)}, of which missing: {n_lost}")
    if len(missing):
        print("missing by list:\n" + missing["list"].value_counts().sort_index().to_string())
        if a.dscd:
            print("missing by country:\n" + missing["Country"].value_counts().to_string())

    missing.to_csv(chk / "missing_in_statics.csv", index=False)
    dup_lists.to_csv(chk / "codes_in_several_lists.csv", index=False)
    unexpected.to_csv(chk / "unexpected_in_statics.csv", index=False)
    dup.to_csv(chk / "duplicated_in_statics.csv", index=False)
    unresolved.to_csv(chk / "unresolved_rows.csv", index=False)

    # list-by-list comparison: does folder NN contain exactly list L#EU<NN>?
    rows = []
    for col in b.columns:
        m = re.search(r"(\d+)$", col)
        if not m:
            continue
        n = int(m.group(1))
        exp = set(b[col].dropna().map(norm_dscd)) - {None}
        got = set(st.loc[st["list_folder"] == n, "DSCD"].dropna())
        best = None
        if exp and len(exp & got) < 0.9 * len(exp):  # look for the list that matches this folder
            overlaps = {c2: len(set(b[c2].dropna().map(norm_dscd)) & got) for c2 in b.columns}
            best = max(overlaps, key=overlaps.get)
        rows.append({"list": col, "folder": n, "expected": len(exp), "in_folder": len(got),
                     "overlap": len(exp & got), "missing": len(exp - got), "extra": len(got - exp),
                     "folder_matches_list": best or col})
    lc = pd.DataFrame(rows)
    lc.to_csv(chk / "list_vs_folder.csv", index=False)
    bad = lc[(lc["missing"] > 0) | (lc["extra"] > 0)]
    print("\n=== list vs. folder ===")
    print("all folders match their lists" if bad.empty else bad.to_string(index=False))

    # field coverage and domicile check
    print("\n=== static field coverage (share non-empty, resolved rows) ===")
    cov = (~is_err.loc[st_valid.index]).mean().sort_values()
    print(cov.round(3).to_string())
    cov.rename("share_filled").to_csv(chk / "field_coverage.csv")

    if a.dscd and "GEOGN" in st_valid.columns:
        m = st_valid.merge(ref[["DSCD", "Country"]], on="DSCD", how="inner")
        m["geogn_ok"] = m["GEOGN"].str.upper().str.strip() == m["Country"].str.upper().str.strip()
        geo = m.groupby("Country").agg(n=("DSCD", "size"), geogn_match=("geogn_ok", "mean")).round(3)
        geo.to_csv(chk / "geogn_vs_country.csv")
        print("\n=== GEOGN equals Navigator country (share) ===")
        print(geo.to_string())
        m.loc[~m["geogn_ok"], ["DSCD", "Country", "GEOGN"] + [c for c in ("ENAME", "NAME") if c in m]] \
            .to_csv(chk / "geogn_mismatches.csv", index=False)

    print(f"\nWritten: {out / 'statics.csv'} and {chk}")


if __name__ == "__main__":
    main()
