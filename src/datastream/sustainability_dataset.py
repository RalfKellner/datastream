"""Sustainable-finance dataset: emissions panel + TRBC classification + firm controls from the baseline panel.

Inputs (all monthly, keyed by ``DSCD``/``Date`` on the same filtered price universe):

* emissions panel (``43_build_emissions_panel.py``): prices, returns, point-in-time emissions (fiscal year end +
  12 months), intensities, ``co2e_is_estimated``
* baseline panel (``41_build_baseline_panel.py``, rolling): point-in-time Worldscope fundamentals (fiscal year
  end + 4 months), readable names
* statics (``statics_filtered_<p>.csv``): TRBC economic sector / business sector / industry group (names TR1N-TR3N
  and codes TR1-TR3; current classification, not point in time), company name, ISIN, ticker, exchange

Added columns:

* ``is_financial``: TRBC economic sector "Financials" (code 55); intensities per sales are hardly comparable
* ``log_co2e_total_combined``, ``log_co2e_scope1``, ``log_co2e_scope2`` (log of tonnes; zeros -> missing)
* ``co2e_intensity_ind_adj``: log intensity minus the median log intensity of the same TRBC industry group in
  the same month (at least ``min_industry_firms`` firms), i.e. how dirty a firm is relative to its peers
* controls known at the end of month t (definitions as in ``validation.characteristics``): ``log_me``,
  ``log_bm``, ``mom_12_2``, ``op``, ``ag``, ``ep``; plus ``leverage`` = total debt / total assets,
  ``invest_ta`` = capex / total assets, ``roe`` = net income / common equity (positive equity),
  ``sales_growth`` = sales / previous-year sales - 1, ``vol_12m`` = standard deviation of the monthly returns
  t-11..t (12 consecutive months)
* ``ret_next``: return of month t+1 (for return regressions with characteristics known at t)
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

from datastream.naming import PRICE_NAMES, STATIC_NAMES
from datastream.panel_builder import _months_between
from datastream.validation import characteristics

logger = logging.getLogger(__name__)

BASELINE_COLS = ["sales", "sales_prev", "operating_income", "net_income", "total_assets", "total_assets_prev",
                 "common_equity", "total_debt", "capex", "cash", "bm", "ep", "dy_12m", "firm_id",
                 "fund_fye_month", "fund_age_months"]
STATIC_COLS = ["ENAME", "ISIN", "WC05601", "EXMNEM", "TR1N", "TR2N", "TR3N", "TR1", "TR2", "TR3"]
CONTROLS = ["log_me", "log_bm", "mom_12_2", "op", "ag", "ep", "leverage", "invest_ta", "roe", "sales_growth", "vol_12m"]
STATIC_MISSING = {"", "NA", "N/A", "#N/A", "NAN", "NONE", "NULL"}


def _clean_static(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    return s.where(~s.str.upper().isin(STATIC_MISSING))


def industry_adjusted(d: pd.DataFrame, value: str, industry: str, min_firms: int = 5) -> pd.Series:
    """log(value) minus the cross-sectional median of log(value) within industry and month."""
    x = np.log(d[value].where(d[value] > 0))
    keys = [d["Date"], d[industry]]
    med = x.groupby(keys).transform("median")
    n = x.groupby(keys).transform("count")
    return (x - med).where(n >= min_firms)


def add_controls(d: pd.DataFrame) -> pd.DataFrame:
    d = characteristics(d)          # ret_next, log_me, log_bm, mom_12_2, str_1, op, ag, ep (+ me_lag)
    ta = d["total_assets"].where(d["total_assets"] > 0) if "total_assets" in d else np.nan
    if "total_debt" in d:
        d["leverage"] = d["total_debt"] / ta
    if "capex" in d:
        d["invest_ta"] = d["capex"] / ta
    if {"net_income", "common_equity"} <= set(d.columns):
        d["roe"] = d["net_income"] / d["common_equity"].where(d["common_equity"] > 0)
    if {"sales", "sales_prev"} <= set(d.columns):
        d["sales_growth"] = d["sales"] / d["sales_prev"].where(d["sales_prev"] > 0) - 1
    g = d.groupby("DSCD")
    # 12 consecutive monthly returns: ret is missing across gaps, so min_periods=12 enforces consecutiveness
    d["vol_12m"] = g["ret"].rolling(12, min_periods=12).std().reset_index(level=0, drop=True).reindex(d.index)
    return d


def build_sustainability_dataset(emissions: pd.DataFrame, baseline: pd.DataFrame | None, statics: pd.DataFrame | None,
                                 min_industry_firms: int = 5, emissions_only: bool = True) -> tuple[pd.DataFrame, dict]:
    d = emissions.rename(columns={k: v for k, v in PRICE_NAMES.items() if k in emissions.columns}).copy()
    d["DSCD"] = d["DSCD"].astype(str)

    if baseline is not None:
        b = baseline[["DSCD", "Date"] + [c for c in BASELINE_COLS if c in baseline.columns]].copy()
        b["DSCD"] = b["DSCD"].astype(str)
        d = d.merge(b, on=["DSCD", "Date"], how="left", validate="one_to_one")
        missing = [c for c in BASELINE_COLS if c not in baseline.columns]
        if missing:
            logger.info(f"Not in the baseline panel: {missing}")

    if statics is not None:
        st = statics[["DSCD"] + [c for c in STATIC_COLS if c in statics.columns]].copy()
        st["DSCD"] = st["DSCD"].astype(str).str.strip()
        for c in st.columns[1:]:
            st[c] = _clean_static(st[c])
        st = st.drop_duplicates("DSCD").rename(columns=STATIC_NAMES)
        d = d.merge(st, on="DSCD", how="left")
    sector = "trbc_economic_sector"
    if sector in d.columns:
        d["is_financial"] = np.where(d[sector].isna(), np.nan, (d[sector] == "Financials").astype(float))

    for c in ["co2e_total_combined", "co2e_scope1", "co2e_scope2"]:
        if c in d.columns:
            d[f"log_{c}"] = np.log(d[c].where(d[c] > 0))
    if "trbc_industry_group" in d.columns and "co2e_intensity" in d.columns:
        d["co2e_intensity_ind_adj"] = industry_adjusted(d, "co2e_intensity", "trbc_industry_group", min_industry_firms)

    if {"market_cap", "ret"} <= set(d.columns):
        d = add_controls(d)
        d = d.drop(columns=[c for c in ["me_lag"] if c in d.columns])

    if emissions_only:
        d = d[d["co2e_total_combined"].notna()]
    d = d.sort_values(["DSCD", "Date"]).reset_index(drop=True)

    order = (["DSCD", "Date", "company_name", "isin", "ticker", "exchange",
              "trbc_economic_sector", "trbc_business_sector", "trbc_industry_group",
              "trbc_economic_sector_code", "trbc_business_sector_code", "trbc_industry_group_code", "is_financial",
              "ret", "ret_next", "retx", "market_cap", "price", "return_index", "size_group"]
             + [c for c in d.columns if c.startswith(("co2e", "log_co2e"))] + ["sales_fy"]
             + CONTROLS + ["str_1"]
             + [c for c in BASELINE_COLS if c not in ("ep",)]
             + ["em_report_month", "em_fye_month", "em_available_month", "em_age_months"])
    order = list(dict.fromkeys(c for c in order if c in d.columns))
    d = d[order + [c for c in d.columns if c not in order]]
    return d, {"meta": _meta(d, min_industry_firms, emissions_only), "summary_by_year": summary_by_year(d)}


def summary_by_year(d: pd.DataFrame, month: int = 12) -> pd.DataFrame:
    x = d[(d["Date"].dt.month == month) & d["co2e_total_combined"].notna()].copy()
    x["Year"] = x["Date"].dt.year
    g = x.groupby("Year")
    out = {"n_stocks": g.size(), "n_reported": g["co2e_is_estimated"].apply(lambda s: int((s == 0).sum()))}
    if "is_financial" in x:
        out["n_financial"] = g["is_financial"].apply(lambda s: int((s == 1).sum()))
        out["n_no_trbc"] = g["is_financial"].apply(lambda s: int(s.isna().sum()))
    for c in ["co2e_intensity_ind_adj", "log_bm", "op", "leverage", "mom_12_2", "ret_next"]:
        if c in x:
            out[f"share_{c}"] = g[c].apply(lambda s: s.notna().mean())
    if "is_financial" in x:
        nf = x[(x["is_financial"] == 0)]
        out["median_intensity_nonfin"] = nf.groupby("Year")["co2e_intensity"].median()
    return pd.DataFrame(out)


def _meta(d: pd.DataFrame, min_industry_firms: int, emissions_only: bool) -> dict:
    return {
        "created": datetime.now().isoformat(timespec="seconds"),
        "rows": int(len(d)), "stocks": int(d["DSCD"].nunique()),
        "first_month": f"{d['Date'].min():%Y-%m}", "last_month": f"{d['Date'].max():%Y-%m}",
        "emissions_only": emissions_only, "min_industry_firms": min_industry_firms,
        "stocks_reported_ever": int(d.loc[d["co2e_is_estimated"] == 0, "DSCD"].nunique()),
        "share_rows_estimated": float(d["co2e_is_estimated"].mean()),
        "share_rows_financial": float(d["is_financial"].mean()) if "is_financial" in d else None,
        "share_rows_no_trbc": float(d["trbc_industry_group"].isna().mean()) if "trbc_industry_group" in d else None,
        "share_rows_with_fundamentals": float(d["fund_fye_month"].notna().mean()) if "fund_fye_month" in d else None,
        "columns": list(d.columns),
    }
