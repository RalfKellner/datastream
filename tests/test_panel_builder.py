"""Point-in-time logic of the baseline panel on synthetic data (values change 3 months after fiscal year end)."""

import numpy as np
import pandas as pd
import pytest

from datastream import firm_evaluation as fe
from datastream.panel_builder import BaselineConfig, build_baseline
from datastream.preprocessing.firm_data import load_variable
from synthetic_firm_data import make_synthetic

VARS = ["WC02999", "WC03051", "WC03251", "WC03255", "WC03501", "WC01001", "WC01751", "WC02003"]
SIGNS = {v: "nonneg" for v in ["WC02999", "WC03051", "WC03251", "WC03255", "WC01001", "WC02003"]}


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    base = tmp_path_factory.mktemp("synth_base")
    info = make_synthetic(base, n_stocks=150)
    uni = fe.build_monthly_universe(info["price_dir"] / "US_data_panel_filtered_0.2.feather",
                                    info["price_dir"] / "statics_filtered_0.2.csv")
    return info, uni


@pytest.fixture(scope="module")
def rolling(synth):
    info, uni = synth
    cfg = BaselineConfig(variables=VARS, signs=SIGNS)
    panel, meta = build_baseline(info["firm_root"], uni, cfg)
    return panel, meta


def raw(info, v):
    d = load_variable(info["firm_root"], v, align_month_end=True)
    return d.set_index(["DSCD", "Date"])[v]


def test_rows_are_the_universe(synth, rolling):
    _, uni = synth
    panel, _ = rolling
    assert len(panel) == len(uni)
    assert not panel.duplicated(["DSCD", "Date"]).any()


def test_no_look_ahead_value_equals_raw_three_months_earlier(synth, rolling):
    info, _ = synth
    panel, _ = rolling
    planted = info["planted"]
    special = set(planted["stale"]) | set(planted["reversal"]) | {planted["jump"], planted["spac"]}
    p = panel[~panel["DSCD"].isin(special) & panel["WC02999"].notna()].copy()
    p["lagged"] = (p["Date"].dt.to_period("M") - 3).dt.to_timestamp(how="end").dt.normalize()
    r = raw(info, "WC02999")
    p["raw_lagged"] = r.reindex(pd.MultiIndex.from_frame(p[["DSCD", "lagged"]])).to_numpy()
    p = p[p["raw_lagged"].notna() & (p["raw_lagged"] > 0)]
    assert len(p) > 1000
    assert np.allclose(p["WC02999"], p["raw_lagged"])
    # and it is NOT the contemporaneous value in the months right after a report
    p["raw_now"] = r.reindex(pd.MultiIndex.from_frame(p[["DSCD", "Date"]])).to_numpy()
    assert (p["raw_now"] != p["WC02999"]).mean() > 0.15      # 3 of 12 months per year differ


def test_zero_debt_is_carried_with_reports(synth, rolling):
    info, _ = synth
    panel, _ = rolling
    z = panel[panel["DSCD"].isin(info["planted"]["zero_debt"]) & panel["WC02999"].notna()]
    assert len(z) > 100
    assert (z["WC03051"] == 0).all()                           # not expired although the value never changes


def test_stale_reporting_expires(synth, rolling):
    info, _ = synth
    panel, meta = rolling
    s = panel[panel["DSCD"].isin(info["planted"]["stale"]) & (panel["Date"].dt.year.isin([2006, 2007]))]
    assert len(s) > 0
    assert s["WC02999"].isna().all()                          # no report since 2003: older than 18 months
    assert panel["fund_age_months"].max() <= 18


def test_cleaning(synth, rolling):
    info, _ = synth
    panel, info_b = rolling
    meta = info_b["meta"]
    assert meta["cleaning"]["sign_violations"]["WC02999"] > 0
    assert (panel["WC02999"].dropna() >= 0).all()
    assert meta["cleaning"]["unit_error_events"] >= 3
    rev = panel[panel["DSCD"].isin(info["planted"]["reversal"])]
    med = rev.groupby("DSCD")["WC02999"].transform("median")
    assert (rev["WC02999"] / med).max() < 50                  # the x1000 year is gone
    spac = panel[panel["DSCD"] == info["planted"]["spac"]]["WC02999"].dropna()
    assert spac.max() / spac.min() > 1000                      # economic jump is kept


def test_returns_and_derived(synth, rolling):
    _, uni = synth
    panel, _ = rolling
    p = panel.sort_values(["DSCD", "Date"])
    one = p[p["DSCD"] == p["DSCD"].iloc[0]]
    expected = one["ReturnIndex"] / one["ReturnIndex"].shift(1) - 1
    assert np.allclose(one["ret"].iloc[1:], expected.iloc[1:])
    assert "bm" in p and "ep" in p and "dy_12m" in p
    bm = p.dropna(subset=["bm"])
    assert np.allclose(bm["bm"], bm["WC03501"] / (bm["MarketCAP"] * 1000))


def test_ff_convention(synth):
    info, uni = synth
    cfg = BaselineConfig(variables=["WC02999"], convention="ff", fye_offset_months=3, signs=SIGNS)
    panel, _ = build_baseline(info["firm_root"], uni, cfg)
    r = raw(info, "WC02999")
    planted = info["planted"]
    special = set(planted["stale"]) | set(planted["reversal"]) | {planted["jump"], planted["spac"]}
    p = panel[~panel["DSCD"].isin(special) & panel["WC02999"].notna()]
    # changes only in June
    ch = p.groupby("DSCD")["WC02999"].diff().fillna(0) != 0
    assert set(p.loc[ch, "Date"].dt.month) <= {6}
    # in June of year Y the value reported in year Y-1 (fiscal year ending in Y-1 for December firms) is used
    row = p[p["Date"] == pd.Timestamp("2008-06-30")].iloc[0]
    fy_value = r.loc[row["DSCD"]]
    assert row["fund_report_month"] < pd.Timestamp("2008-06-30")
    assert np.isclose(row["WC02999"], fy_value.loc[row["fund_report_month"]])


def test_previous_report_values(synth, rolling):
    info, _ = synth
    panel, _ = rolling
    p = panel.dropna(subset=["WC02999", "WC02999_prev"])
    planted = info["planted"]
    special = set(planted["stale"]) | set(planted["reversal"]) | {planted["jump"], planted["spac"]}
    p = p[~p["DSCD"].isin(special)]
    gap = ((p["fund_report_month"].dt.year - p["fund_prev_report_month"].dt.year) * 12
           + p["fund_report_month"].dt.month - p["fund_prev_report_month"].dt.month)
    assert (gap == 12).mean() > 0.95                    # previous fiscal year
    r = raw(info, "WC02999")
    idx = pd.MultiIndex.from_arrays([p["DSCD"], p["fund_prev_report_month"]])
    assert np.allclose(p["WC02999_prev"], r.reindex(idx).to_numpy())
