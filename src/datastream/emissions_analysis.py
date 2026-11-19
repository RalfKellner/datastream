"""Determinants of emissions and industry descriptives on the sustainable-finance dataset (notebook 05).

* ``firm_year_sample``: one observation per firm and fiscal year from the monthly dataset. Emissions are annual,
  so the monthly rows repeat each value up to 12 times; regressions on firm-months would inflate N and weight
  firms by how long a value is used. Per (DSCD, emission fiscal year end) the first month in which the value is
  used is taken; fundamentals must belong to the same fiscal year (``fund_fye_month == em_fye_month``); market
  cap is taken at the fiscal year end (from the emissions panel, which covers all universe months).
* ``fe_regression``: OLS with industry and year fixed effects (dummies) and one- or two-way clustered standard
  errors (statsmodels; two-way = Cameron-Gelbach-Miller).
* ``industry_table`` / ``aggregate_intensity_by_year``: descriptives by TRBC industry group / economic sector.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import statsmodels.api as sm

from datastream.panel_builder import WS_UNIT

CONTROLS = ["log_sales", "log_me", "log_bm", "leverage", "invest_ta", "roe", "sales_growth"]
DEPENDENT = {
    "log_co2e_total_combined": "log total CO2e (t)",
    "log_co2e_scope1": "log scope 1 (t)",
    "log_co2e_scope2": "log scope 2 (t)",
    "log_co2e_intensity": "log total intensity (t / USD m sales)",
    "log_co2e_scope1_intensity": "log scope 1 intensity (t / USD m sales)",
}


def winsorize_by(d: pd.DataFrame, cols: list[str], by: str, lower: float = 0.01, upper: float = 0.99) -> pd.DataFrame:
    d = d.copy()
    for c in cols:
        if c not in d:
            continue
        lo = d.groupby(by)[c].transform(lambda s: s.quantile(lower))
        hi = d.groupby(by)[c].transform(lambda s: s.quantile(upper))
        d[c] = d[c].clip(lo, hi)
    return d


def firm_year_sample(ds: pd.DataFrame, em_panel: pd.DataFrame | None = None, exclude_financials: bool = True,
                     winsor: tuple[float, float] | None = (0.01, 0.99), winsor_dependent: bool = True
                     ) -> tuple[pd.DataFrame, dict]:
    """Firm-year observations (see module docstring). Returns (sample, counts of the selection steps).

    ``winsor``: controls (except log sales / log market cap) are winsorized per fiscal year at these quantiles;
    with ``winsor_dependent`` also the regression dependent variables (``DEPENDENT``: log emissions and log
    intensities; tiny sales or emissions produce extreme values). The level columns (co2e_*, sales_fy) stay
    raw for the descriptive tables."""
    d = ds.dropna(subset=["co2e_total_combined", "em_fye_month"]).sort_values(["DSCD", "em_fye_month", "Date"])
    counts = {"firm_months_with_emissions": int(len(d))}
    fy = d.drop_duplicates(["DSCD", "em_fye_month"], keep="first").copy()
    fy["fiscal_year"] = fy["em_fye_month"].dt.year
    counts["firm_years"] = int(len(fy))

    if exclude_financials and "is_financial" in fy:
        fy = fy[fy["is_financial"] == 0]          # also drops stocks without TRBC classification
        counts["after_dropping_financials_and_no_trbc"] = int(len(fy))
    else:
        fy = fy[fy["trbc_industry_group"].notna()]

    same_fy = fy["fund_fye_month"] == fy["em_fye_month"]
    counts["share_fundamentals_same_fiscal_year"] = float(same_fy.mean())
    fund_cols = ["sales", "sales_prev", "operating_income", "net_income", "total_assets", "total_assets_prev",
                 "common_equity", "total_debt", "capex", "leverage", "invest_ta", "roe", "sales_growth"]
    fy.loc[~same_fy, [c for c in fund_cols if c in fy]] = np.nan

    # market cap at the fiscal year end; if the stock is not in the universe that month (listed during the
    # fiscal year, temporarily filtered), the first universe month within the following 12 months
    if em_panel is not None:
        mc = em_panel[["DSCD", "Date", "MarketCAP"]].dropna().copy()
        mc["DSCD"] = mc["DSCD"].astype(str)
        exact = mc.rename(columns={"Date": "em_fye_month", "MarketCAP": "me_fye"})
        fy = fy.merge(exact, on=["DSCD", "em_fye_month"], how="left")
        counts["share_market_cap_at_fye"] = float(fy["me_fye"].notna().mean())
        miss = fy["me_fye"].isna()
        if miss.any():
            fwd = pd.merge_asof(fy.loc[miss, ["DSCD", "em_fye_month"]].reset_index().sort_values("em_fye_month"),
                                mc.sort_values("Date"), left_on="em_fye_month", right_on="Date", by="DSCD",
                                direction="forward", tolerance=pd.Timedelta(days=366)).set_index("index")
            fy.loc[fwd.index, "me_fye"] = fwd["MarketCAP"]
        counts["share_market_cap_after_fallback"] = float(fy["me_fye"].notna().mean())
    else:
        fy["me_fye"] = np.nan

    fy["log_me"] = np.log(fy["me_fye"].where(fy["me_fye"] > 0))
    be = fy["common_equity"].where(fy["common_equity"] > 0)
    fy["log_bm"] = np.log(be / (fy["me_fye"] * WS_UNIT))
    fy["log_sales"] = np.log(fy["sales_fy"].where(fy["sales_fy"] > 0))
    fy["log_co2e_intensity"] = np.log(fy["co2e_intensity"].where(fy["co2e_intensity"] > 0))
    fy["log_co2e_scope1_intensity"] = np.log(fy["co2e_scope1_intensity"].where(fy["co2e_scope1_intensity"] > 0))
    if winsor:
        cols = [c for c in CONTROLS if c not in ("log_sales", "log_me")]
        if winsor_dependent:
            cols += [c for c in DEPENDENT if c in fy]
            counts["winsorized_dependent"] = {c: int(((fy[c] < fy.groupby("fiscal_year")[c].transform(lambda x: x.quantile(winsor[0])))
                                                      | (fy[c] > fy.groupby("fiscal_year")[c].transform(lambda x: x.quantile(winsor[1])))).sum())
                                              for c in DEPENDENT if c in fy}
        fy = winsorize_by(fy, cols, "fiscal_year", *winsor)
    return fy.reset_index(drop=True), counts


def fe_regression(d: pd.DataFrame, y: str, xs: list[str], fe: tuple[str, ...] = ("trbc_industry_group", "fiscal_year"),
                  cluster: tuple[str, ...] = ("DSCD", "fiscal_year")):
    """OLS of ``y`` on ``xs`` with fixed effects (dummies) and clustered standard errors (one or two dimensions).
    Returns (statsmodels result, estimation sample)."""
    cols = [y] + list(xs) + list(fe) + [c for c in cluster if c not in fe and c not in xs]
    s = d[list(dict.fromkeys(cols))].replace([np.inf, -np.inf], np.nan).dropna()
    X = s[list(xs)].astype(float)
    for f in fe:
        X = pd.concat([X, pd.get_dummies(s[f].astype(str), prefix=f, drop_first=True, dtype=float)], axis=1)
    X = sm.add_constant(X, has_constant="add")
    groups = np.column_stack([pd.factorize(s[c])[0] for c in cluster])
    groups = groups[:, 0] if groups.shape[1] == 1 else groups
    with warnings.catch_warnings():
        # two-way clustering can give negative variances for some fixed-effect dummies (not reported)
        warnings.simplefilter("ignore", RuntimeWarning)
        res = sm.OLS(s[y].astype(float), X).fit(cov_type="cluster", cov_kwds={"groups": groups})
    return res, s


def regression_table(d: pd.DataFrame, specs: dict[str, tuple[str, list[str]]], **kw) -> pd.DataFrame:
    """Columns = specifications {label: (y, xs)}; rows = coefficient and t-statistic per regressor, N, firms,
    R2 and the R2 of the fixed effects alone."""
    out = {}
    regs = list(dict.fromkeys(x for _, xs in specs.values() for x in xs))
    for label, (y, xs) in specs.items():
        res, s = fe_regression(d, y, xs, **kw)
        col = {}
        for x in regs:
            col[(x, "coef")] = res.params.get(x, np.nan)
            col[(x, "t")] = res.tvalues.get(x, np.nan)
        fe_only, _ = fe_regression(s, y, [], **kw)
        col[("N", "")] = int(res.nobs)
        col[("firms", "")] = int(s["DSCD"].nunique()) if "DSCD" in s else np.nan
        col[("R2", "")] = res.rsquared
        col[("R2 FE only", "")] = fe_only.rsquared
        out[label] = col
    return pd.DataFrame(out)


def industry_table(fy: pd.DataFrame, level: str = "trbc_industry_group", min_firms: int = 5) -> pd.DataFrame:
    """Per industry: firms, share reported, total emissions (Mt) and share of all emissions, median emissions per
    firm (kt), median intensity, aggregate intensity (sum of emissions / sum of sales, t / USD m)."""
    g = fy.groupby(level)
    out = pd.DataFrame({
        "firms": g["DSCD"].nunique(),
        "share_reported": g["co2e_is_estimated"].apply(lambda s: (s == 0).mean()),
        "total_mt": g["co2e_total_combined"].sum() / 1e6,
        "median_kt": g["co2e_total_combined"].median() / 1e3,
        "median_intensity": g["co2e_intensity"].median(),
        "aggregate_intensity": g["co2e_total_combined"].sum() / (g["sales_fy"].sum() / 1000),
    })
    out["share_of_total"] = out["total_mt"] / out["total_mt"].sum()
    if "trbc_economic_sector" in fy and level != "trbc_economic_sector":
        out.insert(0, "sector", g["trbc_economic_sector"].agg(lambda s: s.mode().iat[0] if len(s.mode()) else ""))
    return out[out["firms"] >= min_firms]


def aggregate_intensity_by_year(fy: pd.DataFrame, level: str = "trbc_economic_sector", reported_only: bool = True
                                ) -> pd.DataFrame:
    """Aggregate intensity (sum emissions / sum sales) per fiscal year and sector."""
    d = fy[fy["co2e_is_estimated"] == 0] if reported_only else fy
    d = d.dropna(subset=["co2e_total_combined", "sales_fy"])
    g = d.groupby(["fiscal_year", level])
    return (g["co2e_total_combined"].sum() / (g["sales_fy"].sum() / 1000)).unstack()


def format_table(tab: pd.DataFrame, digits: int = 3) -> pd.DataFrame:
    """Paper-style view of ``regression_table``: coefficient with stars (|t| >= 1.645 / 1.96 / 2.576) and the
    t-statistic in parentheses below; regressors not in a specification stay blank."""
    out = {}
    for col in tab.columns:
        c = {}
        for (var, stat), v in tab[col].items():
            if stat == "coef":
                t = tab[col].get((var, "t"), np.nan)
                stars = "" if pd.isna(t) else "***" if abs(t) >= 2.576 else "**" if abs(t) >= 1.96 else "*" if abs(t) >= 1.645 else ""
                c[(var, "")] = "" if pd.isna(v) else f"{v:.{digits}f}{stars}"
            elif stat == "t":
                c[(var, " ")] = "" if pd.isna(v) else f"({v:.2f})"
            elif var in ("N", "firms"):
                c[(var, "")] = "" if pd.isna(v) else f"{int(v):,}"
            else:
                c[(var, "")] = "" if pd.isna(v) else f"{v:.3f}"
        out[col] = c
    f = pd.DataFrame(out)
    f.index = [f"{a}" if b == "" else "" for a, b in f.index]
    return f


def missing_overview(fy: pd.DataFrame, y: str, xs: list[str]) -> pd.DataFrame:
    """Share missing per variable and the remaining firm-years when the variables are required one after another
    (in the order given) - where the regression sample is lost."""
    rows, mask = [], fy[y].notna()
    rows.append({"variable": y, "share_missing": float(fy[y].isna().mean()), "remaining_firm_years": int(mask.sum())})
    for x in xs:
        mask &= fy[x].replace([np.inf, -np.inf], np.nan).notna()
        rows.append({"variable": x, "share_missing": float(fy[x].isna().mean()), "remaining_firm_years": int(mask.sum())})
    out = pd.DataFrame(rows).set_index("variable")
    out["lost_by_this_step"] = (-out["remaining_firm_years"].diff()).fillna(len(fy) - out["remaining_firm_years"].iloc[0]).astype(int)
    return out
