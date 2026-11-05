"""seed-stats module: Wilson intervals, per-seed / per-gate / metric stats, and the CLI.

Uses the small project from ``test_runs_cli``: the panel's window mean is ``a * (4.5 + seed)``
and its one gate is ``m >= 5``, so ``paper_only`` (a=1) fails at seed 0 and passes from seed 1.
"""
from __future__ import annotations

import pytest

from pawtrace import seedstats as ss
from test_runs_cli import call, jcall, make_project


def enable(capsys, root, name="seed-stats"):
    code, _ = jcall(capsys, root, "modules", "enable", name)
    assert code == 0


# -- intervals ---------------------------------------------------------------


def test_wilson_known_values():
    lo, hi = ss.wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4)
    assert hi == pytest.approx(0.7634, abs=1e-4)
    lo, hi = ss.wilson(10, 10)
    assert hi == 1.0
    assert lo == pytest.approx(0.7225, abs=1e-4)
    lo, hi = ss.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-4)


def test_wilson_empty_and_bad_input():
    assert ss.wilson(0, 0) is None
    with pytest.raises(ValueError):
        ss.wilson(3, 2)
    with pytest.raises(ValueError):
        ss.wilson(1, 2, confidence=1.0)


def test_wilson_narrows_with_more_seeds_and_lower_confidence():
    w = lambda k, n, c=0.95: (lambda ci: ci[1] - ci[0])(ss.wilson(k, n, c))  # noqa: E731
    assert w(8, 10) > w(80, 100)
    assert w(8, 10, 0.9) < w(8, 10, 0.99)


def test_seeds_for_halfwidth_is_minimal():
    n = ss.seeds_for_halfwidth(0.5, 0.1)
    assert ss._halfwidth(0.5, n, 0.95) <= 0.1 < ss._halfwidth(0.5, n - 1, 0.95)
    assert ss.seeds_for_halfwidth(1.0, 0.1) < n   # all-pass needs fewer seeds for the same width
    with pytest.raises(ValueError):
        ss.seeds_for_halfwidth(0.5, 0.0)


def test_summarize():
    s = ss.summarize([1.0, 2.0, 3.0, 10.0])
    assert s["n"] == 4 and s["mean"] == 4.0 and s["median"] == 2.5 and s["min"] == 1.0 and s["max"] == 10.0
    assert s["std"] == pytest.approx(4.0825, abs=1e-4)
    assert ss.summarize([2.0])["std"] is None
    assert ss.summarize([]) == {"n": 0}


# -- panel_stats on hand-made records ----------------------------------------------


def _rec(rid, seed, passed, *, config="adopted", gh="h1", created="2026-01-01T00:00:00", m=1.0, commit="c1", gates=None):
    gates = gates or {"g": {"pass": passed, "required": True}}
    return {
        "id": rid, "seed": seed, "source": "run", "config_name": config, "config_hash": "cfg", "created_at": created,
        "git": {"commit": commit},
        "checks": [{"gates_hash": gh, "report": {"pass": passed, "n_passed": int(passed), "n_required": 1, "gates": gates, "metrics": {"m": m, "curve": [1, 2]}}}],
    }


def test_panel_stats_dedupes_seeds_and_keeps_latest():
    recs = [
        _rec("a", 0, False, created="2026-01-01T00:00:00", m=1.0),
        _rec("b", 0, True, created="2026-01-02T00:00:00", m=3.0),   # same seed, newer: replaces a
        _rec("c", 1, True, m=5.0),
    ]
    st = ss.panel_stats("demo/p1", recs, gates_hash="h1")
    (g,) = st["configs"]
    assert g["pass"]["k"] == 2 and g["pass"]["n"] == 2
    assert g["superseded"] == ["a"]
    assert [s["run"] for s in g["seeds"]] == ["b", "c"]
    assert g["metrics"]["m"]["mean"] == 4.0
    assert "curve" not in g["metrics"]        # only scalar metrics are summarized


def test_panel_stats_skips_stale_and_unchecked():
    recs = [_rec("a", 0, True, gh="old"), _rec("b", 1, True), {**_rec("c", 2, True), "checks": []}]
    st = ss.panel_stats("demo/p1", recs, gates_hash="h1")
    assert st["stale"] == ["a"] and st["unchecked"] == ["c"]
    assert st["configs"][0]["pass"]["n"] == 1
    assert any("check demo/p1 --all" in h for h in st["hints"])
    st = ss.panel_stats("demo/p1", recs, gates_hash="h1", include_stale=True)
    assert st["configs"][0]["pass"]["n"] == 2
    assert any("different gates" in w for w in st["configs"][0]["warnings"])


def test_panel_stats_per_gate_rates_and_info_gates():
    gates_pass = {"g": {"pass": True, "required": True}, "h": {"pass": False, "required": False}}
    gates_fail = {"g": {"pass": False, "required": True}, "h": {"pass": True, "required": False}}
    recs = [_rec("a", 0, True, gates=gates_pass), _rec("b", 1, False, gates=gates_fail), _rec("c", 2, True, gates=gates_pass)]
    g = ss.panel_stats("demo/p1", recs, gates_hash="h1")["configs"][0]
    assert g["gates"]["g"]["k"] == 2 and g["gates"]["g"]["n"] == 3
    assert g["gates"]["h"]["required"] is False and g["gates"]["h"]["k"] == 1
    assert g["seeds"][1]["failing"] == ["g"]


def test_panel_stats_groups_by_config_and_warns_on_mixed_commits():
    recs = [_rec("a", 0, True, config="adopted", commit="c1"), _rec("b", 1, True, config="adopted", commit="c2"),
            _rec("c", 0, False, config="paper_only")]
    st = ss.panel_stats("demo/p1", recs, gates_hash="h1")
    assert [g["config"] for g in st["configs"]] == ["adopted", "paper_only"]
    assert any("2 commits" in w for w in st["configs"][0]["warnings"])
    only = ss.panel_stats("demo/p1", recs, gates_hash="h1", config="paper_only")
    assert [g["config"] for g in only["configs"]] == ["paper_only"]


def test_records_without_seed_each_count_once():
    recs = [{**_rec("a", None, True), "source": "import", "config_name": None}, {**_rec("b", None, False), "source": "import", "config_name": None}]
    g = ss.panel_stats("demo/p1", recs, gates_hash="h1")["configs"][0]
    assert g["config"] == ss.IMPORTED
    assert g["pass"]["n"] == 2
    assert any("no seed" in w for w in g["warnings"])


# -- CLI -------------------------------------------------------------------------


def test_cli_needs_module_on(tmp_path, capsys):
    root = make_project(tmp_path)
    code, out, err = call(capsys, "seed-stats", "--project", str(root))
    assert code == 2 and "seed-stats module is off" in err


def test_cli_pass_rates_per_config(tmp_path, capsys):
    root = make_project(tmp_path)
    enable(capsys, root)
    jcall(capsys, root, "run", "p1", "--config", "paper_only", "--seed", "0", "--seed", "1", "--seed", "2")
    jcall(capsys, root, "run", "p1", "--seed", "0", "--seed", "1")
    code, data = jcall(capsys, root, "seed-stats", "p1", "--target-halfwidth", "0.2")
    assert code == 0
    (st,) = data["panels"]
    groups = {g["config"]: g for g in st["configs"]}
    po = groups["paper_only"]
    assert (po["pass"]["k"], po["pass"]["n"]) == (2, 3)
    assert [(s["seed"], s["pass"]) for s in po["seeds"]] == [(0, False), (1, True), (2, True)]
    assert po["metrics"]["m"]["mean"] == pytest.approx(5.5)
    assert po["gates"]["g"]["k"] == 2
    assert po["guidance"]["seeds_needed"] > 3
    assert (groups["adopted"]["pass"]["k"], groups["adopted"]["pass"]["n"]) == (2, 2)

    code, out, _ = call(capsys, "seed-stats", "p1", "--project", str(root))
    assert code == 0
    assert "config paper_only: 2/3 seeds pass (67%" in out
    assert "seeds: 0:FAIL, 1:PASS, 2:PASS" in out


def test_cli_stale_after_gate_change_until_rechecked(tmp_path, capsys):
    root = make_project(tmp_path)
    enable(capsys, root)
    jcall(capsys, root, "run", "p1", "--config", "paper_only", "--seed", "0", "--seed", "1", "--seed", "2")
    panel = root / "papers" / "demo" / "panels" / "p1.yaml"
    panel.write_text(panel.read_text().replace("min: 5", "min: 6"))
    _, data = jcall(capsys, root, "seed-stats", "p1")
    st = data["panels"][0]
    assert len(st["stale"]) == 3 and st["configs"] == []
    _, data = jcall(capsys, root, "seed-stats", "p1", "--any-gates")
    assert data["panels"][0]["configs"][0]["pass"]["n"] == 3
    jcall(capsys, root, "check", "p1", "--all")
    _, data = jcall(capsys, root, "seed-stats", "p1")
    g = data["panels"][0]["configs"][0]
    assert (g["pass"]["k"], g["pass"]["n"]) == (1, 3)   # only seed 2 (mean 6.5) clears 6


def test_cli_bad_options(tmp_path, capsys):
    root = make_project(tmp_path)
    enable(capsys, root)
    code, _, err = call(capsys, "seed-stats", "--project", str(root), "--confidence", "1.5")
    assert code == 2 and "confidence" in err
    code, _, err = call(capsys, "seed-stats", "--project", str(root), "--target-halfwidth", "0.7")
    assert code == 2


def test_cli_no_runs(tmp_path, capsys):
    root = make_project(tmp_path)
    enable(capsys, root)
    code, out, _ = call(capsys, "seed-stats", "--project", str(root))
    assert code == 0 and "no checked runs" in out
