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


def test_fye_from_wc05350_handles_transition_year():
    """With the monthly WC05350 series the fiscal year end comes from the data: firm F0 switches from a December
    to a June fiscal year end in 2021 (short fiscal year Jan-Jun 2021, shown from January 2021)."""
    wide, uni = _data(fye_months=(12,))
    fye = []
    for d in DATES:
        if d < pd.Timestamp("2021-01-01"):
            end = pd.Timestamp(f"{d.year}-12-31")
        else:
            end = pd.Timestamp(f"{d.year if d.month <= 6 else d.year + 1}-06-30")
        fye.append(("F0", d, end))
    fye = pd.DataFrame(fye, columns=["DSCD", "Date", "WC05350"])
    # values: one per fiscal period (encoded by its end date)
    wide["WC02999"] = fye["WC05350"].dt.year * 100 + fye["WC05350"].dt.month
    data = {"WC02999": wide, "WC05350": fye}
    panel, info = build_baseline(None, uni, BaselineConfig(variables=["WC02999"], derived=False),
                                 load=lambda v: data[v][["DSCD", "Date", v]])
    p = panel.set_index("Date")
    # short fiscal year ending June 2021: available end of October 2021 (rule +11 would give April 2022)
    assert p.loc["2021-09-30", "WC02999"] == 202012
    assert p.loc["2021-10-31", "WC02999"] == 202106
    assert p.loc["2021-10-31", "fund_fye_month"] == pd.Timestamp("2021-06-30")
    st = info["meta"]["fiscal_year_end"]
    assert st["n_with_fye_variable"] == st["n_snapshots"]
    assert st["share_equal_to_rule"] < 1 and st["n_implausible"] == 0


def test_date_valued_import(tmp_path):
    """WC05350 downloads: Excel date cells (and day numbers) become datetime64 values."""
    from datastream.preprocessing.firm_data import read_firm_file
    rows = [["Name", "AMERICAN AIRLINES - FISCAL PERIOD END DATE", "X - FISCAL PERIOD END DATE"],
            ["Code", "93801Q(WC05350)", "123456(WC05350)"],
            ["CURRENCY", "", ""],
            [pd.Timestamp("2020-01-17"), pd.Timestamp("2020-12-31"), 44196],
            [pd.Timestamp("2020-02-17"), pd.Timestamp("2020-12-31"), None]]
    f = tmp_path / "WC05350_01.xlsx"
    pd.DataFrame(rows[1:], columns=rows[0]).to_excel(f, index=False)
    long, rep = read_firm_file(f, "WC05350")
    assert pd.api.types.is_datetime64_any_dtype(long["WC05350"])
    assert (long["WC05350"] == pd.Timestamp("2020-12-31")).all() and len(long) == 3
    assert rep.n_coerced == 0
