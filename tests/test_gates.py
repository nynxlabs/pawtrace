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
    assert math.isnan(L.pearson(a, b, trailing="nan"))  # strict: any missing entry


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


# --- ops added for the Paper A Fig 2/5 and Paper B Fig 3/4 examples ---------


def test_window_stat_percentile_and_window_fraction():
    s = curve(np.arange(1.0, 101.0))  # 1..100 at x 0..99
    assert L.window_stat(s, "percentile", q=25) == float(np.percentile(s.y, 25))
    assert L.window_stat(s, "percentile", q=50) == L.window_stat(s, "median")
    with pytest.raises(TypeError):
        L.window_stat(s, "percentile")
    assert L.window_fraction(s, lo=90, at_least=95.5) == 0.5  # x 90..99 -> y 91..100
    assert L.window_fraction(s, hi=9, at_most=2) == 0.2
    assert math.isnan(L.window_fraction(s, lo=500, at_least=0))
    with pytest.raises(TypeError):
        L.window_fraction(s, at_least=1, at_most=2)


def test_smooth_shrink_edges():
    s = curve([0.0, 0.0, 3.0, 0.0, 0.0])
    pad = L.smooth(s, 3)
    shrink = L.smooth(s, 3, edges="shrink")
    assert pad.y.tolist() == [0.0, 1.0, 1.0, 1.0, 0.0]
    assert shrink.y.tolist() == [0.0, 1.0, 1.0, 1.0, 0.0]
    edge = L.smooth(curve([3.0, 0.0, 0.0, 0.0]), 4, edges="shrink")
    # window i-2 .. i+1: first sample averages y[0:2] only
    assert edge.y[0] == 1.5 and edge.y[1] == 1.0


def test_on_grid_progress_first_x():
    s = curve([10.0, 10.0, 6.0, 2.0, 2.0], x=[0.0, 1.0, 2.0, 3.0, 4.0])
    g = L.on_grid(s, 0, 4, 9)  # x step 0.5
    assert g.xf.tolist() == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
    p = L.progress(g, start=10.0, end=2.0)
    assert p.y[0] == 0.0 and p.y[-1] == 1.0
    assert L.first_x(p, at_least=0.5) == 2.0  # y 6 is halfway from 10 to 2
    assert math.isnan(L.first_x(p, at_least=1.5))
    assert L.first_x(g, at_most=4.0) == 2.5
    assert np.isnan(L.progress(g, start=10.0, end=2.0, direction="up").y).all()
    assert np.isnan(L.progress(g, start=5.0, end=5.0).y).all()  # no movement: undefined
    assert L.on_grid(s, 4, 6, 3).y.tolist() == [2.0, 2.0, 2.0]  # end value held past the data


def test_elementwise_arithmetic_and_abs():
    a = curve([1.0, 2.0, 3.0])
    b = curve([1.0, 1.0, 0.0])
    assert L.sub(3.0, 1.0) == 2.0 and isinstance(L.sub(3.0, 1.0), float)
    d = L.sub(a, b)
    assert isinstance(d, Series) and d.y.tolist() == [0.0, 1.0, 3.0]
    r = L.ratio(a, b)
    assert r.y[:2].tolist() == [1.0, 2.0] and math.isnan(r.y[2])
    assert L.mul(a, 2.0).y.tolist() == [2.0, 4.0, 6.0]
    with pytest.raises(ValueError):
        L.sub(a, curve([1.0, 2.0, 3.0], x=[0.0, 1.0, 5.0]))
    assert L.abs_(-2.5) == 2.5
    assert L.abs_(L.sub(b, a)).y.tolist() == [0.0, 1.0, 3.0]


def test_across_and_only_if():
    assert L.across([3.0, 1.0, 2.0], "ptp") == 2.0
    assert L.across([30.0, 25], "min") == 25.0
    assert math.isnan(L.across([1.0, float("nan")], "mean"))
    assert L.only_if(4.2, 500.0, min=500) == 4.2
    assert math.isnan(L.only_if(4.2, 499.0, min=500))
    assert np.isnan(L.only_if(curve([1.0, 2.0]), 1.0, min=2).y).all()


def test_in_range_closed():
    assert L.in_range(150.0, 150, 187.5)
    assert not L.in_range(150.0, 150, 187.5, closed="right")
    assert L.in_range(187.5, 150, 187.5, closed="right")
    assert not L.in_range(187.5, 150, 187.5, closed="neither")


def test_gate_config_with_new_ops():
    run = curve([10.0, 10.0, 10.0, 7.0, 4.0, 4.0, 4.0])
    cfg = {
        "metrics": {
            "pre": {"op": "window_mean", "of": "run.y", "hi": 2},
            "late": {"op": "window_mean", "of": "run.y", "lo": 4},
            "g": {"op": "on_grid", "of": "run.y", "lo": 2, "hi": 6, "n": 41},
            "prog": {"op": "progress", "of": "g", "start": "pre", "end": "late", "direction": "down"},
            "t50": {"op": "first_x", "of": "prog", "at_least": 0.5},
            "levels": {"op": "across", "of": ["pre", "late", 99], "stat": "max"},
        },
        "gates": {
            "t50_at_3": {"check": "abs_diff_at_most", "a": "t50", "b": 3.0, "max": 1e-9},
            "max_is_99": {"check": "equals", "value": "levels", "expected": 99},
        },
    }
    rep = evaluate(cfg, {"run.y": run})
    assert rep.passed, rep.to_json()


def test_from_y_scalar_is_one_sample():
    s = Series.from_y(True)
    assert len(s) == 1 and s.y.tolist() == [1.0] and s.xf.tolist() == [0.0]
