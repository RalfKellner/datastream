"""Synthetic checks for datastream.emissions and scripts/32_check_emissions.py."""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from datastream import emissions as em

REPO = Path(__file__).resolve().parents[1]


def _synthetic(tmp_path: Path):
    """30 stocks 2010-2022. Stocks 0-19 report emissions (scope 1 + 2 = total), stamped 2 months after FYE
    (December), i.e. in February, the same month as Worldscope; stocks 20-29 have estimates only. Estimated
    total = reported total where reported. Stocks 0 and 1 are two share classes of one company."""
    dates = pd.date_range("2010-01-31", "2022-12-31", freq="ME")
    rng = np.random.default_rng(0)
    uni = pd.DataFrame([(f"S{i:02d}", d) for i in range(30) for d in dates], columns=["DSCD", "Date"])
    uni["MarketCAP"] = rng.uniform(10, 1000, len(uni))
    uni["size_group"] = (uni.groupby("Date")["MarketCAP"].rank(pct=True) * 5).clip(upper=4.999).astype(int) + 1
    uni["first_price_month"] = dates[0]
    uni["last_price_month"] = dates[-1]
    uni["delisting_date"] = pd.NaT

    def annual(i, base, start=2012):
        vals = []
        for d in dates:
            fy = d.year - 1 if d.month >= 2 else d.year - 2      # value of fiscal year fy known from February
            vals.append(np.nan if fy < start else base * (1 + 0.1 * (fy - start)) * (1 + i / 100))
        return np.array(vals)

    rows = {v: [] for v in ["ENERDP023", "ENERDP024", "ENERDP025", "ENERDP123", "WC02999", "WC01001"]}
    for i in range(30):
        s1, s2 = annual(i, 700), annual(i, 300)
        tot = s1 + s2
        for v, x in [("WC02999", annual(i, 5000, 2009)), ("WC01001", annual(i, 2000, 2009))]:
            rows[v].append(pd.DataFrame({"Date": dates, "DSCD": f"S{i:02d}", v: x}))
        if i < 20:
            for v, x in [("ENERDP023", tot), ("ENERDP024", s1), ("ENERDP025", s2), ("ENERDP123", tot)]:
                rows[v].append(pd.DataFrame({"Date": dates, "DSCD": f"S{i:02d}", v: x}))
        else:
            rows["ENERDP123"].append(pd.DataFrame({"Date": dates, "DSCD": f"S{i:02d}", "ENERDP123": tot * 1.2}))
    root = tmp_path / "firm" / "US"
    vdir = root / "Paneldata" / "variables"
    vdir.mkdir(parents=True)
    panels = {}
    for v, parts in rows.items():
        df = pd.concat(parts, ignore_index=True)
        df.to_parquet(vdir / f"{v}.parquet", index=False)
        panels[v] = df
    price = tmp_path / "price"
    price.mkdir()
    uni.to_parquet(price / "monthly_universe_0.25.parquet", index=False)
    st = pd.DataFrame({"DSCD": [f"S{i:02d}" for i in range(30)],
                       "ENAME": ["EXXON MOBIL"] + [f"FIRM {i}" for i in range(1, 30)],
                       "WC06105": ["C0", "C0"] + [f"C{i}" for i in range(2, 30)]})
    st.to_csv(price / "statics_filtered_0.25.csv", index=False)
    st.to_csv(price / "statics.csv", index=False)
    return root, price, uni, panels


def test_tables(tmp_path):
    root, price, uni, panels = _synthetic(tmp_path)
    ep = {v: panels[v] for v in ["ENERDP023", "ENERDP024", "ENERDP025", "ENERDP123"]}
    w = em.wide_emissions(ep)

    cov = em.coverage_by_year(w, uni)
    assert cov.loc[2015, "n_universe"] == 30
    assert cov.loc[2015, "n_any_reported"] == 20 and cov.loc[2015, "n_estimated_only"] == 10
    assert cov.loc[2015, "ew_reported_or_estimated"] == pytest.approx(1.0)
    assert cov.loc[2011, "n_reported_or_estimated"] == 0

    counts = em.firm_counts(w, uni)
    assert counts.loc["any_reported", "n_stocks"] == 20
    assert counts.loc["estimated_only", "n_stocks"] == 10

    ws = em.ws_report_months({v: panels[v] for v in em.WS_REPORT_VARS})
    lag = em.lag_vs_worldscope(ep, ws, uni)
    assert lag.loc["ENERDP023", "0"] == pytest.approx(1.0)      # stamped in the Worldscope report month

    months = em.change_month_distribution(ep, uni)
    assert months.loc["ENERDP023", "m02"] == pytest.approx(1.0)

    ident = em.scope_identity(w)
    assert (ident["share_equal"] == 1).all()

    rve = em.reported_vs_estimated(w)
    assert (rve["share_equal_where_both"].dropna() == 1).all()
    assert rve.loc[2015, "n_estimated_only"] == 10

    al = em.change_alignment(ep)
    assert al.loc["ENERDP023", "ENERDP123"] == pytest.approx(1.0)

    sc = em.share_class_consistency(w, uni, em.firm_ids(pd.read_csv(price / "statics.csv", dtype=str)))
    assert sc.loc["ENERDP023", "share_identical"] == 0.0         # lines of company C0 differ in the synthetic data

    ex = em.example_firms(pd.read_csv(price / "statics.csv", dtype=str), uni)
    assert ex.iloc[0]["DSCD"] == "S00"
    ev = em.example_events(w, ws, ex, None)
    assert len(ev) > 0 and ev["ws_report"].any()


def test_script(tmp_path):
    root, price, _, _ = _synthetic(tmp_path)
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "32_check_emissions.py"), "--root", str(root),
                        "--price-path", str(price), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rep = (out / "emissions_report.md").read_text(encoding="utf-8")
    assert "08_lag_vs_worldscope_report_month" in rep and "ENERDP123" in rep


def test_fiscal_alignment():
    """Emission value stamped at month m equals activity of the fiscal year whose sales appear at the next
    Worldscope update: the +1 offset must have the highest correlation."""
    rng = np.random.default_rng(1)
    rows_e, rows_s = [], []
    for i in range(200):
        g = rng.normal(0, 0.2, 12)
        level = np.exp(np.cumsum(g))
        for t, yr in enumerate(range(2012, 2024)):
            d = pd.Timestamp(f"{yr}-01-31")
            rows_s.append((f"F{i}", d, 1000 * level[t]))                       # FY yr-1 accounts
            if t + 1 < 12:
                rows_e.append((f"F{i}", d, 50 * level[t + 1] * np.exp(rng.normal(0, 0.02))))   # FY yr activity
    dates = lambda rows, v: pd.DataFrame(rows, columns=["DSCD", "Date", v])
    res = em.fiscal_alignment(dates(rows_e, "ENERDP023"), "ENERDP023", dates(rows_s, "WC01001"))
    assert res["spearman_all"].idxmax() == 1
    assert res.loc[1, "spearman_all"] > 0.9
