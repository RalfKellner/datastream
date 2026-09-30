"""Import, inventory and merge of monthly firm variables from Datastream Excel downloads.

The firm-variable pipeline has two separate steps:

1. **Import (one variable at a time).** All Excel files of one variable (one file per Datastream list)
   are read, cleaned and stacked into one long panel ``Date | DSCD | <VAR>`` that is stored as
   ``Paneldata/variables/<VAR>.parquet``. Each import also writes a per-file report and updates
   ``Paneldata/variables/_import_log.csv`` with the counts needed for later availability checks.
   Only variables that are new or whose raw files changed since the last import need to be re-run.

2. **Merge (optional).** Any selection of imported variables is combined into one multi-variable panel
   ``Paneldata/merged/<name>.parquet``. A JSON sidecar ``<name>.json`` records which variables and which
   version of each variable panel went into it.

``build_inventory`` combines the raw folders, the import log, the merged-dataset sidecars and the variable
registry (``config/firm_variables.csv``) into one overview table.

Folder layout below ``root`` (e.g. ``D:/Datastream/Firmcharacteristics_Monthly/US``)::

    <VAR>/<VAR>_<nn>.xlsx                        raw downloads, one file per Datastream list
    Paneldata/variables/<VAR>.parquet            long panel per variable
    Paneldata/variables/_import_log.csv          latest import summary per variable
    Paneldata/variables/_reports/<VAR>_files.csv per-file import report
    Paneldata/merged/<name>.parquet, <name>.json optional multi-variable panels
    Paneldata/variable_inventory.csv             overview raw -> variable panel -> merged datasets

Expected Excel layout (as produced by the Datastream Excel add-in for the firm-variable requests):
header row with series names, first data row with the series codes (``DPL#(<DSCD>(<VAR>))`` or
``<DSCD>(<VAR>)``), one further metadata row, then one row per date with the date in the first column.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PANEL_SUBDIR = Path("Paneldata")
VARIABLES_SUBDIR = PANEL_SUBDIR / "variables"
MERGED_SUBDIR = PANEL_SUBDIR / "merged"
IMPORT_LOG_NAME = "_import_log.csv"
REPORTS_SUBDIR_NAME = "_reports"
INVENTORY_NAME = "variable_inventory.csv"

# Number of leading rows below the header that hold metadata (codes, currency/label row), not data.
N_META_ROWS = 2

_DSCD_PATTERN = re.compile(r"^\s*(?:DPL#\()?([^(\s]+)\(")

# File statuses. Only "ok" and "no_data" are acceptable for a complete import.
STATUS_OK = "ok"                  # file read, at least one non-missing value
STATUS_NO_DATA = "no_data"        # file read correctly, but Datastream delivered no values for this list
STATUS_EMPTY = "empty_template"   # workbook without data (template created but never filled/saved)
STATUS_FAILED = "failed"          # reading raised an error
BLOCKING_STATUSES = (STATUS_EMPTY, STATUS_FAILED)


# ---------------------------------------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------------------------------------

def resolve_root(root: str | os.PathLike | None = None, region: str = "US") -> Path:
    """Root folder of the monthly firm variables for one region.

    Priority: explicit ``root`` > environment variable ``DS_FIRM_ROOT`` (a folder that contains the
    region folders) > Windows default ``D:/Datastream/Firmcharacteristics_Monthly``.
    """
    if root is not None:
        return Path(root)
    base = os.environ.get("DS_FIRM_ROOT", "D:/Datastream/Firmcharacteristics_Monthly")
    return Path(base) / region


def variable_panel_path(root: Path, variable: str) -> Path:
    return Path(root) / VARIABLES_SUBDIR / f"{variable}.parquet"


def raw_files(root: Path, variable: str) -> list[Path]:
    """Sorted raw Excel files of one variable (``<VAR>_*.xlsx``); Excel lock files are ignored."""
    folder = Path(root) / variable
    if not folder.is_dir():
        return []
    return sorted(
        p for p in folder.glob(f"{variable}_*.xlsx")
        if not p.name.startswith("~$")
    )


def discover_raw_variables(root: Path) -> list[str]:
    """All variable folders below ``root`` that contain at least one ``<VAR>_*.xlsx`` file."""
    root = Path(root)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and p.name != PANEL_SUBDIR.name and raw_files(root, p.name):
            out.append(p.name)
    return out


def _newest_mtime(paths: list[Path]) -> float | None:
    return max((p.stat().st_mtime for p in paths), default=None)


def needs_import(root: Path, variable: str) -> bool:
    """True if the variable panel is missing or older than the newest raw file."""
    files = raw_files(root, variable)
    if not files:
        return False
    panel = variable_panel_path(root, variable)
    if not panel.exists():
        return True
    return _newest_mtime(files) > panel.stat().st_mtime


# ---------------------------------------------------------------------------------------------------------
# Reading one Excel file
# ---------------------------------------------------------------------------------------------------------

def parse_dscd(code) -> str | None:
    """Extract the Datastream code from ``DPL#(<DSCD>(<VAR>))`` or ``<DSCD>(<VAR>)``."""
    if not isinstance(code, str):
        return None
    m = _DSCD_PATTERN.match(code)
    return m.group(1) if m else None


@dataclass
class FileReport:
    file: str
    status: str = STATUS_OK
    n_series: int = 0             # columns requested in this list (incl. '#ERROR' columns)
    n_error_series: int = 0       # '#ERROR' columns: Datastream could not deliver the series
    n_unparsed_ids: int = 0       # columns whose code could not be parsed into a DSCD (dropped)
    n_duplicate_ids: int = 0      # the same DSCD appears in more than one column of this file
    n_firms_with_data: int = 0
    n_obs: int = 0                # non-missing firm-month values
    n_dates: int = 0
    n_bad_dates: int = 0          # data rows whose date could not be parsed (dropped)
    n_coerced: int = 0            # non-numeric cells (e.g. '$$ER' strings) set to NaN
    coerced_examples: str = ""
    error: str = ""


def read_firm_file(path: str | os.PathLike, variable: str) -> tuple[pd.DataFrame, FileReport]:
    """Read one Datastream firm-variable file into a long panel ``Date | DSCD | <variable>``.

    Missing values are dropped. Everything that is dropped or coerced is counted in the report.
    """
    path = Path(path)
    rep = FileReport(file=path.name)
    empty = pd.DataFrame({"Date": pd.Series(dtype="datetime64[ns]"),
                          "DSCD": pd.Series(dtype=object),
                          variable: pd.Series(dtype=float)})

    raw = pd.read_excel(path, sheet_name=0, header=0, engine="openpyxl")
    if raw.shape[1] <= 1 or raw.shape[0] <= N_META_ROWS:
        rep.status = STATUS_EMPTY
        return empty, rep

    header = raw.columns.astype(str)
    is_error = header.str.startswith("#ERROR")
    rep.n_series = int(raw.shape[1] - 1)
    rep.n_error_series = int(is_error[1:].sum())
    raw = raw.loc[:, ~is_error]

    # DSCD codes: first metadata row; fall back to the header (layout of the price downloads).
    value_cols = list(raw.columns[1:])
    ids = [parse_dscd(raw.iloc[0][c]) or parse_dscd(str(c)) for c in value_cols]
    keep = [c for c, i in zip(value_cols, ids) if i is not None]
    ids = [i for i in ids if i is not None]
    rep.n_unparsed_ids = len(value_cols) - len(keep)
    if rep.n_unparsed_ids:
        logger.warning(f"{path.name}: {rep.n_unparsed_ids} column(s) without a parsable DSCD dropped.")

    data = raw.iloc[N_META_ROWS:][[raw.columns[0]] + keep].copy()
    data.columns = ["Date"] + ids

    dates = pd.to_datetime(data["Date"], errors="coerce")
    rep.n_bad_dates = int(dates.isna().sum() - data["Date"].isna().sum())
    data["Date"] = dates
    data = data[data["Date"].notna()]
    rep.n_dates = int(data["Date"].nunique())

    values = data.drop(columns="Date")
    numeric = values.apply(pd.to_numeric, errors="coerce")
    coerced_mask = values.notna() & numeric.isna()
    rep.n_coerced = int(coerced_mask.to_numpy().sum())
    if rep.n_coerced:
        examples = pd.unique(values.to_numpy()[coerced_mask.to_numpy()].astype(str))[:5]
        rep.coerced_examples = " | ".join(examples)
        logger.warning(f"{path.name}: {rep.n_coerced} non-numeric cell(s) set to NaN, e.g. {rep.coerced_examples}")

    numeric.insert(0, "Date", data["Date"].to_numpy())
    long = numeric.melt(id_vars="Date", var_name="DSCD", value_name=variable).dropna(subset=[variable])
    long["DSCD"] = long["DSCD"].astype(str).str.strip()

    rep.n_duplicate_ids = int(pd.Series(ids).duplicated().sum())
    rep.n_firms_with_data = int(long["DSCD"].nunique())
    rep.n_obs = int(len(long))
    if rep.n_obs == 0:
        rep.status = STATUS_NO_DATA
    return long, rep


# ---------------------------------------------------------------------------------------------------------
# Step 1: import one variable
# ---------------------------------------------------------------------------------------------------------

@dataclass
class ImportSummary:
    variable: str
    status: str                   # complete | incomplete | no_raw_files
    imported_at: str = ""
    panel_written: bool = False
    n_files: int = 0
    n_files_ok: int = 0
    n_files_no_data: int = 0
    n_files_empty: int = 0
    n_files_failed: int = 0
    n_series: int = 0             # firms requested over all lists
    n_error_series: int = 0
    n_unparsed_ids: int = 0
    n_firms_with_data: int = 0
    n_obs: int = 0
    first_date: str = ""
    last_date: str = ""
    date_convention: str = ""     # day of month of the dates, e.g. 'month_end', 'day_01', 'mixed'
    n_duplicate_obs: int = 0      # (DSCD, Date) pairs present in more than one file
    n_conflicting_duplicates: int = 0  # ... of which with different values
    n_coerced: int = 0
    newest_raw_file: str = ""
    problem_files: str = ""
    report_file: str = ""
    extra: dict = field(default_factory=dict, repr=False)


def describe_date_convention(dates: pd.Series) -> str:
    """Summarise on which day of the month the observations are stamped."""
    d = pd.to_datetime(pd.Series(dates).drop_duplicates())
    if d.empty:
        return ""
    if d.dt.is_month_end.all():
        return "month_end"
    days = d.dt.day.unique()
    if len(days) == 1:
        return f"day_{int(days[0]):02d}"
    return "mixed"


def import_variable(
    root: str | os.PathLike,
    variable: str,
    allow_incomplete: bool = False,
) -> ImportSummary:
    """Import all raw files of one variable into ``Paneldata/variables/<variable>.parquet``.

    If a file failed to load or is an unfilled template, one list (~1,000 firms) would be missing.
    In that case the panel is **not** written (an older panel stays in place and the inventory
    marks it as stale) unless ``allow_incomplete=True``.
    """
    root = Path(root)
    files = raw_files(root, variable)
    summary = ImportSummary(variable=variable, status="no_raw_files",
                            imported_at=datetime.now().isoformat(timespec="seconds"))
    if not files:
        logger.warning(f"{variable}: no raw files found in {root / variable}")
        return summary

    panels, reports = [], []
    for f in files:
        try:
            long, rep = read_firm_file(f, variable)
        except Exception as e:  # keep going, but record the failure
            long, rep = None, FileReport(file=f.name, status=STATUS_FAILED, error=f"{type(e).__name__}: {e}")
            logger.error(f"{variable}: {f.name} failed: {rep.error}")
        reports.append(rep)
        if long is not None and len(long):
            long["_file"] = f.name
            panels.append(long)

    rep_df = pd.DataFrame([asdict(r) for r in reports])
    report_dir = root / VARIABLES_SUBDIR / REPORTS_SUBDIR_NAME
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{variable}_files.csv"
    rep_df.to_csv(report_path, index=False)

    summary.report_file = str(report_path.relative_to(root))
    summary.n_files = len(files)
    counts = rep_df["status"].value_counts()
    summary.n_files_ok = int(counts.get(STATUS_OK, 0))
    summary.n_files_no_data = int(counts.get(STATUS_NO_DATA, 0))
    summary.n_files_empty = int(counts.get(STATUS_EMPTY, 0))
    summary.n_files_failed = int(counts.get(STATUS_FAILED, 0))
    summary.n_series = int(rep_df["n_series"].sum())
    summary.n_error_series = int(rep_df["n_error_series"].sum())
    summary.n_unparsed_ids = int(rep_df["n_unparsed_ids"].sum())
    summary.n_coerced = int(rep_df["n_coerced"].sum())
    newest = max(files, key=lambda p: p.stat().st_mtime)
    summary.newest_raw_file = f"{newest.name} ({datetime.fromtimestamp(newest.stat().st_mtime):%Y-%m-%d %H:%M})"
    problems = rep_df[rep_df["status"].isin(BLOCKING_STATUSES)]
    summary.problem_files = "; ".join(f"{r.file} [{r.status}]" for r in problems.itertuples())
    summary.status = "incomplete" if len(problems) else "complete"

    if panels:
        panel = pd.concat(panels, ignore_index=True)
    else:
        panel = pd.DataFrame({"Date": pd.Series(dtype="datetime64[ns]"), "DSCD": pd.Series(dtype=object),
                              variable: pd.Series(dtype=float), "_file": pd.Series(dtype=object)})

    # A DSCD should only be in one list. If it appears in several files, keep the first and report.
    dup = panel.duplicated(subset=["DSCD", "Date"], keep=False)
    if dup.any():
        d = panel[dup]
        summary.n_duplicate_obs = int(panel.duplicated(subset=["DSCD", "Date"]).sum())
        summary.n_conflicting_duplicates = int((d.groupby(["DSCD", "Date"])[variable].nunique() > 1).sum())
        d.sort_values(["DSCD", "Date", "_file"]).to_csv(report_dir / f"{variable}_duplicates.csv", index=False)
        logger.warning(f"{variable}: {summary.n_duplicate_obs} duplicate (DSCD, Date) observations across files "
                       f"({summary.n_conflicting_duplicates} with different values); first file kept.")
        panel = panel.drop_duplicates(subset=["DSCD", "Date"], keep="first")

    panel = (panel.drop(columns="_file")
                  .sort_values(["DSCD", "Date"])
                  .reset_index(drop=True))
    panel["Date"] = pd.to_datetime(panel["Date"]).astype("datetime64[ns]")
    panel[variable] = panel[variable].astype("float64")

    summary.n_firms_with_data = int(panel["DSCD"].nunique())
    summary.n_obs = int(len(panel))
    if len(panel):
        summary.first_date = f"{panel['Date'].min():%Y-%m-%d}"
        summary.last_date = f"{panel['Date'].max():%Y-%m-%d}"
        summary.date_convention = describe_date_convention(panel["Date"])

    if summary.status == "incomplete" and not allow_incomplete:
        logger.error(f"{variable}: NOT written, problem files: {summary.problem_files}. "
                     f"Fix the downloads or rerun with allow_incomplete=True.")
    else:
        out = variable_panel_path(root, variable)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".parquet.tmp")
        panel.to_parquet(tmp, index=False)
        os.replace(tmp, out)  # atomic: a crash never leaves a half-written panel
        summary.panel_written = True
        logger.info(f"{variable}: {summary.n_obs:,} obs, {summary.n_firms_with_data:,} firms with data out of "
                    f"{summary.n_series:,} requested, {summary.first_date} to {summary.last_date} -> {out.name}")

    update_import_log(root, summary)
    return summary


def update_import_log(root: Path, summary: ImportSummary) -> pd.DataFrame:
    """Replace the row of ``summary.variable`` in the import log (one row per variable)."""
    log_path = Path(root) / VARIABLES_SUBDIR / IMPORT_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {k: v for k, v in asdict(summary).items() if k != "extra"}
    if log_path.exists():
        log = pd.read_csv(log_path, dtype=str)
        log = log[log["variable"] != summary.variable]
        log = pd.concat([log, pd.DataFrame([row]).astype(str)], ignore_index=True)
    else:
        log = pd.DataFrame([row]).astype(str)
    log = log.sort_values("variable").reset_index(drop=True)
    log.to_csv(log_path, index=False)
    return log


def read_import_log(root: Path) -> pd.DataFrame:
    log_path = Path(root) / VARIABLES_SUBDIR / IMPORT_LOG_NAME
    if not log_path.exists():
        return pd.DataFrame(columns=["variable"])
    return pd.read_csv(log_path)


# ---------------------------------------------------------------------------------------------------------
# Step 2 (optional): merge several variables
# ---------------------------------------------------------------------------------------------------------

def load_variable(root: str | os.PathLike, variable: str, align_month_end: bool = False) -> pd.DataFrame:
    """Load one variable panel. With ``align_month_end`` the dates are moved to the calendar month end."""
    path = variable_panel_path(Path(root), variable)
    if not path.exists():
        raise FileNotFoundError(f"{variable}: no variable panel at {path}. Import it first.")
    df = pd.read_parquet(path)
    return _align_month_end(df, variable) if align_month_end else df


def _align_month_end(df: pd.DataFrame, variable: str) -> pd.DataFrame:
    df = df.copy()
    df["Date"] = df["Date"].dt.to_period("M").dt.to_timestamp(how="end").dt.normalize()
    dup = df.duplicated(subset=["DSCD", "Date"])
    if dup.any():
        raise ValueError(f"{variable}: {int(dup.sum())} firm-month(s) have more than one observation after "
                         f"aligning to month end. The raw data are not monthly.")
    return df


def merge_variables(
    root: str | os.PathLike,
    variables: list[str],
    align_month_end: bool = True,
) -> tuple[pd.DataFrame, dict]:
    """Outer-join several variable panels on (DSCD, Date).

    ``align_month_end=True`` (default) stamps all observations at the calendar month end, which is the
    date convention of the monthly returns built from the price panel (``resample('ME')``) and makes
    variables downloaded with different date settings line up. Without alignment, variables with
    different date conventions would end up in separate rows; this raises an error.
    """
    root = Path(root)
    variables = list(dict.fromkeys(variables))  # unique, order kept
    frames, meta_vars = [], {}
    conventions = {}
    for v in variables:
        df = load_variable(root, v, align_month_end=False)
        conventions[v] = describe_date_convention(df["Date"])
        if align_month_end:
            df = _align_month_end(df, v)
        path = variable_panel_path(root, v)
        meta_vars[v] = {
            "panel_modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "raw_date_convention": conventions[v],
            "n_obs": int(len(df)),
            "n_firms": int(df["DSCD"].nunique()),
        }
        frames.append(df.set_index(["DSCD", "Date"])[v])

    if not align_month_end and len(set(conventions.values())) > 1:
        raise ValueError(f"Variables use different date conventions {conventions}; "
                         f"merge with align_month_end=True.")

    merged = pd.concat(frames, axis=1, join="outer").sort_index().reset_index()
    meta = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "root": str(root),
        "variables": variables,
        "align_month_end": align_month_end,
        "n_rows": int(len(merged)),
        "n_firms": int(merged["DSCD"].nunique()),
        "first_date": f"{merged['Date'].min():%Y-%m-%d}" if len(merged) else "",
        "last_date": f"{merged['Date'].max():%Y-%m-%d}" if len(merged) else "",
        "variable_panels": meta_vars,
    }
    return merged, meta


def write_merged(root: str | os.PathLike, name: str, merged: pd.DataFrame, meta: dict) -> Path:
    """Write ``Paneldata/merged/<name>.parquet`` and its sidecar ``<name>.json``."""
    out_dir = Path(root) / MERGED_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.parquet"
    tmp = path.with_suffix(".parquet.tmp")
    merged.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    meta = dict(meta, name=name, file=path.name)
    (out_dir / f"{name}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return path


def read_merged_manifests(root: Path) -> list[dict]:
    out_dir = Path(root) / MERGED_SUBDIR
    if not out_dir.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out_dir.glob("*.json"))]


# ---------------------------------------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------------------------------------

def read_registry(path: str | os.PathLike | None) -> pd.DataFrame:
    """Variable registry: one row per mnemonic with description, category and source."""
    if path is None or not Path(path).exists():
        return pd.DataFrame(columns=["variable"])
    reg = pd.read_csv(path, dtype=str).fillna("")
    return reg.drop_duplicates(subset="variable")


def build_inventory(root: str | os.PathLike, registry_path: str | os.PathLike | None = None) -> pd.DataFrame:
    """One row per variable with its state in each pipeline stage.

    Stages: registered (registry) -> raw (Excel files) -> variable panel (import) -> merged datasets.
    ``panel_state`` is one of: missing (never imported), current, stale (raw files changed after the last
    import), incomplete (last import had failed or unfilled files, an older panel is still in place),
    blocked (no panel yet because the import had failed or unfilled files).
    """
    root = Path(root)
    registry = read_registry(registry_path)
    log = read_import_log(root)
    manifests = read_merged_manifests(root)

    panel_dir = root / VARIABLES_SUBDIR
    panel_vars = sorted(p.stem for p in panel_dir.glob("*.parquet")) if panel_dir.is_dir() else []
    variables = sorted(set(registry["variable"]) | set(discover_raw_variables(root)) | set(panel_vars)
                       | set(log["variable"].astype(str)))

    rows = []
    for v in variables:
        files = raw_files(root, v)
        panel = variable_panel_path(root, v)
        newest = _newest_mtime(files)
        row = {
            "variable": v,
            "registered": v in set(registry["variable"]),
            "n_raw_files": len(files),
            "raw_modified": f"{datetime.fromtimestamp(newest):%Y-%m-%d %H:%M}" if newest else "",
            "panel_modified": (f"{datetime.fromtimestamp(panel.stat().st_mtime):%Y-%m-%d %H:%M}"
                               if panel.exists() else ""),
        }
        if not panel.exists():
            row["panel_state"] = "missing"
        elif newest is not None and newest > panel.stat().st_mtime:
            row["panel_state"] = "stale"
        else:
            row["panel_state"] = "current"
        lr = log[log["variable"].astype(str) == v]
        if len(lr):
            lr = lr.iloc[-1]
            if str(lr.get("status")) == "incomplete":
                row["panel_state"] = "blocked" if row["panel_state"] == "missing" else "incomplete"
            for col in ["status", "n_files_ok", "n_files_no_data", "n_files_empty", "n_files_failed",
                        "n_series", "n_error_series", "n_firms_with_data", "n_obs", "first_date",
                        "last_date", "date_convention", "n_conflicting_duplicates", "imported_at"]:
                row[col if col != "status" else "last_import_status"] = lr.get(col, "")
        row["in_merged"] = ", ".join(m["name"] for m in manifests if v in m.get("variables", []))
        rows.append(row)

    inv = pd.DataFrame(rows)
    if inv.empty:
        return inv
    if len(registry.columns) > 1:
        inv = registry.merge(inv, on="variable", how="right")
    for c in ["n_files_ok", "n_files_no_data", "n_files_empty", "n_files_failed", "n_series", "n_error_series",
              "n_firms_with_data", "n_obs", "n_conflicting_duplicates"]:
        if c in inv.columns:
            inv[c] = pd.to_numeric(inv[c], errors="coerce").astype("Int64")
    if {"n_firms_with_data", "n_series"} <= set(inv.columns):
        inv["share_firms_with_data"] = (pd.to_numeric(inv["n_firms_with_data"], errors="coerce")
                                        / pd.to_numeric(inv["n_series"], errors="coerce").replace(0, np.nan)).round(3)
    return inv


def write_inventory(root: str | os.PathLike, registry_path: str | os.PathLike | None = None) -> pd.DataFrame:
    inv = build_inventory(root, registry_path)
    out = Path(root) / PANEL_SUBDIR / INVENTORY_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    inv.to_csv(out, index=False)
    logger.info(f"Inventory of {len(inv)} variables written to {out}")
    return inv
