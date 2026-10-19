"""Tests for the extra readers in pawtrace.formats_extra (WPD, Engauge, table CSV)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from pawtrace import formats
from pawtrace.formats import read
from pawtrace.formats_extra import read_engauge_csv, read_table_csv, read_wpd_csv, read_wpd_json


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def wpd_project(tmp_path: Path) -> Path:
    payload = {
        "version": 2,
        "datasetColl": [
            {
                "name": "PD no Treatment",
                "colorRGB": [200, 30, 30, 255],
                "data": [{"value": [3, 30]}, {"value": [1, 10]}, {"value": None}, {"value": [2, 20]}],
            },
            {
                "name": "Healthy Control",
                "colorRGB": [30, 30, 200],
                "data": [{"value": [1, 5]}, {"value": [2, 6]}],
            },
            {"name": "Empty", "data": []},
        ],
    }
    return _write(tmp_path / "fig1b.wpd.json", json.dumps(payload))


@pytest.fixture
def wpd_csv(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "fig1b_wpd.csv",
        "PD no Treatment,,Healthy Control,\n"
        "X,Y,X,Y\n"
        "3,30,1,5\n"
        "1,10,2,6\n"
        ",,2,\n"
        "2,20,,\n",
    )


@pytest.fixture
def engauge_shared(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "shared.csv",
        "x,Curve A,Curve B\n3,30,\n1,10,5\n2,,6\n",
    )


@pytest.fixture
def table_csv(tmp_path: Path) -> Path:
    return _write(
        tmp_path / "bars.csv",
        "Method,Accuracy,Std,Latency\n"
        "Baseline,0.71,0.02,12\n"
        "Ours,0.83,0.01,\n"
        "Noerr,0.6,,8\n"
        "PD,,,7\n"
        "Other,0.5,0.03,9\n",
    )


# --- WebPlotDigitizer -------------------------------------------------------


def test_wpd_json_names_values_and_color(wpd_project: Path) -> None:
    series = read_wpd_json(wpd_project)
    assert list(series) == ["PD no Treatment", "Healthy Control"]  # empty dataset dropped

    pd = series["PD no Treatment"]
    assert len(pd) == 3  # null point dropped
    np.testing.assert_array_equal(pd.xf, [1.0, 2.0, 3.0])  # sorted by x
    np.testing.assert_array_equal(pd.y, [10.0, 20.0, 30.0])
    assert pd.meta["color_rgba"] == [200, 30, 30, 255]
    assert pd.kind == "curve"

    healthy = series["Healthy Control"]
    np.testing.assert_array_equal(healthy.y, [5.0, 6.0])
    assert healthy.meta["color_rgba"] == [30, 30, 200, 255]  # alpha defaults to 255


def test_wpd_json_series_map_renames(wpd_project: Path) -> None:
    series = read_wpd_json(wpd_project, series_map={"PD no Treatment": "pd"})
    assert list(series) == ["pd", "Healthy Control"]
    assert series["pd"].meta["color_rgba"] == [200, 30, 30, 255]


def test_wpd_csv_two_series(wpd_csv: Path) -> None:
    series = read_wpd_csv(wpd_csv)
    assert list(series) == ["PD no Treatment", "Healthy Control"]

    pd = series["PD no Treatment"]
    np.testing.assert_array_equal(pd.xf, [1.0, 2.0, 3.0])
    np.testing.assert_array_equal(pd.y, [10.0, 20.0, 30.0])
    assert "color_rgba" not in pd.meta  # CSV carries no color

    healthy = series["Healthy Control"]
    np.testing.assert_array_equal(healthy.xf, [1.0, 2.0])
    np.testing.assert_array_equal(healthy.y, [5.0, 6.0])


def test_wpd_csv_series_map(wpd_csv: Path) -> None:
    series = read_wpd_csv(wpd_csv, series_map={"Healthy Control": "healthy"})
    assert list(series) == ["PD no Treatment", "healthy"]


# --- Engauge Digitizer ------------------------------------------------------


def test_engauge_shared_x_columns(engauge_shared: Path) -> None:
    series = read_engauge_csv(engauge_shared)
    assert list(series) == ["Curve A", "Curve B"]
    np.testing.assert_array_equal(series["Curve A"].xf, [1.0, 3.0])  # row with empty y skipped, sorted
    np.testing.assert_array_equal(series["Curve A"].y, [10.0, 30.0])
    np.testing.assert_array_equal(series["Curve B"].xf, [1.0, 2.0])
    np.testing.assert_array_equal(series["Curve B"].y, [5.0, 6.0])


def test_engauge_series_map(engauge_shared: Path) -> None:
    series = read_engauge_csv(engauge_shared, series_map={"Curve A": "pd"})
    assert list(series) == ["pd", "Curve B"]


def test_engauge_raw_blocks(tmp_path: Path) -> None:
    path = _write(tmp_path / "raw.csv", "x,Trace one\n0.5,1\n0.1,2\nx,Trace two\n1,3\n")
    series = read_engauge_csv(path)
    assert list(series) == ["Trace one", "Trace two"]
    np.testing.assert_array_equal(series["Trace one"].xf, [0.1, 0.5])
    np.testing.assert_array_equal(series["Trace one"].y, [2.0, 1.0])
    np.testing.assert_array_equal(series["Trace two"].y, [3.0])


def test_engauge_two_column_with_xy_header(tmp_path: Path) -> None:
    path = _write(tmp_path / "simple.csv", "x,y\n0.2,2.5\n0.1,1.5\n0.3,\n")
    series = read_engauge_csv(path)
    assert list(series) == ["series"]
    np.testing.assert_array_equal(series["series"].xf, [0.1, 0.2])
    np.testing.assert_array_equal(series["series"].y, [1.5, 2.5])


# --- table CSV --------------------------------------------------------------


def test_table_label_values_and_errors(table_csv: Path) -> None:
    series = read_table_csv(table_csv, label="Method", value=["Accuracy", "Latency"], err={"Accuracy": "Std"})
    assert list(series) == ["Accuracy", "Latency"]

    acc = series["Accuracy"]
    assert acc.categorical
    assert acc.x == ["Baseline", "Ours", "Other"]  # PD: empty value; Noerr: empty error -> dropped
    np.testing.assert_array_equal(acc.y, [0.71, 0.83, 0.5])
    np.testing.assert_array_equal(acc.yerr, [0.02, 0.01, 0.03])
    assert acc.kind == "bars"

    lat = series["Latency"]
    assert lat.x == ["Baseline", "Noerr", "PD", "Other"]  # Ours: empty latency
    np.testing.assert_array_equal(lat.y, [12.0, 8.0, 7.0, 9.0])
    assert lat.yerr is None


def test_table_default_value_columns(table_csv: Path) -> None:
    series = read_table_csv(table_csv)  # label = first column, values = the rest
    assert list(series) == ["Accuracy", "Std", "Latency"]
    assert series["Std"].x[0] == "Baseline"


def test_table_bad_inputs_raise(table_csv: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no column 'Nope'"):
        read_table_csv(table_csv, label="Method", value="Nope")
    with pytest.raises(ValueError, match="not one of the value columns"):
        read_table_csv(table_csv, label="Method", value="Latency", err={"Accuracy": "Std"})
    bad = _write(tmp_path / "bad.csv", "Method,Value\nA,oops\n")
    with pytest.raises(ValueError, match="not a number"):
        read_table_csv(bad)


# --- registry ---------------------------------------------------------------


def test_registered_and_dispatched(wpd_project: Path, wpd_csv: Path, table_csv: Path) -> None:
    for name in ("wpd-json", "wpd-csv", "engauge-csv", "table-csv"):
        assert name in formats.READERS
    assert formats.guess_format(wpd_project) == "wpd-json"
    assert formats.guess_format(wpd_csv) == "wpd-csv"
    assert list(read(wpd_project)) == ["PD no Treatment", "Healthy Control"]
    assert read(table_csv, fmt="table-csv", label="Method", value="Latency")["Latency"].kind == "bars"


def test_formats_extra_imports_without_formats_first() -> None:
    # Importing the new module first must not hit a circular-import failure.
    code = "import pawtrace.formats_extra as m, pawtrace.formats as f; assert 'table-csv' in f.READERS; print('ok')"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
