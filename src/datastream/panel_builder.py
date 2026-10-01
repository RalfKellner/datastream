"""Build the baseline monthly panel: filtered price universe + Worldscope firm variables, point in time.

Design
------
* **Universe.** One row per stock-month of the filtered price universe (``monthly_universe_<p>.parquet``,
  built by ``scripts/05_build_monthly_universe.py``). Firm data never add rows: values after delisting,
  pre-listing months and securities outside the universe are dropped by construction.
* **Report events.** Datastream stamps Worldscope values at the fiscal period (about one month after fiscal
  year end), and all items of a firm change in the same month. A firm's *report months* are therefore the
  months in which any of its Worldscope items changes (plus its first month with data). At each report month
  the values of all items are taken as one snapshot - including items whose value did not change (e.g. zero
  debt), so their age is reset correctly.
* **Availability (point in time).**
  - ``convention='rolling'`` (default): a snapshot is used from ``report month + lag_months`` on. With the
    default ``lag_months=3`` and report months about one month after fiscal year end, values become available
    about four months after fiscal year end (10-K deadlines are 60-90 days).
  - ``convention='ff'``: Fama-French timing; the fiscal year ending in calendar year t (fiscal year end =
    report month - ``fye_offset_months``) is used from June of t+1.
* **Maximum age.** A snapshot is used for at most ``max_age_months`` after its report month (default 18 for
  rolling, 24 for ff). Firms that stop reporting do not carry old values forward indefinitely.
* **Cleaning (before snapshots).** Negative values of non-negative items (registry ``sign = nonneg``) are set
  to missing. Unit-error episodes (``firm_evaluation.unit_error_candidates``: several items jump by the same
  power of 1000 in one month) are set to missing for the involved items until the item changes again.
* **Previous report.** ``<var>_prev`` holds the value of the latest earlier report at least
  ``prev_min_gap_months`` (9) months older, i.e. normally the previous fiscal year; it becomes available
  together with the current report, so growth rates are point in time as well.
* **Market data and derived variables.** Monthly return from month-end ReturnIndex (consecutive months only,
  so it contains the delisting return applied in 02_filter.py), MarketCAP, Close, MTBV, size group, and
  ``bm`` (common equity / market cap), ``ep`` (net income / market cap), ``dy_12m`` (12-month dividend yield
  from ReturnIndex vs. price), all with the current market cap and the point-in-time fundamentals.
  Worldscope items are in thousands of USD, MarketCAP in millions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from datastream import firm_evaluation as fe
from datastream.preprocessing.firm_data import PANEL_SUBDIR, load_variable

logger = logging.getLogger(__name__)

BASELINE_SUBDIR = PANEL_SUBDIR / "baseline"
WS_UNIT = 1000.0   # Worldscope (thousands) per MarketCAP unit (millions)


@dataclass
class BaselineConfig:
    variables: list[str]
    convention: str = "rolling"          # rolling | ff
    lag_months: int = 3                  # rolling: months after the report month
    max_age_months: int | None = None    # default: 18 (rolling), 24 (ff)
    fye_offset_months: int = 1           # ff: report month - fiscal year end
    signs: dict = field(default_factory=dict)
    clean_unit_errors: bool = True
    derived: bool = True
    # values of the previous report (for growth rates), taken from the latest report at least
    # prev_min_gap_months earlier; stored as <var>_prev
    prev_variables: list[str] = field(default_factory=lambda: ["WC02999", "WC01751", "WC03501", "WC01001"])
    prev_min_gap_months: int = 9

    def resolved_max_age(self) -> int:
        if self.max_age_months is not None:
            return self.max_age_months
        return 18 if self.convention == "rolling" else 24


def _months_between(later: pd.Series, earlier: pd.Series) -> pd.Series:
    return (later.dt.year - earlier.dt.year) * 12 + (later.dt.month - earlier.dt.month)


def _add_months(s: pd.Series, n: int) -> pd.Series:
    return (s.dt.to_period("M") + n).dt.to_timestamp(how="end").dt.normalize()


# ---------------------------------------------------------------------------------------------------------
# Raw firm data: load, clean, wide
# ---------------------------------------------------------------------------------------------------------

def load_wide(root, variables: list[str], firms: set[str] | None = None, load=None) -> pd.DataFrame:
    """Wide raw panel ``DSCD | Date | <vars>`` (month-end dates), restricted to ``firms`` if given."""
    load = load or (lambda v: load_variable(root, v, align_month_end=True))
    frames = []
    for v in variables:
        d = load(v)
        d["DSCD"] = d["DSCD"].astype(str)
        if firms is not None:
            d = d[d["DSCD"].isin(firms)]
        frames.append(d.set_index(["DSCD", "Date"])[v])
    wide = pd.concat(frames, axis=1, join="outer").sort_index().reset_index()
    return wide


def clean_raw(wide: pd.DataFrame, variables: list[str], signs: dict, clean_unit_errors: bool = True
              ) -> tuple[pd.DataFrame, dict]:
    """Set sign violations and unit-error episodes to missing. Returns (cleaned, counts)."""
    w = wide.copy()
    counts = {"sign_violations": {}, "unit_error_events": 0, "unit_error_values": {}}
    for v in variables:
        if signs.get(v) == "nonneg":
            bad = w[v] < 0
            counts["sign_violations"][v] = int(bad.sum())
            w.loc[bad, v] = np.nan

    if clean_unit_errors:
        level = [v for v in variables if signs.get(v) == "nonneg"]
        jumps = {v: fe.unit_jumps(fe.prepare_panel(w[["DSCD", "Date", v]], v), v) for v in level}
        cand = fe.unit_error_candidates(jumps)
        ue = cand[cand["unit_error"]]
        counts["unit_error_events"] = int(len(ue))
        w = w.sort_values(["DSCD", "Date"]).reset_index(drop=True)
        for row in ue.itertuples():
            firm_rows = w.index[(w["DSCD"] == row.DSCD) & (w["Date"] >= row.Date)]
            for v in row.variables.split(","):
                s = w.loc[firm_rows, v]
                # episode: from the jump month until the value changes again
                moved = s.ne(s.iloc[0])
                idx = s.index[: int(moved.to_numpy().argmax())] if moved.any() else s.index
                counts["unit_error_values"][v] = counts["unit_error_values"].get(v, 0) + int(len(idx))
                w.loc[idx, v] = np.nan
    return w, counts


# ---------------------------------------------------------------------------------------------------------
# Report events and snapshots
# ---------------------------------------------------------------------------------------------------------

def report_snapshots(wide: pd.DataFrame, variables: list[str]) -> pd.DataFrame:
    """One row per firm and report month: the firm's first month with data and every month in which at least
    one item changes (a value appears, disappears or differs from the previous month)."""
    w = wide.sort_values(["DSCD", "Date"]).reset_index(drop=True)
    vals = w[variables]
    prev = vals.groupby(w["DSCD"]).shift(1)
    differs = ~((vals == prev) | (vals.isna() & prev.isna()))
    first = w["DSCD"] != w["DSCD"].shift(1)
    # a gap in the monthly series also starts a new report (the data were re-delivered)
    gap = _months_between(w["Date"], w.groupby("DSCD")["Date"].shift(1)) > 1
    event = first | differs.any(axis=1) | gap.fillna(False)
    snaps = w.loc[event, ["DSCD", "Date"] + variables].rename(columns={"Date": "report_month"})
    return snaps.reset_index(drop=True)


def add_previous(snaps: pd.DataFrame, prev_vars: list[str], min_gap: int) -> pd.DataFrame:
    """Add ``<var>_prev`` and ``fund_prev_report_month``: the values of the latest earlier report that is at
    least ``min_gap`` months older (normally the previous fiscal year)."""
    if not prev_vars:
        return snaps
    s = snaps.sort_values(["DSCD", "report_month"]).reset_index(drop=True)
    s["_key"] = _add_months(s["report_month"], -min_gap)
    right = s[["DSCD", "report_month"] + prev_vars].rename(
        columns={"report_month": "fund_prev_report_month", **{v: f"{v}_prev" for v in prev_vars}})
    out = pd.merge_asof(s.sort_values("_key"), right.sort_values("fund_prev_report_month"),
                        left_on="_key", right_on="fund_prev_report_month", by="DSCD", direction="backward")
    return out.drop(columns="_key").sort_values(["DSCD", "report_month"]).reset_index(drop=True)


def availability(snaps: pd.DataFrame, cfg: BaselineConfig) -> pd.DataFrame:
    s = snaps.copy()
    if cfg.convention == "rolling":
        s["available_month"] = _add_months(s["report_month"], cfg.lag_months)
    elif cfg.convention == "ff":
        fye = _add_months(s["report_month"], -cfg.fye_offset_months)
        s["available_month"] = pd.to_datetime((fye.dt.year + 1).astype(str) + "-06-30")
    else:
        raise ValueError(f"unknown convention {cfg.convention!r}")
    # several reports with the same availability: the latest report wins
    s = s.sort_values(["DSCD", "available_month", "report_month"])
    s = s.drop_duplicates(subset=["DSCD", "available_month"], keep="last")
    return s.reset_index(drop=True)


def attach_point_in_time(uni: pd.DataFrame, snaps: pd.DataFrame, variables: list[str], max_age: int,
                         extra: list[str] | None = None) -> pd.DataFrame:
    """For each universe stock-month, the latest snapshot available at that month (and not older than
    ``max_age`` months since its report month)."""
    left = uni.sort_values("Date").reset_index(drop=True)
    extra = [c for c in (extra or []) if c in snaps.columns]
    right = snaps.sort_values("available_month")[["DSCD", "available_month", "report_month"] + variables + extra]
    out = pd.merge_asof(left, right, left_on="Date", right_on="available_month", by="DSCD", direction="backward")
    out["fund_age_months"] = _months_between(out["Date"], out["report_month"])
    too_old = out["fund_age_months"] > max_age
    out.loc[too_old, variables + extra + ["report_month", "available_month"]] = np.nan
    out.loc[too_old, "fund_age_months"] = np.nan
    out = out.rename(columns={"report_month": "fund_report_month", "available_month": "fund_available_month"})
    return out.sort_values(["DSCD", "Date"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------
# Market data and derived variables
# ---------------------------------------------------------------------------------------------------------

def add_returns(m: pd.DataFrame) -> pd.DataFrame:
    """Monthly return and price-only return from month-end ReturnIndex / Close (consecutive months only)."""
    m = m.sort_values(["DSCD", "Date"]).copy()
    g = m.groupby("DSCD")
    consecutive = _months_between(m["Date"], g["Date"].shift(1)) == 1
    m["ret"] = (m["ReturnIndex"] / g["ReturnIndex"].shift(1) - 1).where(consecutive)
    if "Close" in m.columns:
        m["retx"] = (m["Close"] / g["Close"].shift(1) - 1).where(consecutive)
    return m


def add_derived(m: pd.DataFrame) -> pd.DataFrame:
    m = m.sort_values(["DSCD", "Date"]).reset_index(drop=True)
    me = m["MarketCAP"] * WS_UNIT
    if "WC03501" in m.columns:
        m["bm"] = m["WC03501"] / me
    if "WC01751" in m.columns:
        m["ep"] = m["WC01751"] / me
    if {"ret", "retx"} <= set(m.columns):
        # dividend return = total minus price return; delisting adjustments only affect ret, hence clip at 0.
        # min_periods=12 requires 12 consecutive monthly returns (ret is missing across gaps).
        div = (m["ret"] - m["retx"]).clip(lower=0)
        div = div.where(div > 1e-10, 0.0).where(div.notna())        # floating-point noise
        m["dy_12m"] = (div.groupby(m["DSCD"]).rolling(12, min_periods=12).sum()
                       .reset_index(level=0, drop=True).reindex(m.index))
    return m


# ---------------------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------------------

def build_baseline(root, uni: pd.DataFrame, cfg: BaselineConfig, load=None, registry: pd.DataFrame | None = None,
                   statics: pd.DataFrame | None = None) -> tuple[pd.DataFrame, dict]:
    """Build the baseline panel. Returns (panel, metadata).

    ``registry``: with a ``name`` column, the output uses readable names (``naming.output_mapping``) for firm
    variables (also ``_prev``), price and static columns; without it the Datastream mnemonics are kept.
    ``statics``: static data (statics_filtered_<p>.csv); company name, ISIN, ticker, exchange and TRBC
    classification are joined (current values, not point in time)."""
    if "ReturnIndex" not in uni.columns:
        raise ValueError("The monthly universe has no ReturnIndex column. Rebuild it with "
                         "scripts/05_build_monthly_universe.py (current version).")
    variables = list(dict.fromkeys(cfg.variables))
    firms = set(uni["DSCD"].astype(str))
    max_age = cfg.resolved_max_age()

    wide = load_wide(root, variables, firms, load=load)
    wide, cleaning = clean_raw(wide, variables, cfg.signs, cfg.clean_unit_errors)
    prev_vars = [v for v in cfg.prev_variables if v in variables]
    snaps = availability(add_previous(report_snapshots(wide, variables), prev_vars, cfg.prev_min_gap_months), cfg)
    extra = [f"{v}_prev" for v in prev_vars] + (["fund_prev_report_month"] if prev_vars else [])

    base_cols = ["DSCD", "Date"] + [c for c in ["MarketCAP", "Close", "MTBV", "ReturnIndex", "size_group",
                                                 "n_days", "delisting_date"] if c in uni.columns]
    m = add_returns(uni[base_cols])
    m = attach_point_in_time(m, snaps, variables, max_age, extra)
    if cfg.derived:
        m = add_derived(m)
    if statics is not None:
        from datastream.naming import STATIC_NAMES
        cols = [c for c in STATIC_NAMES if c in statics.columns]
        st = statics[["DSCD"] + cols].astype(str).replace({"nan": np.nan, "NA": np.nan, "": np.nan})
        st["DSCD"] = st["DSCD"].str.strip()
        m = m.merge(st.drop_duplicates("DSCD"), on="DSCD", how="left")

    cov = (m.assign(Year=m["Date"].dt.year).groupby("Year")[variables].apply(lambda d: d.notna().mean()))
    names = {}
    if registry is not None and "name" in registry.columns:
        from datastream.naming import output_mapping
        names = output_mapping(m.columns, registry)
        m = m.rename(columns=names)
        cov = cov.rename(columns=names)
    meta = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "config": {**asdict(cfg), "max_age_months": max_age},
        "n_rows": int(len(m)), "n_stocks": int(m["DSCD"].nunique()),
        "first_month": f"{m['Date'].min():%Y-%m}", "last_month": f"{m['Date'].max():%Y-%m}",
        "n_report_snapshots": int(len(snaps)),
        "prev_variables": prev_vars,
        "cleaning": cleaning,
        "column_names": names,      # output name <- Datastream mnemonic / internal column
        "share_with_fundamentals": float(m["fund_report_month"].notna().mean()),
        "median_fund_age_months": float(m["fund_age_months"].median()),
    }
    return m, {"meta": meta, "coverage_by_year": cov}


def write_baseline(root, name: str, panel: pd.DataFrame, info: dict) -> Path:
    out_dir = Path(root) / BASELINE_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.parquet"
    tmp = path.with_suffix(".parquet.tmp")
    panel.to_parquet(tmp, index=False)
    tmp.replace(path)
    (out_dir / f"{name}.json").write_text(json.dumps(info["meta"], indent=2, default=str), encoding="utf-8")
    info["coverage_by_year"].to_csv(out_dir / f"{name}_coverage_by_year.csv")
    return path
