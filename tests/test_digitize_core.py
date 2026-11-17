"""Tests for pawtrace.digitize.core: calibration math, document build, residuals, review, IO."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from pawtrace import formats
from pawtrace.data import Series
from pawtrace.digitize.core import (
    Axis,
    Calibration,
    DigitizeError,
    apply_review,
    build_document,
    load_document,
    residual,
    sha256_file,
    write_document,
)

NOW = "2026-11-07T00:00:00Z"


def _cal() -> Calibration:
    """x: 40 px per unit, 0 at px x=100 and 10 at px x=500 (row 400).
    y: 40 px per unit, 0 at px y=400 and 9 at px y=40 (column 60)."""
    return Calibration(
        x=Axis("linear", (100, 400), 0.0, (500, 400), 10.0),
        y=Axis("linear", (60, 400), 0.0, (60, 40), 9.0),
    )


def px(x: float, y: float) -> list[float]:
    """Pixel of data (x, y) on _cal()."""
    return [100.0 + 40.0 * x, 400.0 - 40.0 * y]


def _state(series: list[dict]) -> dict:
    return {"calibration": _cal().to_json(), "series": series}


def _affine_cal(m: np.ndarray, t: np.ndarray) -> Calibration:
    """Calibration whose pixels come from the affine map P = m @ (x, y) + t."""

    def pix(x: float, y: float) -> list[float]:
        return (m @ np.array([x, y]) + t).tolist()

    return Calibration(
        x=Axis("linear", pix(2, 0), 2.0, pix(10, 0), 10.0),
        y=Axis("linear", pix(0, 2), 2.0, pix(0, 7), 7.0),
    )


_THETA = math.radians(10.0)
_ROT = 40.0 * np.array([[math.cos(_THETA), -math.sin(_THETA)], [math.sin(_THETA), math.cos(_THETA)]]) @ np.diag(
    [1.0, -1.0]
)  # 10 degree rotation, image y down: perpendicular axes
_SKEW = np.array([[38.0, 9.0], [7.0, -29.0]])  # non-perpendicular axes


# --- calibration math -------------------------------------------------------------

def test_linear_axis_aligned_is_plain_interpolation():
    cal = _cal()
    d = cal.to_data([[100, 400], [500, 400], [100, 40], [300, 250]])
    np.testing.assert_allclose(d, [[0, 0], [10, 0], [0, 9], [5, 3.75]], atol=1e-12)

    pts = np.array([[123.0, 301.0], [480.5, 77.0]])
    got = cal.to_data(pts)
    np.testing.assert_allclose(got[:, 0], (pts[:, 0] - 100) / 40)
    np.testing.assert_allclose(got[:, 1], (400 - pts[:, 1]) / 40)
    np.testing.assert_allclose(cal.to_pixel(got), pts, atol=1e-9)


def test_log_axes_interpolate_in_log_space():
    cal = Calibration(
        x=Axis("log", (100, 400), 1.0, (500, 400), 1000.0),
        y=Axis("log", (60, 400), 1.0, (60, 100), 100.0),
    )
    # x: log10 goes 0 -> 3 over px 100 -> 500, so px 300 is log10 1.5
    np.testing.assert_allclose(cal.to_data([[300, 250]]), [[10**1.5, 10.0]], rtol=1e-12)
    np.testing.assert_allclose(cal.to_data([[100, 400], [500, 100]]), [[1, 1], [1000, 100]], rtol=1e-12)
    pts = np.array([[300.0, 250.0], [180.0, 330.0]])
    np.testing.assert_allclose(cal.to_pixel(cal.to_data(pts)), pts, atol=1e-9)


@pytest.mark.parametrize("m", [_ROT, _SKEW], ids=["rotated-10deg", "skewed"])
def test_rotated_and_skewed_axes_round_trip(m):
    t = np.array([120.0, 380.0])
    cal = _affine_cal(m, t)

    def pix(x: float, y: float) -> np.ndarray:
        return m @ np.array([x, y]) + t

    cal_px = [pix(2, 0), pix(10, 0), pix(0, 2), pix(0, 7)]
    # the four calibration pixels come back on the calibrated coordinate
    np.testing.assert_allclose(cal.to_data(cal_px), [[2, 0], [10, 0], [0, 2], [0, 7]], atol=1e-9)
    np.testing.assert_allclose(cal.to_pixel([[2, 0], [10, 0], [0, 2], [0, 7]]), cal_px, atol=1e-9)

    rng = np.random.default_rng(0)
    d = rng.uniform([-5.0, -5.0], [15.0, 10.0], size=(25, 2))
    p = d @ m.T + t
    np.testing.assert_allclose(cal.to_data(p), d, atol=1e-8)
    np.testing.assert_allclose(cal.to_pixel(d), p, atol=1e-8)
    np.testing.assert_allclose(cal.to_data(cal.to_pixel(d)), d, atol=1e-8)


def test_scale_defaults_to_linear_and_json_round_trips():
    obj = _cal().to_json()
    del obj["x"]["scale"]
    cal = Calibration.from_json(obj)
    assert cal.x.scale == "linear"
    assert cal.to_json()["x"]["scale"] == "linear"
    assert Calibration.from_json(cal.to_json()).to_json() == cal.to_json()


def _base_json() -> dict:
    return _cal().to_json()


@pytest.mark.parametrize(
    "mutate, msg",
    [
        (lambda o: o["x"].update(scale="sqrt"), "scale must be one of"),
        (lambda o: o["x"]["points"][1].update(value=0.0), "must differ"),
        (lambda o: (o["x"].update(scale="log"), o["x"]["points"][0].update(value=0.0)), "> 0"),
        (lambda o: o["x"]["points"][1].update(px=o["x"]["points"][0]["px"]), "identical"),
        (lambda o: o["y"]["points"][1].update(px=[160.0, 400.0]), "parallel"),
        (lambda o: o["x"]["points"][0].update(value=float("nan")), "finite"),
        (lambda o: o["x"]["points"][0].update(px=[float("inf"), 400.0]), "finite"),
        (lambda o: o["y"].update(points=o["y"]["points"][:1]), "exactly two"),
    ],
)
def test_validation_errors(mutate, msg):
    obj = _base_json()
    mutate(obj)
    with pytest.raises(DigitizeError, match=re.escape(msg)):
        Calibration.from_json(obj)


def test_log_and_negative_linear_axes_are_valid():
    Calibration(
        x=Axis("log", (0, 0), 1.0, (10, 0), 10.0),
        y=Axis("linear", (0, 0), -5.0, (0, 10), 5.0),
    )


# --- build_document ---------------------------------------------------------------

def test_curve_is_sorted_by_data_x_and_meta_follows_order():
    state = _state(
        [
            {
                "name": "A",
                "kind": "curve",
                "color": [10, 20, 30, 255],
                "points": [
                    {"px": px(5, 4.5), "src": 0},
                    {"px": px(1, 1.2), "src": 1, "snapped": True},
                    {"px": px(3, 7.0)},
                ],
            }
        ]
    )
    doc = build_document(state, image={"file": "fig.png"}, by="a tester", now=NOW, tool_version="t")
    ent = doc["series"]["A"]
    assert ent["n"] == 3
    assert ent["xy"]["x"] == [1.0, 3.0, 5.0]
    assert ent["xy"]["y"] == [1.2, 7.0, 4.5]
    assert "kind" not in ent and "yerr" not in ent
    assert ent["color_rgba"] == [10, 20, 30, 255]
    assert ent["meta"]["px"] == [[140.0, 352.0], [220.0, 120.0], [300.0, 220.0]]
    assert ent["meta"]["src"] == [1, None, 0]
    assert ent["meta"]["snapped"] == [True, False, False]
    assert ent["meta"]["err_px"] == [None, None, None]

    digi = doc["meta"]["digitization"]
    assert digi["tool"] == "pawtrace digitize"
    assert digi["tool_version"] == "t"
    assert digi["method"] == "manual" and digi["by"] == "human"
    assert digi["name"] == "a tester"
    assert digi["date"] == NOW
    assert digi["image"] == {"file": "fig.png"}
    assert digi["calibration"] == _cal().to_json()
    assert digi["assist"] == {"used": True, "snap_to_color": None}


def test_default_date_is_utc_iso_and_name_omitted_without_by():
    doc = build_document(_state([{"name": "A", "points": [{"px": px(1, 1)}]}]), image={})
    digi = doc["meta"]["digitization"]
    assert "name" not in digi
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", digi["date"])
    assert digi["assist"] == {"used": False, "snap_to_color": None}


def test_scatter_keeps_click_order():
    state = _state([{"name": "S", "kind": "scatter", "points": [{"px": px(5, 1)}, {"px": px(1, 2)}]}])
    ent = build_document(state, image={}, now=NOW)["series"]["S"]
    assert ent["kind"] == "scatter"
    assert ent["xy"]["x"] == [5.0, 1.0]
    assert ent["xy"]["y"] == [1.0, 2.0]


def test_bars_use_labels_and_err_px_for_yerr_and_snap_assist():
    state = _state(
        [
            {
                "name": "B",
                "kind": "bars",
                "points": [
                    {"px": px(2, 0.5), "label": " ctrl ", "err_px": px(2, 0.7)},
                    {"px": px(4, 0.8), "label": "tx", "err_px": px(4, 0.6), "snapped": True, "src": 1},
                    {"px": px(6, 0.3), "label": "sham"},
                ],
            }
        ]
    )
    doc = build_document(state, image={}, now=NOW, assist={"threshold": 12})
    ent = doc["series"]["B"]
    assert ent["kind"] == "bars"
    assert ent["xy"]["x"] == ["ctrl", "tx", "sham"]
    assert ent["xy"]["y"] == [0.5, 0.8, 0.3]
    assert ent["yerr"] == [0.2, 0.2, 0.0]
    assert ent["meta"]["err_px"] == [[180.0, 372.0], [260.0, 376.0], None]
    assert ent["meta"]["src"] == [None, 1, None]
    assert doc["meta"]["digitization"]["assist"] == {"used": True, "snap_to_color": {"threshold": 12}}


def test_yerr_only_when_some_point_has_err_px_and_zero_fill():
    state = _state(
        [
            {
                "name": "A",
                "points": [{"px": px(1, 1), "err_px": px(1, 1.25)}, {"px": px(2, 2)}],
            }
        ]
    )
    ent = build_document(state, image={}, now=NOW)["series"]["A"]
    assert ent["yerr"] == [0.25, 0.0]


def test_bars_need_labels_and_unique_names_and_kinds():
    ok_point = {"px": px(1, 1), "label": "a"}
    with pytest.raises(DigitizeError, match="label"):
        build_document(_state([{"name": "B", "kind": "bars", "points": [{"px": px(1, 1)}]}]), image={})
    with pytest.raises(DigitizeError, match="duplicate bars label"):
        build_document(
            _state([{"name": "B", "kind": "bars", "points": [ok_point, {"px": px(2, 2), "label": " a "}]}]),
            image={},
        )
    with pytest.raises(DigitizeError, match="duplicate series name"):
        build_document(
            _state([{"name": "A", "points": [{"px": px(1, 1)}]}, {"name": "A", "points": [{"px": px(2, 2)}]}]),
            image={},
        )
    with pytest.raises(DigitizeError, match="kind must be one of"):
        build_document(_state([{"name": "A", "kind": "pie", "points": [{"px": px(1, 1)}]}]), image={})
    with pytest.raises(DigitizeError, match="non-empty name"):
        build_document(_state([{"name": " ", "points": [{"px": px(1, 1)}]}]), image={})


def test_empty_series_are_skipped():
    doc = build_document(
        _state([{"name": "A", "points": [{"px": px(1, 1)}]}, {"name": "E", "kind": "bars", "points": []}]),
        image={},
        now=NOW,
    )
    assert list(doc["series"]) == ["A"]


def test_values_round_to_six_significant_digits_and_pixels_to_two_decimals():
    state = _state([{"name": "A", "points": [{"px": [100 + 40 * 1.23456789, 400 - 40 * 0.5]}]}])
    ent = build_document(state, image={}, now=NOW)["series"]["A"]
    assert ent["xy"]["x"] == [1.23457]
    assert ent["xy"]["y"] == [0.5]
    assert ent["meta"]["px"] == [[149.38, 380.0]]


def test_to_pixel_rejects_non_positive_values_on_log_axis():
    cal = Calibration(
        x=Axis("log", (100, 400), 1.0, (500, 400), 1000.0),
        y=Axis("linear", (60, 400), 0.0, (60, 40), 9.0),
    )
    with pytest.raises(DigitizeError, match="> 0"):
        cal.to_pixel([[0.0, 1.0]])
    with pytest.raises(DigitizeError, match="finite"):
        cal.to_pixel([[float("nan"), 1.0]])


# --- write / read round trip ------------------------------------------------------

def test_write_document_and_read_back_with_curves_json_reader(tmp_path: Path):
    state = _state(
        [
            {
                "name": "curve A",
                "kind": "curve",
                "points": [
                    {"px": px(1, 1.2), "err_px": px(1, 1.5)},
                    {"px": px(3, 7.0)},
                    {"px": px(5, 4.5), "err_px": px(5, 4.0)},
                ],
            },
            {
                "name": "B",
                "kind": "bars",
                "color": [1, 2, 3, 255],
                "points": [
                    {"px": px(2, 0.5), "label": "ctrl", "err_px": px(2, 0.7)},
                    {"px": px(4, 0.8), "label": "tx"},
                ],
            },
        ]
    )
    doc = build_document(state, image={"file": "fig.png"}, by="a tester", now=NOW, tool_version="t")
    path = tmp_path / "out" / "fig1.curves.json"
    write_document(path, doc)

    assert path.read_text(encoding="utf-8").endswith("\n")
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    assert load_document(path) == json.loads(json.dumps(doc))

    series = formats.read(path, "curves-json")
    a = series["curve A"]
    assert isinstance(a, Series)
    assert a.kind == "curve"
    np.testing.assert_allclose(a.xf, [1.0, 3.0, 5.0])
    np.testing.assert_allclose(a.y, [1.2, 7.0, 4.5])
    np.testing.assert_allclose(a.yerr, [0.3, 0.0, 0.5])  # sorted by x: |1.5-1.2|, none, |4.0-4.5|

    b = series["B"]
    assert b.kind == "bars"
    assert b.categorical
    assert b.x == ["ctrl", "tx"]
    np.testing.assert_allclose(b.y, [0.5, 0.8])
    np.testing.assert_allclose(b.yerr, [0.2, 0.0])
    # formats.read("curves-json") does not surface the top-level color_rgba key; it stays in the file
    assert json.loads(path.read_text(encoding="utf-8"))["series"]["B"]["color_rgba"] == [1, 2, 3, 255]


# --- residual ---------------------------------------------------------------------

ORIG = {
    "A": Series(x=[0.0, 1.0, 2.0, 3.0], y=[0.0, 1.0, 4.0, 9.0]),
    "B": Series(x=["ctrl", "tx", "sham"], y=[0.5, 0.8, 0.3], kind="bars"),
    "gone": Series(x=[0.0, 1.0], y=[1.0, 2.0]),
}


def _res_doc() -> dict:
    state = _state(
        [
            {
                "name": "A",
                "kind": "curve",
                "points": [
                    {"px": px(0, 0), "src": 0},
                    {"px": px(1, 1.5), "src": 1},
                    {"px": px(3, 9), "src": 3},
                    {"px": px(1.5, 2.5)},  # added in the editor
                ],
            },
            {
                "name": "B",
                "kind": "bars",
                "points": [
                    {"px": px(2, 0.5), "label": "ctrl", "src": 0},
                    {"px": px(4, 0.9), "label": "tx", "src": 1},
                    {"px": px(6, 0.2), "label": "new"},
                ],
            },
            {"name": "C", "kind": "scatter", "points": [{"px": px(1, 1)}]},
        ]
    )
    return build_document(state, image={}, now=NOW)


def test_numeric_residual_counts_and_errors():
    res = residual(ORIG, _res_doc())
    a = res["series"]["A"]
    assert a["n_original"] == 4 and a["n_edited"] == 4
    assert a["added"] == 1
    assert a["removed"] == 1  # original index 2 is referenced by no point
    assert a["moved"] == 1  # only src 1 moved (y 1 -> 1.5)
    assert a["max_abs_dy"] == pytest.approx(0.5)
    assert a["rms_dy"] == pytest.approx(math.sqrt(0.25 / 3), rel=1e-5)
    assert a["max_abs_dx"] == 0.0
    # interp of edited curve at original x 0..3, minus original y
    expected = math.sqrt((0.25 + (2.5 + 6.5 / 3 - 4.0) ** 2) / 4)
    assert a["curve_rms"] == pytest.approx(expected, rel=1e-5)
    assert a["y_range"] == pytest.approx(9.0)


def test_label_residual_matches_by_label_and_lists_added_removed():
    res = residual(ORIG, _res_doc())
    b = res["series"]["B"]
    assert b["n_original"] == 3 and b["n_edited"] == 3
    assert b["added"] == 1
    assert b["removed"] == 1  # "sham" (src 2) is no longer referenced
    assert b["moved"] == 1  # "tx" y 0.8 -> 0.9
    assert b["max_abs_dy"] == pytest.approx(0.1)
    assert b["rms_dy"] == pytest.approx(math.sqrt(0.01 / 2), rel=1e-5)
    assert b["labels_added"] == ["new"]
    assert b["labels_removed"] == ["sham"]
    assert b["y_range"] == pytest.approx(0.5)
    assert "curve_rms" not in b


def test_added_and_removed_series():
    res = residual(ORIG, _res_doc())
    assert res["added_series"] == ["C"]
    assert res["removed_series"] == ["gone"]
    assert set(res["series"]) == {"A", "B"}


def test_curve_rms_is_none_with_fewer_than_two_edited_points():
    doc = build_document(_state([{"name": "A", "points": [{"px": px(1, 1), "src": 0}]}]), image={}, now=NOW)
    res = residual({"A": ORIG["A"]}, doc)
    a = res["series"]["A"]
    assert a["curve_rms"] is None
    assert a["n_edited"] == 1 and a["removed"] == 3 and a["added"] == 0


def test_residual_rejects_src_outside_original():
    doc = _res_doc()
    doc["series"]["A"]["meta"]["src"][0] = 9
    with pytest.raises(DigitizeError, match="outside"):
        residual(ORIG, doc)


# --- review -----------------------------------------------------------------------

def test_apply_review_keeps_history_and_does_not_mutate_inputs():
    prev_history_entry = {
        "replaced": "2026-09-01T00:00:00Z",
        "meta": {"digitization": {"date": "2026-08-01T00:00:00Z"}},
        "series": {"A": {"n": 0}},
    }
    prev_meta = {
        "digitization": {"tool": "pawtrace digitize", "date": "2026-10-01T00:00:00Z"},
        "history": [prev_history_entry],
    }
    previous = {"series": {"A": {"n": 1, "xy": {"x": [1.0], "y": [2.0]}}}, "meta": prev_meta}
    doc = _res_doc()
    res = residual(ORIG, doc)
    doc_before, prev_before = copy.deepcopy(doc), copy.deepcopy(previous)
    original = {"path": "fig1.json", "sha256": "ab"}

    out = apply_review(doc, original=original, residual=res, by="a tester", now=NOW, previous=previous)

    assert doc == doc_before and previous == prev_before
    hist = out["meta"]["history"]
    assert len(hist) == 2
    assert hist[0] == prev_history_entry
    assert hist[1] == {
        "replaced": NOW,
        "meta": {"digitization": {"tool": "pawtrace digitize", "date": "2026-10-01T00:00:00Z"}},
        "series": previous["series"],
    }
    assert "history" not in hist[1]["meta"]
    assert out["meta"]["review"] == {
        "reviewed_by": "human",
        "name": "a tester",
        "date": NOW,
        "original": original,
        "residual": res,
    }
    assert out["series"] == doc["series"]
    assert out["meta"]["digitization"] == doc["meta"]["digitization"]


def test_apply_review_without_previous_or_by():
    doc = _res_doc()
    res = residual(ORIG, doc)
    before = copy.deepcopy(doc)
    out = apply_review(doc, original={"path": "x"}, residual=res, now=NOW)
    assert doc == before
    assert "history" not in out["meta"]
    assert "name" not in out["meta"]["review"]
    assert out["meta"]["review"]["date"] == NOW


# --- file helpers -----------------------------------------------------------------

def test_sha256_and_load_document(tmp_path: Path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    assert sha256_file(f) == hashlib.sha256(b"abc").hexdigest()

    assert load_document(tmp_path / "missing.json") is None
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    assert load_document(tmp_path / "list.json") is None
    (tmp_path / "ok.json").write_text('{"series": {}}', encoding="utf-8")
    assert load_document(tmp_path / "ok.json") == {"series": {}}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(DigitizeError, match="not valid JSON"):
        load_document(tmp_path / "bad.json")
