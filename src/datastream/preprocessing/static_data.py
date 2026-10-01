"""Import of static firm variables (one value per firm, e.g. ENERDP124, WC05350) with snapshot history.

Datastream static requests only return the **current** value. To build a history over time, every download
is kept as a dated snapshot and all snapshots are stacked into one table ``DSCD | <VAR> | as_of``.

Raw layout below ``<root>/Static/<VAR>/``::

    <VAR>_01.xlsx ... <VAR>_39.xlsx                 current download; as_of = date of the newest file
    2026-09-30/<VAR>_01.xlsx ...                    archived download; as_of = folder name (YYYY-MM-DD)

To keep a download before replacing it with a new one, move its files into a folder named after the
download date. The variable table is rebuilt from **all** raw snapshots on every import, so the history
can always be reproduced from the raw files.

Outputs below ``<root>/Paneldata/static/``::

    <VAR>.parquet                 DSCD | <VAR> | as_of   (one row per firm and snapshot)
    _import_log.csv               latest import summary per variable
    _reports/<VAR>_files.csv      one row per raw file
    _reports/<VAR>_values.csv     value distribution per snapshot (categories, or fiscal-year-end months)

Expected Excel layout (Datastream static request): header ``Type | <VAR> | CURRENCY``, then one row per firm
with the DSCD in the first column and the value in the second. ``#NA`` means not available.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from datastream.preprocessing.firm_data import (
    BLOCKING_STATUSES,
    IMPORT_LOG_NAME,
    PANEL_SUBDIR,
    REPORTS_SUBDIR_NAME,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_NO_DATA,
    STATUS_OK,
)

logger = logging.getLogger(__name__)

STATIC_RAW_SUBDIR = Path("Static")
STATIC_PANEL_SUBDIR = PANEL_SUBDIR / "static"

MISSING_TOKENS = {"", "#NA", "NA", "N/A", "#N/A", "NAN", "NONE"}
_ERROR_PATTERN = re.compile(r"^\s*(\$\$ER|#ERROR)", re.IGNORECASE)
_DATE_FOLDER = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DSCD_LENGTH = 6


# ---------------------------------------------------------------------------------------------------------
# Paths and discovery
# ---------------------------------------------------------------------------------------------------------

def static_panel_path(root: Path, variable: str) -> Path:
    return Path(root) / STATIC_PANEL_SUBDIR / f"{variable}.parquet"


def static_snapshots(root: Path, variable: str) -> dict[str, list[Path]]:
    """Raw snapshots of one static variable: ``{as_of 'YYYY-MM-DD': [files]}``.

    Files directly in ``Static/<VAR>/`` form one snapshot dated by the newest file; each subfolder named
    ``YYYY-MM-DD`` is one archived snapshot. If both have the same date, they are treated as one snapshot.
    """
    folder = Path(root) / STATIC_RAW_SUBDIR / variable
    if not folder.is_dir():
        return {}
    pattern = f"{variable}_*.xlsx"
    out: dict[str, list[Path]] = {}
    for sub in sorted(p for p in folder.iterdir() if p.is_dir() and _DATE_FOLDER.match(p.name)):
        files = sorted(f for f in sub.glob(pattern) if not f.name.startswith("~$"))
        if files:
            out.setdefault(sub.name, []).extend(files)
    current = sorted(f for f in folder.glob(pattern) if not f.name.startswith("~$"))
    if current:
        as_of = f"{datetime.fromtimestamp(max(f.stat().st_mtime for f in current)):%Y-%m-%d}"
        out.setdefault(as_of, []).extend(current)
    return dict(sorted(out.items()))


def static_raw_files(root: Path, variable: str) -> list[Path]:
    return [f for files in static_snapshots(root, variable).values() for f in files]


def discover_static_variables(root: Path) -> list[str]:
    folder = Path(root) / STATIC_RAW_SUBDIR
    if not folder.is_dir():
        return []
    return [p.name for p in sorted(folder.iterdir()) if p.is_dir() and static_raw_files(root, p.name)]


def static_needs_import(root: Path, variable: str) -> bool:
    files = static_raw_files(root, variable)
    if not files:
        return False
    panel = static_panel_path(root, variable)
    return (not panel.exists()) or max(f.stat().st_mtime for f in files) > panel.stat().st_mtime


# ---------------------------------------------------------------------------------------------------------
# Reading one file
# ---------------------------------------------------------------------------------------------------------

@dataclass
class StaticFileReport:
    file: str
    as_of: str
    status: str = STATUS_OK
    n_series: int = 0             # firms requested (rows)
    n_firms_with_data: int = 0
    n_missing: int = 0            # '#NA' and empty cells
    n_errors: int = 0             # '$$ER' / '#ERROR' cells
    error_examples: str = ""
    n_coerced: int = 0            # values that did not fit the detected type (set to missing)
    n_padded_ids: int = 0         # numeric DSCDs zero-padded to 6 characters
    n_blank_ids: int = 0
    n_duplicate_ids: int = 0
    value_type: str = ""          # string | datetime | numeric
    error: str = ""


def _clean_dscd(x) -> tuple[str | None, bool]:
    """DSCD as string. Excel stores numeric codes (e.g. 902242) as numbers, which can drop leading zeros."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None, False
    if isinstance(x, (int, np.integer)) or (isinstance(x, float) and float(x).is_integer()):
        s = str(int(x))
        return s.zfill(DSCD_LENGTH), len(s) < DSCD_LENGTH
    s = str(x).strip()
    return (s or None), False


def _infer_and_convert(values: pd.Series) -> tuple[pd.Series, str, int]:
    """Detect the value type (datetime, numeric or string) and convert. Returns (values, type, n_coerced)."""
    nonnull = values.dropna()
    if nonnull.empty:
        return values.astype(object), "", 0
    is_dt = nonnull.map(lambda v: isinstance(v, (datetime, date, pd.Timestamp))).mean()
    is_num = nonnull.map(lambda v: isinstance(v, (int, float, np.integer, np.floating))
                         and not isinstance(v, bool)).mean()
    if is_dt >= 0.5:
        out = pd.to_datetime(values, errors="coerce")
        return out, "datetime", int(out.isna().sum() - values.isna().sum())
    if is_num >= 0.5:
        out = pd.to_numeric(values, errors="coerce")
        return out.astype("float64"), "numeric", int(out.isna().sum() - values.isna().sum())
    return values.map(lambda v: v if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip()), \
        "string", 0


def read_static_file(path: str | os.PathLike, variable: str, as_of: str = "") -> tuple[pd.DataFrame, StaticFileReport]:
    """Read one static Datastream file into ``DSCD | <variable>`` (missing values dropped)."""
    path = Path(path)
    rep = StaticFileReport(file=str(path.name if not _DATE_FOLDER.match(path.parent.name)
                                    else f"{path.parent.name}/{path.name}"), as_of=as_of)
    empty = pd.DataFrame({"DSCD": pd.Series(dtype=object), variable: pd.Series(dtype=object)})

    raw = pd.read_excel(path, sheet_name=0, header=0, dtype=object, na_filter=False, engine="openpyxl")
    if raw.shape[1] < 2 or raw.shape[0] == 0:
        rep.status = STATUS_EMPTY
        return empty, rep

    cols = [str(c) for c in raw.columns]
    if variable in cols:
        value_col = raw.columns[cols.index(variable)]
    else:
        value_col = raw.columns[1]
        logger.warning(f"{path.name}: no column named {variable}, using '{value_col}'.")
    if str(value_col).startswith("#ERROR"):
        rep.status, rep.error = STATUS_NO_DATA, f"value column is '{value_col}'"
        rep.n_series = int(len(raw))
        return empty, rep

    ids = raw.iloc[:, 0].map(_clean_dscd)
    dscd = ids.map(lambda t: t[0])
    rep.n_padded_ids = int(ids.map(lambda t: t[1]).sum())
    rep.n_blank_ids = int(dscd.isna().sum())

    vals = raw[value_col]
    as_text = vals.map(lambda v: str(v).strip().upper() if isinstance(v, str) else None)
    is_missing = as_text.isin(MISSING_TOKENS) | vals.map(lambda v: v is None or (isinstance(v, float) and np.isnan(v)))
    is_error = as_text.map(lambda s: bool(s) and bool(_ERROR_PATTERN.match(s)))
    rep.n_missing = int(is_missing.sum())
    rep.n_errors = int(is_error.sum())
    if rep.n_errors:
        rep.error_examples = " | ".join(pd.unique(vals[is_error].astype(str))[:5])

    vals = vals.where(~(is_missing | is_error), None)
    vals, rep.value_type, rep.n_coerced = _infer_and_convert(vals)

    df = pd.DataFrame({"DSCD": dscd, variable: vals})
    rep.n_series = int(df["DSCD"].notna().sum())
    df = df[df["DSCD"].notna() & df[variable].notna()]
    rep.n_duplicate_ids = int(df["DSCD"].duplicated().sum())
    rep.n_firms_with_data = int(df["DSCD"].nunique())
    if rep.n_firms_with_data == 0:
        rep.status = STATUS_NO_DATA
    return df.reset_index(drop=True), rep


# ---------------------------------------------------------------------------------------------------------
# Import one variable (all snapshots)
# ---------------------------------------------------------------------------------------------------------

@dataclass
class StaticImportSummary:
    variable: str
    status: str                   # complete | incomplete | no_raw_files
    imported_at: str = ""
    panel_written: bool = False
    value_type: str = ""
    n_snapshots: int = 0
    snapshots: str = ""
    latest_as_of: str = ""
    n_files: int = 0
    n_files_ok: int = 0
    n_files_no_data: int = 0
    n_files_empty: int = 0
    n_files_failed: int = 0
    n_series: int = 0             # firms requested in the latest snapshot
    n_firms_with_data: int = 0    # firms with a value in the latest snapshot
    n_distinct_values: int = 0    # latest snapshot
    n_changed_vs_previous: int | str = ""   # firms whose value differs from the previous snapshot
    n_duplicate_ids: int = 0
    problem_files: str = ""
    report_file: str = ""


def _value_report(panel: pd.DataFrame, variable: str, value_type: str) -> pd.DataFrame:
    """Distribution per snapshot: categories for strings, fiscal-year-end month for dates."""
    if panel.empty or value_type not in ("string", "datetime"):
        return pd.DataFrame()
    key = panel[variable] if value_type == "string" else panel[variable].dt.month.rename("month")
    name = "value" if value_type == "string" else "month"
    return (panel.assign(**{name: key.to_numpy()}).groupby(["as_of", name]).size()
            .rename("n_firms").reset_index().sort_values(["as_of", "n_firms"], ascending=[True, False]))


def import_static_variable(root: str | os.PathLike, variable: str, allow_incomplete: bool = False) -> StaticImportSummary:
    root = Path(root)
    snaps = static_snapshots(root, variable)
    summary = StaticImportSummary(variable=variable, status="no_raw_files",
                                  imported_at=datetime.now().isoformat(timespec="seconds"))
    out_dir = root / STATIC_PANEL_SUBDIR
    if not snaps:
        logger.warning(f"{variable}: no static raw files in {root / STATIC_RAW_SUBDIR / variable}")
        update_static_import_log(root, summary)
        return summary

    frames, reports = [], []
    for as_of, files in snaps.items():
        for f in files:
            try:
                df, rep = read_static_file(f, variable, as_of=as_of)
            except Exception as e:
                df, rep = None, StaticFileReport(file=f.name, as_of=as_of, status=STATUS_FAILED,
                                                 error=f"{type(e).__name__}: {e}")
                logger.error(f"{variable}: {f.name} failed: {rep.error}")
            reports.append(rep)
            if df is not None and len(df):
                frames.append(df.assign(as_of=as_of, _file=rep.file))

    rep_df = pd.DataFrame([asdict(r) for r in reports])
    report_dir = out_dir / REPORTS_SUBDIR_NAME
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{variable}_files.csv"
    rep_df.to_csv(report_path, index=False)
    summary.report_file = str(report_path.relative_to(root))

    types = sorted(t for t in rep_df["value_type"].unique() if t)
    summary.value_type = types[0] if len(types) == 1 else ("mixed: " + ",".join(types) if types else "")
    counts = rep_df["status"].value_counts()
    summary.n_files = len(rep_df)
    summary.n_files_ok = int(counts.get(STATUS_OK, 0))
    summary.n_files_no_data = int(counts.get(STATUS_NO_DATA, 0))
    summary.n_files_empty = int(counts.get(STATUS_EMPTY, 0))
    summary.n_files_failed = int(counts.get(STATUS_FAILED, 0))
    problems = rep_df[rep_df["status"].isin(BLOCKING_STATUSES)]
    summary.problem_files = "; ".join(f"{r.as_of}/{r.file} [{r.status}]" for r in problems.itertuples())
    summary.status = "incomplete" if len(problems) else "complete"
    summary.n_snapshots = len(snaps)
    summary.snapshots = ", ".join(snaps)
    summary.latest_as_of = list(snaps)[-1]
    summary.n_series = int(rep_df.loc[rep_df["as_of"] == summary.latest_as_of, "n_series"].sum())

    panel = pd.concat(frames, ignore_index=True) if frames else \
        pd.DataFrame({"DSCD": pd.Series(dtype=object), variable: pd.Series(dtype=object),
                      "as_of": pd.Series(dtype=object), "_file": pd.Series(dtype=object)})
    dup = panel.duplicated(subset=["DSCD", "as_of"])
    summary.n_duplicate_ids = int(dup.sum())
    if dup.any():
        panel[panel.duplicated(subset=["DSCD", "as_of"], keep=False)].to_csv(
            report_dir / f"{variable}_duplicates.csv", index=False)
        logger.warning(f"{variable}: {summary.n_duplicate_ids} duplicate DSCD(s) within a snapshot; first kept.")
        panel = panel[~dup]
    panel = panel.drop(columns="_file")
    panel["as_of"] = pd.to_datetime(panel["as_of"])
    if summary.value_type == "datetime":
        panel[variable] = pd.to_datetime(panel[variable]).astype("datetime64[ns]")
    elif summary.value_type == "numeric":
        panel[variable] = panel[variable].astype("float64")
    else:
        panel[variable] = panel[variable].astype(str).where(panel[variable].notna(), None)
    panel = panel.sort_values(["DSCD", "as_of"]).reset_index(drop=True)[["DSCD", variable, "as_of"]]

    latest = panel[panel["as_of"] == pd.Timestamp(summary.latest_as_of)]
    summary.n_firms_with_data = int(latest["DSCD"].nunique())
    summary.n_distinct_values = int(latest[variable].nunique())
    if len(snaps) > 1:
        prev_date = pd.Timestamp(list(snaps)[-2])
        prev = panel[panel["as_of"] == prev_date].set_index("DSCD")[variable]
        cur = latest.set_index("DSCD")[variable]
        both = prev.index.intersection(cur.index)
        summary.n_changed_vs_previous = int((prev.loc[both] != cur.loc[both]).sum())

    _value_report(panel, variable, summary.value_type).to_csv(report_dir / f"{variable}_values.csv", index=False)

    if summary.status == "incomplete" and not allow_incomplete:
        logger.error(f"{variable}: NOT written, problem files: {summary.problem_files}.")
    else:
        path = static_panel_path(root, variable)
        tmp = path.with_suffix(".parquet.tmp")
        panel.to_parquet(tmp, index=False)
        os.replace(tmp, path)
        summary.panel_written = True
        logger.info(f"{variable} (static, {summary.value_type}): {summary.n_snapshots} snapshot(s), latest "
                    f"{summary.latest_as_of}: {summary.n_firms_with_data:,} of {summary.n_series:,} firms with a "
                    f"value -> {path.name}")

    update_static_import_log(root, summary)
    return summary


def update_static_import_log(root: Path, summary: StaticImportSummary) -> pd.DataFrame:
    log_path = Path(root) / STATIC_PANEL_SUBDIR / IMPORT_LOG_NAME
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = pd.DataFrame([asdict(summary)]).astype(str)
    if log_path.exists():
        log = pd.read_csv(log_path, dtype=str)
        log = pd.concat([log[log["variable"] != summary.variable], row], ignore_index=True)
    else:
        log = row
    log = log.sort_values("variable").reset_index(drop=True)
    log.to_csv(log_path, index=False)
    return log


def read_static_import_log(root: Path) -> pd.DataFrame:
    path = Path(root) / STATIC_PANEL_SUBDIR / IMPORT_LOG_NAME
    return pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["variable"])


def load_static(root: str | os.PathLike, variable: str, as_of: str | None = "latest") -> pd.DataFrame:
    """Load a static variable. ``as_of='latest'``: latest snapshot per firm; a date: the latest snapshot on or
    before that date per firm; ``None``: all snapshots."""
    path = static_panel_path(Path(root), variable)
    if not path.exists():
        raise FileNotFoundError(f"{variable}: no static panel at {path}. Import it first.")
    df = pd.read_parquet(path)
    if as_of is None:
        return df
    if as_of != "latest":
        df = df[df["as_of"] <= pd.Timestamp(as_of)]
    return df.sort_values("as_of").groupby("DSCD", as_index=False).last()
