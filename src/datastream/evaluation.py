"""Sanity checks of a filtered Datastream price panel against the Fama-French market factor.

The functions are used by analyses/01_market_sanity_checks.ipynb. They expect the output of
scripts/02_filter.py (columns 'Stock', 'Date', 'ReturnIndex', 'MarketCAP', 'Return', ...).

Conventions
-----------
* Monthly returns come from month-end ReturnIndex values (utils.determine_monthly_returns).
* Portfolio weights use the market cap at the end of the PREVIOUS calendar month (monthly) or of the
  PREVIOUS trading day (daily). A stock whose previous month / day is missing gets no weight.
* All returns are decimals. The FF market return is Mkt-RF + RF.
"""
import numpy as np
import pandas as pd
import statsmodels.api as sm

from datastream.utils import (
    determine_monthly_returns,
    equally_weighted_portfolio,
    value_weighted_portfolio,
)

# Heuristic thresholds for the pass/check flags in the summary table. They are rules of thumb for a
# broad U.S. common-stock universe versus the CRSP-based FF market, not values from the literature.
THRESHOLDS = {
    "monthly_corr": 0.99,
    "monthly_r2": 0.98,
    "monthly_beta": (0.95, 1.05),
    "monthly_alpha_abs_t": 2.0,
    "monthly_te_annual": 0.02,
    "daily_corr": 0.98,
    "max_weight": 0.10,
}


# ----------------------------------------------------------------------------------------- monthly
def monthly_panel(df, price_column="ReturnIndex", mcap_column="MarketCAP"):
    """Stock-month panel with month-end ReturnIndex, MarketCAP and MonthlyReturn.

    Uses utils.determine_monthly_returns per stock. Unlike analyses/00_data_universe_check.ipynb, rows
    with a missing MonthlyReturn are NOT dropped: determine_monthly_returns resamples to a gap-free
    monthly grid, so keeping all rows makes the one-row shift inside value_weighted_portfolio equal to
    a one-calendar-month lag. Dropping them first would let a stock with a gap month be weighted with
    a market cap from two or more months earlier.
    """
    cols = ["Date", price_column, mcap_column]
    m = (
        df.groupby("Stock", group_keys=True)[cols]
        .apply(lambda x: determine_monthly_returns(x, price_column=price_column, date_column="Date"))
        .reset_index()
        .sort_values(["Stock", "Date"])
        .reset_index(drop=True)
    )
    m["Month"] = m["Date"].dt.to_period("M")
    return m


def monthly_portfolios(m, return_column="MonthlyReturn", mcap_column="MarketCAP"):
    """EW and VW monthly portfolio returns (PeriodIndex 'M') using the helpers in utils."""
    vw = value_weighted_portfolio(m, return_column, mcap_column, "Stock", "Date")
    ew = equally_weighted_portfolio(m, return_column, mcap_column, "Stock", "Date")
    out = pd.DataFrame({
        "VW": vw.set_index(vw["Date"].dt.to_period("M"))["PortfolioReturn"],
        "EW": ew.set_index(ew["Date"].dt.to_period("M"))["PortfolioReturn"],
    })
    out.index.name = "Month"
    return out


def monthly_weights(m, mcap_column="MarketCAP"):
    """Stock-month weights of the VW portfolio (lagged market cap, same eligibility as utils)."""
    w = m.sort_values(["Stock", "Date"]).copy()
    w["LagMCAP"] = w.groupby("Stock")[mcap_column].shift(1)
    w = w[w["LagMCAP"].notna() & w["MonthlyReturn"].notna()]
    w["Weight"] = w["LagMCAP"] / w.groupby("Month")["LagMCAP"].transform("sum")
    return w[["Stock", "Month", "LagMCAP", "Weight", "MonthlyReturn"]]


# ------------------------------------------------------------------------------------------- daily
def daily_portfolios(df, return_column="Return", mcap_column="MarketCAP"):
    """EW and VW daily portfolio returns.

    The weight of a stock on day t is its market cap on the previous trading day (previous date of the
    panel's trading calendar). If that day is missing for the stock (e.g. removed by a filter), the stock
    gets no weight on day t.
    """
    d = df[["Stock", "Date", return_column, mcap_column]].sort_values(["Stock", "Date"])
    calendar = pd.Index(np.sort(d["Date"].unique()))
    day_idx = calendar.get_indexer(d["Date"])
    same_stock_prev = d["Stock"].eq(d["Stock"].shift(1)).to_numpy()
    prev_day_idx = np.r_[-10, day_idx[:-1]]
    consecutive = pd.Series(same_stock_prev & (day_idx - prev_day_idx == 1), index=d.index)
    lag_mcap = d[mcap_column].shift(1).where(consecutive)

    valid = lag_mcap.notna() & d[return_column].notna()
    r = d.loc[valid, return_column]
    w = lag_mcap[valid]
    dates = d.loc[valid, "Date"]

    vw = (r * w).groupby(dates).sum() / w.groupby(dates).sum()
    ew = r.groupby(dates).mean()
    n = r.groupby(dates).size()
    return pd.DataFrame({"VW": vw, "EW": ew, "N": n})


# ------------------------------------------------------------------------------ comparison to FF
def _ols(y, x, hac_lags):
    X = sm.add_constant(x)
    return sm.OLS(y, X, missing="drop").fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})


def compare_to_market(port, ff, periods_per_year, hac_lags):
    """Compare a portfolio return series with the FF market.

    port : Series of portfolio returns (index aligned with ff)
    ff   : DataFrame with 'Mkt-RF' and 'RF' (decimals)
    Returns (stats dict, fitted statsmodels result of (port - RF) on (Mkt-RF)).
    """
    data = pd.concat([port.rename("P"), ff[["Mkt-RF", "RF"]]], axis=1, join="inner").dropna()
    mkt = data["Mkt-RF"] + data["RF"]
    res = _ols(data["P"] - data["RF"], data["Mkt-RF"], hac_lags)
    diff = data["P"] - mkt
    stats = {
        "n_obs": len(data),
        "start": str(data.index.min()),
        "end": str(data.index.max()),
        "corr": data["P"].corr(mkt),
        "alpha": res.params["const"],
        "alpha_annual": res.params["const"] * periods_per_year,
        "alpha_t_hac": res.tvalues["const"],
        "beta": res.params["Mkt-RF"],
        "beta_t_hac": res.tvalues["Mkt-RF"],
        "r2": res.rsquared,
        "mean_port_annual": data["P"].mean() * periods_per_year,
        "mean_mkt_annual": mkt.mean() * periods_per_year,
        "mean_diff_annual": diff.mean() * periods_per_year,
        "tracking_error_annual": diff.std() * np.sqrt(periods_per_year),
        "vol_port_annual": data["P"].std() * np.sqrt(periods_per_year),
        "vol_mkt_annual": mkt.std() * np.sqrt(periods_per_year),
    }
    return stats, res


def regress_on_factors(port, ff, factors, hac_lags):
    """Regression of (port - RF) on a list of FF factors (HAC standard errors)."""
    data = pd.concat([port.rename("P"), ff], axis=1, join="inner").dropna()
    return _ols(data["P"] - data["RF"], data[factors], hac_lags)


def rolling_fit(port, ff, window):
    """Rolling correlation and beta of the portfolio with the FF market."""
    data = pd.concat([port.rename("P"), ff[["Mkt-RF", "RF"]]], axis=1, join="inner").dropna()
    mkt = data["Mkt-RF"] + data["RF"]
    ex = data["P"] - data["RF"]
    corr = data["P"].rolling(window).corr(mkt)
    beta = ex.rolling(window).cov(data["Mkt-RF"]) / data["Mkt-RF"].rolling(window).var()
    return pd.DataFrame({"corr": corr, "beta": beta})


def subperiod_table(port, ff, periods, periods_per_year, hac_lags):
    """compare_to_market for each (label, start, end) in periods."""
    rows = []
    for label, start, end in periods:
        stats, _ = compare_to_market(port.loc[start:end], ff.loc[start:end], periods_per_year, hac_lags)
        rows.append({"period": label, **stats})
    return pd.DataFrame(rows).set_index("period")


def largest_deviations(port, ff, n=10):
    data = pd.concat([port.rename("Portfolio"), ff[["Mkt-RF", "RF"]]], axis=1, join="inner").dropna()
    data["FF_Mkt"] = data["Mkt-RF"] + data["RF"]
    data["Diff"] = data["Portfolio"] - data["FF_Mkt"]
    return data.reindex(data["Diff"].abs().sort_values(ascending=False).index)[["Portfolio", "FF_Mkt", "Diff"]].head(n)


# ------------------------------------------------------------------------------ universe checks
def universe_over_time(m, weights):
    """Per month: number of stocks with a return, total lagged market cap, max single-stock weight."""
    g = weights.groupby("Month")
    out = pd.DataFrame({
        "n_stocks": g["Stock"].size(),
        "total_mcap_lag": g["LagMCAP"].sum(),
        "max_weight": g["Weight"].max(),
        "top10_weight": g["Weight"].apply(lambda s: s.nlargest(10).sum()),
    })
    idx = weights.loc[weights.groupby("Month")["Weight"].idxmax()]
    out["max_weight_stock"] = idx.set_index("Month")["Stock"]
    return out


def daily_return_profile(df, return_column="Return"):
    """Per year: share of zero daily returns, share of |r| > 50%, stock-days, flags if present."""
    d = df[["Date", return_column]].copy()
    d["Year"] = d["Date"].dt.year
    r = d[return_column]
    out = pd.DataFrame({
        "stockdays": d.groupby("Year").size(),
        "share_zero_returns": (r == 0).groupby(d["Year"]).mean(),
        "share_abs_ret_gt_50pct": (r.abs() > 0.5).groupby(d["Year"]).mean(),
        "share_missing_returns": r.isna().groupby(d["Year"]).mean(),
    })
    for flag in ["IsFilled", "DelistingReturnApplied"]:
        if flag in df.columns:
            agg = "mean" if flag == "IsFilled" else "sum"
            out[f"{flag}_{agg}"] = df[flag].astype(float).groupby(d["Year"]).agg(agg)
    return out


def stock_volatility_profile(df, return_column="Return"):
    """Distribution of per-stock daily return standard deviations (L&S report 0.11%-40% after filtering)."""
    return df.groupby("Stock")[return_column].std().describe(percentiles=[0.01, 0.05, 0.5, 0.95, 0.99])


def mcap_consistency(m, mcap_column="MarketCAP", threshold=3.0):
    """Stock-months where market cap growth and return disagree by more than a factor `threshold`.

    (MV_t / MV_{t-1}) / (1 + r_t) is the implied change in shares outstanding (up to dividends). Values far
    from 1 are either genuine (issues, buybacks, mergers) or data errors in MV, which directly distort VW
    weights. The table lists candidates for manual inspection.
    """
    x = m.sort_values(["Stock", "Date"]).copy()
    x["LagMCAP"] = x.groupby("Stock")[mcap_column].shift(1)
    x["ImpliedShareChange"] = (x[mcap_column] / x["LagMCAP"]) / (1 + x["MonthlyReturn"])
    flag = (x["ImpliedShareChange"] > threshold) | (x["ImpliedShareChange"] < 1 / threshold)
    out = x.loc[flag, ["Stock", "Month", "LagMCAP", mcap_column, "MonthlyReturn", "ImpliedShareChange"]]
    return out.sort_values("LagMCAP", ascending=False)


def undo_delisting_adjustment(df, delisting_return, flag_col="DelistingReturnApplied"):
    """Copy of the panel with the delisting return removed from ReturnIndex (for an impact comparison)."""
    out = df.copy()
    if flag_col in out.columns:
        mask = out[flag_col].astype(bool)
        out.loc[mask, "ReturnIndex"] = out.loc[mask, "ReturnIndex"] / (1 + delisting_return)
    return out


def flag(value, rule):
    """'ok' / 'CHECK' according to a threshold rule (min value, (low, high) range, or max)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    if isinstance(rule, tuple):
        return "ok" if rule[0] <= value <= rule[1] else "CHECK"
    return "ok" if value >= rule else "CHECK"
