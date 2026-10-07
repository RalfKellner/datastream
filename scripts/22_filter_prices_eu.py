"""Filter the European daily price panel (Landis & Skouras 2021), country by country.

Input:  <data-path>/statics.csv and panel_<nn>.feather from
        `uv run python scripts/21_load_price_panels.py --region EU`.
Output: EU_data_panel_filtered_<penny>/<COUNTRY>.feather (one file per country; load all or some with
        datastream.utils.load_filtered_eu()), statics_filtered_<penny>.csv and filter_report_<penny>.csv (stocks per country after
        every step) in <data-path>.

Memory: the panel (~186 million rows) is processed country by country. The panel files are split once into
<data-path>/_split_by_country/<COUNTRY>/ (only one panel file in memory at a time). Later runs reuse the split
as long as the panel files and the result of the static filters (1)-(5) are unchanged; otherwise it is rebuilt
automatically. --resplit forces a rebuild.

    uv run python scripts/22_filter_prices_eu.py
    uv run python scripts/22_filter_prices_eu.py --countries AUSTRIA GERMANY      # quick test run

Differences to scripts/22_filter_prices_us.py (U.S.), see NOTES_EU_data.md:
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
import gc
import hashlib
import json
import io
import logging
import os
import re
import shutil

import numpy as np
import pandas as pd
from bidask import edge_rolling

from datastream.preprocessing.filter import DSPreprocess

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STRING_COLUMNS = ["Type", "DSCD", "ENAME", "EXMNEM", "GEOGN", "ISIN", "ISINID", "LOC", "PCUR", "TRAC",
                  "WC05601", "TYPE", "TR1N", "TR2N", "TR3N", "CURRENCY", "WC06105", "WC06035"]
EUR_PAIRS = [("ReturnIndex", "ReturnIndex_EUR"), ("MarketCAP", "MarketCAP_EUR"),
             ("UnadjClose", "UnadjClose_EUR")]


def quiet(fn, *args, **kwargs):
    """Run a DSPreprocess filter and return (result, its printed message)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*args, **kwargs)
    return out, buf.getvalue().strip()


class Tracker:
    """Remaining stocks (and observations) per country after each step -> filter_report_<p>.csv."""

    def __init__(self, country_of: dict):
        self.country_of = country_of
        self.stocks: dict[str, dict] = {}   # step -> {country: n stocks}
        self.obs: dict[str, int] = {}       # step -> observations (summed over countries)
        self.order: list[str] = []

    def _add(self, step, counts: dict, n_obs=None):
        if step not in self.stocks:
            self.stocks[step] = {}
            self.order.append(step)
        self.stocks[step].update(counts)
        if n_obs is not None:
            self.obs[step] = self.obs.get(step, 0) + int(n_obs)

    def __call__(self, step: str, panel: pd.DataFrame | None = None, stocks=None, country: str | None = None):
        if panel is not None:
            stocks = panel["Stock"].unique()
        if country is None:
            counts = pd.Series(stocks, dtype=object).map(self.country_of).value_counts().to_dict()
        else:
            counts = {country: len(stocks)}
        self._add(step, counts, None if panel is None else panel.shape[0])
        if country is None:
            logging.info(f"{step}: {sum(counts.values())} stocks")

    def frame(self, countries: list[str]) -> pd.DataFrame:
        rows = []
        for step in self.order:
            c = self.stocks[step]
            rows.append({"step": step, "TOTAL": sum(c.values()), "obs_total": self.obs.get(step, np.nan),
                         **{k: c.get(k, 0) for k in countries}})
        return pd.DataFrame(rows)


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


def split_fingerprint(data_path: str, kept: set) -> dict:
    """What the split depends on: the panel files (name, size, modification time) and the lines that pass the
    static filters (count and hash). Stored in <split_dir>/_complete.json."""
    files = sorted(f for f in os.listdir(data_path) if re.fullmatch(r"panel_\d+\.feather", f))
    stats = {f: [os.path.getsize(os.path.join(data_path, f)), int(os.path.getmtime(os.path.join(data_path, f)))]
             for f in files}
    digest = hashlib.sha256("\n".join(sorted(kept)).encode()).hexdigest()
    return {"panel_files": stats, "n_kept_lines": len(kept), "kept_lines_sha256": digest}


def split_by_country(data_path: str, split_dir: str, kept: set, country_of: dict, resplit: bool):
    """One pass over panel_<nn>.feather: rows of the remaining lines -> <split_dir>/<COUNTRY>/part_<nn>.feather.
    Only one panel file is in memory at a time. An existing split is reused only if the panel files and the
    result of the static filters are unchanged (see split_fingerprint); otherwise it is rebuilt automatically.
    resplit=True forces a rebuild."""
    flag = os.path.join(split_dir, "_complete.json")
    fp = split_fingerprint(data_path, kept)
    if os.path.exists(flag) and not resplit:
        old = json.loads(open(flag, encoding="utf-8").read())
        if old == fp:
            logging.info(f"Using existing country split in {split_dir} (panel files and static filters unchanged).")
            return
        reasons = []
        if old.get("panel_files") != fp["panel_files"]:
            reasons.append("panel files changed (re-import with 21_load_price_panels.py)")
        if old.get("kept_lines_sha256") != fp["kept_lines_sha256"]:
            reasons.append(f"static filters keep different lines ({old.get('n_kept_lines')} -> {fp['n_kept_lines']})")
        logging.info(f"Country split is outdated: {'; '.join(reasons)}. Rebuilding.")
    if os.path.isdir(split_dir):
        shutil.rmtree(split_dir)
    os.makedirs(split_dir)
    files = sorted(fp["panel_files"])
    logging.info(f"Splitting {len(files)} panel files by country into {split_dir}.")
    for f in files:
        p = pd.read_feather(os.path.join(data_path, f))
        p["DSCD"] = p["DSCD"].astype(str).str.strip()
        p = p[p["DSCD"].isin(kept)]
        p["Country"] = p["DSCD"].map(country_of)
        nn = re.search(r"(\d+)", f).group(1)
        for c, part in p.groupby("Country"):
            os.makedirs(os.path.join(split_dir, c), exist_ok=True)
            part.drop(columns="Country").reset_index(drop=True).to_feather(
                os.path.join(split_dir, c, f"part_{nn}.feather"))
        logging.info(f"  {f}: {p['DSCD'].nunique()} lines split")
        del p
        gc.collect()
    with open(flag, "w", encoding="utf-8") as fh:
        json.dump(fp, fh)


def filter_country(c: str, split_dir: str, statics: pd.DataFrame, args, track: Tracker,
                   delisting_return, kept: set) -> pd.DataFrame | None:
    """All non-static filters for one country. Returns the filtered panel or None (country dropped)."""
    folder = os.path.join(split_dir, c)
    if not os.path.isdir(folder):
        logging.warning(f"{c}: no price data after static filters.")
        return None
    panel = pd.concat([pd.read_feather(os.path.join(folder, f)) for f in sorted(os.listdir(folder))],
                      ignore_index=True).rename(columns={"DSCD": "Stock"})
    panel = panel[panel["Stock"].isin(kept)]   # only lines that pass the current static filters (1)-(5)
    panel = panel.sort_values(["Stock", "Date"]).drop_duplicates(["Stock", "Date"], keep="first")
    panel["Country"] = c
    track("panel after static filters", panel, country=c)

    for ri in ("ReturnIndex", "ReturnIndex_EUR"):
        panel.loc[panel[ri] < 1e-6, ri] = np.nan
    g = panel.groupby("Stock")
    panel["Return"] = g["ReturnIndex"].pct_change(fill_method=None)          # local currency: used by filters
    panel["Return_EUR"] = g["ReturnIndex_EUR"].pct_change(fill_method=None)  # EUR: for analysis
    del g
    panel = panel.sort_values(["Date", "Stock"]).reset_index(drop=True)

    def step(name, fn, *a, **kw):
        nonlocal panel
        panel, _ = quiet(fn, panel, *a, **kw)
        panel = panel.drop(columns=["DSCD"], errors="ignore")
        track(name, panel, country=c)

    step("(11) without return index", DSPreprocess.filter_companies_wo_return_index_data)
    n_stocks = panel["Stock"].nunique()
    if args.min_stocks > 0 and n_stocks < args.min_stocks:
        logging.warning(f"{c}: filter (6) removes the country ({n_stocks} < {args.min_stocks} stocks).")
        track("(6) countries with few stocks", stocks=[], country=c)
        return None
    track("(6) countries with few stocks", panel, country=c)

    st_c = statics[statics["GEOGN"] == c]
    step("(7) implausible returns", DSPreprocess.filter_implausible_returns)
    step("(13) padded values before delisting", DSPreprocess.filter_padded_values_delistings, st_c, keep_padded=9)
    step("(12) short history", DSPreprocess.filter_short_history_stocks, threshold=120)
    step("(8) zero returns", DSPreprocess.filter_zero_return_stocks)
    step("(14) stale prices", DSPreprocess.filter_stale_prices)
    step("(9) high volatility", DSPreprocess.filter_stocks_by_high_volatility, volatility_threshold=0.40)
    step("(10) low volatility", DSPreprocess.filter_stocks_by_low_volatility)
    step("(15) outlier errors", DSPreprocess.filter_outlier_errors, up_ts=1.0, down_ts=-0.5, method="drop")
    step("(16) holidays", DSPreprocess.filter_holidays)
    step("(18) adjustment inconsistencies", DSPreprocess.filter_adjustment_inconsistencies, threshold=0.05)
    step("(19) nonsense values", DSPreprocess.filter_nonsense_values)
    step("(own) implausible OHLC", DSPreprocess.filter_implausible_prices)
    step("(own) no trading activity", DSPreprocess.filter_no_trading_activity)

    ffill = ("Open", "High", "Low", "Close", "ReturnIndex", "ReturnIndex_EUR", "AdjFactor",
             "UnadjClose", "UnadjClose_EUR")
    require = ("ReturnIndex", "ReturnIndex_EUR", "UnadjClose")
    step("(own) leading missings", DSPreprocess.handle_missings, st_c, c, ffill_cols=ffill, require_cols=require)

    panel.replace([np.inf, -np.inf], np.nan, inplace=True)
    if "DelistingDate" not in panel.columns:
        panel = panel.merge(st_c[["DSCD", "DelistingDate"]], left_on="Stock", right_on="DSCD", how="left")
    step("(own) delisting adjustment", DSPreprocess.adjust_for_delisting, delisting_return=delisting_return)
    panel = apply_eur_delisting(panel, delisting_return)

    # Filter (21) - penny stocks on the EUR unadjusted price (comparable across legacy-currency and euro lines)
    panel = panel.rename(columns={"UnadjClose": "_UnadjClose_local", "UnadjClose_EUR": "UnadjClose"})
    panel, _ = quiet(DSPreprocess.filter_penny_stocks, panel, threshold=args.penny)
    panel = panel.rename(columns={"UnadjClose": "UnadjClose_EUR", "_UnadjClose_local": "UnadjClose"})
    panel = panel.drop(columns=["prev_UnadjClose"], errors="ignore")
    track("(21) penny stocks", panel, country=c)

    panel.replace([np.inf, -np.inf], np.nan, inplace=True)
    panel = panel.sort_values(["Stock", "Date"]).reset_index(drop=True)
    panel["BidAsk_Spread"] = (
        panel.groupby("Stock", group_keys=False)[["Open", "High", "Low", "Close"]]
        .apply(lambda grp: edge_rolling(grp, window=21, sign=True))
    ).clip(lower=0.0, upper=0.25)

    panel = panel[(panel["Date"] > args.start) & (panel["Date"] <= args.end)]
    track(f"output period {args.start} - {args.end}", panel, country=c)
    return panel.sort_values(["Date", "Stock"]).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-path", default="D:/Datastream/PriceData/EU/processed")
    ap.add_argument("--penny", type=float, default=0.25, help="Penny-stock quantile per country (L&S: 0.25)")
    ap.add_argument("--min-stocks", type=int, default=20, help="Filter (6): minimum stocks per country (0 = off)")
    ap.add_argument("--delisting-return", type=float, default=-0.35, help="Set to nan to switch off")
    ap.add_argument("--start", default="1993-10-01", help="First date kept in the output (exclusive)")
    ap.add_argument("--end", default="2025-12-31", help="Last date kept in the output")
    ap.add_argument("--countries", nargs="+", default=None, help="GEOGN names (default: config/eu_countries.csv)")
    ap.add_argument("--resplit", action="store_true", help="Force a rebuild of the per-country split (normally detected automatically)")
    args = ap.parse_args()
    delisting_return = None if np.isnan(args.delisting_return) else args.delisting_return
    data_path = args.data_path
    tag = f"{args.penny:g}"

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

    # Filter (4) - foreign firms: only lines whose GEOGN is one of the configured countries
    config_countries = [c for c in pd.read_csv(os.path.join(REPO, "config", "eu_countries.csv"))["country"]
                        .str.upper() if c in present]
    statics = statics[statics["GEOGN"].isin(set(config_countries) | set(countries))]
    country_of = statics.set_index("DSCD")["GEOGN"].to_dict()
    track = Tracker(country_of)
    track("(4) statics of sample countries", stocks=statics["DSCD"].tolist())

    # ---------------------------------------------------------------- static filters (1)-(5)
    # always for all configured countries (a few seconds), so test runs and full runs share one split
    kept = static_filters(statics, sorted(set(config_countries) | set(countries)), track)

    # ---------------------------------------------------------------- split once, then country by country
    split_dir = os.path.join(data_path, "_split_by_country")
    split_by_country(data_path, split_dir, kept, country_of, args.resplit)

    out_dir = os.path.join(data_path, f"EU_data_panel_filtered_{tag}")
    os.makedirs(out_dir, exist_ok=True)
    remaining = []
    for c in countries:
        logging.info(f"===== {c} =====")
        res = filter_country(c, split_dir, statics, args, track, delisting_return, kept)
        out_file = os.path.join(out_dir, f"{c}.feather")
        if res is None or res.empty:
            if os.path.exists(out_file):
                os.remove(out_file)
            continue
        res.to_feather(out_file)
        remaining.extend(res["Stock"].unique().tolist())
        logging.info(f"{c}: {res['Stock'].nunique()} stocks, {res.shape[0]} rows saved.")
        del res
        gc.collect()

    statics[statics["DSCD"].isin(set(remaining))].to_csv(
        os.path.join(data_path, f"statics_filtered_{tag}.csv"), index=False)
    report = track.frame(countries)   # report columns: the countries of this run
    report.to_csv(os.path.join(data_path, f"filter_report_{tag}.csv"), index=False)
    logging.info(f"Done: {len(remaining)} stocks in {out_dir} (one file per country); report filter_report_{tag}.csv")


if __name__ == "__main__":
    main()
