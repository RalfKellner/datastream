"""The firm-variable checks must catch the errors planted in the synthetic data."""

import pandas as pd
import pytest

from datastream import firm_evaluation as fe
from datastream.preprocessing.firm_data import load_variable
from datastream.preprocessing.static_data import load_static
from synthetic_firm_data import make_synthetic


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    base = tmp_path_factory.mktemp("synth")
    info = make_synthetic(base, n_stocks=150)
    uni = fe.build_monthly_universe(info["price_dir"] / "US_data_panel_filtered_0.2.feather",
                                    info["price_dir"] / "statics_filtered_0.2.csv")
    return info, uni


def test_universe(synth):
    info, uni = synth
    assert not uni.duplicated(["DSCD", "Date"]).any()
    assert uni["Date"].dt.is_month_end.all()
    assert set(uni["size_group"].dropna().unique()) == {1, 2, 3, 4, 5}
    # batch-wise reduction equals a direct computation
    d = pd.read_feather(info["price_dir"] / "US_data_panel_filtered_0.2.feather")
    d["M"] = d["Date"].dt.to_period("M")
    direct = d.sort_values("Date").groupby(["Stock", "M"])["MarketCAP"].last().to_numpy()
    assert len(direct) == len(uni)
    assert abs(uni.sort_values(["DSCD", "Date"])["MarketCAP"].to_numpy() - direct).max() < 1e-9


def test_planted_errors_detected(synth):
    info, uni = synth
    root = info["firm_root"]
    fye = fe.fye_months(load_static(root, "WC05350"))
    planted = info["planted"]
    level = ["WC02999", "WC03051", "WC03251", "WC03255", "WC01001", "WC02003"]
    signs = {v: "nonneg" for v in level}
    res = {v: fe.check_variable(load_variable(root, v), v, uni, fye, sign=signs.get(v, ""))
           for v in level + ["WC03501", "WC08231"]}
    r = res["WC02999"]
    s = r["summary"]
    jumps = r["jumps"]
    in_uni = set(r["panel_universe"]["DSCD"])
    assert set(jumps.loc[jumps["type"] == "reversal", "DSCD"]) >= set(planted["reversal"]) & in_uni
    assert planted["jump"] in set(jumps.loc[jumps["type"] == "jump", "DSCD"])
    assert planted["spac"] in set(jumps["DSCD"])
    assert len(jumps) == len(set(jumps["DSCD"]))       # one episode per firm, reversals not double counted
    assert s["share_sign_violations"] > 0
    assert s["share_dead_firms_padded"] > 0.1
    assert s["modal_lag_months"] == 3
    assert s["median_changes_per_year"] == 1
    assert res["WC08231"]["jumps"].empty               # no jump search for ratios / signed variables

    cand = fe.unit_error_candidates({v: x["jumps"] for v, x in res.items()})
    ue = set(cand.loc[cand["unit_error"], "DSCD"])
    assert ue >= (set(planted["reversal"]) | {planted["jump"]}) & in_uni
    assert planted["spac"] not in ue                    # different factors across items: economic, not units
    summ = fe.add_unit_errors(fe.summary_table([x["summary"] for x in res.values()]), cand)
    assert "padding after delisting" in summ.loc["WC02999", "flags"]
    assert "unit errors" in summ.loc["WC02999", "flags"]
    assert summ.loc["WC08231", "n_unit_errors"] == 0


def test_zero_runs_are_not_stale(synth):
    info, uni = synth
    root = info["firm_root"]
    r = fe.check_variable(load_variable(root, "WC03051"), "WC03051", uni, None, sign="nonneg")
    s = r["summary"]
    assert s["share_zero_runs_while_trading"] > 0
    stale_firms = set(info["planted"]["stale"])
    p = r["panel_universe"]
    zero_firms = set(info["planted"]["zero_debt"]) & set(p["DSCD"])
    assert zero_firms
    # stale share is explained by the planted stale firms only
    share_planted = p["DSCD"].isin(stale_firms).mean()
    assert s["share_stale_while_trading"] <= share_planted


def test_empty_panel_is_skipped(synth):
    info, uni = synth
    assert fe.check_variable(load_variable(info["firm_root"], "WC05350"), "WC05350", uni) is None


def test_relations(synth):
    info, uni = synth
    root = info["firm_root"]
    dec = fe.december_panel(root, ["WC02999", "WC03051", "WC03251", "WC03255", "WC03501", "WC08231"], uni)
    t, by_year, ex = fe.evaluate_relations(dec, fe.read_relations("config/firm_relations.csv"))
    t = t.set_index("relation")
    assert 0.005 < t.loc["total_debt_identity", "share_violations"] < 0.05
    assert t.loc["total_debt_identity", "share_ratio_within_5pct"] > 0.95   # 0/0 cases excluded
    assert t.loc["ebitda_margin", "status"].startswith("skipped")
    assert t.loc["capex_le_assets", "status"].startswith("skipped")
    assert abs(t.loc["debt_to_equity", "median_ratio"] - 1) < 0.01
    assert "total_debt_identity" in by_year.columns


def test_alignment(synth):
    info, _ = synth
    root = info["firm_root"]
    panels = {v: fe.prepare_panel(load_variable(root, v), v) for v in ["WC02999", "WC01001"]}
    m = fe.update_alignment(panels)
    assert m.loc["WC02999", "WC01001"] > 0.95
