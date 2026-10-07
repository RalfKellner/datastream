"""Tests for datastream.eu_evaluation on small synthetic data."""
import numpy as np
import pandas as pd
import pytest

from datastream import eu_evaluation as ev


@pytest.fixture
def uni():
    months = pd.date_range("2000-01-31", periods=4, freq="ME")
    rows = []
    for d, c, me, ri in [("A", "GERMANY", 100.0, [100, 110, 121, 133.1]),
                         ("B", "SWEDEN", 300.0, [100, 100, 90, 99])]:
        for i, m in enumerate(months):
            rows.append({"DSCD": d, "Date": m, "Country": c, "MarketCAP_EUR": me, "ReturnIndex_EUR": ri[i],
                         "ReturnIndex": ri[i] * (1 if c == "GERMANY" else 10 * (1 + 0.01 * i))})
    return pd.DataFrame(rows)


def test_vw_and_country_returns(uni):
    m = ev.monthly_returns(uni)
    vw = ev.vw_returns(m)
    # Feb: A +10% (weight 100), B 0% (weight 300) -> 2.5%
    assert vw.loc[pd.Period("2000-02", "M")] == pytest.approx(0.025)
    by = ev.vw_returns(m, by="Country")
    assert by.loc[pd.Period("2000-03", "M"), "SWEDEN"] == pytest.approx(-0.1)
    assert ev.vw_returns(m, countries=["GERMANY"]).loc[pd.Period("2000-02", "M")] == pytest.approx(0.1)


def test_gap_month_gets_no_return(uni):
    m = ev.monthly_returns(uni[~((uni.DSCD == "A") & (uni.Date == "2000-02-29"))])
    a = m[m.DSCD == "A"].set_index("Month")
    assert np.isnan(a.loc[pd.Period("2000-03", "M"), "ret_eur"])     # Jan -> Mar is not consecutive


def test_currency_conversion_roundtrip():
    idx = pd.period_range("2000-01", periods=3, freq="M")
    fx = pd.Series([1.0, 1.1, 1.0], index=idx)
    r = pd.Series([np.nan, 0.05, -0.02], index=idx, name="VW")
    usd = ev.eur_to_usd(r, fx)
    assert usd.iloc[0] == pytest.approx(1.05 * 1.1 - 1)
    assert ev.usd_to_eur(usd, fx).values == pytest.approx(r.dropna().values)


def test_local_vs_eur_and_top_n(uni):
    m = ev.monthly_returns(uni)
    t = ev.local_vs_eur(m, {"GERMANY": "1999-01-01"}).set_index(["Country", "period"])
    assert t.loc[("GERMANY", "after"), "share_differs"] == 0
    assert t.loc[("SWEDEN", "no euro"), "share_differs"] == 1
    top1 = ev.top_n_portfolio(m, n=1)
    assert top1.loc[pd.Period("2000-02", "M")] == pytest.approx(0.0)  # B is the largest


def test_fx_month_end_from_daily(tmp_path):
    p = tmp_path / "DEXUSEU.csv"
    p.write_text("observation_date,DEXUSEU\n2000-01-28,1.0\n2000-01-31,1.1\n2000-02-01,.\n2000-02-29,1.2\n")
    s = ev.fx_month_end(str(p))
    assert s.loc[pd.Period("2000-01", "M")] == 1.1 and s.loc[pd.Period("2000-02", "M")] == 1.2


def test_ds_market_country_names():
    from datastream.eu_evaluation import ds_market_by_country, ds_market_country
    assert ds_market_country("UK-DS Market") == "UNITED KINGDOM"
    assert ds_market_country("CZECH REP.-DS Market") == "CZECH REPUBLIC"
    assert ds_market_country("SWITZ-DS Market") == "SWITZERLAND"
    assert ds_market_country("NETHERLAND-DS Market") == "NETHERLANDS"
    assert ds_market_country("LUXEMBURG-DS Market") == "LUXEMBOURG"
    assert ds_market_country("GERMANY-DS Market - TOT RETURN IND") == "GERMANY"
    assert ds_market_country("Code") is None
    idx = pd.DataFrame({"UK-DS Market": [0.01], "SRI LANKA-DS Market": [0.02], "x": [0.0]})
    out = ds_market_by_country(idx, countries=["UNITED KINGDOM", "GERMANY"])
    assert list(out.columns) == ["UNITED KINGDOM"]


def test_lead_lag_corr_detects_shift():
    import numpy as np
    from datastream.eu_evaluation import lead_lag_corr
    rng = np.random.default_rng(1)
    idx = pd.period_range("2000-01", periods=200, freq="M")
    x = pd.Series(rng.normal(size=200), index=idx)
    ours = pd.DataFrame({"A": x})
    bench = pd.DataFrame({"A": x.shift(1)})          # benchmark dated one month late
    res = lead_lag_corr(ours, bench)
    assert res.loc["A", "best_lag"] == 1 and res.loc["A", "lag_+1"] > 0.99
