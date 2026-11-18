"""Sustainable-finance dataset: merge of emissions panel, baseline controls and TRBC statics."""

import numpy as np
import pandas as pd
import pytest

from datastream.emissions_panel import build_emissions_panel
from datastream.sustainability_dataset import build_sustainability_dataset, industry_adjusted

import test_emissions_panel as tep


def _inputs():
    wide, uni = tep._data()
    load = lambda v: wide[["DSCD", "Date", v]].dropna() if v in wide.columns else (_ for _ in ()).throw(FileNotFoundError(v))
    em, _ = build_emissions_panel(None, uni, load=load)
    bl = uni[["DSCD", "Date"]].copy()
    bl["total_assets"], bl["total_assets_prev"] = 1000.0, 800.0
    bl["total_debt"], bl["capex"] = 250.0, 50.0
    bl["net_income"], bl["common_equity"] = 30.0, 300.0
    bl["sales"], bl["sales_prev"] = 500.0, 400.0
    bl["operating_income"] = 60.0
    bl["fund_fye_month"] = pd.Timestamp("2015-12-31")
    st = pd.DataFrame({"DSCD": ["F0", "F1", "F2"], "ENAME": ["A", "B", "C"],
                       "TR1N": ["Energy", "Financials", "NA"], "TR2N": ["Energy", "Banking", "NA"],
                       "TR3N": ["Oil & Gas", "Banking Services", "NA"], "TR1": ["50", "55", ""],
                       "TR2": ["5010", "5510", ""], "TR3": ["501010", "551010", ""]})
    return em, bl, st


def test_merge_and_columns():
    em, bl, st = _inputs()
    d, info = build_sustainability_dataset(em, bl, st)
    assert d["co2e_total_combined"].notna().all()                     # emissions-only by default
    r = d[(d["DSCD"] == "F0") & (d["Date"] == "2021-12-31")].iloc[0]
    assert r["trbc_economic_sector"] == "Energy" and r["trbc_industry_group_code"] == "501010"
    assert r["is_financial"] == 0
    assert r["leverage"] == pytest.approx(0.25) and r["invest_ta"] == pytest.approx(0.05)
    assert r["roe"] == pytest.approx(0.1) and r["sales_growth"] == pytest.approx(0.25)
    assert r["ag"] == pytest.approx(0.25)
    assert r["log_co2e_total_combined"] == pytest.approx(np.log(3000))
    assert d.loc[d["DSCD"] == "F1", "is_financial"].eq(1).all()
    assert d.loc[d["DSCD"] == "F2", "trbc_industry_group"].isna().all()     # 'NA' -> missing
    assert d.loc[d["DSCD"] == "F2", "is_financial"].isna().all()
    # ret_next is the next month's return of the same stock
    f0 = d[d["DSCD"] == "F0"].set_index("Date")
    assert f0.loc["2021-11-30", "ret_next"] == pytest.approx(f0.loc["2021-12-31", "ret"])
    assert info["meta"]["stocks"] == 3


def test_all_rows_keeps_universe():
    em, bl, st = _inputs()
    d, _ = build_sustainability_dataset(em, bl, st, emissions_only=False)
    assert len(d) == len(em)


def test_industry_adjusted():
    d = pd.DataFrame({"Date": pd.Timestamp("2020-12-31"), "ind": ["a"] * 5 + ["b"] * 2,
                      "x": [1.0, 2.0, 4.0, 8.0, 16.0, 1.0, 10.0]})
    adj = industry_adjusted(d, "x", "ind", min_firms=5)
    assert adj.iloc[2] == pytest.approx(0.0) and adj.iloc[4] == pytest.approx(np.log(4))
    assert adj.iloc[5:].isna().all()                                    # industry b: fewer than 5 firms
