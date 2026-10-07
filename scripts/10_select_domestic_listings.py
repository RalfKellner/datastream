"""Filter Datastream Navigator equity exports to domestic exchanges (European sample).

Step before creating the LSEG lists: one Navigator export (.xlsx) per country,
already filtered to Category = Equities. Following Landis & Skouras (2021), only
quote lines traded on an exchange located in the security's home market are kept.
Cross-listings (e.g. Austrian stocks on Berne, Milan GEM, Prague, Mexico, OTC)
are dropped. The remaining static screens (MAJOR, ISINID, TYPE, GEOGN, name
keywords) are applied later on the downloaded static data.

Home market = the Navigator column "Market". Domestic exchanges per country are
defined as regex patterns in config/eu_domestic_exchanges.csv.

Outputs (in --out):
  <Country>_domestic.csv   rows kept for that country (all Navigator columns)
  EU_DSCD.csv              all kept rows of all countries (input for list batching)
  exchange_report.csv      every exchange per country with counts and domestic flag
  warnings.txt             items that need a manual look

Usage:
  python scripts/10_select_domestic_listings.py --inp D:/Datastream/EU_Navigator --out D:/Datastream/EU_lists
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
CFG_EXCH = REPO / "config" / "eu_domestic_exchanges.csv"
CFG_COUNTRIES = REPO / "config" / "eu_countries.csv"


def load_patterns() -> dict[str, re.Pattern]:
    cfg = pd.read_csv(CFG_EXCH, dtype=str).fillna("")
    return {r.country: re.compile(r.exchange_pattern, re.IGNORECASE) for r in cfg.itertuples()}


def process_file(path: Path, patterns: dict[str, re.Pattern], warnings: list[str]):
    df = pd.read_excel(path, dtype={"DSCD": str, "Symbol": str})
    df["DSCD"] = df["DSCD"].astype(str).str.strip()
    df = df[df["DSCD"].ne("") & df["DSCD"].ne("nan")]

    markets = df["Market"].dropna().unique()
    if len(markets) != 1:
        warnings.append(f"{path.name}: expected one Market, found {list(markets)}")
    country = df["Market"].mode().iat[0]

    if "Category" in df:
        non_eq = (df["Category"] != "Equities").sum()
        if non_eq:
            warnings.append(f"{country}: {non_eq} rows with Category != Equities dropped")
        df = df[df["Category"] == "Equities"]

    if country not in patterns:
        warnings.append(f"{country}: no domestic-exchange pattern in config, file skipped")
        return country, None, None
    pat = patterns[country]
    df["domestic"] = df["Exchange"].fillna("").map(lambda x: bool(pat.search(x)) if pat.pattern != "^$" else False)

    # RIC exchange suffix (e.g. ERST.VI -> VI, ATRS.F^B22 -> F) as an independent
    # cross-check of the venue behind each Navigator exchange name
    df["ric_suffix"] = (df["RIC"].astype(str).str.split("^").str[0]
                        .str.extract(r"\.([A-Za-z]+)$", expand=False))
    rep = (
        df.groupby("Exchange", dropna=False)
        .agg(n=("DSCD", "size"), n_active=("Activity", lambda s: (s == "Active").sum()),
             domestic=("domestic", "all"),
             ric_suffixes=("ric_suffix", lambda s: "; ".join(s.value_counts().index[:3])),
             currencies=("Currency", lambda s: "; ".join(s.value_counts().index[:3])))
        .reset_index().sort_values("n", ascending=False)
    )
    rep.insert(0, "country", country)
    rep["share"] = (rep["n"] / rep["n"].sum()).round(3)

    # plausibility checks: the largest venue should normally be domestic,
    # and no non-domestic venue should carry a large share of the lines.
    if not df["domestic"].any():
        warnings.append(f"{country}: no domestic rows matched")
    elif not rep.iloc[0]["domestic"]:
        warnings.append(f"{country}: largest exchange '{rep.iloc[0]['Exchange']}' is NOT domestic - check")
    # a large share on an exchange that is domestic for another country (e.g. Frankfurt)
    # is an ordinary cross-listing; only unknown venues with a large share are flagged
    for r in rep[(~rep["domestic"]) & (rep["share"] >= 0.10)].itertuples():
        if any(p.pattern != "^$" and p.search(str(r.Exchange)) for c, p in patterns.items() if c != country):
            continue
        warnings.append(f"{country}: non-domestic exchange '{r.Exchange}' has {r.share:.0%} of rows - check")
    unknown = df.loc[df["Exchange"].fillna("-").isin(["-", ""]), "DSCD"].size
    if unknown:
        warnings.append(f"{country}: {unknown} rows without exchange name ('-') dropped - check")

    kept = df[df["domestic"]].drop(columns=["domestic", "ric_suffix"])
    dup = kept["DSCD"].duplicated().sum()
    if dup:
        warnings.append(f"{country}: {dup} duplicate DSCDs removed")
        kept = kept.drop_duplicates("DSCD")
    kept.insert(0, "Country", country)
    return country, kept, rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inp", required=True, type=Path, help="folder with Navigator .xlsx exports")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)

    patterns = load_patterns()
    countries = pd.read_csv(CFG_COUNTRIES)
    warnings: list[str] = []
    kept_all, reports, processed = [], [], set()

    for f in sorted(a.inp.glob("*.xlsx")):
        if f.name.startswith("~$"):
            continue
        country, kept, rep = process_file(f, patterns, warnings)
        if kept is None:
            continue
        processed.add(country)
        kept.to_csv(a.out / f"{country}_domestic.csv", index=False)
        kept_all.append(kept)
        reports.append(rep)
        print(f"{country:<16} kept {len(kept):>6} of {int(rep['n'].sum()):>6} lines "
              f"({kept['Activity'].eq('Active').sum()} active)")

    missing = sorted(set(countries["country"]) - processed)
    if missing:
        warnings.append("No export processed for: " + ", ".join(missing))

    allk = pd.concat(kept_all, ignore_index=True)
    n_dup = allk["DSCD"].duplicated().sum()
    if n_dup:
        warnings.append(f"{n_dup} DSCDs appear in more than one country file - kept first occurrence")
        allk = allk.drop_duplicates("DSCD")
    allk.to_csv(a.out / "EU_DSCD.csv", index=False)
    pd.concat(reports).to_csv(a.out / "exchange_report.csv", index=False)
    (a.out / "warnings.txt").write_text("\n".join(warnings) + "\n", encoding="utf-8")

    print(f"\nTotal kept: {len(allk)} DSCDs -> {a.out / 'EU_DSCD.csv'}")
    print("\n".join(["", "Warnings:"] + warnings) if warnings else "\nNo warnings.")


if __name__ == "__main__":
    main()
