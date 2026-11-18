"""Point-in-time emissions panel on synthetic data with the real Datastream convention (annual values shown in
every month of their fiscal year, WC05350 = fiscal year end of the value shown)."""

import numpy as np
import pandas as pd
import pytest

from datastream.emissions_panel import EmissionsConfig, build_emissions_panel

DATES = pd.date_range("2012-01-31", "2024-12-31", freq="ME")


def _data():
    """F0: December FYE, reported scope 1/2 + total, estimate = total. F1: December FYE, estimates only, with a
    unit error in fiscal 2020 (value / 1e6). F2: June FYE, reported from fiscal 2016 on. Sales = 1000 * (fy - 2000)."""
    rows, uni = [], []
    for i, fye_m in enumerate([12, 12, 6]):
        for d in DATES:
            fy = d.year if d.month <= fye_m else d.year + 1
            fye = pd.Timestamp(year=fy, month=fye_m, day=1) + pd.offsets.MonthEnd(0)
            s1, s2 = 100.0 * (fy - 2000), 50.0 * (fy - 2000)
            r = {"DSCD": f"F{i}", "Date": d, "WC05350": fye, "WC01001": 1000.0 * (fy - 2000)}
            if i == 0:
                r.update(ENERDP023=s1 + s2, ENERDP024=s1, ENERDP025=s2, ENERDP123=s1 + s2)
            elif i == 1:
                r.update(ENERDP123=(s1 + s2) * (1e-6 if fy == 2020 else 1.0))
            elif fy >= 2016:
                r.update(ENERDP023=s1 + s2, ENERDP024=s1, ENERDP025=s2, ENERDP123=s1 + s2)
            rows.append(r)
            uni.append((f"F{i}", d, 100.0 + i, 1.0, 100.0 + DATES.get_loc(d), 3))
    wide = pd.DataFrame(rows)
    uni = pd.DataFrame(uni, columns=["DSCD", "Date", "MarketCAP", "Close", "ReturnIndex", "size_group"])
    return wide, uni


def _build(cfg=None):
    wide, uni = _data()
    load = lambda v: wide[["DSCD", "Date", v]].dropna() if v in wide.columns else (_ for _ in ()).throw(FileNotFoundError(v))
    panel, info = build_emissions_panel(None, uni, cfg, load=load)
    return panel.set_index(["DSCD", "Date"]), info


def test_timing_lag12_from_fiscal_year_end():
    p, info = _build()
    f0 = p.loc["F0"]
    # fiscal 2020 (Jan-Dec 2020, shown by Datastream from January 2020) is used from December 2021
    assert f0.loc["2021-11-30", "co2e_total_combined"] == 150 * 19
    assert f0.loc["2021-12-31", "co2e_total_combined"] == 150 * 20
    assert f0.loc["2021-12-31", "em_fye_month"] == pd.Timestamp("2020-12-31")
    assert f0.loc["2021-12-31", "em_age_months"] == 12
    # June FYE: fiscal 2021 (Jul 2020 - Jun 2021) from June 2022
    f2 = p.loc["F2"]
    assert f2.loc["2022-05-31", "co2e_total_combined"] == 150 * 20
    assert f2.loc["2022-06-30", "co2e_total_combined"] == 150 * 21
    assert info["meta"]["fiscal_year_end"]["n_with_fye_variable"] == info["meta"]["fiscal_year_end"]["n_snapshots"]


def test_lag_parameter():
    p, _ = _build(EmissionsConfig(lag_months=6))
    assert p.loc[("F0", "2021-06-30"), "co2e_total_combined"] == 150 * 20
    assert p.loc[("F0", "2021-05-31"), "co2e_total_combined"] == 150 * 19


def test_reported_estimated_flag_and_intensity():
    p, _ = _build()
    r = p.loc[("F0", "2021-12-31")]
    assert r["co2e_is_estimated"] == 0 and r["co2e_total_reported"] == 3000
    assert r["sales_fy"] == 20000 and r["co2e_intensity"] == pytest.approx(3000 / 20)
    assert r["co2e_scope1_intensity"] == pytest.approx(2000 / 20)
    assert r["co2e_growth"] == pytest.approx(np.log(20 / 19))
    e = p.loc[("F1", "2019-12-31")]
    assert e["co2e_is_estimated"] == 1 and np.isnan(e["co2e_total_reported"]) and e["co2e_total_combined"] == 150 * 18     # fiscal 2018, available Dec 2019


def test_unit_error_dropped_and_max_age():
    p, info = _build()
    f1 = p.loc["F1"]
    # fiscal 2020 estimate (unit error) dropped: fiscal 2019 stays until max age 24 after its FYE (Dec 2021)
    assert info["meta"]["cleaning"]["unit_errors"]["ENERDP123"] == 1
    assert f1.loc["2021-11-30", "co2e_total_combined"] == 150 * 19
    assert np.isnan(f1.loc["2022-01-31", "co2e_total_combined"]) or f1.loc["2022-01-31", "co2e_total_combined"] != 150 * 19 * 1e-6
    assert f1.loc["2022-12-31", "co2e_total_combined"] == 150 * 21
    # no growth across the gap
    assert np.isnan(f1.loc["2022-12-31", "co2e_growth"])


def test_no_value_before_fiscal_year_end_plus_lag():
    p, _ = _build()
    d = p.dropna(subset=["co2e_total_combined"])
    assert (pd.Series(d.index.get_level_values("Date"), index=d.index) >= d["em_available_month"]).all()
    assert (d["em_age_months"] >= 12).all()


def test_stock_counts_and_coverage():
    _, info = _build()
    sc = info["stock_counts"]
    assert sc.loc["universe (prices)", "n_stocks"] == 3
    assert sc.loc["only estimated emissions", "n_stocks"] == 1
    cov = info["coverage_by_year"]
    assert cov.loc[2015, "n_reported"] == 1 and cov.loc[2018, "n_reported"] == 2
