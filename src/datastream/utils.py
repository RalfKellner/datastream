import numpy as np
import pandas as pd


def determine_monthly_returns(df, price_column, date_column, discrete=True):
    """
    Resample a single-stock daily DataFrame to month-end prices and compute
    monthly returns.

    The first month is retained with NaN return — there is no prior price to
    compute a return from. The caller is responsible for dropping NaN returns
    after applying this across a panel (see usage below).

    Parameters
    ----------
    df : pd.DataFrame
    price_column : str  — numeric price or total-return index column
    date_column  : str  — datetime column used for resampling
    discrete     : bool — True → pct_change, False → log difference
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame.")
    if price_column not in df.columns:
        raise ValueError(f"'{price_column}' must be a column in the DataFrame.")
    if date_column not in df.columns:
        raise ValueError(f"'{date_column}' must be a column in the DataFrame.")
    if not pd.api.types.is_datetime64_any_dtype(df[date_column]):
        raise TypeError(f"'{date_column}' must be datetime dtype.")
    if not pd.api.types.is_numeric_dtype(df[price_column]):
        raise TypeError(f"'{price_column}' must be numeric.")

    df = df.sort_values(date_column)
    df_monthly = df.resample(rule="ME", on=date_column).last()

    if discrete:
        df_monthly["MonthlyReturn"] = df_monthly[price_column].pct_change(fill_method = None)
    else:
        df_monthly["MonthlyReturn"] = np.log(df_monthly[price_column]).diff()

    return df_monthly


def _vw_group(group, return_col):
    """Value-weighted return for one date group, re-normalising over valid obs."""
    mask = group["_LagMCAP"].notna() & group[return_col].notna()
    g = group.loc[mask]
    if g.empty:
        return np.nan
    w = g["_LagMCAP"] / g["_LagMCAP"].sum()
    return (w * g[return_col]).sum()


def value_weighted_portfolio(df, return_column, mcap_column,
                             stock_id_column, date_column):
    """
    Construct a value-weighted portfolio.

    Weights are based on each stock's market cap from the *prior* month
    (LagMarketCAP). Stocks with a missing lagged cap or missing return are
    excluded from that month's weight computation; weights are re-normalised
    over the remaining stocks so they always sum to 1.

    NaN portfolio months (e.g. the very first date where all lags are missing)
    are dropped explicitly rather than by positional indexing.
    """
    df = df.copy()
    df["_LagMCAP"] = df.groupby(stock_id_column)[mcap_column].shift(1)

    pf = (
        df.groupby(date_column)
        .apply(_vw_group, return_col=return_column)
        .rename("PortfolioReturn")
        .reset_index()
    )

    pf = pf.dropna(subset=["PortfolioReturn"])
    return pf


def equally_weighted_portfolio(df, return_column, mcap_column,
                                stock_id_column, date_column):
    """
    Construct an equally-weighted portfolio.

    A stock is included in month t only if it had a valid market cap in month
    t-1 AND a valid return in month t. This mirrors the VW construction's
    lagging convention — both portfolios share the same eligible universe.
    """
    df = df.copy()
    df["_LagMCAP"] = df.groupby(stock_id_column)[mcap_column].shift(1)

    mask = df["_LagMCAP"].notna() & df[return_column].notna()

    pf = (
        df.loc[mask]
        .groupby(date_column)[return_column]
        .mean()
        .rename("PortfolioReturn")
        .reset_index()
    )

    return pf

def load_filtered_eu(out_dir, countries=None, columns=None):
    """Load the filtered European price panel from the per-country files written by scripts/22_filter_prices_eu.py.

    out_dir: e.g. "D:/Datastream/PriceData/EU/processed/EU_data_panel_filtered_0.25"
    countries: GEOGN names, e.g. ["GERMANY", "FRANCE"] (default: all files)
    columns: subset of columns to read (saves memory), e.g. ["Date", "Stock", "Country", "Return_EUR"]
    """
    import os
    import pandas as pd
    files = sorted(f for f in os.listdir(out_dir) if f.endswith(".feather"))
    if countries:
        wanted = {c.upper() for c in countries}
        files = [f for f in files if f[: -len(".feather")] in wanted]
    return pd.concat([pd.read_feather(os.path.join(out_dir, f), columns=columns) for f in files],
                     ignore_index=True)
