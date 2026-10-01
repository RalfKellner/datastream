"""Synthetic price panel and firm variables with planted errors, for testing the firm-variable checks.

Planted (and expected to be caught):
* 10 delisted firms keep their Worldscope values for 36 months after the last price (padding)
* 5 firms have the same total-assets value for 6 years while trading (stale)
* 3 firms report total assets x1000 for one year (unit reversal); 1 firm a permanent x1000 jump
* 2% of firm-years violate total debt = short-term + long-term debt
* a few negative total assets
* 20 firms with firm data that are not in the price universe
* small firms are covered less often, and coverage starts late for them
* 10 firms without short-term debt (runs of zeros: economic, must not count as stale)
* 1 SPAC-like firm: assets and sales jump by x5000 while debt is unchanged (large jumps, but not a unit error)
* an empty variable panel (leftover of an earlier import)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from datastream.preprocessing.firm_data import VARIABLES_SUBDIR
from datastream.preprocessing.static_data import STATIC_PANEL_SUBDIR

PLANTED = {}


def make_synthetic(base: Path, n_stocks: int = 300, start="2000-01-01", end="2010-12-31", seed=0) -> dict:
    rng = np.random.default_rng(seed)
    base = Path(base)
    price_dir = base / "PriceData"
    firm_root = base / "Firm" / "US"
    price_dir.mkdir(parents=True, exist_ok=True)

    days = pd.bdate_range(start, end)
    stocks = [f"S{i:05d}" for i in range(n_stocks)]
    size = np.exp(rng.normal(6, 1.5, n_stocks))          # market cap in millions
    last_day = np.full(n_stocks, len(days) - 1)
    delisted = rng.choice(n_stocks, 40, replace=False)
    last_day[delisted] = rng.integers(len(days) // 3, len(days) - 300, 40)

    rows = []
    for i, s in enumerate(stocks):
        d = days[: last_day[i] + 1]
        r = rng.normal(0.0003, 0.02, len(d))
        mc = size[i] * np.exp(np.cumsum(r))
        close = 20 * np.exp(np.cumsum(r))
        rows.append(pd.DataFrame({"Stock": s, "Date": d, "MarketCAP": mc, "Close": close,
                                  "MTBV": np.nan, "ReturnIndex": 100 * np.exp(np.cumsum(r))}))
    price = pd.concat(rows, ignore_index=True)
    price.to_feather(price_dir / "US_data_panel_filtered_0.2.feather", chunksize=50_000)

    statics = pd.DataFrame({"DSCD": stocks,
                            "EXMNEM": np.where(np.arange(n_stocks) % 2 == 0, "NYS", "NAS"),
                            "DelistingDate": ""})
    statics.loc[delisted, "DelistingDate"] = [f"{days[last_day[i]]:%Y-%m-%d}" for i in delisted]
    statics.to_csv(price_dir / "statics_filtered_0.2.csv", index=False)

    # ---- firm variables (monthly, annual update 3 months after FYE) ----
    months = pd.date_range(start, end, freq="MS")
    fye = np.where(rng.random(n_stocks) < 0.8, 12, 6)
    covered = rng.random(n_stocks) < np.clip(0.4 + 0.12 * np.log(size / size.min() + 1), 0, 0.97)
    cov_start = np.where(size < np.median(size), pd.Timestamp("2004-01-01"), pd.Timestamp(start))
    padded = [i for i in delisted if covered[i]][:10]
    stale = [i for i in range(n_stocks) if covered[i] and i not in delisted][:5]
    rev = [i for i in range(n_stocks) if covered[i] and i not in delisted][5:8]
    jump = [i for i in range(n_stocks) if covered[i] and i not in delisted][8]
    healthy = [i for i in range(n_stocks) if covered[i] and i not in delisted]
    zero_debt = healthy[9:19]
    spac = healthy[19]
    PLANTED.update(padded=[stocks[i] for i in padded], stale=[stocks[i] for i in stale],
                   reversal=[stocks[i] for i in rev], jump=stocks[jump], zero_debt=[stocks[i] for i in zero_debt],
                   spac=stocks[spac])

    recs = []
    for i, s in enumerate(stocks + [f"X{j:05d}" for j in range(20)]):
        real = i < n_stocks
        if real and not covered[i]:
            continue
        fm = fye[i] if real else 12
        stop = (days[last_day[i]] + pd.DateOffset(months=36 if i in padded else 0)) if real else pd.Timestamp(end)
        mm = months[(months >= (cov_start[i] if real else months[0])) & (months <= stop)]
        if len(mm) == 0:
            continue
        # fiscal year the value refers to: changes in month fye+3
        fy = mm.year - ((mm.month - (fm % 12) - 3) < 0).astype(int)
        ta0 = (size[i] if real else 500) * 1000 * np.exp(rng.normal(0.3, 0.3))
        fy_idx = fy - fy.min()
        g = rng.normal(0.05, 0.1, int(fy_idx.max()) + 1)
        ta = ta0 * np.exp(np.cumsum(g))[fy_idx]
        if i in stale:
            ta = np.where((mm.year >= 2003) & (mm.year < 2009), ta[(mm.year >= 2003).argmax()], ta)
        if i in rev:
            ta = np.where(fy == fy.min() + 3, ta * 1000, ta)
        if i == jump:
            ta = np.where(fy >= fy.min() + 5, ta * 1000, ta)
        if i == spac:
            ta = np.where(fy >= fy.min() + 4, ta * 5000, ta)
        std = ta * 0.05 if i not in zero_debt else np.zeros(len(mm))
        ltd = ta * 0.2
        if i == spac:  # debt does not move with the trust account
            base = ta / np.where(fy >= fy.min() + 4, 5000, 1)
            std, ltd = base * 0.05, base * 0.2
        tdebt = std + ltd
        eq = ta * 0.4
        sales = ta * 0.8
        ni = sales * 0.05
        ebit = sales * (0.02 + 0.18 * ((i * 7919) % 100) / 100)      # firm-specific margin
        eps = np.full(len(mm), 1.0)
        recs.append(pd.DataFrame({"DSCD": s, "Date": mm, "fy": fy, "WC02999": ta, "WC03051": std, "WC03251": ltd,
                                  "WC03255": tdebt, "WC03501": eq, "WC01001": sales, "WC01751": ni,
                                  "WC02003": ta * 0.1, "WC08231": 100 * tdebt / eq, "EPS": eps,
                                  "WC01250": ebit}))
    firm = pd.concat(recs, ignore_index=True)

    # identity violations in 2% of firm-years; negative total assets
    fyk = firm[["DSCD", "fy"]].drop_duplicates().sample(frac=0.02, random_state=seed)
    bad = firm.set_index(["DSCD", "fy"]).index.isin(fyk.set_index(["DSCD", "fy"]).index)
    firm.loc[bad, "WC03255"] *= 1.5
    negk = firm[["DSCD", "fy"]].drop_duplicates().sample(5, random_state=seed + 1)
    neg = firm.set_index(["DSCD", "fy"]).index.isin(negk.set_index(["DSCD", "fy"]).index)
    firm.loc[neg, "WC02999"] *= -1

    vdir = firm_root / VARIABLES_SUBDIR
    vdir.mkdir(parents=True, exist_ok=True)
    for v in ["WC02999", "WC03051", "WC03251", "WC03255", "WC03501", "WC01001", "WC01751", "WC02003",
              "WC08231", "EPS", "WC01250"]:
        firm[["Date", "DSCD", v]].to_parquet(vdir / f"{v}.parquet", index=False)
    firm[["Date", "DSCD"]].head(0).assign(WC05350=pd.Series(dtype=float)).to_parquet(vdir / "WC05350.parquet",
                                                                                     index=False)

    sdir = firm_root / STATIC_PANEL_SUBDIR
    sdir.mkdir(parents=True, exist_ok=True)
    fye_dates = [pd.Timestamp(2010, m, 1) + pd.offsets.MonthEnd(0) for m in fye]
    pd.DataFrame({"DSCD": stocks, "WC05350": fye_dates, "as_of": pd.Timestamp("2026-09-30")}) \
        .to_parquet(sdir / "WC05350.parquet", index=False)
    return {"price_dir": price_dir, "firm_root": firm_root, "planted": dict(PLANTED)}
