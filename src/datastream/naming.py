"""Readable column names for output datasets.

Internally (raw files, imported variable panels, import logs, checks) variables keep their Datastream
mnemonics, so everything can be traced back to Datastream and new downloads need no mapping step. Output
datasets (baseline panel, merged panels) use the readable names from the ``name`` column of
``config/firm_variables.csv``; the JSON sidecars store the mapping back to the mnemonics.

Descriptions of the Datastream datatypes: ``datatypes/pricedata_variables.md``.
"""

from __future__ import annotations

import re

import pandas as pd

# columns of the monthly universe (from the daily price panel of 22_filter_prices_us.py) -> output names
PRICE_NAMES = {
    "MarketCAP": "market_cap",          # MV, millions of the local currency (US: USD)
    "MarketCAP_EUR": "market_cap_eur",  # MV~E, EUR millions (Europe)
    "Close": "price",                   # P, adjusted close price (local currency)
    "MTBV": "mtbv",                     # market to book value (Datastream)
    "ReturnIndex": "return_index",      # RI (local currency)
    "ReturnIndex_EUR": "return_index_eur",  # RI~E (Europe)
    "Country": "country",               # GEOGN of the line (Europe)
    "UnadjClose": "price_unadjusted",   # UP
    "Volume": "volume",                 # VO
    "n_days": "n_trading_days",
}

# static datatypes -> output names (current values from the statics download, not point in time)
STATIC_NAMES = {
    "ENAME": "company_name",
    "ISIN": "isin",
    "WC05601": "ticker",
    "RIC": "ric",
    "EXMNEM": "exchange",
    "TR1N": "trbc_economic_sector",
    "TR2N": "trbc_business_sector",
    "TR3N": "trbc_industry",
}

_SUFFIXES = ("_prev",)


def firm_names(registry: pd.DataFrame) -> dict[str, str]:
    """Mnemonic -> readable name from the registry; mnemonics without a name are lower-cased."""
    if "name" not in registry.columns:
        return {}
    out = {}
    for v, n in zip(registry["variable"], registry["name"].fillna("")):
        out[str(v)] = str(n).strip() or str(v).lower()
    return out


def output_mapping(columns, registry: pd.DataFrame, include_price: bool = True) -> dict[str, str]:
    """Rename map for a data frame's columns: firm mnemonics (also with ``_prev``), price and static columns."""
    names = firm_names(registry)
    mapping = {}
    for c in columns:
        base, suffix = c, ""
        for s in _SUFFIXES:
            if c.endswith(s):
                base, suffix = c[: -len(s)], s
        if base in names:
            mapping[c] = names[base] + suffix
        elif include_price and c in PRICE_NAMES:
            mapping[c] = PRICE_NAMES[c]
        elif c in STATIC_NAMES:
            mapping[c] = STATIC_NAMES[c]
    dup = pd.Series(list(mapping.values())).duplicated()
    if dup.any():
        raise ValueError(f"Readable names are not unique: {sorted(set(pd.Series(list(mapping.values()))[dup]))}")
    clash = set(mapping.values()) & (set(columns) - set(mapping))
    if clash:
        raise ValueError(f"Readable names clash with existing columns: {sorted(clash)}")
    return mapping


def check_names(registry: pd.DataFrame) -> None:
    """Names must be unique, lower-case identifiers."""
    names = registry.loc[registry.get("name", pd.Series(dtype=str)).fillna("") != "", "name"]
    bad = [n for n in names if not re.fullmatch(r"[a-z][a-z0-9_]*", n)]
    if bad:
        raise ValueError(f"Names must be lower-case identifiers: {bad}")
    dup = names[names.duplicated()]
    if len(dup):
        raise ValueError(f"Duplicate names in the registry: {sorted(set(dup))}")
