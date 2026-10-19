import math

import numpy as np
import pytest

from pawtrace.data import Series
from pawtrace.gates import GateConfigError, evaluate, gates_hash, validate
from pawtrace.gates import library as L


def curve(y, x=None):
    return Series.from_y(y) if x is None else Series(x=x, y=y)


def test_window_mean_closed_vs_left():
    s = curve([1, 2, 3, 4])
    assert L.window_mean(s, hi=2) == 2.0  # x in {0,1,2}
    assert L.window_mean(s, hi=2, closed="left") == 1.5
    assert math.isnan(L.window_mean(s, lo=10))


def test_head_tail_and_block_means_min_count():
    s = curve(np.arange(65, dtype=float))
    assert L.head_mean(s, 5) == 2.0
    assert L.tail_mean(s, 5) == 62.0
    bm = L.block_means(s, width=30, count=3, min_count=30)
    assert bm[:2].tolist() == [14.5, 44.5] and math.isnan(bm[2])  # 5-sample tail isn't an episode


def test_pearson_trailing_vs_interior_missing():
    a = np.array([1.0, 2, 3, 4, 5, np.nan])
    b = np.array([1.0, 2, 3, 4, 6, 7])
    assert L.pearson(a, b) > 0.9  # trailing missing: shorter run
    a2 = np.array([1.0, np.nan, 3, 4, 5, 6])
    assert math.isnan(L.pearson(a2, b))  # interior missing: broken data


def test_half_crossing_and_max_abs():
    v = np.array([1.0, 1, 1, 1, 0.8, 0.6, 0.5, 0.5, 0.5, 0.5])
    assert L.half_crossing(v) == 5.0
    assert math.isnan(L.half_crossing(v[:7]))
    assert math.isnan(L.max_abs(np.array([0.1, np.nan])))


def test_within_block_std_removes_block_means():
    y = np.r_[np.full(30, 1.0), np.full(30, 5.0)]
    assert L.within_block_std(curve(y), lo=0, hi=60, block=30) == 0.0


def test_resample_and_value_at():
    a = curve([0.0, 10.0], x=[0.0, 10.0])
    b = curve([9, 9, 9], x=[0.0, 5.0, 10.0])
    assert L.resample(a, b).y.tolist() == [0.0, 5.0, 10.0]
    assert L.value_at(a, x=2.5) == 2.5
    bars = Series(x=["ctrl", "drug"], y=[3.0, 1.0], kind="bars")
    assert L.value_at(bars, label="drug") == 1.0
    assert math.isnan(L.value_at(bars, label="nope"))


def test_checks_fail_on_nan():
    nan = float("nan")
    assert not L.rel_close(nan, 1.0, 0.5)
    assert not L.less_than(nan, 1.0)
    assert not L.ratio_in_band(1.0, 0.0, [0, 2])
    assert not L.ratio_in_band(-1.0, -1.0, [0.5, 2], ref_positive=True)
    assert L.ratio_in_band(-1.0, -1.0, [0.5, 2])


BAR_CFG = {
    "metrics": {
        "ours_drop": {"op": "sub", "a": "ours_ctrl", "b": "ours_drug"},
        "paper_drop": {"op": "sub", "a": "paper_ctrl", "b": "paper_drug"},
        "ours_ctrl": {"op": "value_at", "of": "run.bars", "label": "ctrl"},
        "ours_drug": {"op": "value_at", "of": "run.bars", "label": "drug"},
        "paper_ctrl": {"op": "value_at", "of": "ref.paper", "label": "ctrl"},
        "paper_drug": {"op": "value_at", "of": "ref.paper", "label": "drug"},
    },
    "gates": {
        "order": {"check": "label_order", "of": "run.bars", "order": ["ctrl", "mid", "drug"], "why": "ordering"},
        "drop": {"check": "ratio_in_band", "value": "ours_drop", "ref": "paper_drop", "band": [0.5, 2], "why": "size"},
        "info": {"check": "at_least", "value": "ours_ctrl", "min": 100, "required": False},
    },
}


def test_bar_chart_gates_and_info_gate():
    inputs = {
        "run.bars": Series(x=["ctrl", "mid", "drug"], y=[10.0, 7.0, 4.0], kind="bars"),
        "ref.paper": Series(x=["ctrl", "mid", "drug"], y=[9.0, 6.5, 5.0], kind="bars"),
    }
    rep = evaluate(BAR_CFG, inputs)
    assert rep.verdicts == {"order": True, "drop": True, "info": False}
    assert rep.passed  # info gate doesn't count
    out = rep.to_json()
    assert out["n_required"] == 2 and out["metrics"]["ours_drop"] == 6.0


def test_missing_input_fails_gate_not_report():
    rep = evaluate(BAR_CFG, {"run.bars": Series(x=["ctrl"], y=[1.0])})
    assert not rep.results["drop"].passed
    assert "missing input series" in rep.results["drop"].error


def test_validate_and_cycles():
    assert validate({"metrics": {"a": {"op": "nope"}}, "gates": {"g": {"check": "at_least", "value": "zz", "min": 0}}})
    cyc = {"metrics": {"a": {"op": "sub", "a": "b", "b": 1}, "b": {"op": "sub", "a": "a", "b": 1}},
           "gates": {"g": {"check": "at_least", "value": "a", "min": 0}}}
    with pytest.raises(GateConfigError, match="cycle"):
        evaluate(cyc, {})
    with pytest.raises(GateConfigError):
        evaluate({"gates": {"g": {"check": "bogus"}}}, {})


def test_gates_hash_ignores_notes():
    a = {"metrics": {}, "gates": {"g": {"check": "at_least", "value": 1, "min": 0, "why": "x"}}}
    b = {"metrics": {}, "gates": {"g": {"check": "at_least", "value": 1, "min": 0, "why": "y", "history": [1]}}}
    c = {"metrics": {}, "gates": {"g": {"check": "at_least", "value": 1, "min": 0.1}}}
    assert gates_hash(a) == gates_hash(b) != gates_hash(c)
