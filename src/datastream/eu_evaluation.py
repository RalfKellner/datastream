"""Sanity checks of the filtered European price data (analyses/04_eu_market_sanity_checks.ipynb).

Input is the monthly universe of scripts/40_build_monthly_universe.py --region EU (one row per stock-month with
Country, month-end MarketCAP_EUR / ReturnIndex_EUR and the local-currency columns). Working on the monthly
universe instead of the daily panel keeps the checks fast and small in memory.

Conventions (as in evaluation.py for the U.S.)
* Monthly returns from month-end return indexes of consecutive months only.
* Value weights: market cap at the end of the previous month (same stock, consecutive month).
* Returns are decimals. The comparison functions of evaluation.py (compare_to_market, subperiod_table,
  rolling_fit, largest_deviations) take the outputs of this module directly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Kenneth French's "Europe" region (Fama/French European factors)
FF_EUROPE_COUNTRIES = [
    "AUSTRIA", "BELGIUM", "DENMARK", "FINLAND", "FRANCE", "GERMANY", "GREECE", "IRELAND", "ITALY",
    "NETHERLANDS", "NORWAY", "PORTUGAL", "SPAIN", "SWEDEN", "SWITZERLAND", "UNITED KINGDOM",
]


# ----------------------------------------------------------------------------------- monthly panel
def monthly_returns(uni: pd.DataFrame) -> pd.DataFrame:
    """Add ret_eur, ret_local and lagged EUR market cap (lag_me_eur) to the monthly universe."""
    m = uni.sort_values(["DSCD", "Date"]).copy()
    m["Date"] = pd.to_datetime(m["Date"])
    m["Month"] = m["Date"].dt.to_period("M")
    g = m.groupby("DSCD")
    consecutive = (m["Month"] - g["Month"].shift(1)).apply(lambda x: getattr(x, "n", np.nan)) == 1
    m["ret_eur"] = (m["ReturnIndex_EUR"] / g["ReturnIndex_EUR"].shift(1) - 1).where(consecutive)
    m["ret_local"] = (m["ReturnIndex"] / g["ReturnIndex"].shift(1) - 1).where(consecutive)
    m["lag_me_eur"] = g["MarketCAP_EUR"].shift(1).where(consecutive)
    return m


def vw_returns(m: pd.DataFrame, return_col: str = "ret_eur", by: str | None = None,
               countries: list[str] | None = None) -> pd.DataFrame | pd.Series:
    """Value-weighted (lagged EUR market cap) monthly returns; per group if ``by`` (e.g. 'Country')."""
    d = m if countries is None else m[m["Country"].isin(countries)]
    d = d[d[return_col].notna() & (d["lag_me_eur"] > 0)]
    keys = ["Month"] + ([by] if by else [])
    num = (d[return_col] * d["lag_me_eur"]).groupby([d[k] for k in keys]).sum()
    den = d["lag_me_eur"].groupby([d[k] for k in keys]).sum()
    out = num / den
    return out.unstack(by) if by else out.rename("VW")


def universe_by_country(m: pd.DataFrame) -> pd.DataFrame:
    """Per month and country: number of stocks with a return and total EUR market cap (EUR millions)."""
    d = m[m["ret_eur"].notna()]
    return pd.DataFrame({
        "n_stocks": d.groupby(["Month", "Country"])["DSCD"].nunique(),
        "me_eur": d.groupby(["Month", "Country"])["lag_me_eur"].sum(),
    }).reset_index()


def local_vs_eur(m: pd.DataFrame, euro_adoption: dict[str, str]) -> pd.DataFrame:
    """Share of stock-months with |ret_local - ret_eur| > 1e-6 after euro adoption (should be ~0) and before
    (non-zero only where the line is quoted in a non-euro currency)."""
    d = m.dropna(subset=["ret_eur", "ret_local"]).copy()
    d["adopt"] = pd.to_datetime(d["Country"].map(euro_adoption))
    d["differs"] = (d["ret_local"] - d["ret_eur"]).abs() > 1e-6
    d["period"] = np.where(d["adopt"].isna(), "no euro", np.where(d["Date"] >= d["adopt"], "after", "before"))
    return (d.groupby(["Country", "period"])["differs"].agg(["mean", "size"])
            .rename(columns={"mean": "share_differs", "size": "n"}).reset_index())


# ----------------------------------------------------------------------------------------- currency
def fx_month_end(path: str) -> pd.Series:
    """USD per EUR at month end from a FRED CSV (DEXUSEU daily: last value of the month; EXUSEU monthly:
    monthly averages, used as given with a warning). Returns a Series indexed by Period 'M'."""
    fx = pd.read_csv(path)
    date_col, val_col = fx.columns[0], fx.columns[1]
    fx[date_col] = pd.to_datetime(fx[date_col])
    fx[val_col] = pd.to_numeric(fx[val_col], errors="coerce")     # FRED marks holidays with '.'
    fx = fx.dropna().sort_values(date_col)
    per_month = fx.groupby(fx[date_col].dt.to_period("M")).size()
    if per_month.median() <= 1:
        import warnings
        warnings.warn(f"{val_col} looks monthly (FRED monthly series are averages, not month-end rates). "
                      "Use the daily series DEXUSEU for exact conversions.")
    s = fx.groupby(fx[date_col].dt.to_period("M"))[val_col].last()
    s.index.name = "Month"
    return s.rename("USD_per_EUR")


def eur_to_usd(r_eur: pd.Series, usd_per_eur: pd.Series) -> pd.Series:
    """Convert monthly EUR returns into USD returns: (1 + r_EUR) * fx_t / fx_t-1 - 1."""
    fx_ret = usd_per_eur / usd_per_eur.shift(1)
    d = pd.concat([r_eur.rename("r"), fx_ret.rename("fx")], axis=1, join="inner").dropna()
    return ((1 + d["r"]) * d["fx"] - 1).rename(r_eur.name)


def usd_to_eur(r_usd: pd.Series, usd_per_eur: pd.Series) -> pd.Series:
    fx_ret = usd_per_eur / usd_per_eur.shift(1)
    d = pd.concat([r_usd.rename("r"), fx_ret.rename("fx")], axis=1, join="inner").dropna()
    return ((1 + d["r"]) / d["fx"] - 1).rename(r_usd.name)


# --------------------------------------------------------------------------------- index comparison
def index_returns(path: str, sheet=0) -> pd.DataFrame:
    """Monthly returns from an index file (Datastream export or CSV): first column dates, one column per
    index level (e.g. total return index ~E). Month-end values of consecutive months."""
    df = pd.read_csv(path) if str(path).lower().endswith(".csv") else pd.read_excel(path, sheet_name=sheet)
    df = df.rename(columns={df.columns[0]: "Date"})
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date").apply(pd.to_numeric, errors="coerce")
    me = df.resample("ME").last()
    me.index = me.index.to_period("M")
    me.index.name = "Month"
    return me / me.shift(1) - 1


# Datastream abbreviations in the names of the "<COUNTRY>-DS Market" indices -> Country (GEOGN, upper case)
DS_MARKET_NAME_ALIASES = {
    "NETHERLAND": "NETHERLANDS", "SWITZ": "SWITZERLAND", "UK": "UNITED KINGDOM", "CZECH REP.": "CZECH REPUBLIC",
    "LUXEMBURG": "LUXEMBOURG",
}


def ds_market_country(column) -> str | None:
    """Country of a Datastream Total Market index column, e.g. 'UK-DS Market' -> 'UNITED KINGDOM'.

    Works with the index names as exported by Datastream ('<COUNTRY>-DS Market', optionally followed by the
    datatype, e.g. 'GERMANY-DS Market - TOT RETURN IND'). Returns None for other columns."""
    name = str(column).strip().upper()
    if "-DS MARKET" not in name:
        return None
    country = name.split("-DS MARKET")[0].strip()
    return DS_MARKET_NAME_ALIASES.get(country, country)


def ds_market_by_country(idx: pd.DataFrame, countries=None) -> pd.DataFrame:
    """Rename the '<COUNTRY>-DS Market' columns of index_returns() output to our Country names.

    Columns that are not DS Market indices or whose country is not in ``countries`` (if given) are dropped."""
    mapping = {c: ds_market_country(c) for c in idx.columns}
    mapping = {c: k for c, k in mapping.items() if k and (countries is None or k in set(countries))}
    return idx[list(mapping)].rename(columns=mapping)


def compare_series(ours: pd.DataFrame, bench: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """Correlation, beta, tracking error and mean difference (annualised) per mapped column pair."""
    rows = []
    for ours_col, bench_col in mapping.items():
        if ours_col not in ours or bench_col not in bench:
            continue
        d = pd.concat([ours[ours_col].rename("o"), bench[bench_col].rename("b")], axis=1).dropna()
        if len(d) < 24:
            continue
        diff = d["o"] - d["b"]
        rows.append({"series": ours_col, "benchmark": bench_col, "n_months": len(d),
                     "start": str(d.index.min()), "end": str(d.index.max()),
                     "corr": d["o"].corr(d["b"]), "beta": d["o"].cov(d["b"]) / d["b"].var(),
                     "tracking_error_annual": diff.std() * np.sqrt(12), "mean_diff_annual": diff.mean() * 12})
    return pd.DataFrame(rows).set_index("series") if rows else pd.DataFrame()


def lead_lag_corr(ours: pd.DataFrame, bench: pd.DataFrame, lags=(-2, -1, 0, 1, 2)) -> pd.DataFrame:
    """Correlation of ours_t with bench_(t+lag) per common column. A maximum away from lag 0 means the dates of
    the two series are shifted (e.g. benchmark dated at the start instead of the end of the month)."""
    out = {}
    for c in ours.columns.intersection(bench.columns):
        out[c] = {lag: ours[c].corr(bench[c].shift(-lag)) for lag in lags}
    res = pd.DataFrame(out).T
    res.columns = [f"lag_{l:+d}" for l in res.columns]
    res["best_lag"] = [int(c.replace("lag_", "")) for c in res.abs().idxmax(axis=1)] if len(res) else []
    return res


def raw_date_profile(path, sheet=0) -> pd.Series:
    """Day-of-month profile of the first column of an index file (to check month-end vs. start-of-month dates)."""
    df = pd.read_csv(path) if str(path).lower().endswith(".csv") else pd.read_excel(path, sheet_name=sheet)
    d = pd.to_datetime(df.iloc[:, 0], errors="coerce").dropna()
    return pd.Series({"n_dates": len(d), "first": str(d.min().date()), "last": str(d.max().date()),
                      "share_month_end": float(d.dt.is_month_end.mean()),
                      "share_day_1": float((d.dt.day == 1).mean()),
                      "median_days_between": float(d.diff().dt.days.median()),
                      "unparsed_rows": int(len(df) - len(d))})


def top_n_portfolio(m: pd.DataFrame, n: int = 600, countries: list[str] | None = None,
                    return_col: str = "ret_eur") -> pd.Series:
    """VW return of the n largest stocks by lagged EUR market cap each month (STOXX 600-like, no free float)."""
    d = m if countries is None else m[m["Country"].isin(countries)]
    d = d[d[return_col].notna() & (d["lag_me_eur"] > 0)]
    d = d[d.groupby("Month")["lag_me_eur"].rank(ascending=False, method="first") <= n]
    return ((d[return_col] * d["lag_me_eur"]).groupby(d["Month"]).sum()
            / d["lag_me_eur"].groupby(d["Month"]).sum()).rename(f"Top{n}")
