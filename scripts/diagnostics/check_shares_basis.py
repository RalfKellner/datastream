"""Diagnostic (October 2026): does WC05301 match the line shares from the unadjusted price (MV / UP) or from the
adjusted price (MV / P)? Datastream may restate Worldscope share counts for later capital changes like the
adjusted price. Prints the share of lines with ratio in [0.95, 1.05] per basis and writes
<baseline>_shares_basis.csv. Usage: uv run python scripts/diagnostics/check_shares_basis.py [baseline.parquet]
"""
import sys
import numpy as np
import pandas as pd

pd.set_option("display.width", 160)

BASELINE = sys.argv[1] if len(sys.argv) > 1 else "D:/Datastream/Firmcharacteristics_Monthly/EU/Paneldata/baseline/baseline_rolling_0.25.parquet"
cols = ["DSCD", "Date", "country", "market_cap", "price", "price_unadjusted", "common_shares_outstanding",
        "fund_report_month", "shares_ratio_flag"]
b = pd.read_parquet(BASELINE, columns=cols)
b["Date"] = pd.to_datetime(b["Date"])
b["fye"] = (pd.to_datetime(b["fund_report_month"]).dt.to_period("M") - 1).dt.to_timestamp(how="end").dt.normalize()

# line shares at fiscal year end on both bases
look = b[["DSCD", "Date", "market_cap", "price", "price_unadjusted"]].rename(columns={"Date": "fye"})
x = b.dropna(subset=["fye", "common_shares_outstanding"]).merge(look, on=["DSCD", "fye"], suffixes=("", "_fye"))
x["r_unadj"] = x["common_shares_outstanding"] / (x["market_cap_fye"] / x["price_unadjusted_fye"])
x["r_adj"] = x["common_shares_outstanding"] / (x["market_cap_fye"] / x["price_fye"])
x["r_adj_now"] = x["common_shares_outstanding"] / (x["market_cap"] / x["price"])   # current month, adjusted

def norm(s, by):   # remove unit scale (power of ten of the median) per country
    med = s.groupby(by).transform("median")
    return s / 10.0 ** np.round(np.log10(med))

out = {}
for c in ["r_unadj", "r_adj", "r_adj_now"]:
    x[c] = norm(x[c].where(np.isfinite(x[c]) & (x[c] > 0)), x["country"])
    line = x.groupby("DSCD")[c].median()
    out[c] = {"lines": int(line.notna().sum()),
              "share_in_0.95_1.05": round(float(line.between(0.95, 1.05).mean()), 3),
              "share_above_1.05": round(float((line > 1.05).mean()), 3),
              "share_below_0.95": round(float((line < 0.95).mean()), 3),
              "rows_in_0.95_1.05": round(float(x[c].between(0.95, 1.05).mean()), 3)}
print(pd.DataFrame(out).T)
by_c = x.groupby("country")[["r_unadj", "r_adj"]].agg(lambda s: s.between(0.95, 1.05).mean()).round(3)
print(by_c.sort_values("r_unadj"))
x.groupby("DSCD")[["r_unadj", "r_adj", "r_adj_now"]].median().to_csv(BASELINE.replace(".parquet", "_shares_basis.csv"))
