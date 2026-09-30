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
    r = fe.check_variable(load_variable(root, "WC02999"), "WC02999", uni, fye, sign="nonneg")
    s = r["summary"]
    jumps = r["jumps"]
    planted = info["planted"]
    assert set(jumps.loc[jumps["type"] == "reversal", "DSCD"]) >= set(planted["reversal"]) & set(r["panel"]["DSCD"])
    assert planted["jump"] in set(jumps.loc[jumps["type"] == "jump", "DSCD"])
    assert len(jumps) == len(set(jumps["DSCD"]))       # one episode per firm, reversals not double counted
    assert s["share_sign_violations"] > 0
    assert s["share_dead_firms_padded"] > 0.1
    assert s["modal_lag_months"] == 3
    assert s["median_changes_per_year"] == 1
    flags = fe.summary_table([s]).loc["WC02999", "flags"]
    assert "padding after delisting" in flags and "unit jumps" in flags


def test_relations(synth):
    info, uni = synth
    root = info["firm_root"]
    dec = fe.december_panel(root, ["WC02999", "WC03051", "WC03251", "WC03255", "WC03501", "WC08231"], uni)
    t, by_year, ex = fe.evaluate_relations(dec, fe.read_relations("config/firm_relations.csv"))
    t = t.set_index("relation")
    assert 0.005 < t.loc["total_debt_identity", "share_violations"] < 0.05
    assert t.loc["capex_le_assets", "status"].startswith("skipped")
    assert abs(t.loc["debt_to_equity", "median_ratio"] - 1) < 0.01
    assert "total_debt_identity" in by_year.columns


def test_alignment(synth):
    info, _ = synth
    root = info["firm_root"]
    panels = {v: fe.prepare_panel(load_variable(root, v), v) for v in ["WC02999", "WC01001"]}
    m = fe.update_alignment(panels)
    assert m.loc["WC02999", "WC01001"] > 0.95
