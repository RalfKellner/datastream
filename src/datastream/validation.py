"""Validation of the baseline panel with well-known asset-pricing results.

Used by ``analyses/03_baseline_validation.ipynb``:

1. ``replicate_ff_factors``: SMB, HML, RMW, CMA with the Fama-French method (2x3 sorts on NYSE breakpoints,
   formed in June, value-weighted July-June) from a panel built with ``--convention ff``; compared with the
   factors from Ken French's library (``compare_factors``).
2. ``characteristics`` + ``fama_macbeth``: monthly cross-sectional regressions of next-month returns on size,
   B/M, momentum, short-term reversal, profitability, asset growth and E/P (rolling panel).
3. ``decile_sorts``: EW / VW decile returns and the 10-1 spread per characteristic.
4. ``event_study``: market-adjusted returns around report months, by earnings growth, to see when the market
   learns the numbers relative to the availability lag.

Units: Worldscope items in thousands of USD, MarketCAP in millions (``WS_UNIT``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

from datastream.panel_builder import WS_UNIT, _add_months, _months_between

CHARACTERISTICS = ["log_me", "log_bm", "mom_12_2", "str_1", "op", "ag", "ep"]

# signs expected in the literature for the US cross-section (e.g. Lewellen 2015, Fama & French 2015)
EXPECTED_SIGNS = {
    "log_me": "- (weak since the 1980s)",
    "log_bm": "+ (weak since ~2007)",
    "mom_12_2": "+ (weak after 2000, 2009 crash)",
    "str_1": "- (robust)",
    "op": "+",
    "ag": "-",
    "ep": "+",
}


def _hac_mean(x: pd.Series, lags: int) -> tuple[float, float, int]:
    x = x.dropna()
    if len(x) < 3:
        return np.nan, np.nan, len(x)
    res = sm.OLS(x.to_numpy(), np.ones(len(x))).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return float(res.params[0]), float(res.tvalues[0]), len(x)


def winsorize_by_month(df: pd.DataFrame, cols: list[str], q: float = 0.01) -> pd.DataFrame:
    out = df.copy()
    g = out.groupby("Date")
    for c in cols:
        lo, hi = g[c].transform(lambda s: s.quantile(q)), g[c].transform(lambda s: s.quantile(1 - q))
        out[c] = out[c].clip(lo, hi)
    return out


# ---------------------------------------------------------------------------------------------------------
# Characteristics
# ---------------------------------------------------------------------------------------------------------

def characteristics(panel: pd.DataFrame, interest_var: str | None = None) -> pd.DataFrame:
    """Characteristics known at the end of month t and the return of month t+1 (``ret_next``).

    log_me   log market cap (USD m)
    log_bm   log(common equity / market cap), positive book equity only
    mom_12_2 cumulative return of months t-11..t-1 (11 consecutive months, skipping month t)
    str_1    return of month t (short-term reversal)
    op       (operating income - interest expense) / common equity, positive book equity only
             (without ``interest_var``: operating income / common equity)
    ag       total assets / previous-report total assets - 1
    ep       net income / market cap
    """
    d = panel.sort_values(["DSCD", "Date"]).reset_index(drop=True).copy()
    g = d.groupby("DSCD")
    next_consecutive = _months_between(g["Date"].shift(-1), d["Date"]) == 1
    d["ret_next"] = g["ret"].shift(-1).where(next_consecutive)
    prev_consecutive = _months_between(d["Date"], g["Date"].shift(1)) == 1
    d["me_lag"] = g["MarketCAP"].shift(1).where(prev_consecutive)

    me = d["MarketCAP"] * WS_UNIT
    d["log_me"] = np.log(d["MarketCAP"].where(d["MarketCAP"] > 0))
    be = d["WC03501"].where(d["WC03501"] > 0) if "WC03501" in d else np.nan
    d["log_bm"] = np.log(be / me)
    lr = np.log1p(d["ret"])
    roll = lr.groupby(d["DSCD"]).rolling(11, min_periods=11).sum().reset_index(level=0, drop=True)
    d["mom_12_2"] = np.expm1(roll.groupby(d["DSCD"]).shift(1))
    d["str_1"] = d["ret"]
    if "WC01250" in d:
        num = d["WC01250"] - (d[interest_var].fillna(0) if interest_var and interest_var in d else 0)
        d["op"] = num / be
    if {"WC02999", "WC02999_prev"} <= set(d.columns):
        d["ag"] = d["WC02999"] / d["WC02999_prev"].where(d["WC02999_prev"] > 0) - 1
    if "WC01751" in d:
        d["ep"] = d["WC01751"] / me
    return d


# ---------------------------------------------------------------------------------------------------------
# Fama-MacBeth
# ---------------------------------------------------------------------------------------------------------

def fama_macbeth(d: pd.DataFrame, xs: list[str], y: str = "ret_next", min_obs: int = 100, standardize: bool = True,
                 winsor: float = 0.01, hac_lags: int = 6, start=None, end=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monthly cross-sectional OLS of ``y`` on ``xs`` (winsorized, optionally standardized per month).

    Returns (summary: mean coefficient, Newey-West t, months; coefficient time series). With
    ``standardize`` the coefficients are monthly returns per one cross-sectional standard deviation."""
    data = d[["Date", y] + xs].dropna()
    if start is not None:
        data = data[data["Date"] >= pd.Timestamp(start)]
    if end is not None:
        data = data[data["Date"] <= pd.Timestamp(end)]
    data = winsorize_by_month(data, xs, winsor) if winsor else data
    if standardize:
        g = data.groupby("Date")[xs]
        data[xs] = (data[xs] - g.transform("mean")) / g.transform("std")
    rows = []
    for date, cs in data.groupby("Date"):
        if len(cs) < min_obs:
            continue
        X = sm.add_constant(cs[xs].to_numpy(), has_constant="add")
        beta, *_ = np.linalg.lstsq(X, cs[y].to_numpy(), rcond=None)
        resid = cs[y].to_numpy() - X @ beta
        r2 = 1 - resid.var() / cs[y].to_numpy().var()
        rows.append({"Date": date, "n": len(cs), "r2": r2, "const": beta[0], **dict(zip(xs, beta[1:]))})
    coefs = pd.DataFrame(rows, columns=["Date", "n", "r2", "const"] + xs).set_index("Date")
    out = []
    for c in ["const"] + xs:
        m, t, n = _hac_mean(coefs[c], hac_lags)
        out.append({"variable": c, "coef": m, "t_nw": t, "months": n})
    summ = pd.DataFrame(out).set_index("variable")
    summ.loc[:, "expected"] = [EXPECTED_SIGNS.get(v, "") for v in summ.index]
    summ.attrs["avg_n"] = float(coefs["n"].mean()) if len(coefs) else np.nan
    summ.attrs["avg_r2"] = float(coefs["r2"].mean()) if len(coefs) else np.nan
    return summ, coefs


# ---------------------------------------------------------------------------------------------------------
# Decile sorts
# ---------------------------------------------------------------------------------------------------------

def decile_sorts(d: pd.DataFrame, char: str, n: int = 10, min_obs: int = 100, hac_lags: int = 6
                 ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Monthly decile portfolios on ``char`` (all-stock breakpoints), EW and VW (weights: market cap at t).

    Returns (table: mean monthly return per decile and of the n-1 spread, with NW t; monthly spread series)."""
    data = d[["Date", "DSCD", char, "ret_next", "MarketCAP"]].dropna()
    data = data[data.groupby("Date")[char].transform("size") >= min_obs]
    data["q"] = data.groupby("Date")[char].transform(
        lambda s: pd.qcut(s.rank(method="first"), n, labels=False) + 1)
    ew = data.groupby(["Date", "q"])["ret_next"].mean().unstack()
    data["w"] = data["MarketCAP"]
    vw = (data.assign(wr=data["w"] * data["ret_next"]).groupby(["Date", "q"])[["wr", "w"]].sum()
          .pipe(lambda x: x["wr"] / x["w"]).unstack())
    rows = {}
    for name, tab in [("EW", ew), ("VW", vw)]:
        means = tab.mean()
        spread = tab[n] - tab[1]
        m, t, _ = _hac_mean(spread, hac_lags)
        rows[name] = {**{f"D{int(k)}": v for k, v in means.items()}, f"D{n}-D1": m, "t_nw": t}
    series = pd.DataFrame({"EW": ew[n] - ew[1], "VW": vw[n] - vw[1]})
    return pd.DataFrame(rows).T, series


# ---------------------------------------------------------------------------------------------------------
# Fama-French factor replication
# ---------------------------------------------------------------------------------------------------------

def _june_sorts(ff_panel: pd.DataFrame, nyse: set[str], interest_var: str | None) -> pd.DataFrame:
    """One row per stock and June: size, BM, OP, INV and the 2x3 portfolio labels."""
    p = ff_panel
    june = p[p["Date"].dt.month == 6].copy()
    dec = p[p["Date"].dt.month == 12][["DSCD", "Date", "MarketCAP"]].copy()
    dec["Date"] = dec["Date"] + pd.offsets.MonthEnd(6)          # Dec t-1 -> June t
    june = june.merge(dec.rename(columns={"MarketCAP": "me_dec"}), on=["DSCD", "Date"], how="left")
    june = june[june["MarketCAP"] > 0]
    be = june["WC03501"].where(june["WC03501"] > 0)
    june["BM"] = be / (june["me_dec"].where(june["me_dec"] > 0) * WS_UNIT)
    if "WC01250" in june:
        interest = june[interest_var].fillna(0) if interest_var and interest_var in june else 0
        june["OP"] = (june["WC01250"] - interest) / be
    else:
        june["OP"] = np.nan
    if "WC02999_prev" in june:
        june["INV"] = june["WC02999"] / june["WC02999_prev"].where(june["WC02999_prev"] > 0) - 1
    else:
        june["INV"] = np.nan
    june["nyse"] = june["DSCD"].isin(nyse)

    out = []
    for date, cs in june.groupby("Date"):
        ny = cs[cs["nyse"]]
        if len(ny) < 50:
            continue
        cs = cs.copy()
        cs["S"] = np.where(cs["MarketCAP"] <= ny["MarketCAP"].median(), "S", "B")
        for var, lo_lab, hi_lab in [("BM", "L", "H"), ("OP", "W", "R"), ("INV", "C", "A")]:
            lo, hi = ny[var].quantile(0.3), ny[var].quantile(0.7)
            lab = np.where(cs[var] <= lo, lo_lab, np.where(cs[var] > hi, hi_lab, "N"))
            cs[f"p_{var}"] = np.where(cs[var].notna(), lab, None)
        out.append(cs[["DSCD", "Date", "S", "p_BM", "p_OP", "p_INV"]])
    return pd.concat(out, ignore_index=True).rename(columns={"Date": "formation"})


def replicate_ff_factors(ff_panel: pd.DataFrame, nyse: set[str], interest_var: str | None = None) -> pd.DataFrame:
    """Monthly SMB (B/M sorts and FF5 average), HML, RMW, CMA from a panel built with ``convention='ff'``.

    Portfolios are formed at the end of June t with ME of June t, B/M = BE (fiscal year t-1) / ME of December
    t-1, OP = (operating income - interest) / BE, INV = asset growth; NYSE breakpoints (median size; 30/70);
    value-weighted returns (weights: market cap of the previous month) from July t to June t+1."""
    sorts = _june_sorts(ff_panel, nyse, interest_var)
    p = ff_panel.sort_values(["DSCD", "Date"]).copy()
    g = p.groupby("DSCD")
    consecutive = _months_between(p["Date"], g["Date"].shift(1)) == 1
    p["w"] = g["MarketCAP"].shift(1).where(consecutive)
    p = p.dropna(subset=["ret", "w"]).copy()
    p["formation"] = pd.to_datetime(np.where(p["Date"].dt.month >= 7, p["Date"].dt.year, p["Date"].dt.year - 1)
                                    .astype(str)) + pd.offsets.MonthEnd(6)
    h = p[["DSCD", "Date", "ret", "w", "formation"]].merge(sorts, on=["DSCD", "formation"], how="inner")

    def vw(col):
        x = h.dropna(subset=[col])
        grp = x.assign(wr=x["w"] * x["ret"]).groupby(["Date", "S", col])[["wr", "w"]].sum()
        return (grp["wr"] / grp["w"]).unstack(["S", col])

    full = lambda t, labs: t.reindex(columns=pd.MultiIndex.from_product([["S", "B"], labs]))  # noqa: E731
    bm, op, inv = full(vw("p_BM"), ["L", "N", "H"]), full(vw("p_OP"), ["W", "N", "R"]), full(vw("p_INV"), ["C", "N", "A"])
    f = pd.DataFrame(index=bm.index)
    f["SMB_BM"] = bm["S"].mean(axis=1, skipna=False) - bm["B"].mean(axis=1, skipna=False)
    f["HML"] = (bm[("S", "H")] + bm[("B", "H")]) / 2 - (bm[("S", "L")] + bm[("B", "L")]) / 2
    f["RMW"] = (op[("S", "R")] + op[("B", "R")]) / 2 - (op[("S", "W")] + op[("B", "W")]) / 2
    f["CMA"] = (inv[("S", "C")] + inv[("B", "C")]) / 2 - (inv[("S", "A")] + inv[("B", "A")]) / 2
    smb_op = op["S"].mean(axis=1, skipna=False) - op["B"].mean(axis=1, skipna=False)
    smb_inv = inv["S"].mean(axis=1, skipna=False) - inv["B"].mean(axis=1, skipna=False)
    f["SMB"] = pd.concat([f["SMB_BM"], smb_op, smb_inv], axis=1).mean(axis=1, skipna=False)
    f.index = f.index.to_period("M")
    f.index.name = "Month"
    n = h.groupby("Date")["DSCD"].nunique()
    n.index = n.index.to_period("M")
    f["n_stocks"] = n
    return f


def compare_factors(rep: pd.DataFrame, ff: pd.DataFrame, pairs: dict[str, str] | None = None,
                    hac_lags: int = 6) -> pd.DataFrame:
    """Correlation, means, volatilities and regression of the French factor on the replicated one."""
    pairs = pairs or {"SMB": "SMB", "SMB_BM": "SMB", "HML": "HML", "RMW": "RMW", "CMA": "CMA"}
    rows = []
    for mine, theirs in pairs.items():
        if mine not in rep or theirs not in ff:
            continue
        x = pd.concat([rep[mine].rename("mine"), ff[theirs].rename("ff")], axis=1, join="inner").dropna()
        if len(x) < 24:
            continue
        res = sm.OLS(x["ff"], sm.add_constant(x["mine"])).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
        rows.append({
            "factor": mine, "french": theirs, "months": len(x), "start": str(x.index.min()), "end": str(x.index.max()),
            "corr": x["mine"].corr(x["ff"]),
            "mean_mine_annual": 12 * x["mine"].mean(), "mean_ff_annual": 12 * x["ff"].mean(),
            "t_mine": _hac_mean(x["mine"], hac_lags)[1], "t_ff": _hac_mean(x["ff"], hac_lags)[1],
            "vol_mine_annual": np.sqrt(12) * x["mine"].std(), "vol_ff_annual": np.sqrt(12) * x["ff"].std(),
            "slope": res.params["mine"], "r2": res.rsquared,
            "tracking_error_annual": np.sqrt(12) * (x["mine"] - x["ff"]).std(),
        })
    return pd.DataFrame(rows).set_index("factor")


# ---------------------------------------------------------------------------------------------------------
# Event study around report months
# ---------------------------------------------------------------------------------------------------------

def report_events(root, uni: pd.DataFrame, signs: dict | None = None,
                  variables: tuple[str, ...] = ("WC01751", "WC02999")) -> pd.DataFrame:
    """Report snapshots (all report months, no availability lag) with previous-report values, for the firms
    of the universe; same cleaning as the baseline panel."""
    from datastream.panel_builder import add_previous, clean_raw, load_wide, report_snapshots
    variables = list(variables)
    wide = load_wide(root, variables, set(uni["DSCD"].astype(str)))
    wide, _ = clean_raw(wide, variables, signs or {}, clean_unit_errors=False)
    return add_previous(report_snapshots(wide, variables), variables, 9)


def event_study(snaps: pd.DataFrame, monthly: pd.DataFrame, k_min: int = -3, k_max: int = 12, n_groups: int = 5,
                min_gap: int = 9, max_gap: int = 15) -> dict:
    """Market-adjusted returns around report months, by earnings growth.

    ``snaps``: report snapshots with ``WC01751``, ``WC01751_prev``, ``WC02999_prev`` and
    ``fund_prev_report_month`` (``panel_builder.add_previous``). Signal: (NI - NI_prev) / TA_prev for reports
    9-15 months after the previous one (annual reports). Groups: quintiles per calendar year of the report.
    ``monthly``: ``DSCD | Date | ret`` of the universe; adjusted return = ret - equal-weighted universe mean.

    Returns dict with the mean adjusted return per group and event month, the top-minus-bottom spread with
    t-statistics across report-month cohorts, and cumulative returns."""
    s = snaps.dropna(subset=["WC01751", "WC01751_prev", "WC02999_prev"]).copy()
    gap = _months_between(s["report_month"], s["fund_prev_report_month"])
    s = s[(gap >= min_gap) & (gap <= max_gap) & (s["WC02999_prev"] > 0)]
    s["signal"] = (s["WC01751"] - s["WC01751_prev"]) / s["WC02999_prev"]
    s["year"] = s["report_month"].dt.year
    s["group"] = s.groupby("year")["signal"].transform(
        lambda x: pd.qcut(x.rank(method="first"), n_groups, labels=False) + 1 if len(x) >= 5 * n_groups else np.nan)
    s = s.dropna(subset=["group"])

    m = monthly[["DSCD", "Date", "ret"]].dropna().copy()
    m["adj"] = m["ret"] - m.groupby("Date")["ret"].transform("mean")
    frames = []
    for k in range(k_min, k_max + 1):
        e = s[["DSCD", "report_month", "group"]].copy()
        e["Date"] = _add_months(e["report_month"], k)
        e = e.merge(m[["DSCD", "Date", "adj"]], on=["DSCD", "Date"], how="inner")
        e["k"] = k
        frames.append(e)
    ev = pd.concat(frames, ignore_index=True)
    by_group = ev.groupby(["k", "group"])["adj"].mean().unstack()
    cohort = ev.groupby(["k", "report_month", "group"])["adj"].mean().unstack()
    spread_c = (cohort[n_groups] - cohort[1]).dropna()
    rows = []
    for k, x in spread_c.groupby(level="k"):
        rows.append({"k": k, "spread": x.mean(), "t": x.mean() / (x.std(ddof=1) / np.sqrt(len(x))), "cohorts": len(x)})
    spread = pd.DataFrame(rows).set_index("k")
    return {"by_group": by_group, "spread": spread, "cum_by_group": by_group.cumsum(),
            "n_events": int(len(s)), "events": s}


def window_returns(spread: pd.DataFrame, windows: dict[str, tuple[int, int]]) -> pd.DataFrame:
    """Sum of the mean spread over event-month windows (e.g. before vs. after availability)."""
    return pd.DataFrame({name: {"cum_spread": spread.loc[a:b, "spread"].sum(), "months": b - a + 1}
                         for name, (a, b) in windows.items()}).T
