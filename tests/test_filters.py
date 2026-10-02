"""Tests for the filters changed on branch review/fixes-2026-09 (see CHANGES_review.md).

Run with:  uv run pytest -q
All tests use small synthetic data; no Datastream files are needed.
"""
import numpy as np
import pandas as pd
import pytest

from datastream.preprocessing.filter import DSPreprocess
from datastream.utils import determine_monthly_returns


def _panel(stock, returns, start="2020-01-01", delist=None):
    dates = pd.bdate_range(start, periods=len(returns))
    ri = 100 * np.cumprod(1 + np.nan_to_num(np.asarray(returns, dtype=float)))
    return pd.DataFrame({
        "Stock": stock,
        "Date": dates,
        "Return": returns,
        "ReturnIndex": ri,
        "DelistingDate": pd.NaT if delist is None else pd.Timestamp(delist),
    })


# --------------------------------------------------------------------------- filter (1)
@pytest.fixture
def statics_f1():
    return pd.DataFrame({
        "DSCD":  ["A", "B", "C", "D", "E"],
        "TRAC":  ["ORD", "ORD", "PREF", "PREF", np.nan],
        "ENAME": ["ALPHA INC", "BETA PREFERRED SHARES", "GAMMA CORP", "DELTA PREFERRED", "DEAD CO DELIST.01/02/05"],
    })


def test_non_common_landis_requires_both_screens(statics_f1):
    panel = pd.DataFrame({"Stock": list("ABCDE"), "Date": pd.Timestamp("2020-01-02")})
    out = DSPreprocess.filter_non_common_stocks(panel, statics_f1, "UNITED STATES", mode="landis")
    # A: ORD + clean name -> keep; B: ORD but 'PREFERRED' in name -> drop;
    # C: TRAC=PREF -> drop; D: both fail -> drop; E: TRAC missing (unknown) + clean name -> keep
    assert sorted(out.Stock) == ["A", "E"]


def test_non_common_legacy_or_reproduces_old_behaviour(statics_f1):
    panel = pd.DataFrame({"Stock": list("ABCDE"), "Date": pd.Timestamp("2020-01-02")})
    out = DSPreprocess.filter_non_common_stocks(panel, statics_f1, "UNITED STATES", mode="legacy_or")
    assert sorted(out.Stock) == ["A", "B", "C", "E"]


def test_non_common_missing_trac_as_string(statics_f1):
    # statics are cast to str in 02_filter.py -> NaN becomes "nan"
    st = statics_f1.astype(str)
    panel = pd.DataFrame({"Stock": list("ABCDE"), "Date": pd.Timestamp("2020-01-02")})
    out = DSPreprocess.filter_non_common_stocks(panel, st, "UNITED STATES", mode="landis")
    assert sorted(out.Stock) == ["A", "E"]


def test_non_common_status_suffix_is_not_screened():
    st = pd.DataFrame({
        "DSCD":  ["A", "B", "C", "D", "E"],
        "TRAC":  [np.nan, np.nan, np.nan, np.nan, np.nan],
        "ENAME": ["ANDOVER TOGS DEAD - LASD 01/05/96",          # ordinary dead stock -> keep
                  "FIRST VIRGINIA BANKS DEAD - ACQUISITION BY 992305",  # -> keep
                  "SPEEDCOM WIRELESS UNITS DEAD - LASD 17/02/98",       # unit in the name -> drop
                  "MEDFORD BANCORP DEAD - DUPL SEE 510373",             # duplicate line -> drop
                  "COMPUTER POWER UNIT 1/7/91 EXPIRED 01/07/91"],       # unit -> drop
    })
    panel = pd.DataFrame({"Stock": list("ABCDE")})
    out = DSPreprocess.filter_non_common_stocks(panel, st, "UNITED STATES")
    assert sorted(out.Stock) == ["A", "B"]
    plain = DSPreprocess.filter_non_common_stocks(panel, st, "UNITED STATES",
                                                  strip_status_suffix=False, ord_override=False)
    # plain L&S screen removes all but D ("DUPL SEE" is not caught by the pattern "DUPLICATE")
    assert sorted(plain.Stock) == ["D"]


def test_non_common_ord_override_only_for_generic_patterns():
    st = pd.DataFrame({
        "DSCD":  ["A", "B", "C", "D"],
        "TRAC":  ["ORD", "ORD", np.nan, "ORD"],
        "ENAME": ["CONSOLIDATED EDISON", "ARBOR REALTY TRUST", "COMMUNITY TRUST BANCORP",
                  "BANNIX ACQUISITION"],
    })
    panel = pd.DataFrame({"Stock": list("ABCD")})
    out = DSPreprocess.filter_non_common_stocks(panel, st, "UNITED STATES")
    # A: ORD + only generic pattern -> keep; B: 'REALTY ' is not overridable -> drop;
    # C: TRAC not confirmed -> drop; D: SPAC pattern not overridable -> drop
    assert sorted(out.Stock) == ["A"]


def test_duplicate_loc_codes_also_by_isin():
    st = pd.DataFrame({
        "DSCD":   ["A", "B", "C", "D", "E"],
        "LOC":    ["L1", "L2", np.nan, "L4", np.nan],
        "ISIN":   ["IE1", "IE1", "CH1", "CH1", np.nan],
        "ISINID": ["P", "S", "P", "S", "S"],
    })
    panel = pd.DataFrame({"Stock": list("ABCDE")})
    # different LOC -> plain L&S filter keeps everything
    assert sorted(DSPreprocess.filter_duplicate_loc_codes(panel, st, also_by_isin=False).Stock) == list("ABCDE")
    # ISIN rule drops the secondary lines B and D; E has no ISIN and is kept
    assert sorted(DSPreprocess.filter_duplicate_loc_codes(panel, st).Stock) == ["A", "C", "E"]


def test_french_nr_pattern_needs_leading_blank():
    st = pd.DataFrame({"DSCD": ["A", "B"], "TRAC": [np.nan, np.nan],
                       "ENAME": ["SEBDO ENR", "ALPHA NR 2"]})
    out = DSPreprocess.filter_non_common_stocks(pd.DataFrame({"Stock": ["A", "B"]}), st, "FRANCE")
    assert out.Stock.tolist() == ["A"]


# --------------------------------------------------------------------------- filter (4)
def test_foreign_stocks_accepts_geogn_and_short_keys():
    statics = pd.DataFrame({"DSCD": ["A", "B"], "GEOGN": ["GERMANY", "FRANCE"]})
    panel = pd.DataFrame({"Stock": ["A", "B"]})
    assert DSPreprocess.filter_foreign_stocks(panel, statics, "GERMANY").Stock.tolist() == ["A"]
    assert DSPreprocess.filter_foreign_stocks(panel, statics, "Germany").Stock.tolist() == ["A"]
    with pytest.raises(ValueError):
        DSPreprocess.filter_foreign_stocks(panel, statics, "ATLANTIS")


# --------------------------------------------------------------------------- filter (13)
def test_padded_values_keeps_nine_removes_tenth_and_later():
    # 20 trading days, 15 trailing padded (zero) returns, delisting after the last row
    rets = [np.nan, 0.01, -0.02, 0.03, 0.01] + [0.0] * 15
    p = _panel("A", rets, delist="2020-02-28")
    statics = pd.DataFrame({"DSCD": ["A"], "DelistingDate": [pd.Timestamp("2020-02-28")], "ENAME": ["A DELIST.28/02/20"]})
    out = DSPreprocess.filter_padded_values_delistings(p.drop(columns="DelistingDate"), statics, keep_padded=9)
    assert len(out) == 5 + 9
    legacy = DSPreprocess.filter_padded_values_delistings(p.drop(columns="DelistingDate"), statics, keep_padded=0)
    assert len(legacy) == 5


def test_padded_values_truncates_after_delisting_and_ignores_active_stocks():
    rets = [np.nan] + [0.01] * 19
    p = pd.concat([_panel("A", rets), _panel("B", rets)])
    statics = pd.DataFrame({"DSCD": ["A", "B"],
                            "DelistingDate": [p.Date.iloc[9], pd.NaT],
                            "ENAME": ["A DELIST", "B INC"]})
    out = DSPreprocess.filter_padded_values_delistings(p.drop(columns="DelistingDate"), statics)
    assert (out.Stock == "A").sum() == 10     # truncated at delisting date
    assert (out.Stock == "B").sum() == 20     # untouched


# --------------------------------------------------------------------------- filter (19)
def test_nonsense_values():
    p = pd.DataFrame({"UnadjClose": [1.0, 0.0, -2.0, np.nan]})
    out = DSPreprocess.filter_nonsense_values(p)
    assert len(out) == 2 and out.UnadjClose.isna().sum() == 1


# --------------------------------------------------------------------------- delisting return
def test_delisting_return_applied_to_last_row_and_return_index():
    rets = [np.nan, 0.01, 0.02, 0.0, 0.0]
    p = _panel("A", rets, delist="2020-01-31")          # delisting date is AFTER the last row
    ri_before = p.ReturnIndex.iloc[-1]
    out = DSPreprocess.adjust_for_delisting(p, delisting_return=-0.35)
    last = out.iloc[-1]
    assert last.DelistingReturnApplied
    assert last.Return == pytest.approx(-0.35)          # (1+0)*(1-0.35)-1
    assert last.ReturnIndex == pytest.approx(ri_before * 0.65)
    assert out.DelistingReturnApplied.sum() == 1


def test_delisting_return_compounds_with_existing_return():
    p = _panel("A", [np.nan, 0.01, 0.10], delist="2020-01-03")
    out = DSPreprocess.adjust_for_delisting(p, delisting_return=-0.35)
    assert out.Return.iloc[-1] == pytest.approx(1.10 * 0.65 - 1)


def test_delisting_return_reaches_monthly_returns():
    rets = [np.nan] + [0.0] * 30
    p = _panel("A", rets, start="2020-01-01", delist="2020-02-28")
    out = DSPreprocess.adjust_for_delisting(p, delisting_return=-0.35)
    m = determine_monthly_returns(out, "ReturnIndex", "Date").dropna(subset=["MonthlyReturn"])
    assert m.MonthlyReturn.iloc[-1] == pytest.approx(-0.35)


def test_delisting_return_can_be_disabled_and_skips_active_stocks():
    p = pd.concat([_panel("A", [np.nan, 0.01, 0.02], delist="2020-01-03"), _panel("B", [np.nan, 0.01, 0.02])])
    out = DSPreprocess.adjust_for_delisting(p, delisting_return=None)
    assert not out.DelistingReturnApplied.any()
    out = DSPreprocess.adjust_for_delisting(p, delisting_return=-0.35)
    assert out.loc[out.Stock == "B", "DelistingReturnApplied"].sum() == 0


# --------------------------------------------------------------------------- handle_missings
def test_handle_missings_does_not_fill_volume_and_flags_filled_rows():
    dates = pd.bdate_range("2020-01-01", periods=4)
    p = pd.DataFrame({
        "Stock": "A", "Date": dates,
        "Open": [np.nan, 1.0, np.nan, 1.2], "High": [np.nan, 1.1, np.nan, 1.3],
        "Low": [np.nan, 0.9, np.nan, 1.1], "Close": [np.nan, 1.0, np.nan, 1.2],
        "Volume": [np.nan, 100.0, np.nan, 50.0], "ReturnIndex": [np.nan, 10.0, np.nan, 12.0],
        "AdjFactor": [np.nan, 1.0, np.nan, 1.0], "UnadjClose": [np.nan, 1.0, np.nan, 1.2],
    })
    statics = pd.DataFrame({"DSCD": ["A"], "GEOGN": ["UNITED STATES"]})
    out = DSPreprocess.handle_missings(p, statics, "UNITED STATES")
    assert len(out) == 3                              # leading all-missing row dropped (unchanged)
    assert np.isnan(out.Volume.iloc[1])               # volume no longer forward filled
    assert out.Close.iloc[1] == 1.0                   # prices still forward filled
    assert out.IsFilled.tolist() == [False, True, False]
