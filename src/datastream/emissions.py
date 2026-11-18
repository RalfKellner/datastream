"""Availability and structure of the Refinitiv ESG emission variables (step 1 of the sustainable-finance dataset).

The generic firm-variable battery (``firm_evaluation.check_variable``, notebook 02) is run for every emission
variable as well. On top of it, this module answers the questions that matter for adding emissions to the
monthly panel:

1. **Coverage.** How many stocks of the filtered price universe have emissions, per year, equal- and
   value-weighted, by size group; reported (ENERDP023/024/025) vs. estimated (ENERDP123) and combined.
2. **Timing.** Emissions are annual. In which month does Datastream stamp a new value - relative to the
   Worldscope report month of the same firm (change month of total assets / sales) and to the fiscal-year end?
   If emissions change in the same month as Worldscope, Datastream stamps them at the fiscal period (not at
   the publication date) and the availability lag must be chosen for ESG separately (ESG data are collected
   from sustainability reports, often many months after the annual report).
3. **Consistency.** total vs. scope 1 + scope 2; reported total vs. estimated total (is the estimate equal to
   the reported value where one exists, i.e. a gap-filled series?); do the variables change in the same month?
4. **Padding.** Values carried forward after the last report / after delisting (stale runs).
5. **Share classes.** Emissions belong to the company: do all lines of the same Worldscope company (statics
   WC06105 / WC06035) carry the same value? (Matters for value-weighted aggregates and the later merge.)
6. **Examples.** The change events of a few well-known firms, to eyeball the stamping.

Everything uses the mnemonics; readable names come from ``config/firm_variables.csv``.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from datastream import firm_evaluation as fe

logger = logging.getLogger(__name__)

TOTAL, SCOPE1, SCOPE2, ESTIMATED = "ENERDP023", "ENERDP024", "ENERDP025", "ENERDP123"
REPORTED = [TOTAL, SCOPE1, SCOPE2]
# Worldscope items whose change month defines the firm's report month (as in panel_builder.report_snapshots)
WS_REPORT_VARS = ["WC02999", "WC01001"]
EXAMPLE_NAMES = ["EXXON", "APPLE", "MICROSOFT", "DUKE ENERGY", "WALMART", "DOW INC", "NEXTERA", "AMERICAN AIRLINES"]


# ---------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------

def _months_between(later: pd.Series, earlier: pd.Series) -> pd.Series:
    return (later.dt.year - earlier.dt.year) * 12 + (later.dt.month - earlier.dt.month)


def universe_cross_sections(uni: pd.DataFrame, month: int = 12) -> pd.DataFrame:
    """Universe rows of one calendar month per year (default December)."""
    return uni[uni["Date"].dt.month == month]


def wide_emissions(panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """``DSCD | Date | <vars>`` (outer join of the prepared panels)."""
    frames = [fe.prepare_panel(p, v).set_index(["DSCD", "Date"])[v] for v, p in panels.items()]
    return pd.concat(frames, axis=1, join="outer").reset_index()


# ---------------------------------------------------------------------------------------------------------
# 1. raw overview and coverage
# ---------------------------------------------------------------------------------------------------------

def raw_overview(panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for v, panel in panels.items():
        p = fe.prepare_panel(panel, v)
        if p.empty:
            rows.append({"variable": v, "n_firms": 0})
            continue
        fy = fe.firm_years(p, v)
        nd = p.groupby("DSCD")[v].nunique()
        rows.append({
            "variable": v, "n_firms": p["DSCD"].nunique(), "n_firm_months": len(p),
            "first": f"{p['Date'].min():%Y-%m}", "last": f"{p['Date'].max():%Y-%m}",
            "first_year_100_firms": _first_year(fy, 100), "first_year_500_firms": _first_year(fy, 500),
            "median_distinct_values_per_firm": float(nd.median()),
            "share_zero": float((p[v] == 0).mean()), "share_negative": float((p[v] < 0).mean()),
            "p01": p[v].quantile(0.01), "p50": p[v].median(), "p99": p[v].quantile(0.99),
        })
    return pd.DataFrame(rows).set_index("variable")


def _first_year(fy: pd.DataFrame, n: int):
    c = fy.groupby("Year")["DSCD"].nunique()
    yrs = c.index[c >= n]
    return int(yrs.min()) if len(yrs) else np.nan


def add_groups(w: pd.DataFrame) -> pd.DataFrame:
    """Combined availability columns: any reported item, estimated, reported or estimated."""
    w = w.copy()
    rep = [c for c in REPORTED if c in w.columns]
    w["any_reported"] = w[rep].notna().any(axis=1) if rep else False
    w["scope12_reported"] = w[[SCOPE1, SCOPE2]].notna().all(axis=1) if {SCOPE1, SCOPE2} <= set(w.columns) else False
    w["estimated"] = w[ESTIMATED].notna() if ESTIMATED in w.columns else False
    w["reported_or_estimated"] = w["any_reported"] | w["estimated"]
    w["estimated_only"] = w["estimated"] & ~w["any_reported"]
    return w


def coverage_by_year(w: pd.DataFrame, uni: pd.DataFrame, month: int = 12) -> pd.DataFrame:
    """Per year (December cross-section of the universe): stocks, and for each variable / group the number,
    EW share and VW share of universe stocks with a value."""
    cs = universe_cross_sections(uni, month)[["DSCD", "Date", "MarketCAP"]]
    w = add_groups(w)
    cols = [c for c in w.columns if c not in ("DSCD", "Date")]
    m = cs.merge(w, on=["DSCD", "Date"], how="left")
    for c in cols:
        m[c] = m[c].notna() & (m[c] != False)  # noqa: E712  (bool groups and numeric values)
    m["Year"] = m["Date"].dt.year
    g = m.groupby("Year")
    out = {"n_universe": g.size(), "mcap_universe_bn": g["MarketCAP"].sum() / 1000}
    for c in cols:
        out[f"n_{c}"] = g[c].sum()
    for c in cols:
        out[f"ew_{c}"] = g[c].mean()
        out[f"vw_{c}"] = (m["MarketCAP"].where(m[c], 0)).groupby(m["Year"]).sum() / g["MarketCAP"].sum()
    return pd.DataFrame(out)


def coverage_by_size(w: pd.DataFrame, uni: pd.DataFrame, group: str = "reported_or_estimated",
                     month: int = 12, first_year: int = 2000) -> pd.DataFrame:
    cs = universe_cross_sections(uni, month)
    cs = cs[cs["Date"].dt.year >= first_year][["DSCD", "Date", "size_group"]].dropna(subset=["size_group"])
    w = add_groups(w)[["DSCD", "Date", group]]
    m = cs.merge(w, on=["DSCD", "Date"], how="left")
    m[group] = m[group].eq(True)
    return m.groupby([m["Date"].dt.year.rename("Year"), "size_group"])[group].mean().unstack()


def firm_counts(w: pd.DataFrame, uni: pd.DataFrame, first_year: int = 2002) -> pd.DataFrame:
    """Distinct stocks of the universe (from ``first_year`` on) ever with each kind of emission data and their
    number of firm-years (December cross-sections) with data."""
    u = uni[uni["Date"].dt.year >= first_year]
    stocks = set(u["DSCD"])
    w = add_groups(w)
    w = w[w["DSCD"].isin(stocks)]
    m = universe_cross_sections(u)[["DSCD", "Date"]].merge(w, on=["DSCD", "Date"], how="left")
    rows = [{"group": "universe", "n_stocks": len(stocks), "n_stock_years": len(m)}]
    for c in ["any_reported", "scope12_reported", "estimated", "reported_or_estimated", "estimated_only"]:
        ever = w.loc[w[c].astype(bool), "DSCD"].nunique()
        rows.append({"group": c, "n_stocks": ever, "n_stock_years": int(m[c].eq(True).sum())})
    out = pd.DataFrame(rows).set_index("group")
    out["share_of_universe_stocks"] = out["n_stocks"] / len(stocks)
    return out


# ---------------------------------------------------------------------------------------------------------
# 2. timing
# ---------------------------------------------------------------------------------------------------------

def ws_report_months(ws_panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """``DSCD | ws_report_month``: months in which any of the Worldscope report items changes."""
    events = []
    for v, panel in ws_panels.items():
        p = fe.prepare_panel(panel, v)
        events.append(fe.change_events(p, v)[["DSCD", "Date"]])
    if not events:
        return pd.DataFrame(columns=["DSCD", "ws_report_month"])
    ev = pd.concat(events).drop_duplicates().rename(columns={"Date": "ws_report_month"})
    return ev.sort_values(["DSCD", "ws_report_month"]).reset_index(drop=True)


def change_month_distribution(panels: dict[str, pd.DataFrame], uni: pd.DataFrame | None = None) -> pd.DataFrame:
    """Share of value changes per calendar month (rows: variables)."""
    rows = {}
    for v, panel in panels.items():
        p = fe.prepare_panel(panel, v)
        if uni is not None:
            p = fe.in_universe(p, uni)
        ch = fe.change_events(p, v)
        rows[v] = ch["Date"].dt.month.value_counts(normalize=True).reindex(range(1, 13), fill_value=0.0)
    out = pd.DataFrame(rows).T
    out.columns = [f"m{c:02d}" for c in out.columns]
    return out


def lag_vs_worldscope(panels: dict[str, pd.DataFrame], ws_events: pd.DataFrame, uni: pd.DataFrame | None = None,
                      max_lag: int = 12) -> pd.DataFrame:
    """For every emission value change: months since the firm's latest Worldscope report month (0 = same
    month). Rows: variables; columns: share of changes at 0, 1, ..., max_lag and '>max_lag' / 'no_ws'."""
    rows = {}
    ws = ws_events.sort_values("ws_report_month")
    for v, panel in panels.items():
        p = fe.prepare_panel(panel, v)
        if uni is not None:
            p = fe.in_universe(p, uni)
        ch = fe.change_events(p, v)[["DSCD", "Date"]].sort_values("Date")
        if ch.empty:
            continue
        j = pd.merge_asof(ch, ws, left_on="Date", right_on="ws_report_month", by="DSCD", direction="backward")
        lag = _months_between(j["Date"], j["ws_report_month"])
        cats = lag.clip(upper=max_lag + 1).map(lambda x: "no_ws" if pd.isna(x) else
                                               (f">{max_lag}" if x > max_lag else str(int(x))))
        order = [str(i) for i in range(max_lag + 1)] + [f">{max_lag}", "no_ws"]
        s = cats.value_counts(normalize=True).reindex(order, fill_value=0.0)
        s["n_changes"] = len(ch)
        rows[v] = s
    return pd.DataFrame(rows).T


def lag_vs_fye(panels: dict[str, pd.DataFrame], fye: pd.DataFrame | None, uni: pd.DataFrame | None = None
               ) -> pd.DataFrame:
    if fye is None or fye.empty:
        return pd.DataFrame()
    rows = {}
    for v, panel in panels.items():
        p = fe.prepare_panel(panel, v)
        if uni is not None:
            p = fe.in_universe(p, uni)
        lag = fe.reporting_lag(p, v, fye)
        if len(lag):
            rows[v] = lag["share"]
    out = pd.DataFrame(rows).T
    out.columns = [f"fye+{c}" for c in out.columns]
    return out


def latest_year_completeness(panels: dict[str, pd.DataFrame], uni: pd.DataFrame) -> pd.DataFrame:
    """Number of universe stocks whose value changed in each of the last calendar years (a proxy for how many
    new annual values have arrived): shows how far the most recent years are still incomplete."""
    rows = {}
    last = uni["Date"].max().year
    for v, panel in panels.items():
        p = fe.in_universe(fe.prepare_panel(panel, v), uni)
        ch = fe.change_events(p, v)
        first = p.groupby("DSCD").head(1)[["DSCD", "Date"]]
        ev = pd.concat([ch[["DSCD", "Date"]], first])
        rows[v] = ev["Date"].dt.year.value_counts().reindex(range(last - 8, last + 1), fill_value=0)
    return pd.DataFrame(rows).T


# ---------------------------------------------------------------------------------------------------------
# 3. consistency
# ---------------------------------------------------------------------------------------------------------

def scope_identity(w: pd.DataFrame, tol: float = 0.01) -> pd.DataFrame:
    """total / (scope 1 + scope 2) on December firm-years with all three values, per year."""
    if not {TOTAL, SCOPE1, SCOPE2} <= set(w.columns):
        return pd.DataFrame()
    d = w[w["Date"].dt.month == 12].dropna(subset=[TOTAL, SCOPE1, SCOPE2])
    s = d[SCOPE1] + d[SCOPE2]
    r = d[TOTAL] / s.where(s > 0)
    d = d.assign(r=r, Year=d["Date"].dt.year).dropna(subset=["r"])
    g = d.groupby("Year")["r"]
    return pd.DataFrame({
        "n": g.size(), "median_ratio": g.median(),
        "share_equal": g.apply(lambda x: ((x - 1).abs() <= tol).mean()),
        "share_total_below": g.apply(lambda x: (x < 1 - tol).mean()),
        "share_total_above": g.apply(lambda x: (x > 1 + tol).mean()),
    })


def reported_vs_estimated(w: pd.DataFrame, tol: float = 0.001) -> pd.DataFrame:
    """Per year (December): firm-years with reported total only, estimated only, both; where both exist the
    share with estimated == reported (within ``tol``) and the median ratio."""
    if not {TOTAL, ESTIMATED} <= set(w.columns):
        return pd.DataFrame()
    d = w[w["Date"].dt.month == 12]
    d = d[d[TOTAL].notna() | d[ESTIMATED].notna()].assign(Year=lambda x: x["Date"].dt.year)
    both = d[TOTAL].notna() & d[ESTIMATED].notna()
    ratio = (d[ESTIMATED] / d[TOTAL].where(d[TOTAL] > 0)).where(both)
    d = d.assign(both=both, rep_only=d[TOTAL].notna() & d[ESTIMATED].isna(),
                 est_only=d[TOTAL].isna() & d[ESTIMATED].notna(), ratio=ratio,
                 equal=((ratio - 1).abs() <= tol).where(both))
    g = d.groupby("Year")
    return pd.DataFrame({
        "n_reported_only": g["rep_only"].sum(), "n_estimated_only": g["est_only"].sum(), "n_both": g["both"].sum(),
        "share_equal_where_both": g["equal"].mean(), "median_ratio_where_both": g["ratio"].median(),
        "p10_ratio": g["ratio"].quantile(0.1), "p90_ratio": g["ratio"].quantile(0.9),
    })


def change_alignment(panels: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Share of value changes of the row variable that coincide with a change of the column variable in the
    same firm-month (among firm-months where both variables have values)."""
    ev, keys = {}, {}
    for v, panel in panels.items():
        p = fe.prepare_panel(panel, v)
        keys[v] = pd.MultiIndex.from_frame(p[["DSCD", "Date"]])
        ev[v] = pd.MultiIndex.from_frame(fe.change_events(p, v)[["DSCD", "Date"]])
    vs = list(panels)
    mat = pd.DataFrame(np.nan, index=vs, columns=vs)
    for a in vs:
        for b in vs:
            ea = ev[a][ev[a].isin(keys[b])]
            if len(ea) >= 50:
                mat.loc[a, b] = float(ea.isin(ev[b]).mean())
    return mat


# ---------------------------------------------------------------------------------------------------------
# 5. share classes
# ---------------------------------------------------------------------------------------------------------

def firm_ids(statics: pd.DataFrame | None) -> pd.Series | None:
    if statics is None:
        return None
    ids = [c for c in ("WC06105", "WC06035") if c in statics.columns]
    if not ids:
        return None
    st = statics.assign(DSCD=statics["DSCD"].astype(str).str.strip()).drop_duplicates("DSCD").set_index("DSCD")
    fid = st[ids].astype(str).replace({"nan": np.nan, "NA": np.nan, "": np.nan}).bfill(axis=1).iloc[:, 0]
    return fid.dropna()


def share_class_consistency(w: pd.DataFrame, uni: pd.DataFrame, fid: pd.Series | None) -> pd.DataFrame:
    """December firm-years of companies with several lines in the universe: do all lines carry the same value?"""
    if fid is None:
        return pd.DataFrame()
    cs = universe_cross_sections(uni)[["DSCD", "Date"]].assign(firm_id=lambda d: d["DSCD"].map(fid))
    cs = cs.dropna(subset=["firm_id"])
    multi = cs.groupby(["firm_id", "Date"])["DSCD"].transform("size") > 1
    m = cs[multi].merge(w, on=["DSCD", "Date"], how="left")
    rows = []
    for v in [c for c in w.columns if c not in ("DSCD", "Date")]:
        g = m.dropna(subset=[v]).groupby(["firm_id", "Date"])[v]
        n_lines = g.size()
        sub = g.nunique()[n_lines > 1]
        rows.append({"variable": v, "n_multi_line_firm_years": int(len(sub)),
                     "share_identical": float((sub == 1).mean()) if len(sub) else np.nan,
                     "n_firm_years_some_lines_missing": int((m.groupby(["firm_id", "Date"])[v]
                                                             .apply(lambda s: s.notna().any() and s.isna().any())).sum())})
    return pd.DataFrame(rows).set_index("variable")


# ---------------------------------------------------------------------------------------------------------
# 6. examples
# ---------------------------------------------------------------------------------------------------------

def example_firms(statics: pd.DataFrame | None, uni: pd.DataFrame, patterns=EXAMPLE_NAMES) -> pd.DataFrame:
    """One line per pattern: the universe stock with the largest market cap whose ENAME contains it."""
    if statics is None or "ENAME" not in statics.columns:
        return pd.DataFrame(columns=["pattern", "DSCD", "ENAME"])
    st = statics.assign(DSCD=statics["DSCD"].astype(str).str.strip()).drop_duplicates("DSCD")
    size = uni.groupby("DSCD")["MarketCAP"].max()
    rows = []
    for pat in patterns:
        c = st[st["ENAME"].astype(str).str.upper().str.contains(pat, regex=False)].copy()
        c["mcap"] = c["DSCD"].map(size)
        c = c.dropna(subset=["mcap"]).sort_values("mcap", ascending=False)
        if len(c):
            rows.append({"pattern": pat, "DSCD": c.iloc[0]["DSCD"], "ENAME": c.iloc[0]["ENAME"]})
    return pd.DataFrame(rows)


def example_events(w: pd.DataFrame, ws_events: pd.DataFrame, examples: pd.DataFrame,
                   fye: pd.DataFrame | None, from_year: int = 2015) -> pd.DataFrame:
    """For the example firms: every month from ``from_year`` in which an emission value or the Worldscope
    report changes, with the values after the change."""
    out = []
    vars_ = [c for c in w.columns if c not in ("DSCD", "Date")]
    for ex in examples.itertuples():
        d = w[w["DSCD"] == ex.DSCD].sort_values("Date")
        if d.empty:
            continue
        changed = d[vars_].ne(d[vars_].shift(1)) & ~(d[vars_].isna() & d[vars_].shift(1).isna())
        ev = d[changed.any(axis=1)].copy()
        ev["changed"] = changed[changed.any(axis=1)].apply(lambda r: ",".join(r.index[r]), axis=1)
        wsm = set(ws_events.loc[ws_events["DSCD"] == ex.DSCD, "ws_report_month"])
        extra = pd.DataFrame({"Date": sorted(m for m in wsm if m not in set(ev["Date"]))})
        ev = pd.concat([ev, extra.assign(DSCD=ex.DSCD, changed="")], ignore_index=True)
        ev["ws_report"] = ev["Date"].isin(wsm)
        ev = ev[ev["Date"].dt.year >= from_year].sort_values("Date")
        ev.insert(0, "firm", ex.ENAME)
        if fye is not None:
            fm = fye.loc[fye["DSCD"] == ex.DSCD, "fye_month"]
            ev["fye_month"] = int(fm.iloc[0]) if len(fm) else np.nan
        out.append(ev)
    if not out:
        return pd.DataFrame()
    res = pd.concat(out, ignore_index=True)
    res["Date"] = res["Date"].dt.strftime("%Y-%m")
    return res


# ---------------------------------------------------------------------------------------------------------
# 7. fiscal-year alignment: which fiscal year does a value stamped in month m describe?
# ---------------------------------------------------------------------------------------------------------

def _annual_snapshots(panel: pd.DataFrame, var: str) -> pd.DataFrame:
    """First observation and every value change of a firm (one row per annual update)."""
    p = fe.prepare_panel(panel, var)
    first = p.groupby("DSCD").head(1)
    ev = pd.concat([first, fe.change_events(p, var)[["DSCD", "Date", var]]])
    return ev.drop_duplicates(["DSCD", "Date"]).sort_values(["DSCD", "Date"]).reset_index(drop=True)


def fiscal_alignment(e_panel: pd.DataFrame, e_var: str, s_panel: pd.DataFrame, s_var: str = "WC01001",
                     offsets=(-1, 0, 1), min_gap: int = 10, max_gap: int = 14) -> pd.DataFrame:
    """Growth of the emission variable between two consecutive annual updates vs. growth of Worldscope sales,
    with sales taken from the update in effect at the emission stamp (offset 0), the previous one (-1) or the
    next one (+1). Emissions move with activity, so the offset with the highest correlation tells which
    Worldscope fiscal year the emission value belongs to. +1: the emission stamped in month m describes the
    fiscal year whose accounts appear only at the *next* Worldscope update (i.e. the year starting at m).

    Returns one row per offset (all years and per event year 2019-2021, where COVID gives a strong signal):
    Spearman correlation and the number of pairs."""
    e = _annual_snapshots(e_panel, e_var).rename(columns={e_var: "E"})
    e = e[e["E"] > 0]
    g = e.groupby("DSCD")
    gap = _months_between(e["Date"], g["Date"].shift(1))
    e["dE"] = np.log(e["E"] / g["E"].shift(1)).where(gap.between(min_gap, max_gap))
    e = e.dropna(subset=["dE"])

    s = _annual_snapshots(s_panel, s_var).rename(columns={s_var: "S"})
    s = s[s["S"] > 0].copy()
    s["k"] = s.groupby("DSCD").cumcount()
    gs = s.groupby("DSCD")
    s["dS"] = np.log(s["S"] / gs["S"].shift(1)).where(
        _months_between(s["Date"], gs["Date"].shift(1)).between(min_gap, max_gap))

    j = pd.merge_asof(e.sort_values("Date"), s[["DSCD", "Date", "k"]].rename(columns={"Date": "s_date"}).sort_values("s_date"),
                      left_on="Date", right_on="s_date", by="DSCD", direction="backward").dropna(subset=["k"])
    lookup = s.set_index(["DSCD", "k"])["dS"]
    rows = []
    for off in offsets:
        idx = pd.MultiIndex.from_arrays([j["DSCD"], (j["k"] + off).astype(int)])
        d = j.assign(dS=lookup.reindex(idx).to_numpy()).dropna(subset=["dS"])
        d = d[(d["dE"].abs() < 3) & (d["dS"].abs() < 3)]
        row = {"offset": off, "n_pairs": len(d), "spearman_all": d["dE"].corr(d["dS"], method="spearman")}
        for y in (2019, 2020, 2021):
            dy = d[d["Date"].dt.year == y]
            row[f"spearman_{y}"] = dy["dE"].corr(dy["dS"], method="spearman") if len(dy) >= 30 else np.nan
            row[f"median_dE_{y}"] = dy["dE"].median() if len(dy) else np.nan
        rows.append(row)
    return pd.DataFrame(rows).set_index("offset")
