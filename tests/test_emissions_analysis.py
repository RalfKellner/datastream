"""Firm-year sample, fixed-effects regressions and industry tables (datastream.emissions_analysis)."""

import numpy as np
import pandas as pd
import pytest

from datastream import emissions_analysis as ea


def synthetic(n_firms=300, years=range(2010, 2022), seed=0):
    """Monthly dataset: each fiscal-year value is used for 12 months (FYE+12 .. FYE+23). True model:
    log E = 0.8 log sales + 0.3 log ME + industry effect + year effect + firm effect + noise."""
    rng = np.random.default_rng(seed)
    inds = [f"ind{i}" for i in range(8)]
    ind_eff = dict(zip(inds, rng.normal(0, 1.5, 8)))
    rows, mc = [], []
    for f in range(n_firms):
        ind = inds[f % 8]
        fin = 1.0 if f % 25 == 0 else 0.0
        firm_eff = rng.normal(0, 0.3)
        size = rng.normal(13, 1.5)
        for y in years:
            fye = pd.Timestamp(f"{y}-12-31")
            log_sales = size + rng.normal(0, 0.2)
            log_me = 0.7 * log_sales + rng.normal(-3, 0.5)
            log_e = 0.8 * log_sales + 0.3 * log_me + ind_eff[ind] + 0.05 * (y - 2010) + firm_eff + rng.normal(0, 0.3)
            sales = np.exp(log_sales)
            e = np.exp(log_e)
            mc.append((f"F{f}", fye, np.exp(log_me)))
            for k in range(12, 24):
                d = (fye + pd.offsets.MonthEnd(k))
                rows.append({"DSCD": f"F{f}", "Date": d, "em_fye_month": fye, "em_available_month": fye + pd.offsets.MonthEnd(12),
                             "fund_fye_month": fye if k < 16 else fye + pd.offsets.MonthEnd(12),
                             "co2e_total_combined": e, "co2e_scope1": 0.7 * e, "co2e_scope2": 0.3 * e,
                             "co2e_is_estimated": float(f % 3 == 0), "sales_fy": sales,
                             "co2e_intensity": e / (sales / 1000), "co2e_scope1_intensity": 0.7 * e / (sales / 1000),
                             "log_co2e_total_combined": np.log(e), "log_co2e_scope1": np.log(0.7 * e),
                             "log_co2e_scope2": np.log(0.3 * e),
                             "common_equity": 0.5 * np.exp(log_me) * 1000, "total_assets": sales, "total_debt": 0.3 * sales,
                             "capex": 0.05 * sales, "net_income": 0.1 * sales, "leverage": 0.3, "invest_ta": 0.05,
                             "roe": 0.2, "sales_growth": 0.05,
                             "is_financial": fin, "trbc_industry_group": ind, "trbc_economic_sector": f"sec{f % 4}"})
    ds = pd.DataFrame(rows)
    em = pd.DataFrame(mc, columns=["DSCD", "Date", "MarketCAP"])
    return ds, em


def test_firm_year_sample():
    ds, em = synthetic(n_firms=50, years=range(2015, 2018))
    fy, counts = ea.firm_year_sample(ds, em)
    assert counts["firm_years"] == 50 * 3
    assert len(fy) == 48 * 3                                   # 2 financials dropped
    assert fy.groupby(["DSCD", "fiscal_year"]).size().max() == 1
    assert (fy["Date"] == fy["em_available_month"]).all()       # first month of use
    assert counts["share_fundamentals_same_fiscal_year"] == 1.0
    assert counts["share_market_cap_at_fye"] == 1.0
    assert fy["log_bm"].round(6).eq(round(np.log(0.5), 6)).all()


def test_regression_recovers_coefficients():
    ds, em = synthetic()
    fy, _ = ea.firm_year_sample(ds, em, winsor=None)
    res, s = ea.fe_regression(fy, "log_co2e_total_combined", ["log_sales", "log_me"])
    assert res.params["log_sales"] == pytest.approx(0.8, abs=0.1)
    assert res.params["log_me"] == pytest.approx(0.3, abs=0.1)
    # intensity = E / S: coefficient on log sales is 0.8 - 1
    res_i, _ = ea.fe_regression(fy, "log_co2e_intensity", ["log_sales", "log_me"])
    assert res_i.params["log_sales"] == pytest.approx(-0.2, abs=0.1)
    # one-way and industry x year clustering run as well
    r1, _ = ea.fe_regression(fy, "log_co2e_total_combined", ["log_sales"], cluster=("DSCD",))
    r2, _ = ea.fe_regression(fy, "log_co2e_total_combined", ["log_sales"], cluster=("trbc_industry_group", "fiscal_year"))
    assert np.isfinite(r1.bse["log_sales"]) and np.isfinite(r2.bse["log_sales"])


def test_regression_table_and_industry_tables():
    ds, em = synthetic(n_firms=120, years=range(2015, 2020))
    fy, _ = ea.firm_year_sample(ds, em)
    tab = ea.regression_table(fy, {"(1)": ("log_co2e_total_combined", ["log_sales"]),
                                   "(2)": ("log_co2e_total_combined", ["log_sales", "log_me", "leverage"])})
    assert tab.loc[("N", ""), "(1)"] == len(fy)
    assert tab.loc[("R2", ""), "(2)"] >= tab.loc[("R2 FE only", ""), "(2)"]
    assert np.isnan(tab.loc[("log_me", "coef"), "(1)"])
    it = ea.industry_table(fy[fy["fiscal_year"] == 2019])
    assert it["share_of_total"].sum() == pytest.approx(1.0)
    assert set(it.columns) >= {"total_mt", "median_intensity", "aggregate_intensity", "sector"}
    ts = ea.aggregate_intensity_by_year(fy, reported_only=True)
    assert ts.index.min() == 2015 and ts.shape[1] == 4


def test_market_cap_fallback_and_formatting():
    ds, em = synthetic(n_firms=40, years=range(2015, 2018))
    # F1 not in the universe at its fiscal year end 2016, but two months later
    em = em[~((em["DSCD"] == "F1") & (em["Date"] == "2016-12-31"))]
    em = pd.concat([em, pd.DataFrame({"DSCD": ["F1"], "Date": [pd.Timestamp("2017-02-28")], "MarketCAP": [123.0]})])
    fy, counts = ea.firm_year_sample(ds, em)
    assert counts["share_market_cap_at_fye"] < 1 and counts["share_market_cap_after_fallback"] == 1
    assert fy.loc[(fy["DSCD"] == "F1") & (fy["fiscal_year"] == 2016), "me_fye"].iloc[0] == 123.0

    tab = ea.regression_table(fy, {"(1)": ("log_co2e_total_combined", ["log_sales"]),
                                   "(2)": ("log_co2e_total_combined", ["log_sales", "log_me"])})
    f = ea.format_table(tab)
    assert not f.isna().any().any()
    assert f.loc["log_me", "(1)"] == "" and f.loc["log_sales", "(1)"].endswith("***")
    assert f.iloc[1, 0].startswith("(")

    m = ea.missing_overview(fy.assign(log_bm=np.where(fy.index % 10 == 0, np.nan, fy["log_bm"])),
                            "log_co2e_total_combined", ["log_sales", "log_bm"])
    assert m.loc["log_bm", "lost_by_this_step"] == (fy.index % 10 == 0).sum()


def test_dependent_winsorized_levels_raw():
    ds, em = synthetic(n_firms=200, years=range(2015, 2017))
    ds.loc[ds["DSCD"] == "F1", ["co2e_intensity", "log_co2e_total_combined"]] = [1e6, -5.0]
    fy, counts = ea.firm_year_sample(ds, em)
    f1 = fy[fy["DSCD"] == "F1"]
    assert (f1["log_co2e_intensity"] < np.log(1e6)).all()                       # clipped
    assert (f1["log_co2e_total_combined"] > -5).all()
    assert (f1["co2e_intensity"] == 1e6).all()                                  # raw level kept
    assert counts["winsorized_dependent"]["log_co2e_intensity"] > 0
    fy_raw, _ = ea.firm_year_sample(ds, em, winsor_dependent=False)
    assert (fy_raw.loc[fy_raw["DSCD"] == "F1", "log_co2e_total_combined"] == -5).all()
