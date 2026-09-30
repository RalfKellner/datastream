"""Tests for the firm-variable import/merge/inventory on synthetic Datastream-style Excel files."""

import json

import numpy as np
import pandas as pd
import pytest
from openpyxl import Workbook

from datastream.preprocessing import firm_data as fd


def write_ds_file(path, codes, dates, values, header=None):
    """Mimic the add-in layout: header row, code row, one metadata row, then date rows."""
    wb = Workbook()
    ws = wb.active
    header = header or (["Name"] + [f"FIRM {i}" for i in range(len(codes))])
    ws.append(header)
    ws.append(["Code"] + codes)
    ws.append(["CURRENCY"] + ["U$"] * len(codes))
    for d, row in zip(dates, values):
        ws.append([d] + list(row))
    wb.save(path)


DATES = list(pd.date_range("2020-01-01", periods=4, freq="MS").to_pydatetime())


@pytest.fixture
def root(tmp_path):
    var = "WC02999"
    (tmp_path / var).mkdir()
    write_ds_file(tmp_path / var / f"{var}_01.xlsx",
                  ["AAA(WC02999)", "DPL#(BBB(WC02999))"], DATES,
                  [[1, None], [1, 5], [2, "$$ER: 0904,NO DATA AVAILABLE"], [2, 6]])
    write_ds_file(tmp_path / var / f"{var}_02.xlsx",
                  ["CCC(WC02999)", "DDD(WC02999)"], DATES,
                  [[10, None], [10, None], [11, None], [11, None]],
                  header=["Name", "FIRM C", "#ERROR"])
    Workbook().save(tmp_path / var / f"{var}_03.xlsx")  # unfilled template
    return tmp_path


def test_parse_dscd():
    assert fd.parse_dscd("DPL#(992816(WC02999))") == "992816"
    assert fd.parse_dscd("992816(WC02999)") == "992816"
    assert fd.parse_dscd("#ERROR") is None
    assert fd.parse_dscd(None) is None


def test_read_file_counts(root):
    long, rep = fd.read_firm_file(root / "WC02999" / "WC02999_01.xlsx", "WC02999")
    assert rep.status == fd.STATUS_OK
    assert set(long["DSCD"]) == {"AAA", "BBB"}
    assert rep.n_obs == 6 and rep.n_coerced == 1
    assert "$$ER" in rep.coerced_examples


def test_error_column_counted_and_dropped(root):
    long, rep = fd.read_firm_file(root / "WC02999" / "WC02999_02.xlsx", "WC02999")
    assert rep.n_series == 2 and rep.n_error_series == 1
    assert set(long["DSCD"]) == {"CCC"}


def test_empty_template_blocks_write(root):
    s = fd.import_variable(root, "WC02999")
    assert s.status == "incomplete" and s.n_files_empty == 1
    assert not s.panel_written
    assert not fd.variable_panel_path(root, "WC02999").exists()
    log = fd.read_import_log(root)
    assert log.loc[0, "status"] == "incomplete"


def test_import_complete_and_inventory(root):
    (root / "WC02999" / "WC02999_03.xlsx").unlink()
    s = fd.import_variable(root, "WC02999")
    assert s.panel_written and s.status == "complete"
    assert s.n_firms_with_data == 3 and s.n_obs == 10
    assert s.date_convention == "day_01"
    panel = pd.read_parquet(fd.variable_panel_path(root, "WC02999"))
    assert list(panel.columns) == ["Date", "DSCD", "WC02999"]
    assert not fd.needs_import(root, "WC02999")

    inv = fd.build_inventory(root)
    row = inv.set_index("variable").loc["WC02999"]
    assert row["panel_state"] == "current"
    assert int(row["n_firms_with_data"]) == 3


def test_duplicates_across_files_reported(tmp_path):
    var = "X"
    (tmp_path / var).mkdir()
    write_ds_file(tmp_path / var / "X_01.xlsx", ["AAA(X)"], DATES[:2], [[1], [2]])
    write_ds_file(tmp_path / var / "X_02.xlsx", ["AAA(X)"], DATES[:2], [[1], [3]])
    s = fd.import_variable(tmp_path, var)
    assert s.n_duplicate_obs == 2 and s.n_conflicting_duplicates == 1
    panel = pd.read_parquet(fd.variable_panel_path(tmp_path, var))
    assert panel["X"].tolist() == [1, 2]  # first file kept


def test_merge_aligns_date_conventions(tmp_path):
    for var, dates in [("A", DATES), ("B", [d + pd.offsets.MonthEnd(0) for d in pd.to_datetime(DATES)])]:
        (tmp_path / var).mkdir()
        write_ds_file(tmp_path / var / f"{var}_01.xlsx", [f"F1({var})", f"F2({var})"], list(dates),
                      [[1, 2]] * 4)
        fd.import_variable(tmp_path, var)

    with pytest.raises(ValueError):
        fd.merge_variables(tmp_path, ["A", "B"], align_month_end=False)

    merged, meta = fd.merge_variables(tmp_path, ["A", "B"])
    assert len(merged) == 8  # 2 firms x 4 months, no duplicated rows
    assert merged["Date"].dt.is_month_end.all()
    assert merged[["A", "B"]].notna().all().all()

    fd.write_merged(tmp_path, "ab", merged, meta)
    side = json.loads((tmp_path / fd.MERGED_SUBDIR / "ab.json").read_text())
    assert side["variables"] == ["A", "B"]
    inv = fd.build_inventory(tmp_path).set_index("variable")
    assert inv.loc["A", "in_merged"] == "ab"


def test_stale_after_new_download(root):
    (root / "WC02999" / "WC02999_03.xlsx").unlink()
    fd.import_variable(root, "WC02999")
    import os, time
    f = root / "WC02999" / "WC02999_01.xlsx"
    t = time.time() + 60
    os.utime(f, (t, t))
    assert fd.needs_import(root, "WC02999")
    assert fd.build_inventory(root).set_index("variable").loc["WC02999", "panel_state"] == "stale"
