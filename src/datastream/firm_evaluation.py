"""Sanity and information checks for monthly firm variables (Datastream / Worldscope / Refinitiv ESG).

Companion of ``evaluation.py`` (price data). The notebook ``analyses/02_firm_variable_checks.ipynb`` only
configures and displays; everything is computed here.

Inputs
------
* variable panels ``Date | DSCD | <VAR>`` from ``Paneldata/variables`` (see ``preprocessing/firm_data.py``);
* the **monthly universe** built from the filtered daily price panel by ``build_monthly_universe``
  (``scripts/05_build_monthly_universe.py``): one row per stock and month in which the stock has at least
  one valid day after the Landis & Skouras filters, with month-end MarketCAP, Close, MTBV and size quintile;
* optionally the static fiscal-year end (``WC05350``) and the relations in ``config/firm_relations.csv``.

Check battery
-------------
A  coverage relative to the price universe (EW, VW, by size quintile), firm-years, histories, matching
B  update structure (value changes per firm-year), reporting lag vs. fiscal year end, stale values and
   values after the last price / delisting date
C  cross-sectional distribution per year, implausible values (sign, robust z), unit jumps
D  relations between variables (identities, bounds, recomputed ratios) and alignment of update months
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------------------------
# Heuristic thresholds for the summary flags (adjust to taste; the tables show the raw numbers)
# ---------------------------------------------------------------------------------------------------------
THRESHOLDS = {
    "vw_coverage_ref_min": 0.80,           # VW coverage of the price universe in the reference year
    "share_after_last_price_max": 0.02,    # obs after the stock's last valid price month
    "share_dead_firms_padded_max": 0.10,   # delisted firms with values > 12 months after their last price
    "share_stale_max": 0.05,               # universe obs in a run of identical non-zero values > STALE_MONTHS
    "share_sign_violations_max": 0.001,    # negatives for variables that must be >= 0
    "share_extreme_max": 0.005,            # |robust z| > EXTREME_Z
    "unit_errors_per_1000_firms_max": 1.0,  # co-jumps by the same power of 1000 in several items
    "outside_universe_share_max": 0.50,    # firm-months with data but not in the price universe (info)
}
STALE_MONTHS = 24          # identical values for more than this many months count as stale
EXTREME_Z = 8.0            # robust z-score threshold (log10 for non-negative variables, asinh otherwise)
JUMP_LOG10 = 2.5           # single change by a factor >= ~316: likely unit error
REVERSAL_LOG10 = 0.9       # change by factor >= ~8 ...
REVERSAL_TOL = 0.3         # ... that is undone (log10 ratios sum to ~0) ...
REVERSAL_WINDOW = 24       # ... within this many months
UNIT_ERROR_MIN_VARS = 3    # unit error candidate: jumps in at least this many items of a firm in one month ...
UNIT_ERROR_MAX_SPREAD = 0.15   # ... with (nearly) the same log10 ratio ...
UNIT_ERROR_TOL = 0.2       # ... within this distance of +-3 or +-6 (factor 1,000 or 1,000,000)


def month_end(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.to_period("M").dt.to_timestamp(how="end").dt.normalize()


# ---------------------------------------------------------------------------------------------------------
# Monthly universe from the filtered daily price panel (memory-safe, batch-wise)
# ---------------------------------------------------------------------------------------------------------

UNIVERSE_VALUE_COLUMNS = ["MarketCAP", "Close", "MTBV"]


def build_monthly_universe(
    panel_path: str | Path,
    statics_path: str | Path | None = None,
    value_columns: list[str] = UNIVERSE_VALUE_COLUMNS,
    stock_col: str = "Stock",
    date_col: str = "Date",
    breakpoints: str = "nyse",
    n_size_groups: int = 5,
) -> pd.DataFrame:
    """Condense the filtered daily panel to one row per stock-month.

    The feather file is read record batch by record batch and only the needed columns are
    materialised, so this runs on machines that cannot hold the full daily panel in memory.

    Returns ``DSCD | Date (month end) | n_days | last_day | <value_columns> | size_group | first_price_month |
    last_price_month | delisting_date``. ``size_group`` (1 = smallest) uses NYSE breakpoints if the statics
    contain ``EXMNEM`` with NYSE stocks (``breakpoints='nyse'``), otherwise all stocks. Note that ``EXMNEM``
    is the *current* exchange from the statics.
    """
    import pyarrow as pa
    import pyarrow.ipc as ipc

    parts = []
    with pa.memory_map(str(panel_path), "r") as source:
        reader = ipc.open_file(source)
        names = reader.schema.names
        vals = [c for c in value_columns if c in names]
        missing = sorted(set(value_columns) - set(vals))
        if missing:
            logger.warning(f"Columns not in the price panel, skipped: {missing}")
        cols = [stock_col, date_col] + vals
        for i in range(reader.num_record_batches):
            df = reader.get_batch(i).select(cols).to_pandas()
            parts.append(_reduce_to_months(df, stock_col, date_col, vals))
            if (i + 1) % 200 == 0:
                logger.info(f"{i + 1} of {reader.num_record_batches} record batches")

    m = pd.concat(parts, ignore_index=True)
    # a stock-month can be split across batches: combine (sum days, take last values)
    m = m.sort_values("last_day")
    g = m.groupby(["DSCD", "Date"], sort=False)
    out = g.agg(n_days=("n_days", "sum"), last_day=("last_day", "max"), **{c: (c, "last") for c in vals})
    out = out.reset_index().sort_values(["DSCD", "Date"]).reset_index(drop=True)

    life = out.groupby("DSCD")["Date"].agg(first_price_month="min", last_price_month="max")
    out = out.merge(life, on="DSCD", how="left")

    statics = None
    if statics_path is not None and Path(statics_path).exists():
        statics = pd.read_csv(statics_path, dtype=str)
        statics["DSCD"] = statics["DSCD"].str.strip()
        if "DelistingDate" in statics.columns:
            dl = statics[["DSCD", "DelistingDate"]].rename(columns={"DelistingDate": "delisting_date"})
            dl["delisting_date"] = pd.to_datetime(dl["delisting_date"], errors="coerce")
            out = out.merge(dl.drop_duplicates("DSCD"), on="DSCD", how="left")
    if "delisting_date" not in out.columns:
        out["delisting_date"] = pd.NaT

    out["size_group"] = size_groups(out, statics, breakpoints=breakpoints, n=n_size_groups)
    return out


def _reduce_to_months(df, stock_col, date_col, vals):
    df = df.rename(columns={stock_col: "DSCD", date_col: "Day"})
    df["DSCD"] = df["DSCD"].astype(str).str.strip()
    df["Day"] = pd.to_datetime(df["Day"])
    df["Date"] = month_end(df["Day"])
    df = df.sort_values("Day")
    g = df.groupby(["DSCD", "Date"], sort=False)
    return g.agg(n_days=("Day", "size"), last_day=("Day", "max"), **{c: (c, "last") for c in vals}).reset_index()


def size_groups(m: pd.DataFrame, statics: pd.DataFrame | None, breakpoints: str = "nyse", n: int = 5) -> pd.Series:
    """Size group per month (1 = smallest) from month-end MarketCAP."""
    if "MarketCAP" not in m.columns:
        return pd.Series(pd.NA, index=m.index, dtype="Int64")
    qs = np.linspace(0, 1, n + 1)[1:-1]
    ref = m
    if breakpoints == "nyse" and statics is not None and "EXMNEM" in statics.columns:
        nyse = set(statics.loc[statics["EXMNEM"].astype(str).str.upper().str.startswith("NYS"), "DSCD"])
        if nyse:
            ref = m[m["DSCD"].isin(nyse)]
        else:
            logger.warning("No NYSE stocks found in the statics; size breakpoints use all stocks.")
    bps = ref.groupby("Date")["MarketCAP"].quantile(qs).unstack()
    out = pd.Series(pd.NA, index=m.index, dtype="Int64")
    b = bps.reindex(m["Date"]).to_numpy()
    mc = m["MarketCAP"].to_numpy()
    valid = ~np.isnan(mc) & ~np.isnan(b).any(axis=1)
    grp = (mc[:, None] > b).sum(axis=1) + 1
    out[valid] = grp[valid]
    return out


# ---------------------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------------------

def prepare_panel(panel: pd.DataFrame, var: str) -> pd.DataFrame:
    """Month-end dates, one row per firm-month, sorted."""
    p = panel[["DSCD", "Date", var]].dropna(subset=[var]).copy()
    p["DSCD"] = p["DSCD"].astype(str)
    p["Date"] = month_end(p["Date"])
    p = p.drop_duplicates(subset=["DSCD", "Date"], keep="last").sort_values(["DSCD", "Date"])
    return p.reset_index(drop=True)


def change_events(p: pd.DataFrame, var: str) -> pd.DataFrame:
    """Rows where the value differs from the firm's previous observation (first observation excluded)."""
    prev = p.groupby("DSCD")[var].shift(1)
    first = p["DSCD"] != p["DSCD"].shift(1)
    ch = (~first) & (p[var] != prev)
    out = p.loc[ch, ["DSCD", "Date", var]].copy()
    out["prev"] = prev[ch].to_numpy()
    return out


def firm_years(p: pd.DataFrame, var: str) -> pd.DataFrame:
    """Last observation of each firm in each calendar year."""
    fy = p.assign(Year=p["Date"].dt.year).groupby(["DSCD", "Year"], as_index=False).last()
    return fy


def in_universe(p: pd.DataFrame, uni: pd.DataFrame) -> pd.DataFrame:
    """Firm-months of ``p`` in which the stock is in the filtered price universe."""
    keys = uni[["DSCD", "Date"]].drop_duplicates()
    return p.merge(keys, on=["DSCD", "Date"], how="inner").sort_values(["DSCD", "Date"]).reset_index(drop=True)


def _last_full_year(uni: pd.DataFrame) -> int:
    last = uni["Date"].max()
    return last.year if last.month == 12 else last.year - 1


# ---------------------------------------------------------------------------------------------------------
# A. Coverage
# ---------------------------------------------------------------------------------------------------------

def coverage_by_month(p: pd.DataFrame, var: str, uni: pd.DataFrame) -> pd.DataFrame:
    """Per month: stocks in the universe, of which with a value (EW share) and market-cap share (VW)."""
    u = uni[["DSCD", "Date", "MarketCAP"]].merge(p[["DSCD", "Date"]], on=["DSCD", "Date"], how="left",
                                                 indicator="_m")
    u["_has"] = u["_m"].eq("both")
    u["_mc_has"] = u["MarketCAP"].where(u["_has"], 0.0)
    g = u.groupby("Date")
    out = pd.DataFrame({
        "n_universe": g.size(),
        "n_with_value": g["_has"].sum(),
        "mcap_universe": g["MarketCAP"].sum(),
        "mcap_with_value": g["_mc_has"].sum(),
    })
    out["ew_coverage"] = out["n_with_value"] / out["n_universe"]
    out["vw_coverage"] = out["mcap_with_value"] / out["mcap_universe"].replace(0, np.nan)
    in_uni = p.merge(uni[["DSCD", "Date"]], on=["DSCD", "Date"], how="left", indicator=True)
    outside = in_uni[in_uni["_merge"] == "left_only"].groupby("Date").size()
    out["n_outside_universe"] = outside.reindex(out.index).fillna(0).astype(int)
    return out


def coverage_by_size(p: pd.DataFrame, var: str, uni: pd.DataFrame) -> pd.DataFrame:
    """Average monthly EW coverage per calendar year (rows) and size group (columns, 1 = smallest)."""
    u = uni[["DSCD", "Date", "size_group"]].dropna(subset=["size_group"])
    u = u.merge(p[["DSCD", "Date"]].assign(_has=1.0), on=["DSCD", "Date"], how="left").fillna({"_has": 0.0})
    monthly = u.groupby(["Date", "size_group"])["_has"].mean().reset_index()
    monthly["Year"] = monthly["Date"].dt.year
    return monthly.groupby(["Year", "size_group"])["_has"].mean().unstack().rename_axis(columns="size_group")


def yearly_overview(p: pd.DataFrame, var: str, cov: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per calendar year: firms with a value, firm-month obs, obs per firm, mean/median of the firm-year value."""
    fy = firm_years(p, var)
    y = p.assign(Year=p["Date"].dt.year).groupby("Year")
    out = pd.DataFrame({
        "n_firms": y["DSCD"].nunique(),
        "n_firm_months": y.size(),
    })
    out["months_per_firm"] = out["n_firm_months"] / out["n_firms"]
    g = fy.groupby("Year")[var]
    out["mean"] = g.mean()
    out["median"] = g.median()
    out["mean_trimmed_1pct"] = g.apply(lambda s: s[(s >= s.quantile(0.01)) & (s <= s.quantile(0.99))].mean())
    if cov is not None:
        c = cov.assign(Year=cov.index.year).groupby("Year")[["ew_coverage", "vw_coverage"]].mean()
        out = out.join(c)
    return out


def history_stats(p: pd.DataFrame, var: str) -> dict:
    """Length of firm histories and gaps (months without a value between a firm's first and last value)."""
    g = p.groupby("DSCD")["Date"]
    first, last, n = g.min(), g.max(), g.size()
    span = (last.dt.year - first.dt.year) * 12 + (last.dt.month - first.dt.month) + 1
    gaps = span - n
    return {
        "n_firms": int(len(n)),
        "median_history_months": float(n.median()),
        "share_firms_with_gaps": float((gaps > 0).mean()),
        "share_gap_months": float(gaps.sum() / span.sum()),
    }


def matching(p: pd.DataFrame, uni: pd.DataFrame) -> dict:
    """Firms with data vs. firms in the price universe (over the variable's sample period)."""
    firms_var = set(p["DSCD"])
    lo, hi = p["Date"].min(), p["Date"].max()
    uni_period = uni[(uni["Date"] >= lo) & (uni["Date"] <= hi)]
    firms_uni = set(uni_period["DSCD"])
    return {
        "n_firms_variable": len(firms_var),
        "n_firms_universe_in_period": len(firms_uni),
        "n_firms_both": len(firms_var & firms_uni),
        "share_variable_firms_in_universe": len(firms_var & firms_uni) / max(len(firms_var), 1),
        "share_universe_firms_ever_covered": len(firms_var & firms_uni) / max(len(firms_uni), 1),
    }


# ---------------------------------------------------------------------------------------------------------
# B. Update structure, reporting lag, stale values and padding
# ---------------------------------------------------------------------------------------------------------

UPDATE_BINS = [(0, "0"), (1, "1"), (2, "2-3"), (4, "4"), (5, "5+")]


def update_frequency(p: pd.DataFrame, var: str, min_months: int = 12) -> pd.DataFrame:
    """Share of firm-years with 0, 1, 2-3, 4, 5+ value changes, per year (firm-years with >= min_months obs)."""
    ch = change_events(p, var).assign(Year=lambda d: d["Date"].dt.year).groupby(["DSCD", "Year"]).size()
    obs = p.assign(Year=p["Date"].dt.year).groupby(["DSCD", "Year"]).size()
    df = pd.DataFrame({"n_obs": obs, "n_changes": ch}).fillna({"n_changes": 0})
    df = df[df["n_obs"] >= min_months]
    cats = pd.cut(df["n_changes"], bins=[-0.5, 0.5, 1.5, 3.5, 4.5, np.inf], labels=[b for _, b in UPDATE_BINS])
    out = pd.crosstab(df.index.get_level_values("Year"), cats, normalize="index")
    out.index.name = "Year"
    out["median_changes"] = df.groupby(level="Year")["n_changes"].median()
    return out


def reporting_lag(p: pd.DataFrame, var: str, fye: pd.DataFrame | None) -> pd.DataFrame:
    """Distribution of the month of value changes relative to the fiscal-year-end month (0-11 months after).

    ``fye``: ``DSCD | fye_month`` (from the static WC05350). As WC05350 is only the *current* fiscal year
    end, firms that changed their fiscal year blur the distribution; the mode is what matters.
    """
    if fye is None or fye.empty:
        return pd.DataFrame()
    ch = change_events(p, var).merge(fye[["DSCD", "fye_month"]], on="DSCD", how="inner")
    if ch.empty:
        return pd.DataFrame()
    ch["lag"] = (ch["Date"].dt.month - ch["fye_month"]) % 12
    out = ch["lag"].value_counts(normalize=True).sort_index().rename("share").to_frame()
    out["n"] = ch["lag"].value_counts().sort_index()
    out.index.name = "months_after_fye"
    return out


def stale_and_padding(p: pd.DataFrame, var: str, uni: pd.DataFrame) -> dict:
    """Values after the last valid price month / delisting, before the first price, and stale runs.

    ``share_stale_while_trading``: share of firm-months in the universe whose value is non-zero and has not
    changed for more than ``STALE_MONTHS``. Long runs of zeros are reported separately, as they are usually
    economic (no debt, no dividends, no controversies)."""
    life = uni.groupby("DSCD").agg(first_price_month=("first_price_month", "first"),
                                   last_price_month=("last_price_month", "first"),
                                   delisting_date=("delisting_date", "first"))
    q = p.merge(life, left_on="DSCD", right_index=True, how="inner")
    n = len(q)
    after_last = q["Date"] > q["last_price_month"]
    before_first = q["Date"] < q["first_price_month"]
    after_delist = q["delisting_date"].notna() & (q["Date"] > month_end(q["delisting_date"]))

    # stale: run length of identical consecutive monthly values (runs are measured on the full series, so a
    # run that started before the stock entered the universe counts from its true start)
    new_run = (p[var] != p.groupby("DSCD")[var].shift(1)) | (p["DSCD"] != p["DSCD"].shift(1))
    run_id = new_run.cumsum()
    pos_in_run = p.groupby(run_id).cumcount() + 1
    long_run = pos_in_run > STALE_MONTHS
    alive = p.set_index(["DSCD", "Date"]).index.isin(uni.set_index(["DSCD", "Date"]).index)
    nonzero = (p[var] != 0).to_numpy()
    stale = long_run & nonzero          # repeated zeros (no debt, no dividend, no controversy) are not stale
    n_alive = max(int(alive.sum()), 1)
    # delisted firms (last price before the end of the universe) with values > 12 months after the last price
    end = uni["Date"].max()
    months_after = ((q["Date"].dt.year - q["last_price_month"].dt.year) * 12
                    + (q["Date"].dt.month - q["last_price_month"].dt.month))
    dead = q[q["last_price_month"] < end]
    padded = (months_after[dead.index] > 12).groupby(dead["DSCD"]).any()
    return {
        "n_obs_matched": int(n),
        "share_dead_firms_padded": float(padded.mean()) if len(padded) else np.nan,
        "share_after_last_price": float(after_last.mean()) if n else np.nan,
        "share_after_delisting": float(after_delist.mean()) if n else np.nan,
        "share_before_first_price": float(before_first.mean()) if n else np.nan,
        "share_stale": float(stale.mean()),
        "share_stale_while_trading": float((stale & alive).sum() / n_alive),
        "share_zero_runs_while_trading": float((long_run & ~nonzero & alive).sum() / n_alive),
        "share_firms_with_stale_run": float(pd.Series(stale & alive).groupby(p["DSCD"].to_numpy()).any().mean()),
    }


# ---------------------------------------------------------------------------------------------------------
# C. Distributions, implausible values, unit jumps
# ---------------------------------------------------------------------------------------------------------

QUANTILES = [0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99]


def yearly_quantiles(p: pd.DataFrame, var: str) -> pd.DataFrame:
    fy = firm_years(p, var)
    q = fy.groupby("Year")[var].quantile(QUANTILES).unstack()
    q.columns = [f"p{int(c * 100)}" for c in q.columns]
    q["n"] = fy.groupby("Year").size()
    return q


def implausible_values(p: pd.DataFrame, var: str, sign: str = "") -> tuple[dict, pd.DataFrame]:
    """Zeros, negatives (violations if ``sign == 'nonneg'``) and extreme values (robust z per year on firm-years;
    log10 for non-negative variables, asinh(x / yearly median |x|) otherwise). Returns (summary, most extreme
    firm-years)."""
    fy = firm_years(p, var)
    x = fy[var]
    summary = {"share_zero": float((x == 0).mean()), "share_negative": float((x < 0).mean())}
    summary["share_sign_violations"] = summary["share_negative"] if sign == "nonneg" else 0.0

    if sign == "nonneg":
        z_in = np.log10(x.where(x > 0))
    else:  # signed, heavy-tailed: scale-free asinh transform per year
        scale = x.abs().groupby(fy["Year"]).transform("median").replace(0, np.nan)
        z_in = np.arcsinh(x / scale)
    med = z_in.groupby(fy["Year"]).transform("median")
    mad = (z_in - med).abs().groupby(fy["Year"]).transform("median") * 1.4826
    mad = mad.where(mad > 1e-9 * (1 + med.abs()))      # (near) constant cross-sections: no z-score
    z = (z_in - med) / mad
    fy = fy.assign(robust_z=z)
    summary["share_extreme"] = float((z.abs() > EXTREME_Z).mean())
    top = fy.loc[z.abs().sort_values(ascending=False).index[:25], ["DSCD", "Year", "Date", var, "robust_z"]]
    return summary, top


def unit_jumps(p: pd.DataFrame, var: str) -> pd.DataFrame:
    """Large scale changes of positive values: a single change by >= 10**JUMP_LOG10, or a change by
    >= 10**REVERSAL_LOG10 that is reversed within REVERSAL_WINDOW months (one row per episode).

    On their own these are mostly economic (SPAC IPOs, mergers; cash and short-term debt can move by orders of
    magnitude). Unit errors are identified across items with ``unit_error_candidates``."""
    ch = change_events(p, var)
    ch = ch[(ch[var] > 0) & (ch["prev"] > 0)].copy()
    if ch.empty:
        return pd.DataFrame(columns=["DSCD", "Date", "prev", var, "log10_ratio", "type"])
    ch["log10_ratio"] = np.log10(ch[var] / ch["prev"])
    ch["next_ratio"] = ch.groupby("DSCD")["log10_ratio"].shift(-1)
    ch["next_date"] = ch.groupby("DSCD")["Date"].shift(-1)
    months_to_next = ((ch["next_date"].dt.year - ch["Date"].dt.year) * 12
                      + (ch["next_date"].dt.month - ch["Date"].dt.month))
    big = ch["log10_ratio"].abs() >= JUMP_LOG10
    rev = ((ch["log10_ratio"].abs() >= REVERSAL_LOG10)
           & ((ch["log10_ratio"] + ch["next_ratio"]).abs() <= REVERSAL_TOL)
           & (months_to_next <= REVERSAL_WINDOW))
    # the change that undoes a reversal belongs to the same episode and is not counted again
    undo = rev.groupby(ch["DSCD"]).shift(1, fill_value=False).astype(bool)
    keep = (big | rev) & ~undo
    out = ch[keep].copy()
    out["type"] = np.where(rev[keep], "reversal", "jump")
    return out[["DSCD", "Date", "prev", var, "log10_ratio", "type"]].reset_index(drop=True)


def unit_error_candidates(jump_tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Firm-months in which several items jump by (nearly) the same power of 1000.

    A unit error (thousands vs. units or millions) scales all items of a firm-year by the same factor, while
    economic events (SPAC IPOs, mergers, spin-offs) change items by different factors. Returns one row per
    firm-month with jumps in at least two items: ``n_vars``, ``variables``, median and spread of the log10
    ratios and ``unit_error`` (True if at least ``UNIT_ERROR_MIN_VARS`` items, spread <=
    ``UNIT_ERROR_MAX_SPREAD`` and median within ``UNIT_ERROR_TOL`` of +-3 or +-6)."""
    tabs = [t.assign(variable=v) for v, t in jump_tables.items() if len(t)]
    if not tabs:
        return pd.DataFrame(columns=["DSCD", "Date", "n_vars", "variables", "median_log10", "spread", "unit_error"])
    j = pd.concat(tabs, ignore_index=True)
    g = j.groupby(["DSCD", "Date"])
    out = pd.DataFrame({
        "n_vars": g["variable"].nunique(),
        "variables": g["variable"].agg(lambda s: ",".join(sorted(set(s)))),
        "median_log10": g["log10_ratio"].median(),
        "spread": g["log10_ratio"].max() - g["log10_ratio"].min(),
    }).reset_index()
    out = out[out["n_vars"] >= 2]
    m = out["median_log10"].abs()
    near = ((m - 3).abs() <= UNIT_ERROR_TOL) | ((m - 6).abs() <= UNIT_ERROR_TOL)
    out["unit_error"] = (out["n_vars"] >= UNIT_ERROR_MIN_VARS) & (out["spread"] <= UNIT_ERROR_MAX_SPREAD) & near
    return out.sort_values(["unit_error", "n_vars"], ascending=False).reset_index(drop=True)


def add_unit_errors(summary: pd.DataFrame, candidates: pd.DataFrame) -> pd.DataFrame:
    """Add ``n_unit_errors`` and ``unit_errors_per_1000_firms`` per variable and recompute the flags."""
    ue = candidates[candidates["unit_error"]]
    counts = ue["variables"].str.split(",").explode().value_counts() if len(ue) else pd.Series(dtype=int)
    s = summary.copy()
    s["n_unit_errors"] = counts.reindex(s.index).fillna(0).astype(int)
    s["unit_errors_per_1000_firms"] = 1000 * s["n_unit_errors"] / s["n_firms_in_universe"].clip(lower=1)
    return _apply_flags(s)


# ---------------------------------------------------------------------------------------------------------
# D. Relations between variables and alignment of update months
# ---------------------------------------------------------------------------------------------------------

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def read_relations(path: str | Path) -> pd.DataFrame:
    """Relations: name, kind (identity | le | range | compare), lhs, rhs, tolerance, lower, upper, condition,
    note. ``condition`` (optional) is an expression that selects the firm-years the relation applies to."""
    rel = pd.read_csv(path, dtype=str, comment="#").fillna("")
    if "condition" not in rel.columns:
        rel["condition"] = ""
    for c in ["tolerance", "lower", "upper"]:
        rel[c] = pd.to_numeric(rel[c], errors="coerce")
    return rel


def _names(expr: str) -> set[str]:
    return set(_NAME.findall(expr or ""))


def december_panel(root, variables: list[str], uni: pd.DataFrame | None = None,
                   load=None, restrict: bool = True) -> pd.DataFrame:
    """December cross-sections (one row per firm and year) of the given variables, with the universe
    columns (MarketCAP, Close, MTBV) joined. Relations are evaluated here so that repeated monthly values do
    not count several times. ``restrict``: keep only firm-years in the price universe."""
    from datastream.preprocessing.firm_data import load_variable
    load = load or (lambda v: load_variable(root, v, align_month_end=True))
    frames = []
    for v in variables:
        d = load(v)
        d = d[d["Date"].dt.month == 12].set_index(["DSCD", "Date"])[v]
        frames.append(d)
    df = pd.concat(frames, axis=1, join="outer").reset_index() if frames else pd.DataFrame(columns=["DSCD", "Date"])
    if uni is not None:
        cols = [c for c in UNIVERSE_VALUE_COLUMNS if c in uni.columns]
        df = df.merge(uni.loc[uni["Date"].dt.month == 12, ["DSCD", "Date"] + cols], on=["DSCD", "Date"],
                      how="inner" if restrict else "left")
    return df


def evaluate_relations(df: pd.DataFrame, relations: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Evaluate each relation on the December panel.

    kinds: ``identity`` |lhs/rhs - 1| <= tolerance; ``le`` lhs <= rhs * (1 + tolerance);
    ``range`` lower <= lhs <= upper; ``compare`` informational (median ratio, rank correlation).
    Returns (summary per relation, violation share per year, examples per relation)."""
    rows, by_year, examples = [], {}, {}
    for r in relations.itertuples():
        cond = getattr(r, "condition", "") or ""
        needed = (_names(r.lhs) | _names(r.rhs) | _names(cond)) - {"abs", "log", "log10", "exp", "and", "or", "not"}
        miss = sorted(n for n in needed if n not in df.columns)
        row = {"relation": r.name, "kind": r.kind, "lhs": r.lhs, "rhs": r.rhs, "condition": cond, "note": r.note}
        if miss:
            rows.append({**row, "status": f"skipped (missing {', '.join(miss)})"})
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            lhs = df.eval(r.lhs, engine="python").astype(float).replace([np.inf, -np.inf], np.nan)
            rhs = (df.eval(r.rhs, engine="python").astype(float).replace([np.inf, -np.inf], np.nan) if r.rhs
                   else pd.Series(np.nan, index=df.index))
        both = lhs.notna() & (rhs.notna() if r.rhs else True)
        if cond:
            both &= df.eval(cond, engine="python").fillna(False).astype(bool)
        l, rr = lhs[both], rhs[both]
        tol = 0.0 if np.isnan(r.tolerance) else r.tolerance
        if r.kind == "identity":
            viol = ((l / rr.replace(0, np.nan)) - 1).abs() > tol
            viol = viol | ((rr == 0) & (l != 0))
        elif r.kind == "le":
            viol = l > rr * (1 + tol) + 1e-12
        elif r.kind == "range":
            viol = (l < r.lower) | (l > r.upper)
        else:
            viol = pd.Series(False, index=l.index)
        ratio = (l / rr.replace(0, np.nan)) if r.rhs else pd.Series(np.nan, index=l.index)
        within = r.kind in ("identity", "compare") and bool(r.rhs)
        row.update({
            "status": "ok" if len(l) else "no overlapping observations",
            "n_firm_years": int(both.sum()),
            "share_violations": float(viol.mean()) if r.kind != "compare" and len(l) else np.nan,
            "median_ratio": float(ratio.median()) if r.rhs else np.nan,
            # among firm-years with a non-zero rhs; 0/0 cases are reported separately
            "share_ratio_within_5pct": float(((ratio.dropna() - 1).abs() <= 0.05).mean())
            if within and ratio.notna().any() else np.nan,
            "share_both_zero": float(((l == 0) & (rr == 0)).mean()) if r.rhs and len(l) else np.nan,
            "spearman": float(l.corr(rr, method="spearman")) if r.rhs and len(l) > 2 else np.nan,
        })
        rows.append(row)
        if r.kind != "compare":
            years = df.loc[both, "Date"].dt.year
            by_year[r.name] = viol.groupby(years).mean()
            ex = df.loc[viol[viol].index, ["DSCD", "Date"] + sorted(needed)].assign(lhs=l[viol], rhs=rr[viol] if r.rhs else np.nan)
            examples[r.name] = ex.head(50)
    return pd.DataFrame(rows), pd.DataFrame(by_year), examples


def update_alignment(panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """For each pair of variables: share of firm-years (both with exactly one value change) in which the
    change happens in the same month. Low values mean the variables are stamped differently."""
    events = {}
    for v, p in panels.items():
        ch = change_events(p, v)
        ch["Year"] = ch["Date"].dt.year
        one = ch[ch.groupby(["DSCD", "Year"])["Date"].transform("size") == 1]
        events[v] = one.set_index(["DSCD", "Year"])["Date"]
    names = list(panels)
    mat = pd.DataFrame(np.nan, index=names, columns=names)
    for i, a in enumerate(names):
        for b in names[i:]:
            j = pd.concat([events[a].rename("a"), events[b].rename("b")], axis=1, join="inner")
            if len(j) >= 50:
                mat.loc[a, b] = mat.loc[b, a] = float((j["a"] == j["b"]).mean())
    return mat


# ---------------------------------------------------------------------------------------------------------
# One call per variable + summary flags
# ---------------------------------------------------------------------------------------------------------

def check_variable(panel: pd.DataFrame, var: str, uni: pd.DataFrame, fye: pd.DataFrame | None = None,
                   sign: str = "", coverage_year: int | None = None) -> dict | None:
    """Run checks A-C for one variable. Returns a dict of tables and a flat summary row (None if empty).

    Coverage, history, matching and padding use all firm-months of the variable. Timing (B1, B2), levels,
    distributions, implausible values and jumps (C) use only firm-months in the price universe, so that
    padded values after delisting, pre-listing history and securities outside the universe (non-common
    stocks, SPAC units, ...) do not distort them. ``coverage_year``: reference year for the coverage numbers
    in the summary (default: the year before the last full year, because recent values, ESG in particular,
    are published with a delay). Jumps are only searched for non-negative level variables (``sign='nonneg'``).
    """
    p = prepare_panel(panel, var)
    if p.empty:
        return None
    pu = in_universe(p, uni)
    cov = coverage_by_month(p, var, uni)
    res = {
        "panel": p,
        "panel_universe": pu,
        "coverage": cov,
        "coverage_by_size": coverage_by_size(p, var, uni) if "size_group" in uni.columns else pd.DataFrame(),
        "yearly": yearly_overview(pu, var, cov) if len(pu) else pd.DataFrame(),
        "updates": update_frequency(pu, var) if len(pu) else pd.DataFrame(),
        "lag": reporting_lag(pu, var, fye),
        "quantiles": yearly_quantiles(pu, var) if len(pu) else pd.DataFrame(),
        "jumps": (unit_jumps(pu, var).rename(columns={var: "value"}) if sign == "nonneg" and len(pu)
                  else pd.DataFrame(columns=["DSCD", "Date", "prev", "value", "log10_ratio", "type"])),
    }
    if len(pu):
        implaus, top = implausible_values(pu, var, sign)
    else:
        implaus, top = {}, pd.DataFrame(columns=["DSCD", "Year", "Date", var, "robust_z"])
    res["extremes"] = top.rename(columns={var: "value"})
    hist = history_stats(p, var)
    match = matching(p, uni)
    stale = stale_and_padding(p, var, uni)

    ly = _last_full_year(uni)
    ref = coverage_year if coverage_year is not None else ly - 1
    cy = cov.assign(Year=cov.index.year).groupby("Year")[["ew_coverage", "vw_coverage"]].mean()
    above = cy.index[cy["vw_coverage"] >= 0.5]
    n_firms_u = int(pu["DSCD"].nunique())
    lag = res["lag"]
    upd = res["updates"]
    summary = {
        "variable": var,
        "first_date": p["Date"].min(), "last_date": p["Date"].max(),
        **hist,
        "n_firms_in_universe": n_firms_u,
        "share_universe_firms_ever_covered": match["share_universe_firms_ever_covered"],
        "share_variable_firms_in_universe": match["share_variable_firms_in_universe"],
        "outside_universe_share": float(cov["n_outside_universe"].sum() / max(len(p), 1)),
        "first_year_vw_cov_50": int(above.min()) if len(above) else np.nan,
        "coverage_ref_year": ref,
        "ew_coverage_ref": float(cy["ew_coverage"].get(ref, np.nan)),
        "vw_coverage_ref": float(cy["vw_coverage"].get(ref, np.nan)),
        "ew_coverage_last_year": float(cy["ew_coverage"].get(ly, np.nan)),
        "vw_coverage_last_year": float(cy["vw_coverage"].get(ly, np.nan)),
        "median_changes_per_year": float(upd["median_changes"].median()) if len(upd) else np.nan,
        "share_firm_years_no_change": float(upd["0"].mean()) if len(upd) and "0" in upd.columns else np.nan,
        "modal_lag_months": int(lag["share"].idxmax()) if len(lag) else np.nan,
        "share_at_modal_lag": float(lag["share"].max()) if len(lag) else np.nan,
        **{k: v for k, v in stale.items() if k != "n_obs_matched"},
        **implaus,
        "n_large_jumps": int(len(res["jumps"])),
    }
    res["summary"] = summary
    return res


FLAG_RULES = [
    ("vw_coverage_ref", lambda x: x >= THRESHOLDS["vw_coverage_ref_min"], "VW coverage (ref. year)"),
    ("share_after_last_price", lambda x: x <= THRESHOLDS["share_after_last_price_max"], "values after last price"),
    ("share_dead_firms_padded", lambda x: x <= THRESHOLDS["share_dead_firms_padded_max"], "padding after delisting"),
    ("share_stale_while_trading", lambda x: x <= THRESHOLDS["share_stale_max"], "stale while trading"),
    ("share_sign_violations", lambda x: x <= THRESHOLDS["share_sign_violations_max"], "sign violations"),
    ("share_extreme", lambda x: x <= THRESHOLDS["share_extreme_max"], "extreme values"),
    ("unit_errors_per_1000_firms", lambda x: x <= THRESHOLDS["unit_errors_per_1000_firms_max"], "unit errors"),
    ("outside_universe_share", lambda x: x <= THRESHOLDS["outside_universe_share_max"], "outside universe"),
]


def _apply_flags(s: pd.DataFrame) -> pd.DataFrame:
    flags = []
    for _, row in s.iterrows():
        f = [label for col, ok, label in FLAG_RULES if col in s.columns and pd.notna(row.get(col)) and not ok(row[col])]
        flags.append("; ".join(f))
    s["n_flags"] = [len(f.split("; ")) if f else 0 for f in flags]
    s["flags"] = flags
    return s


def summary_table(summaries: list[dict]) -> pd.DataFrame:
    """One row per variable with the key numbers and a list of flagged checks (unit errors are added with
    ``add_unit_errors`` once all variables are checked)."""
    return _apply_flags(pd.DataFrame(summaries).set_index("variable"))


def fye_months(static_fye: pd.DataFrame, var: str = "WC05350") -> pd.DataFrame:
    """``DSCD | fye_month`` from the latest snapshot of the static fiscal-year-end variable."""
    d = static_fye.dropna(subset=[var])
    return pd.DataFrame({"DSCD": d["DSCD"].astype(str), "fye_month": pd.to_datetime(d[var]).dt.month})


# ---------------------------------------------------------------------------------------------------------
# Plots (matplotlib; used by the notebook)
# ---------------------------------------------------------------------------------------------------------

# categorical slots 1-8 and the blue sequential ramp of the default dataviz palette
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
INK, INK_MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"


def set_style():
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb", "axes.edgecolor": GRID,
        "axes.labelcolor": INK_MUTED, "xtick.color": INK_MUTED, "ytick.color": INK_MUTED, "text.color": INK,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.spines.top": False,
        "axes.spines.right": False, "lines.linewidth": 1.6, "axes.titlesize": 10, "axes.titleweight": "bold",
        "font.size": 9, "legend.frameon": False,
    })


def _grid(n, ncols=4, w=3.6, h=2.4):
    import matplotlib.pyplot as plt
    nrows = int(np.ceil(n / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(w * ncols, h * nrows), squeeze=False, sharex=True)
    for ax in axes.flat[n:]:
        ax.set_visible(False)
    return fig, list(axes.flat)


def plot_coverage(results: dict):
    """Small multiples: EW and VW coverage of the price universe per variable."""
    results = {v: r for v, r in results.items() if len(r["coverage"])}
    fig, axes = _grid(len(results))
    for ax, (v, r) in zip(axes, results.items()):
        c = r["coverage"]
        ax.plot(c.index, c["ew_coverage"], color=CATEGORICAL[0], label="EW (share of stocks)")
        ax.plot(c.index, c["vw_coverage"], color=CATEGORICAL[1], label="VW (share of market cap)")
        ax.set_ylim(0, 1.02)
        ax.set_title(v, loc="left")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout()
    return fig


def plot_coverage_by_size(results: dict):
    """Small multiples: EW coverage by size group (x) and year (y); darker = higher coverage."""
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    items = [(v, r["coverage_by_size"]) for v, r in results.items() if len(r["coverage_by_size"])]
    fig, axes = _grid(len(items), ncols=5, w=2.6, h=3.2)
    im = None
    for ax, (v, t) in zip(axes, items):
        im = ax.imshow(t.to_numpy(dtype=float), aspect="auto", cmap=cmap, vmin=0, vmax=1)
        ax.set_yticks(range(0, len(t), max(len(t) // 6, 1)), t.index[::max(len(t) // 6, 1)])
        ax.set_xticks(range(t.shape[1]), [f"Q{int(c)}" for c in t.columns])
        ax.grid(False)
        ax.set_title(v, loc="left")
    fig.subplots_adjust(wspace=0.45, hspace=0.3)
    if im is not None:
        fig.colorbar(im, ax=axes[: len(items)], shrink=0.6, label="coverage (Q1 = smallest)")
    return fig


def plot_yearly_levels(results: dict):
    """Small multiples: median and 1%-trimmed mean of firm-year values over time."""
    results = {v: r for v, r in results.items() if len(r["yearly"])}
    fig, axes = _grid(len(results))
    for ax, (v, r) in zip(axes, results.items()):
        y = r["yearly"]
        ax.plot(y.index, y["median"], color=CATEGORICAL[0], label="median")
        ax.plot(y.index, y["mean_trimmed_1pct"], color=CATEGORICAL[1], label="mean (1% trimmed)")
        ax.set_title(v, loc="left")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout()
    return fig


def plot_quantiles(results: dict, signs: dict | None = None):
    """Small multiples: p5-p95 and p25-p75 bands with the median of firm-year values; log scale for
    non-negative variables."""
    signs = signs or {}
    results = {v: r for v, r in results.items() if len(r["quantiles"])}
    fig, axes = _grid(len(results))
    for ax, (v, r) in zip(axes, results.items()):
        q = r["quantiles"]
        ax.fill_between(q.index, q["p5"], q["p95"], color=BLUE_RAMP[1], lw=0, label="p5-p95")
        ax.fill_between(q.index, q["p25"], q["p75"], color=BLUE_RAMP[3], lw=0, label="p25-p75")
        ax.plot(q.index, q["p50"], color=BLUE_RAMP[6], label="median")
        if signs.get(v) == "nonneg" and (q["p5"] > 0).all():
            ax.set_yscale("log")
        ax.set_title(v, loc="left")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=3, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout()
    return fig


def plot_update_frequency(results: dict):
    """Small multiples: share of firm-years with 0 / 1 / 2-3 / 4 / 5+ value changes (stacked, ordinal ramp)."""
    results = {v: r for v, r in results.items() if len(r["updates"])}
    fig, axes = _grid(len(results))
    colors = [BLUE_RAMP[i] for i in (1, 2, 3, 5, 6)]
    for ax, (v, r) in zip(axes, results.items()):
        u = r["updates"].drop(columns="median_changes", errors="ignore")
        bottom = np.zeros(len(u))
        for col, colr in zip([b for _, b in UPDATE_BINS], colors):
            vals = u[col].to_numpy() if col in u.columns else np.zeros(len(u))
            ax.bar(u.index, vals, bottom=bottom, color=colr, width=0.85, label=col,
                   edgecolor="#fcfcfb", linewidth=0.5)
            bottom += vals
        ax.set_ylim(0, 1)
        ax.set_title(v, loc="left")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncol=5, bbox_to_anchor=(0.5, 1.03),
               title="value changes per firm-year")
    fig.tight_layout()
    return fig


def plot_relation_violations(by_year: pd.DataFrame):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 3.6))
    for i, c in enumerate(by_year.columns[:8]):
        ax.plot(by_year.index, by_year[c], color=CATEGORICAL[i], label=c)
    ax.set_ylabel("share of firm-years violating")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.2))
    fig.tight_layout()
    return fig


def plot_matrix(mat: pd.DataFrame, title: str = ""):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    n = len(mat)
    fig, ax = plt.subplots(figsize=(1 + 0.55 * n, 0.8 + 0.5 * n))
    ax.imshow(mat.to_numpy(dtype=float), cmap=cmap, vmin=0, vmax=1)
    ax.set_xticks(range(n), mat.columns, rotation=90)
    ax.set_yticks(range(n), mat.index)
    ax.grid(False)
    for i in range(n):
        for j in range(n):
            v = mat.iat[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7,
                        color="#ffffff" if v > 0.6 else INK)
    ax.set_title(title, loc="left")
    fig.tight_layout()
    return fig
