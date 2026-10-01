"""Mechanics of the validation tools: planted effects must be recovered."""

import numpy as np
import pandas as pd
import pytest

from datastream import firm_evaluation as fe
from datastream import validation as va
from datastream.panel_builder import BaselineConfig, build_baseline
from synthetic_firm_data import make_synthetic

VARS = ["WC02999", "WC03051", "WC03251", "WC03255", "WC03501", "WC01001", "WC01751", "WC02003"]


def planted_cross_section(n_firms=300, n_months=120, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2000-01-31", periods=n_months, freq="ME")
    d = pd.DataFrame({"Date": np.repeat(dates, n_firms), "DSCD": np.tile(np.arange(n_firms).astype(str), n_months)})
    d["x1"] = rng.normal(size=len(d))
    d["x2"] = rng.normal(size=len(d))
    d["ret_next"] = 0.01 * d["x1"] - 0.005 * d["x2"] + rng.normal(0, 0.05, len(d))
    d["MarketCAP"] = np.exp(rng.normal(6, 1, len(d)))
    return d


def test_fama_macbeth_recovers_planted_coefficients():
    d = planted_cross_section()
    summ, coefs = va.fama_macbeth(d, ["x1", "x2"], min_obs=50, winsor=0)
    assert abs(summ.loc["x1", "coef"] - 0.01) < 0.001
    assert abs(summ.loc["x2", "coef"] + 0.005) < 0.001
    assert summ.loc["x1", "t_nw"] > 10 and summ.loc["x2", "t_nw"] < -5
    assert len(coefs) == 120


def test_decile_sorts_spread():
    d = planted_cross_section()
    tab, series = va.decile_sorts(d, "x1", min_obs=50)
    assert tab.loc["EW", "D10-D1"] > 0.025 and tab.loc["EW", "t_nw"] > 10
    assert (np.diff(tab.loc["EW", [f"D{i}" for i in range(1, 11)]].to_numpy(dtype=float)) > -0.002).all()


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    base = tmp_path_factory.mktemp("synth_val")
    info = make_synthetic(base, n_stocks=200, start="1996-01-01", end="2010-12-31")
    uni = fe.build_monthly_universe(info["price_dir"] / "US_data_panel_filtered_0.2.feather",
                                    info["price_dir"] / "statics_filtered_0.2.csv")
    statics = pd.read_csv(info["price_dir"] / "statics_filtered_0.2.csv")
    nyse = set(statics.loc[statics["EXMNEM"] == "NYS", "DSCD"])
    return info, uni, nyse


def test_characteristics_alignment(synth):
    info, uni, _ = synth
    panel, _ = build_baseline(info["firm_root"], uni, BaselineConfig(variables=VARS))
    d = va.characteristics(panel)
    one = d[d["DSCD"] == d["DSCD"].iloc[0]].reset_index(drop=True)
    assert np.isclose(one.loc[20, "ret_next"], one.loc[21, "ret"])
    manual = np.prod(1 + one.loc[9:19, "ret"]) - 1          # months t-11 .. t-1 for t = 20
    assert np.isclose(one.loc[20, "mom_12_2"], manual)
    assert np.isclose(one.loc[20, "str_1"], one.loc[20, "ret"])
    assert d["ag"].notna().any() and d["log_bm"].notna().any()


def test_ff_replication_mechanics(synth):
    info, uni, nyse = synth
    panel, _ = build_baseline(info["firm_root"], uni,
                              BaselineConfig(variables=VARS, convention="ff", fye_offset_months=3))
    rng = np.random.default_rng(0)
    panel = panel.assign(WC01250=panel["WC01001"] * rng.uniform(0.0, 0.3, len(panel)))
    f = va.replicate_ff_factors(panel, nyse)
    assert len(f) > 60 and (f["n_stocks"] > 50).all()
    # small synthetic sample: before 2004 only large firms have data, so some 2x3 cells are empty
    assert f.loc["2005-07":, ["SMB", "HML", "RMW", "CMA"]].notna().all().all()
    # identical returns for all stocks: every long-short factor is zero
    flat = panel.copy()
    flat["ret"] = flat["Date"].dt.month / 1000.0
    f0 = va.replicate_ff_factors(flat, nyse)
    assert f0[["SMB", "HML", "RMW", "CMA"]].abs().max().max() < 1e-12
    cmp = va.compare_factors(f, f.rename(columns={"SMB_BM": "SMB_x"}), pairs={"HML": "HML"})
    assert np.isclose(cmp.loc["HML", "corr"], 1.0)


def test_event_study_finds_planted_reaction():
    rng = np.random.default_rng(3)
    firms = [f"F{i:03d}" for i in range(200)]
    months = pd.date_range("2000-01-31", "2009-12-31", freq="ME")
    snaps = []
    for f in firms:
        for y in range(2001, 2009):
            ni_prev, ni = rng.normal(100, 10), rng.normal(100, 30)
            snaps.append({"DSCD": f, "report_month": pd.Timestamp(f"{y}-03-31"), "WC01751": ni,
                          "WC01751_prev": ni_prev, "WC02999_prev": 1000.0,
                          "fund_prev_report_month": pd.Timestamp(f"{y - 1}-03-31")})
    snaps = pd.DataFrame(snaps)
    ret = pd.DataFrame([(f, m) for f in firms for m in months], columns=["DSCD", "Date"])
    ret["ret"] = rng.normal(0.01, 0.02, len(ret))
    # the market reacts in the month after the report month (k = 1): +/-3% by sign of earnings growth
    s = snaps.assign(Date=snaps["report_month"] + pd.offsets.MonthEnd(1))
    s["shock"] = np.sign(s["WC01751"] - s["WC01751_prev"]) * 0.03
    ret = ret.merge(s[["DSCD", "Date", "shock"]], on=["DSCD", "Date"], how="left").fillna({"shock": 0})
    ret["ret"] += ret["shock"]
    res = va.event_study(snaps, ret, k_min=-2, k_max=6)
    sp = res["spread"]
    assert sp.loc[1, "spread"] > 0.04 and sp.loc[1, "t"] > 10
    assert sp.drop(index=1)["spread"].abs().max() < 0.01
    w = va.window_returns(sp, {"before availability (0..2)": (0, 2), "after (3..6)": (3, 6)})
    assert w.iloc[0]["cum_spread"] > 5 * abs(w.iloc[1]["cum_spread"])
