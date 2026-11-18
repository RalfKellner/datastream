"""Coverage of the industry classification fields in the statics for stocks with emission data.

For the universe (from --from-year) and for the stocks with emissions in the emissions panel (any / reported /
estimated only): share of stocks with a value in each classification field (TRBC names TR1N-TR5N and codes
TR1-TR5, ICB, SIC/WC07021 - whichever the statics contain), also weighted by stock-months and by market cap
in the last December. Then the number of categories, emission stocks per TRBC economic sector, thin TRBC
industries, and the largest emission stocks without TRBC industry.

    uv run python scripts/diagnostics/check_industry_coverage.py
    uv run python scripts/diagnostics/check_industry_coverage.py --panel emissions_lag12_0.25

Output (also printed): <firm root>/Paneldata/checks/industry/industry_coverage_report.md
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from datastream.preprocessing.firm_data import PANEL_SUBDIR, resolve_root

CANDIDATES = ["TR1N", "TR2N", "TR3N", "TR4N", "TR5N", "TR1", "TR2", "TR3", "TR4", "TR5",
              "ICBIN", "ICBSSN", "ICBSN", "ICBSUN", "INDM", "WC07021", "WC07022", "WC06011", "WC06010"]
MISSING = {"", "NA", "N/A", "#N/A", "NAN", "NONE", "NULL", "UNCLASSIFIED", "UNKNOWN", "0", "-"}


def clean(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    return s.where(~s.str.upper().isin(MISSING) & s.notna())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None)
    ap.add_argument("--price-path", default=None)
    ap.add_argument("--penny", default="0.25")
    ap.add_argument("--panel", default=None, help="Default: emissions_lag12_<penny>")
    ap.add_argument("--from-year", type=int, default=2002)
    args = ap.parse_args()

    root = resolve_root(args.root, args.region)
    price_path = Path(args.price_path or f"D:/Datastream/PriceData/{args.region}/processed")
    panel_name = args.panel or f"emissions_lag12_{args.penny}"
    em = pd.read_parquet(root / PANEL_SUBDIR / "emissions" / f"{panel_name}.parquet",
                         columns=["DSCD", "Date", "MarketCAP", "co2e_total_combined", "co2e_is_estimated"])
    em["DSCD"] = em["DSCD"].astype(str)
    em = em[em["Date"].dt.year >= args.from_year]

    st = pd.read_csv(price_path / f"statics_filtered_{args.penny}.csv", dtype=str)
    st["DSCD"] = st["DSCD"].str.strip()
    st = st.drop_duplicates("DSCD").set_index("DSCD")
    other = []
    allf = price_path / "statics.csv"
    if allf.exists():
        other = [c for c in pd.read_csv(allf, nrows=0).columns if c in CANDIDATES and c not in st.columns]
    fields = [c for c in CANDIDATES if c in st.columns]
    for c in fields:
        st[c] = clean(st[c])

    groups = {
        "universe": em,
        "emissions (any)": em[em["co2e_total_combined"].notna()],
        "reported": em[em["co2e_is_estimated"] == 0],
        "estimated only": em[em["co2e_is_estimated"] == 1],
    }
    last_dec = em.loc[em["Date"].dt.month == 12, "Date"].max()
    rows = []
    for g, d in groups.items():
        stocks = pd.Index(d["DSCD"].unique())
        dec = d[d["Date"] == last_dec]
        for c in fields:
            has = st[c].reindex(stocks).notna()
            hm = d["DSCD"].map(st[c]).notna()
            hd = dec["DSCD"].map(st[c]).notna()
            rows.append({"group": g, "field": c, "n_stocks": len(stocks), "share_stocks": has.mean(),
                         "share_stock_months": hm.mean(),
                         f"vw_share_{last_dec:%Y-%m}": dec["MarketCAP"].where(hd, 0).sum() / dec["MarketCAP"].sum()
                         if len(dec) else np.nan})
    cov = pd.DataFrame(rows).pivot(index="field", columns="group").reindex(fields)
    cov = cov.reorder_levels([1, 0], axis=1).sort_index(axis=1, level=0)[list(groups)]

    em_stocks = pd.Index(groups["emissions (any)"]["DSCD"].unique())
    rep_stocks = pd.Index(groups["reported"]["DSCD"].unique())
    ncat = pd.DataFrame({c: {"categories_all": st[c].nunique(),
                             "categories_emission_stocks": st[c].reindex(em_stocks).nunique()} for c in fields}).T
    sector = None
    if "TR1N" in fields:
        sector = pd.DataFrame({"emission stocks": st["TR1N"].reindex(em_stocks).fillna("(missing)").value_counts(),
                               "reported": st["TR1N"].reindex(rep_stocks).fillna("(missing)").value_counts()}).fillna(0).astype(int)
    thin = None
    if "TR3N" in fields:
        n = st["TR3N"].reindex(em_stocks).value_counts()
        thin = pd.DataFrame({"n_industries": [len(n), int((n < 5).sum()), int((n < 10).sum())]},
                            index=["TRBC industries with emission stocks", "... with < 5 stocks", "... with < 10 stocks"])
    missing_big = None
    key = "TR3N" if "TR3N" in fields else (fields[0] if fields else None)
    if key:
        mc = groups["emissions (any)"].groupby("DSCD")["MarketCAP"].max()
        miss = mc[st[key].reindex(mc.index).isna().to_numpy()].sort_values(ascending=False).head(25)
        missing_big = pd.DataFrame({"ENAME": st["ENAME"].reindex(miss.index) if "ENAME" in st.columns else "",
                                    "max_mcap_mn": miss.round(0),
                                    **{c: st[c].reindex(miss.index) for c in fields if c != key}})
    combos = None
    if {"TR1N", "TR3N"} <= set(fields):
        s = st.reindex(em_stocks)
        combos = pd.Series({"TR1N and TR3N": (s["TR1N"].notna() & s["TR3N"].notna()).sum(),
                            "TR1N only": (s["TR1N"].notna() & s["TR3N"].isna()).sum(),
                            "TR3N only": (s["TR1N"].isna() & s["TR3N"].notna()).sum(),
                            "neither": (s["TR1N"].isna() & s["TR3N"].isna()).sum()}, name="emission stocks").to_frame()

    out = root / PANEL_SUBDIR / "checks" / "industry"
    out.mkdir(parents=True, exist_ok=True)
    parts = [("Classification fields in statics_filtered", pd.DataFrame({"fields": [", ".join(fields)],
                                                                          "only in statics.csv": [", ".join(other)]})),
             (f"Coverage (stocks from {args.from_year}; share with a value)", cov),
             ("Combinations (emission stocks)", combos), ("Number of categories", ncat),
             ("Emission stocks per TRBC economic sector (TR1N)", sector), ("Granularity of TR3N", thin),
             (f"Largest emission stocks without {key}", missing_big)]
    with open(out / "industry_coverage_report.md", "w", encoding="utf-8") as f, \
            pd.option_context("display.width", 250, "display.max_columns", 40, "display.max_rows", 200,
                              "display.float_format", "{:,.3f}".format):
        for title, t in parts:
            if t is None:
                continue
            txt = f"## {title}\n\n```\n{t.to_string()}\n```\n"
            print(txt)
            f.write(txt + "\n")
    print(f"-> {out / 'industry_coverage_report.md'}")


if __name__ == "__main__":
    main()
