"""Check bm/ep of the European baseline panel against Datastream's market-to-book (MTBV).

Datastream's MTBV is market value / book value in one currency and per share (book value per share of the firm,
price of the line). Our bm = WC03501 / firm market value (line market cap x shares_ratio from WC05301; EUR market
cap for euro countries, local otherwise), so mtbv * bm should be 1, also for share classes. If the panel has
bm_line (line-level book-to-market, before the share-class correction), the same check is shown for it as well.
For every line the median of mtbv * bm is classified:

  ok                ratio within [0.95, 1.05]
  legacy_currency   ratio = 1 / (fixed euro conversion rate of the line's quote currency) (+-3%):
                    Worldscope values are in EUR while the line's market cap is in the legacy currency
  share_class       ratio > 1.05, line is one of several share classes (another line of the panel has the
                    same company-name stem): equity of the whole firm divided by one class' market value
  other             everything else (timing differences, data errors)

    uv run python scripts/42_check_bm_consistency.py                 # EU (default)
    uv run python scripts/42_check_bm_consistency.py --region US     # U.S. (share classes, e.g. Alphabet)
    uv run python scripts/42_check_bm_consistency.py --baseline <path.parquet> --statics <statics_filtered.csv>

Writes bm_consistency_lines.csv next to the baseline file and prints a summary by category and country.
"""
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

# Irrevocable euro conversion rates (units of the legacy currency per EUR), keyed by Datastream PCUR code
EURO_RATES = {
    "AS": 13.7603, "BF": 40.3399, "LF": 40.3399, "DM": 1.95583, "EP": 166.386, "FF": 6.55957,
    "£E": 0.787564, "L": 1936.27, "FL": 2.20371, "PE": 200.482, "M": 5.94573, "DR": 340.750,
    "TO": 239.640, "CY": 0.585274, "M£": 0.429300, "KK": 30.1260, "EK": 15.6466, "LV": 0.702804,
    "LT": 3.45280, "KA": 7.53450, "BL": 1.95583,
}
CLASS_WORDS = (r"\b(A|B|C|D|R|SER\.?|SERIES|PREF\.?|PREFERENCE|RSP|RNC|RCV|RISP\.?|SAVINGS|VZ|ST|PC|NV|"
               r"CI|CIP|ADP|AFV|AGRISES|PV|BEARER|REGISTERED|'A'|'B'|'C')\b")


def name_stem(name: str) -> str:
    n = re.sub(r"\s+(DEAD|EXPIRED|SUSP)\b.*$", "", str(name).upper())
    n = re.sub(CLASS_WORDS, " ", n)
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    return " ".join(n.split()[:3])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="EU", choices=["US", "EU"])
    ap.add_argument("--baseline", default=None, help="Default: <firm root>/Paneldata/baseline/baseline_rolling_0.25.parquet")
    ap.add_argument("--statics", default=None, help="Default: D:/Datastream/PriceData/<region>/processed/statics_filtered_0.25.csv")
    args = ap.parse_args()
    args.baseline = args.baseline or (f"D:/Datastream/Firmcharacteristics_Monthly/{args.region}/Paneldata/baseline/"
                                      "baseline_rolling_0.25.parquet")
    args.statics = args.statics or f"D:/Datastream/PriceData/{args.region}/processed/statics_filtered_0.25.csv"

    import pyarrow.parquet as pq
    available = set(pq.read_schema(args.baseline).names)
    cols = [c for c in ["DSCD", "Date", "country", "company_name", "mtbv", "bm", "bm_line", "shares_ratio",
                        "shares_ratio_flag"] if c in available]
    b = pd.read_parquet(args.baseline, columns=cols)
    if "country" not in b.columns:            # U.S. baseline
        b["country"] = "UNITED STATES"
    if "company_name" not in b.columns:
        b["company_name"] = ""
    st = pd.read_csv(args.statics, dtype=str)[["DSCD", "PCUR"]].drop_duplicates("DSCD")
    b["ratio"] = b["mtbv"] * b["bm"]
    line = (b[b["ratio"] > 0].groupby(["DSCD", "country", "company_name"])["ratio"]
            .agg(ratio="median", n_months="size", ratio_q10=lambda s: s.quantile(0.1),
                 ratio_q90=lambda s: s.quantile(0.9)).reset_index())
    line = line.merge(st, on="DSCD", how="left")
    if "bm_line" in b.columns:     # before the share-class correction
        b["ratio_line"] = b["mtbv"] * b["bm_line"]
        line = line.merge(b[b["ratio_line"] > 0].groupby("DSCD")["ratio_line"].median().reset_index(),
                          on="DSCD", how="left")
    if "shares_ratio" in b.columns:
        sr = b.groupby("DSCD").agg(shares_ratio=("shares_ratio", "median"),
                                   shares_flag=("shares_ratio_flag", lambda f: f[f != "missing"].mode().iat[0]
                                                if (f != "missing").any() else "missing"))
        line = line.merge(sr.reset_index(), on="DSCD", how="left")

    rate = line["PCUR"].map(EURO_RATES)
    legacy = rate.notna() & (np.abs(line["ratio"] * rate - 1) < 0.03)
    stems = b.drop_duplicates("DSCD").assign(stem=lambda d: d["company_name"].map(name_stem))
    multi = stems.groupby(["country", "stem"])["DSCD"].transform("size") > 1
    multi_lines = set(stems.loc[multi, "DSCD"])

    line["category"] = "other"
    line.loc[line["ratio"].between(0.95, 1.05), "category"] = "ok"
    line.loc[(line["category"] != "ok") & legacy, "category"] = "legacy_currency"
    line.loc[(line["category"] == "other") & (line["ratio"] > 1.05) & line["DSCD"].isin(multi_lines),
             "category"] = "share_class"
    line["expected_legacy_ratio"] = 1 / rate

    out = Path(args.baseline).with_name("bm_consistency_lines.csv")
    line.sort_values(["category", "country", "ratio"]).to_csv(out, index=False)

    print("Lines by category:\n" + line["category"].value_counts().to_string())
    if "ratio_line" in line.columns:
        ok_line = line["ratio_line"].between(0.95, 1.05).mean()
        print(f"\nShare of lines with mtbv * bm in [0.95, 1.05]: {line['ratio'].between(0.95, 1.05).mean():.1%} "
              f"(firm-level bm) vs. {ok_line:.1%} (line-level bm_line)")
    if "shares_flag" in line.columns:
        print("\nShare-class correction (shares_ratio_flag per line):")
        print(line.groupby("shares_flag")["ratio"].agg(lines="size", median_ratio="median",
                                                       share_ok=lambda r: r.between(0.95, 1.05).mean()).to_string())
    print("\nNon-ok lines by country and category:")
    bad = line[line.category != "ok"]
    tab = pd.crosstab(bad["country"], bad["category"])
    print(tab.loc[tab.sum(axis=1).sort_values(ascending=False).index].to_string())
    print("\nLegacy-currency lines by quote currency:")
    print(line[line.category == "legacy_currency"].groupby("PCUR")["ratio"].agg(["size", "median"]).to_string())
    print("\nExamples 'other':")
    oth = line[line.category == "other"].sort_values("ratio")
    ex = pd.concat([oth.head(5), oth.tail(5)]).drop_duplicates("DSCD")
    print(ex[["DSCD", "country", "company_name", "PCUR", "ratio", "n_months"]].to_string(index=False))
    print(f"\nWritten: {out}")


if __name__ == "__main__":
    main()
