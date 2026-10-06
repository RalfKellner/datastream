"""Filter the European daily price panel (Landis & Skouras 2021), country by country.

Input:  <data-path>/statics.csv and panel_<nn>.feather from
        `uv run python scripts/01_load_merge_panel.py --region EU`.
Output: EU_data_panel_filtered_<penny>.feather, statics_filtered_<penny>.csv and
        filter_report_<penny>.csv (stocks per country after every step) in <data-path>.

    uv run python scripts/eu_02_filter.py
    uv run python scripts/eu_02_filter.py --countries AUSTRIA GERMANY      # quick test run

Differences to scripts/02_filter.py (U.S.), see NOTES_EU_data.md:
* Static filters (1)-(5) are evaluated per country on the statics (country-specific name, cross-listing
  and currency lists); filter (6) removes countries with fewer than --min-stocks remaining stocks.
* All return-based filters use the local-currency series (`Return` from `ReturnIndex`), so exchange-rate
  movements cannot hide stale prices or zero returns. `Return_EUR` (from `ReturnIndex_EUR`) is carried along
  and receives the same row removals and the same delisting adjustment.
* Filter (16) holidays and filter (21) penny stocks are applied per country; the penny threshold uses
  `UnadjClose_EUR`, because a country can contain lines quoted in a legacy currency and in euro.
* handle_missings requires only the return indexes and the unadjusted price to be present (not Open, High,
  Low, Volume), because Datastream's coverage of these items starts later for many European markets.
"""
import argparse
import contextlib
import io
import logging
import os
import re

import numpy as np
import pandas as pd
from bidask import edge_rolling

from datastream.preprocessing.filter import DSPreprocess

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRING_COLUMNS = ["Type", "DSCD", "ENAME", "EXMNEM", "GEOGN", "ISIN", "ISINID", "LOC", "PCUR", "TRAC",
                  "WC05601", "TYPE", "TR1N", "TR2N", "TR3N", "CURRENCY"]
EUR_PAIRS = [("ReturnIndex", "ReturnIndex_EUR"), ("MarketCAP", "MarketCAP_EUR"),
             ("UnadjClose", "UnadjClose_EUR")]


def quiet(fn, *args, **kwargs):
    """Run a DSPreprocess filter and return (result, its printed message)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*args, **kwargs)
    return out, buf.getvalue().strip()


class Tracker:
    """Remaining stocks per country after each step (written to filter_report_<p>.csv)."""

    def __init__(self, country_of: dict):
        self.country_of = country_of
        self.rows = []

    def __call__(self, step: str, panel: pd.DataFrame | None = None, stocks=None):
        if stocks is None:
            stocks = panel["Stock"].unique()
        counts = pd.Series(stocks).map(self.country_of).value_counts()
        row = {"step": step, "TOTAL": int(counts.sum()), **counts.to_dict()}
        if panel is not None:
            row["obs_total"] = int(panel.shape[0])
        self.rows.append(row)
        logging.info(f"{step}: {row['TOTAL']} stocks" + (f", {row['obs_total']} observations" if panel is not None else ""))

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows).fillna(0)


def static_filters(statics: pd.DataFrame, countries: list[str], tracker: Tracker) -> set:
    """Filters (1)-(5) per country on the statics only. Returns the remaining DSCDs."""
    steps = {"(1) non-common": set(), "(2) cross-listings": set(), "(3) duplicate LOC/ISIN": set(),
             "(5) foreign currency": set()}
    for c in countries:
        st = statics[statics["GEOGN"] == c]
        dummy = pd.DataFrame({"Stock": st["DSCD"].unique()})
        k1, m1 = quiet(DSPreprocess.filter_non_common_stocks, dummy, st, c)
        st = st[st["DSCD"].isin(k1["Stock"])]
        k2, m2 = quiet(DSPreprocess.filter_cross_listings, k1, st, c)
        st = st[st["DSCD"].isin(k2["Stock"])]
        k3, m3 = quiet(DSPreprocess.filter_duplicate_loc_codes, k2, st)
        st = st[st["DSCD"].isin(k3["Stock"])]
        k5, m5 = quiet(DSPreprocess.filter_foreign_currency_stocks, k3, st, c)
        for key, k in zip(steps, (k1, k2, k3, k5)):
            steps[key] |= set(k["Stock"])
        logging.info(f"{c}: {len(dummy)} lines -> (1) {len(k1)} -> (2) {len(k2)} -> (3) {len(k3)} -> (5) {len(k5)}")
    for key, kept in steps.items():
        tracker(key, stocks=list(kept))
    return steps["(5) foreign currency"]


def apply_eur_delisting(panel: pd.DataFrame, delisting_return: float | None, flag_col="DelistingReturnApplied"):
    """Apply the same delisting return to the EUR series as adjust_for_delisting applies to the local one."""
    if delisting_return is None or flag_col not in panel.columns:
        return panel
    m = panel[flag_col].fillna(False).astype(bool)
    panel.loc[m, "Return_EUR"] = (1 + panel.loc[m, "Return_EUR"].fillna(0.0)) * (1 + delisting_return) - 1
    panel.loc[m, "ReturnIndex_EUR"] = panel.loc[m, "ReturnIndex_EUR"] * (1 + delisting_return)
    return panel


def per_country(panel: pd.DataFrame, countries: list[str], fn, *args, **kwargs) -> pd.DataFrame:
    parts = []
    for c in countries:
        sub = panel[panel["Country"] == c]
        if sub.empty:
            continue
        out, msg = quiet(fn, sub, *args, **kwargs)
        logging.info(f"  {c}: {msg.splitlines()[-1] if msg else ''}")
        parts.append(out)
    return pd.concat(parts, ignore_index=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-path", default="D:/Datastream/PriceData/EU/processed")
    ap.add_argument("--penny", type=float, default=0.25, help="Penny-stock quantile per country (L&S: 0.25)")
    ap.add_argument("--min-stocks", type=int, default=20, help="Filter (6): minimum stocks per country (0 = off)")
    ap.add_argument("--delisting-return", type=float, default=-0.35, help="Set to nan to switch off")
    ap.add_argument("--start", default="1993-10-01", help="First date kept in the output (exclusive)")
    ap.add_argument("--end", default="2025-12-31", help="Last date kept in the output")
    ap.add_argument("--countries", nargs="+", default=None, help="GEOGN names (default: config/eu_countries.csv)")
    args = ap.parse_args()
    delisting_return = None if np.isnan(args.delisting_return) else args.delisting_return
    data_path = args.data_path

    # ---------------------------------------------------------------- statics
    statics = pd.read_csv(os.path.join(data_path, "statics.csv"))
    cols = [c for c in STRING_COLUMNS if c in statics.columns]
    statics[cols] = statics[cols].astype(str)
    statics["DSCD"] = statics["DSCD"].str.strip()
    statics = statics.drop_duplicates("DSCD")
    delist_str = statics["ENAME"].str.extract(r"DELIST\.(\d{2}/\d{2}/\d{2})")[0]
    statics["DelistingDate"] = pd.to_datetime(delist_str, format="%d/%m/%y", errors="coerce")
    statics["BDATE"] = pd.to_datetime(statics["BDATE"], errors="coerce")

    if args.countries:
        countries = [c.upper() for c in args.countries]
    else:
        countries = pd.read_csv(os.path.join(REPO, "config", "eu_countries.csv"))["country"].str.upper().tolist()
    present = set(statics["GEOGN"])
    missing = [c for c in countries if c not in present]
    if missing:
        logging.warning(f"No statics for: {missing} (e.g. Liechtenstein has no domestic exchange).")
    countries = [c for c in countries if c in present]

    # Filter (4) - foreign firms: only lines whose GEOGN is one of the sample countries
    statics = statics[statics["GEOGN"].isin(countries)]
    country_of = statics.set_index("DSCD")["GEOGN"].to_dict()
    track = Tracker(country_of)
    track("(4) statics of sample countries", stocks=statics["DSCD"].tolist())

    # ---------------------------------------------------------------- static filters (1)-(5)
    kept = static_filters(statics, countries, track)

    # ---------------------------------------------------------------- price panel
    files = sorted(f for f in os.listdir(data_path) if re.fullmatch(r"panel_\d+\.feather", f))
    logging.info(f"Reading {len(files)} panel files.")
    parts = []
    for f in files:
        p = pd.read_feather(os.path.join(data_path, f))
        p["DSCD"] = p["DSCD"].astype(str).str.strip()
        parts.append(p[p["DSCD"].isin(kept)])          # static filters first: saves memory
    panel = pd.concat(parts, ignore_index=True).rename(columns={"DSCD": "Stock"})
    del parts
    panel = panel.sort_values(["Stock", "Date"]).drop_duplicates(["Stock", "Date"], keep="first")
    panel["Country"] = panel["Stock"].map(country_of)
    track("panel after static filters", panel)

    for ri in ("ReturnIndex", "ReturnIndex_EUR"):
        mask = panel[ri] < 1e-6
        logging.info(f"{ri}: share of values < 1e-6 set to NaN: {mask.mean():.6f}")
        panel.loc[mask, ri] = np.nan
    g = panel.groupby("Stock")
    panel["Return"] = g["ReturnIndex"].pct_change(fill_method=None)          # local currency: used by filters
    panel["Return_EUR"] = g["ReturnIndex_EUR"].pct_change(fill_method=None)  # EUR: for analysis
    panel = panel.sort_values(["Date", "Stock"]).reset_index(drop=True)

    # Filter (11) - stocks without return index data
    panel, _ = quiet(DSPreprocess.filter_companies_wo_return_index_data, panel)
    track("(11) without return index", panel)

    # Filter (6) - countries with fewer than min_stocks stocks
    if args.min_stocks > 0:
        n_per_country = panel.groupby("Country")["Stock"].nunique()
        small = n_per_country[n_per_country < args.min_stocks]
        if len(small):
            logging.warning(f"Filter (6) removes countries with < {args.min_stocks} stocks: {small.to_dict()}")
        countries = [c for c in countries if c not in small.index]
        panel = panel[panel["Country"].isin(countries)]
        track("(6) countries with few stocks", panel)

    # ---------------------------------------------------------------- return-based filters (local currency)
    panel, _ = quiet(DSPreprocess.filter_implausible_returns, panel); track("(7) implausible returns", panel)
    panel, _ = quiet(DSPreprocess.filter_padded_values_delistings, panel, statics, keep_padded=9)
    panel = panel.drop(columns=["DSCD"], errors="ignore"); track("(13) padded values before delisting", panel)
    panel, _ = quiet(DSPreprocess.filter_short_history_stocks, panel, threshold=120); track("(12) short history", panel)
    panel, _ = quiet(DSPreprocess.filter_zero_return_stocks, panel); track("(8) zero returns", panel)
    panel, _ = quiet(DSPreprocess.filter_stale_prices, panel); track("(14) stale prices", panel)
    panel, _ = quiet(DSPreprocess.filter_stocks_by_high_volatility, panel, volatility_threshold=0.40)
    track("(9) high volatility", panel)
    panel, _ = quiet(DSPreprocess.filter_stocks_by_low_volatility, panel); track("(10) low volatility", panel)
    panel, _ = quiet(DSPreprocess.filter_outlier_errors, panel, up_ts=1.0, down_ts=-0.5, method="drop")
    track("(15) outlier errors", panel)
    logging.info("Filter (16) holidays, per country:")
    panel = per_country(panel, countries, DSPreprocess.filter_holidays); track("(16) holidays", panel)
    panel, _ = quiet(DSPreprocess.filter_adjustment_inconsistencies, panel, threshold=0.05)
    track("(18) adjustment inconsistencies", panel)
    panel, _ = quiet(DSPreprocess.filter_nonsense_values, panel); track("(19) nonsense values", panel)
    panel, _ = quiet(DSPreprocess.filter_implausible_prices, panel); track("(own) implausible OHLC", panel)
    panel, _ = quiet(DSPreprocess.filter_no_trading_activity, panel); track("(own) no trading activity", panel)

    # ---------------------------------------------------------------- missings, delisting, penny stocks
    logging.info("handle_missings, per country:")
    ffill = ("Open", "High", "Low", "Close", "ReturnIndex", "ReturnIndex_EUR", "AdjFactor",
             "UnadjClose", "UnadjClose_EUR")
    require = ("ReturnIndex", "ReturnIndex_EUR", "UnadjClose")
    parts = []
    for c in countries:
        sub = panel[panel["Country"] == c]
        if sub.empty:
            continue
        out, msg = quiet(DSPreprocess.handle_missings, sub, statics, c, ffill_cols=ffill, require_cols=require)
        parts.append(out)
    panel = pd.concat(parts, ignore_index=True)
    track("(own) leading missings", panel)

    panel.replace([np.inf, -np.inf], np.nan, inplace=True)
    if "DelistingDate" not in panel.columns:
        panel = panel.merge(statics[["DSCD", "DelistingDate"]], left_on="Stock", right_on="DSCD", how="left") \
                     .drop(columns="DSCD")
    panel, msg = quiet(DSPreprocess.adjust_for_delisting, panel, delisting_return=delisting_return)
    logging.info(msg.replace("\n", " | "))
    panel = apply_eur_delisting(panel, delisting_return)
    track("(own) delisting adjustment", panel)

    # Filter (21) - penny stocks, per country, on the EUR unadjusted price
    logging.info(f"Filter (21) penny stocks (quantile {args.penny}) per country on UnadjClose_EUR:")
    panel = panel.rename(columns={"UnadjClose": "_UnadjClose_local", "UnadjClose_EUR": "UnadjClose"})
    panel = per_country(panel, countries, DSPreprocess.filter_penny_stocks, threshold=args.penny)
    panel = panel.rename(columns={"UnadjClose": "UnadjClose_EUR", "_UnadjClose_local": "UnadjClose"})
    panel = panel.drop(columns=["prev_UnadjClose"], errors="ignore")
    track("(21) penny stocks", panel)

    # ---------------------------------------------------------------- spreads, sample period, output
    panel.replace([np.inf, -np.inf], np.nan, inplace=True)
    panel = panel.sort_values(["Stock", "Date"]).reset_index(drop=True)
    logging.info("Estimating bid-ask spreads (EDGE, 21 days, local OHLC).")
    panel["BidAsk_Spread"] = (
        panel.groupby("Stock", group_keys=False)[["Open", "High", "Low", "Close"]]
        .apply(lambda grp: edge_rolling(grp, window=21, sign=True))
    ).clip(lower=0.0, upper=0.25)

    panel = panel[(panel["Date"] > args.start) & (panel["Date"] <= args.end)]
    track(f"output period {args.start} - {args.end}", panel)

    tag = f"{args.penny:g}"
    statics_out = statics[statics["DSCD"].isin(panel["Stock"].unique())]
    panel = panel.sort_values(["Date", "Stock"]).reset_index(drop=True)
    panel.to_feather(os.path.join(data_path, f"EU_data_panel_filtered_{tag}.feather"))
    statics_out.to_csv(os.path.join(data_path, f"statics_filtered_{tag}.csv"), index=False)
    report = track.frame()
    country_cols = [c for c in countries if c in report.columns]
    report = report[["step", "TOTAL", "obs_total"] + country_cols] if "obs_total" in report else report
    report.to_csv(os.path.join(data_path, f"filter_report_{tag}.csv"), index=False)
    logging.info(f"Saved {panel.shape[0]} rows, {panel['Stock'].nunique()} stocks; report filter_report_{tag}.csv")


if __name__ == "__main__":
    main()
