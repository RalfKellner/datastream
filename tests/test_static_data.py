"""Tests for the static-variable import on synthetic files in the Datastream static layout."""

import os
import time
from datetime import datetime

import pandas as pd
import pytest
from openpyxl import Workbook

from datastream.preprocessing import firm_data as fd
from datastream.preprocessing import static_data as sd


def write_static(path, variable, rows):
    """Header 'Type | VAR | CURRENCY', then DSCD, value, 'NA' per firm."""
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append(["Type", variable, "CURRENCY"])
    for dscd, value in rows:
        ws.append([dscd, value, "NA"])
    wb.save(path)


def set_mtime(path, when: datetime):
    t = when.timestamp()
    os.utime(path, (t, t))


@pytest.fixture
def root(tmp_path):
    folder = tmp_path / "Static" / "ENERDP124"
    write_static(folder / "ENERDP124_01.xlsx", "ENERDP124",
                 [("69568X", "#NA"), (902242, "CO2"), (12345, "Reported"), ("923937", "$$ER: 0904,NO DATA")])
    write_static(folder / "ENERDP124_02.xlsx", "ENERDP124", [("AAAAAA", "Median"), ("BBBBBB", "#NA")])
    return tmp_path


def test_read_static_string(root):
    df, rep = sd.read_static_file(root / "Static/ENERDP124/ENERDP124_01.xlsx", "ENERDP124")
    assert rep.value_type == "string"
    assert rep.n_series == 4 and rep.n_firms_with_data == 2
    assert rep.n_missing == 1 and rep.n_errors == 1
    assert rep.n_padded_ids == 1
    assert set(df["DSCD"]) == {"902242", "012345"}  # numeric codes as 6-character strings


def test_read_static_dates(tmp_path):
    p = tmp_path / "WC05350_01.xlsx"
    write_static(p, "WC05350", [("A1", datetime(2025, 12, 31)), ("A2", "#NA"), ("A3", datetime(2026, 6, 30))])
    df, rep = sd.read_static_file(p, "WC05350")
    assert rep.value_type == "datetime" and rep.n_firms_with_data == 2
    assert pd.api.types.is_datetime64_any_dtype(df["WC05350"])


def test_static_folder_not_a_timeseries_variable(root):
    assert fd.discover_raw_variables(root) == []
    assert sd.discover_static_variables(root) == ["ENERDP124"]


def test_snapshots_build_history(root):
    folder = root / "Static" / "ENERDP124"
    # archive an older download in a dated folder, in which firm 902242 was 'Median'
    write_static(folder / "2025-06-30" / "ENERDP124_01.xlsx", "ENERDP124", [(902242, "Median"), (12345, "Reported")])
    s = sd.import_static_variable(root, "ENERDP124")
    assert s.panel_written and s.n_snapshots == 2
    assert s.n_changed_vs_previous == 1
    full = sd.load_static(root, "ENERDP124", as_of=None)
    assert list(full.columns) == ["DSCD", "ENERDP124", "as_of"]
    hist = full[full["DSCD"] == "902242"].sort_values("as_of")["ENERDP124"].tolist()
    assert hist == ["Median", "CO2"]
    assert sd.load_static(root, "ENERDP124", as_of="2025-12-31").set_index("DSCD").loc["902242", "ENERDP124"] == "Median"
    values = pd.read_csv(root / sd.STATIC_PANEL_SUBDIR / "_reports" / "ENERDP124_values.csv")
    assert {"as_of", "value", "n_firms"} <= set(values.columns)


def test_empty_template_blocks(root):
    Workbook().save(root / "Static/ENERDP124/ENERDP124_03.xlsx")
    s = sd.import_static_variable(root, "ENERDP124")
    assert s.status == "incomplete" and not s.panel_written


def test_inventory_has_static_rows(root, tmp_path):
    reg = tmp_path / "reg.csv"
    reg.write_text("variable,type,description\nENERDP124,static,Estimation method\nWC05350,static,FYE\n")
    sd.import_static_variable(root, "ENERDP124")
    inv = fd.build_inventory(root, reg).set_index("variable")
    assert inv.loc["ENERDP124", "type"] == "static"
    assert inv.loc["ENERDP124", "panel_state"] == "current"
    assert inv.loc["WC05350", "panel_state"] == "missing"
    assert inv.loc["ENERDP124", "description"] == "Estimation method"
    time.sleep(0.01)
    set_mtime(root / "Static/ENERDP124/ENERDP124_02.xlsx", datetime.now().replace(year=datetime.now().year + 1))
    assert sd.static_needs_import(root, "ENERDP124")
