"""Brute-force checks of the portfolio construction in datastream.evaluation."""
import numpy as np
import pandas as pd

from datastream import evaluation as ev


def _panel(seed=0, n_stocks=20, n_days=300):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    rows = []
    for k in range(n_stocks):
        r = rng.normal(0, 0.02, n_days)
        ri = 100 * np.cumprod(1 + r)
        rows.append(pd.DataFrame({"Stock": f"S{k}", "Date": dates, "Return": r,
                                  "ReturnIndex": ri, "MarketCAP": ri * rng.uniform(1, 50)}))
    df = pd.concat(rows, ignore_index=True)
    return df.drop(df.sample(frac=0.05, random_state=seed).index)   # create gaps


def test_daily_vw_uses_previous_trading_day_market_cap():
    df = _panel()
    pf = ev.daily_portfolios(df)
    cal = np.sort(df["Date"].unique())
    for t in [5, 100, 250]:
        today = df[df.Date == cal[t]].set_index("Stock")
        prev = df[df.Date == cal[t - 1]].set_index("Stock")
        j = today[["Return"]].join(prev[["MarketCAP"]], how="inner").dropna()
        assert np.isclose((j.Return * j.MarketCAP).sum() / j.MarketCAP.sum(), pf.loc[cal[t], "VW"])
        assert len(j) == pf.loc[cal[t], "N"]


def test_monthly_weights_sum_to_one_and_match_vw():
    df = _panel()
    m = ev.monthly_panel(df)
    w = ev.monthly_weights(m)
    assert np.allclose(w.groupby("Month")["Weight"].sum(), 1)
    pf = ev.monthly_portfolios(m)
    manual = (w["Weight"] * w["MonthlyReturn"]).groupby(w["Month"]).sum()
    assert np.allclose(manual, pf["VW"].reindex(manual.index))
