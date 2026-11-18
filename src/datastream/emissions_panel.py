"""Monthly point-in-time emissions panel (Refinitiv ESG via Datastream) on the filtered price universe.

Timing (see ``CHANGES_ws_dating.md`` and ``scripts/32_check_emissions.py``):

* Datastream shows an annual ESG value in every month of the fiscal year it describes, from the first month of
  that fiscal year on - in the same months as the Worldscope items. A firm's *emission report months* are the
  months in which any emission item changes (plus its first month with data); the values of all items in that
  month form one snapshot.
* Fiscal year end per snapshot: the monthly ``WC05350`` series in the report month (fallback report month + 11),
  as in the baseline panel (``panel_builder.snapshot_fye``).
* Availability: fiscal year end + ``lag_months`` (default **12**; Zhang, "Carbon Returns Across the Globe",
  reports a median lag of about 10 months between fiscal year end and the publication of emission data).
  A snapshot is used for at most ``max_age_months`` after its fiscal year end (default 24), i.e. until the next
  fiscal year arrives or for one year at most.

Variables (tonnes CO2e; readable names):

* ``co2e_scope1`` (ENERDP024), ``co2e_scope2`` (ENERDP025)
* ``co2e_total_reported``: ENERDP023, else scope 1 + scope 2 if both are reported
* ``co2e_total_estimated``: ENERDP123 as delivered (Refinitiv fills gaps with model estimates; equal to the
  reported total where one exists, up to revisions)
* ``co2e_total``: reported total, else the estimate; ``co2e_is_estimated`` = 1 if it comes from the estimate only
* ``sales_fy``: Worldscope net sales (WC01001, thousands of local currency) of the same fiscal year
* ``co2e_intensity`` = co2e_total / (sales_fy / 1000), ``co2e_scope1_intensity``, ``co2e_scope12_intensity``:
  tonnes per million of sales (USD for the US; local currency in the EU - convert before comparing countries)
* ``co2e_growth``: log change of co2e_total against the previous fiscal year (9-15 months earlier), only if both
  years come from the same source (both reported or both estimated)
* ``em_report_month``, ``em_fye_month``, ``em_available_month``, ``em_age_months`` (months since fiscal year end)

Cleaning: negative values are dropped; in each annual series a value that is more than ``unit_factor`` (500)
times smaller or larger than both its neighbouring years is treated as a unit error and dropped (e.g.
Walmart's ENERDP123 = 18.7 in fiscal 2024 next to ~1.8e7).
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from datastream.panel_builder import (
    _months_between,
    _add_months,
    add_returns,
    attach_point_in_time,
    load_wide,
    report_snapshots,
    snapshot_fye,
)
from datastream.preprocessing.firm_data import load_variable

logger = logging.getLogger(__name__)

TOTAL, SCOPE1, SCOPE2, ESTIMATED = "ENERDP023", "ENERDP024", "ENERDP025", "ENERDP123"
EMISSION_VARS = [TOTAL, SCOPE1, SCOPE2, ESTIMATED]
SALES, FYE_VAR = "WC01001", "WC05350"
OUTPUT_VARS = ["co2e_scope1", "co2e_scope2", "co2e_total_reported", "co2e_total_estimated", "co2e_total",
               "co2e_is_estimated", "sales_fy", "co2e_intensity", "co2e_scope1_intensity", "co2e_scope12_intensity",
               "co2e_growth"]
PRICE_COLS = ["Country", "MarketCAP", "MarketCAP_EUR", "Close", "ReturnIndex", "ReturnIndex_EUR", "size_group",
              "size_group_country", "delisting_date"]


@dataclass
class EmissionsConfig:
    lag_months: int = 12                 # months after fiscal year end
    max_age_months: int = 24             # months after fiscal year end
    stamp_to_fye_months: int = 11        # fallback if WC05350 is missing
    unit_factor: float = 500.0
    growth_min_gap: int = 9
    growth_max_gap: int = 15


# ---------------------------------------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------------------------------------

def drop_unit_errors(snaps: pd.DataFrame, var: str, factor: float) -> tuple[pd.DataFrame, int]:
    """Annual series per firm: set a value to NaN if it differs by more than ``factor`` from both neighbours
    (or from its only neighbour at the ends of the series)."""
    s = snaps.sort_values(["DSCD", "report_month"]).copy()
    x = s[var].where(s[var] > 0)
    g = x.groupby(s["DSCD"])
    prev, nxt = g.shift(1), g.shift(-1)
    off = lambda a, b: ((a / b) > factor) | ((b / a) > factor)
    bad_prev, bad_next = off(x, prev), off(x, nxt)
    bad = (bad_prev & bad_next) | (bad_prev & nxt.isna()) | (bad_next & prev.isna())
    bad &= x.notna()
    s.loc[bad, var] = np.nan
    return s, int(bad.sum())


def emission_snapshots(wide: pd.DataFrame, fye_panel: pd.DataFrame | None, cfg: EmissionsConfig
                       ) -> tuple[pd.DataFrame, dict]:
    variables = [v for v in EMISSION_VARS if v in wide.columns]
    w = wide.copy()
    counts = {"negative_values": {}, "unit_errors": {}}
    for v in variables:
        neg = w[v] < 0
        counts["negative_values"][v] = int(neg.sum())
        w.loc[neg, v] = np.nan
    snaps = report_snapshots(w, variables)
    snaps = snaps.dropna(subset=variables, how="all")
    for v in variables:
        snaps, n = drop_unit_errors(snaps, v, cfg.unit_factor)
        counts["unit_errors"][v] = n
    snaps["fye_month"], counts["fiscal_year_end"] = snapshot_fye(snaps, fye_panel, cfg.stamp_to_fye_months, FYE_VAR)
    return snaps.reset_index(drop=True), counts


def sales_by_fiscal_year(sales: pd.DataFrame | None, fye_panel: pd.DataFrame | None, firms: set[str],
                         cfg: EmissionsConfig) -> pd.DataFrame:
    """``DSCD | fye_month | sales_fy``: Worldscope sales of each fiscal year (latest snapshot of that year)."""
    if sales is None or sales.empty:
        return pd.DataFrame(columns=["DSCD", "fye_month", "sales_fy"])
    s = sales[["DSCD", "Date", SALES]].copy()
    s["DSCD"] = s["DSCD"].astype(str)
    s = s[s["DSCD"].isin(firms)]
    snaps = report_snapshots(s, [SALES]).dropna(subset=[SALES])
    snaps["fye_month"], _ = snapshot_fye(snaps, fye_panel, cfg.stamp_to_fye_months, FYE_VAR)
    snaps = snaps.sort_values(["DSCD", "fye_month", "report_month"]).drop_duplicates(["DSCD", "fye_month"], keep="last")
    return snaps.rename(columns={SALES: "sales_fy"})[["DSCD", "fye_month", "sales_fy"]]


def derive(snaps: pd.DataFrame, sales_fy: pd.DataFrame, cfg: EmissionsConfig) -> pd.DataFrame:
    s = snaps.copy()
    for v in EMISSION_VARS:
        if v not in s.columns:
            s[v] = np.nan
    s["co2e_scope1"] = s[SCOPE1]
    s["co2e_scope2"] = s[SCOPE2]
    s["co2e_total_reported"] = s[TOTAL].fillna(s[SCOPE1] + s[SCOPE2])
    s["co2e_total_estimated"] = s[ESTIMATED]
    s["co2e_total"] = s["co2e_total_reported"].fillna(s["co2e_total_estimated"])
    s["co2e_is_estimated"] = np.where(s["co2e_total"].isna(), np.nan,
                                      s["co2e_total_reported"].isna().astype(float))

    s = s.merge(sales_fy, on=["DSCD", "fye_month"], how="left")
    mio = (s["sales_fy"] / 1000).where(s["sales_fy"] > 0)
    s["co2e_intensity"] = s["co2e_total"] / mio
    s["co2e_scope1_intensity"] = s["co2e_scope1"] / mio
    s["co2e_scope12_intensity"] = (s["co2e_scope1"] + s["co2e_scope2"]) / mio

    # growth against the previous fiscal year (one snapshot per fiscal year: the latest)
    fy = s.dropna(subset=["co2e_total"]).sort_values(["DSCD", "fye_month", "report_month"])
    fy = fy.drop_duplicates(["DSCD", "fye_month"], keep="last")[["DSCD", "fye_month", "co2e_total", "co2e_is_estimated"]]
    fy["_key"] = _add_months(fy["fye_month"], -cfg.growth_min_gap)
    right = fy.rename(columns={"fye_month": "_prev_fye", "co2e_total": "_prev", "co2e_is_estimated": "_prev_est"})
    j = pd.merge_asof(fy.sort_values("_key"), right[["DSCD", "_prev_fye", "_prev", "_prev_est"]].sort_values("_prev_fye"),
                      left_on="_key", right_on="_prev_fye", by="DSCD", direction="backward")
    gap = _months_between(j["fye_month"], j["_prev_fye"])
    ok = gap.between(cfg.growth_min_gap, cfg.growth_max_gap) & (j["co2e_is_estimated"] == j["_prev_est"]) \
        & (j["co2e_total"] > 0) & (j["_prev"] > 0)
    j["co2e_growth"] = np.log(j["co2e_total"] / j["_prev"]).where(ok)
    s = s.merge(j[["DSCD", "fye_month", "co2e_growth"]], on=["DSCD", "fye_month"], how="left")
    return s


def availability(snaps: pd.DataFrame, cfg: EmissionsConfig) -> pd.DataFrame:
    s = snaps.copy()
    s["available_month"] = _add_months(s["fye_month"], cfg.lag_months)
    s = s.sort_values(["DSCD", "available_month", "report_month"])
    return s.drop_duplicates(["DSCD", "available_month"], keep="last").reset_index(drop=True)


# ---------------------------------------------------------------------------------------------------------
# panel
# ---------------------------------------------------------------------------------------------------------

def coverage_by_year(m: pd.DataFrame, month: int = 12) -> pd.DataFrame:
    """December cross-sections: stocks with emissions (any, reported, estimated only), EW and VW shares,
    stocks with an intensity."""
    d = m[m["Date"].dt.month == month].copy()
    d["any"] = d["co2e_total"].notna()
    d["reported"] = d["co2e_is_estimated"] == 0
    d["estimated_only"] = d["co2e_is_estimated"] == 1
    d["intensity"] = d["co2e_intensity"].notna()
    d["Year"] = d["Date"].dt.year
    g = d.groupby("Year")
    out = {"n_universe": g.size()}
    for c in ["any", "reported", "estimated_only", "intensity"]:
        out[f"n_{c}"] = g[c].sum()
    for c in ["any", "reported"]:
        out[f"ew_{c}"] = g[c].mean()
        out[f"vw_{c}"] = d["MarketCAP"].where(d[c], 0).groupby(d["Year"]).sum() / g["MarketCAP"].sum()
    return pd.DataFrame(out)


def stock_counts(m: pd.DataFrame, first_year: int = 2002) -> pd.DataFrame:
    """Distinct stocks with prices (universe) and with emissions in the panel, from ``first_year`` on."""
    d = m[m["Date"].dt.year >= first_year]
    rows = {"universe (prices)": d["DSCD"].nunique(),
            "with emissions (reported or estimated)": d.loc[d["co2e_total"].notna(), "DSCD"].nunique(),
            "with reported emissions": d.loc[d["co2e_is_estimated"] == 0, "DSCD"].nunique(),
            "only estimated emissions": (d.loc[d["co2e_total"].notna()].groupby("DSCD")["co2e_is_estimated"].min() == 1).sum(),
            "with intensity": d.loc[d["co2e_intensity"].notna(), "DSCD"].nunique()}
    out = pd.Series(rows, name="n_stocks").to_frame()
    out["n_stock_months"] = [len(d), int(d["co2e_total"].notna().sum()), int((d["co2e_is_estimated"] == 0).sum()),
                             int((d["co2e_is_estimated"] == 1).sum()), int(d["co2e_intensity"].notna().sum())]
    return out


def build_emissions_panel(root, uni: pd.DataFrame, cfg: EmissionsConfig | None = None, load=None
                          ) -> tuple[pd.DataFrame, dict]:
    """Point-in-time emissions on the monthly universe (prices, market cap, returns). Returns (panel, info)."""
    cfg = cfg or EmissionsConfig()
    load = load or (lambda v: load_variable(root, v, align_month_end=True))
    firms = set(uni["DSCD"].astype(str))

    def try_load(v):
        try:
            d = load(v)
            d["DSCD"] = d["DSCD"].astype(str)
            return d
        except (FileNotFoundError, KeyError):
            logger.warning(f"{v} not imported")
            return None

    available = [v for v in EMISSION_VARS if try_load(v) is not None]
    if not available:
        raise SystemExit(f"None of {EMISSION_VARS} imported.")
    wide = load_wide(root, available, firms, load=load)
    fye_panel = try_load(FYE_VAR)
    sales = try_load(SALES)

    snaps, counts = emission_snapshots(wide, fye_panel, cfg)
    snaps = derive(snaps, sales_by_fiscal_year(sales, fye_panel, firms, cfg), cfg)
    snaps = availability(snaps, cfg)

    base = add_returns(uni[["DSCD", "Date"] + [c for c in PRICE_COLS if c in uni.columns]])
    m = attach_point_in_time(base, snaps, OUTPUT_VARS, cfg.max_age_months)
    m = m.rename(columns={"fund_report_month": "em_report_month", "fund_fye_month": "em_fye_month",
                          "fund_available_month": "em_available_month", "fund_age_months": "em_age_months"})

    cov = coverage_by_year(m)
    meta = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "config": asdict(cfg),
        "variables_used": available + [v for v, d in [(FYE_VAR, fye_panel), (SALES, sales)] if d is not None],
        "n_rows": int(len(m)), "n_stocks": int(m["DSCD"].nunique()),
        "first_month": f"{m['Date'].min():%Y-%m}", "last_month": f"{m['Date'].max():%Y-%m}",
        "n_snapshots": int(len(snaps)),
        "cleaning": {k: v for k, v in counts.items() if k != "fiscal_year_end"},
        "fiscal_year_end": counts["fiscal_year_end"],
        "share_with_emissions": float(m["co2e_total"].notna().mean()),
        "share_estimated_where_available": float(m["co2e_is_estimated"].mean()),
        "share_intensity_where_emissions": float(m.loc[m["co2e_total"].notna(), "co2e_intensity"].notna().mean()),
        "median_em_age_months": float(m["em_age_months"].median()),
    }
    return m, {"meta": meta, "coverage_by_year": cov, "stock_counts": stock_counts(m)}


def write_emissions_panel(root, name: str, panel: pd.DataFrame, info: dict):
    import json
    from pathlib import Path
    from datastream.preprocessing.firm_data import PANEL_SUBDIR
    out_dir = Path(root) / PANEL_SUBDIR / "emissions"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.parquet"
    panel.to_parquet(path, index=False)
    info["coverage_by_year"].to_csv(out_dir / f"{name}_coverage_by_year.csv")
    info["stock_counts"].to_csv(out_dir / f"{name}_stock_counts.csv")
    with open(out_dir / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(info["meta"], f, indent=2, default=str)
    return path
