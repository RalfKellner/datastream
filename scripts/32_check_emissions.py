"""Availability and structure of the emission variables (Refinitiv ESG) relative to the filtered price universe.

Step 1 of the sustainable-finance dataset: before the emissions are added to the monthly panel, check
coverage (reported vs. estimated, EW/VW, size groups), timing (month of the annual update relative to the
Worldscope report month and the fiscal-year end), consistency (total vs. scope 1 + 2, reported vs. estimated),
padding and share classes. See src/datastream/emissions.py.

Output: <firm root>/Paneldata/checks/emissions/
    emissions_report.md     all tables in one text file (send this one)
    *.csv                   the same tables as CSV

    uv run python scripts/32_check_emissions.py
    uv run python scripts/32_check_emissions.py --penny 0.2           # if the universe is still the 0.2 version
    uv run python scripts/32_check_emissions.py --vars ENERDP023 ENERDP123

Prerequisites: monthly_universe_<p>.parquet (scripts/40), statics_filtered_<p>.csv / statics.csv (company
names, WC06105/WC06035), the emission variables and WC02999 / WC01001 imported with scripts/30; optional
static WC05350 (fiscal-year end).
"""

import argparse
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from datastream import emissions as em
from datastream import firm_evaluation as fe
from datastream.preprocessing.firm_data import PANEL_SUBDIR, VARIABLES_SUBDIR, load_variable, read_registry, resolve_root
from datastream.preprocessing.static_data import load_static, static_panel_path

logging.basicConfig(format="%(asctime)s : %(levelname)s : %(message)s", level=logging.INFO)
REGISTRY = Path(__file__).resolve().parents[1] / "config" / "firm_variables.csv"

SUMMARY_COLS = ["first_date", "last_date", "n_firms", "n_firms_in_universe", "share_universe_firms_ever_covered",
                "share_variable_firms_in_universe", "outside_universe_share", "first_year_vw_cov_50",
                "coverage_ref_year", "ew_coverage_ref", "vw_coverage_ref", "ew_coverage_last_year",
                "vw_coverage_last_year", "median_changes_per_year", "share_firm_years_no_change",
                "modal_lag_months", "share_at_modal_lag", "share_after_last_price", "share_dead_firms_padded",
                "share_stale_while_trading", "share_sign_violations", "share_extreme", "flags"]


def load_statics(price_path: Path, penny: str) -> pd.DataFrame | None:
    f = price_path / f"statics_filtered_{penny}.csv"
    st = pd.read_csv(f, dtype=str) if f.exists() else None
    allf = price_path / "statics.csv"
    if allf.exists():
        cols = pd.read_csv(allf, nrows=0).columns
        use = ["DSCD"] + [c for c in ("WC06105", "WC06035", "ENAME") if c in cols]
        a = pd.read_csv(allf, dtype=str, usecols=use)
        a["DSCD"] = a["DSCD"].str.strip()
        if st is None:
            st = a
        else:
            st["DSCD"] = st["DSCD"].str.strip()
            st = st.drop(columns=[c for c in use[1:] if c in st.columns]).merge(a.drop_duplicates("DSCD"),
                                                                                 on="DSCD", how="left")
    return st


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="US")
    ap.add_argument("--root", default=None)
    ap.add_argument("--price-path", default=None, help="Default: D:/Datastream/PriceData/<region>/processed")
    ap.add_argument("--penny", default="0.25")
    ap.add_argument("--vars", nargs="+", default=None,
                    help="Default: all imported time-series variables of category 'environmental'")
    ap.add_argument("--out", default=None, help="Default: <firm root>/Paneldata/checks/emissions")
    args = ap.parse_args()

    root = resolve_root(args.root, args.region)
    price_path = Path(args.price_path or f"D:/Datastream/PriceData/{args.region}/processed")
    out = Path(args.out) if args.out else root / PANEL_SUBDIR / "checks" / "emissions"
    out.mkdir(parents=True, exist_ok=True)

    reg = read_registry(REGISTRY)
    available = {p.stem for p in (root / VARIABLES_SUBDIR).glob("*.parquet")}
    if args.vars:
        variables = args.vars
    else:
        env = reg[(reg["category"] == "environmental") & (reg["type"] == "timeseries")]["variable"]
        variables = [v for v in env if v in available]
    missing = [v for v in variables if v not in available]
    if missing:
        raise SystemExit(f"Not imported: {missing}")
    names = dict(zip(reg["variable"], reg["name"].fillna("")))
    signs = dict(zip(reg["variable"], reg.get("sign", pd.Series("", index=reg.index)).fillna("")))

    uni_file = price_path / f"monthly_universe_{args.penny}.parquet"
    uni = pd.read_parquet(uni_file)
    uni["DSCD"] = uni["DSCD"].astype(str)
    logging.info(f"Universe {uni_file.name}: {len(uni):,} stock-months, {uni['DSCD'].nunique():,} stocks, "
                 f"{uni['Date'].min():%Y-%m} to {uni['Date'].max():%Y-%m}")
    statics = load_statics(price_path, args.penny)
    fye = None
    if static_panel_path(root, "WC05350").exists():
        fye = fe.fye_months(load_static(root, "WC05350"), "WC05350")

    logging.info(f"Loading {variables}")
    panels = {v: load_variable(root, v) for v in variables}
    ws_vars = [v for v in em.WS_REPORT_VARS if v in available]
    ws_events = em.ws_report_months({v: load_variable(root, v) for v in ws_vars})
    w = em.wide_emissions(panels)

    tables: dict[str, pd.DataFrame] = {}
    logging.info("Generic checks (firm_evaluation.check_variable)")
    summ = []
    for v in variables:
        r = fe.check_variable(panels[v], v, uni, fye, signs.get(v, ""))
        if r is not None:
            summ.append(r["summary"])
    s = fe.summary_table(summ)
    tables["01_summary_generic"] = s[[c for c in SUMMARY_COLS if c in s.columns]].T
    tables["02_raw_overview"] = em.raw_overview(panels)
    logging.info("Coverage")
    tables["03_coverage_by_year_december"] = em.coverage_by_year(w, uni)
    tables["04_firm_counts"] = em.firm_counts(w, uni)
    tables["05_coverage_by_size_reported_or_estimated"] = em.coverage_by_size(w, uni, "reported_or_estimated")
    tables["06_coverage_by_size_any_reported"] = em.coverage_by_size(w, uni, "any_reported")
    logging.info("Timing")
    tables["07_change_month_distribution"] = em.change_month_distribution(panels, uni)
    tables["08_lag_vs_worldscope_report_month"] = em.lag_vs_worldscope(panels, ws_events, uni)
    tables["09_lag_vs_fiscal_year_end"] = em.lag_vs_fye(panels, fye, uni)
    tables["10_new_values_per_year"] = em.latest_year_completeness(panels, uni)
    logging.info("Consistency")
    tables["11_scope_identity"] = em.scope_identity(w)
    tables["12_reported_vs_estimated"] = em.reported_vs_estimated(w)
    tables["13_change_alignment"] = em.change_alignment(panels)
    tables["14_share_classes"] = em.share_class_consistency(w, uni, em.firm_ids(statics))
    if "WC01001" in available:
        sales = load_variable(root, "WC01001")
        for v in [x for x in (em.TOTAL, em.SCOPE1, em.ESTIMATED) if x in panels]:
            tables[f"16_fiscal_alignment_{v}_vs_sales"] = em.fiscal_alignment(panels[v], v, sales)
    examples = em.example_firms(statics, uni)
    tables["15_example_events"] = em.example_events(w, ws_events, examples, fye)

    with open(out / "emissions_report.md", "w", encoding="utf-8") as f:
        f.write(f"# Emission variables - availability check ({args.region})\n\n")
        f.write(f"created {datetime.now():%Y-%m-%d %H:%M}; universe {uni_file.name} ({uni['DSCD'].nunique():,} "
                f"stocks, {uni['Date'].min():%Y-%m} to {uni['Date'].max():%Y-%m}); fiscal-year end "
                f"{'available' if fye is not None else 'not imported'}; Worldscope report items {ws_vars}\n\n")
        f.write("Variables: " + ", ".join(f"{v} ({names.get(v, '')})" for v in variables) + "\n\n")
        for name, t in tables.items():
            if t is None or len(t) == 0:
                f.write(f"## {name}\n\n(empty / not available)\n\n")
                continue
            t.to_csv(out / f"{name}.csv")
            with pd.option_context("display.width", 250, "display.max_columns", 80, "display.max_rows", 500,
                                   "display.float_format", lambda x: f"{x:,.4g}"):
                f.write(f"## {name}\n\n```\n{t.to_string()}\n```\n\n")
    logging.info(f"Report: {out / 'emissions_report.md'}")


if __name__ == "__main__":
    main()
