"""Datastream dating of annual Worldscope values: a value is shown from the FIRST month of its fiscal year.

Synthetic firms with December (and June) fiscal year ends; the value of fiscal year Y is shown in all months of
fiscal year Y. With the default ``stamp_to_fye_months=11`` and ``lag_months=4`` the fiscal-2020 value of a
December firm must appear at the end of April 2021 (not in April 2020), and with the Fama-French convention in June 2021.
"""

import numpy as np
import pandas as pd

from datastream.panel_builder import BaselineConfig, build_baseline

DATES = pd.date_range("2010-01-31", "2023-12-31", freq="ME")


def _fy_value(firm: int, fy: int) -> float:
    return 1000.0 * (firm + 1) + fy          # encodes firm and fiscal year


def _data(fye_months=(12, 6)):
    rows, uni = [], []
    for i, fye in enumerate(fye_months):
        for d in DATES:
            fy = d.year if d.month <= fye else d.year + 1          # fiscal year the month belongs to
            rows.append((f"F{i}", d, _fy_value(i, fy)))
            uni.append((f"F{i}", d, 100.0, 1.0, 100.0 + DATES.get_loc(d)))
    wide = pd.DataFrame(rows, columns=["DSCD", "Date", "WC02999"])
    uni = pd.DataFrame(uni, columns=["DSCD", "Date", "MarketCAP", "Close", "ReturnIndex"])
    return wide, uni


def _build(cfg):
    wide, uni = _data()
    load = lambda v: wide[["DSCD", "Date", v]]
    panel, _ = build_baseline(None, uni, cfg, load=load)
    return panel.set_index(["DSCD", "Date"])


def test_rolling_uses_fiscal_year_end_plus_lag():
    p = _build(BaselineConfig(variables=["WC02999"], derived=False))
    dec = p.loc["F0"]
    # fiscal 2020 (Jan-Dec 2020) becomes available at the end of April 2021; March 2021 still has fiscal 2019
    assert dec.loc["2021-03-31", "WC02999"] == _fy_value(0, 2019)
    assert dec.loc["2021-04-30", "WC02999"] == _fy_value(0, 2020)
    assert dec.loc["2021-04-30", "fund_fye_month"] == pd.Timestamp("2020-12-31")
    assert dec.loc["2021-04-30", "fund_age_months"] == 4
    # never a value of a fiscal year that has not ended at least 4 months ago
    fy_used = (dec["WC02999"] - 1000).dropna()
    fy_end = pd.to_datetime(fy_used.astype(int).astype(str) + "-12-31")
    assert ((dec.loc[fy_used.index].index.to_series() - fy_end).dt.days > 110).all()
    # June fiscal year end: fiscal 2021 (Jul 2020 - Jun 2021) from the end of October 2021 on
    jun = p.loc["F1"]
    assert jun.loc["2021-09-30", "WC02999"] == _fy_value(1, 2020)
    assert jun.loc["2021-10-31", "WC02999"] == _fy_value(1, 2021)


def test_ff_june_rule():
    p = _build(BaselineConfig(variables=["WC02999"], convention="ff", derived=False))
    dec = p.loc["F0"]
    assert dec.loc["2021-05-31", "WC02999"] == _fy_value(0, 2019)
    assert dec.loc["2021-06-30", "WC02999"] == _fy_value(0, 2020)
    jun = p.loc["F1"]                        # fiscal 2021 ends June 2021 -> used from June 2022
    assert jun.loc["2022-05-31", "WC02999"] == _fy_value(1, 2020)
    assert jun.loc["2022-06-30", "WC02999"] == _fy_value(1, 2021)


def test_max_age_counts_from_fiscal_year_end():
    p = _build(BaselineConfig(variables=["WC02999"], derived=False, lag_months=4, max_age_months=18))
    assert p["fund_age_months"].max() == 4 + 11        # replaced by the next fiscal year after 12 months
    assert np.isfinite(p["fund_age_months"].dropna()).all()
