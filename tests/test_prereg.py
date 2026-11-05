"""preregistration module: freezing a panel's gates, config and reserved seeds, and checking
runs against the freeze (CLI + lint). Uses the project from ``test_runs_cli``."""
from __future__ import annotations

import yaml
import pytest

from pawtrace import prereg
from pawtrace.project import Project
from test_runs_cli import _git, call, jcall, make_project

PANEL = "papers/demo/panels/p1.yaml"


@pytest.fixture
def root(tmp_path, capsys):
    r = make_project(tmp_path)
    assert jcall(capsys, r, "modules", "enable", "preregistration")[0] == 0
    return r


def freeze(capsys, root, *extra):
    return jcall(capsys, root, "prereg", "freeze", "p1", *extra)


def problems(data):
    return [(p["level"], p["message"]) for r in data["panels"] for p in r["problems"]]


# -- seeds -----------------------------------------------------------------------


def test_parse_and_format_seeds():
    assert prereg.parse_seeds("100-103,110 7") == [7, 100, 101, 102, 103, 110]
    assert prereg.parse_seeds([3, "5-6"]) == [3, 5, 6]
    assert prereg.parse_seeds(4) == [4]
    assert prereg.format_seeds([1, 2, 3, 7, 9, 10]) == "1-3,7,9-10"
    assert prereg.format_seeds("100-119") == "100-119"
    for bad in ("", "5-3", "a-b", "-1"):
        with pytest.raises(prereg.PreregError):
            prereg.parse_seeds(bad)


# -- freeze ------------------------------------------------------------------------


def test_needs_module_on(tmp_path, capsys):
    r = make_project(tmp_path)
    code, _, err = call(capsys, "prereg", "check", "--project", str(r))
    assert code == 2 and "preregistration module is off" in err


def test_freeze_writes_the_record(root, capsys):
    code, data = freeze(capsys, root, "--seeds", "100-104")
    assert code == 0
    path = root / "papers" / "demo" / "prereg" / "p1.yaml"
    assert data["wrote"] == str(path)
    text = path.read_text()
    assert text.startswith("# preregistration for demo/p1: written by `pawtrace prereg freeze`; do not edit.")
    rec = yaml.safe_load(text)
    assert rec["panel"] == "demo/p1"
    assert rec["seeds"] == {"reserved": "100-104"}
    assert rec["config_name"] == "adopted" and rec["config"] == {"a": 2.0}
    assert rec["gate_config"]["gates"]["g"]["min"] == 5
    assert rec["gates_hash"] == prereg.gates_hash(rec["gate_config"])
    assert rec["git"]["commit"] and rec["history"] == []


def test_freeze_refuses_used_seeds_unless_forced(root, capsys):
    jcall(capsys, root, "run", "p1", "--seed", "3")
    code, data = freeze(capsys, root, "--seeds", "1-5")
    assert code == 2 and "already used" in data["error"] and "3" in data["error"]
    code, data = freeze(capsys, root, "--seeds", "1-5", "--force")
    assert code == 0 and data["prereg"]["forced_seeds"] == "3"


def test_freeze_refuses_ledger_tested_on_seeds(root, capsys):
    ledger = {
        "entries": [{"id": "dev.a", "item": "eq.1", "type": "deviation", "value": 2.0, "source": "deviation",
                     "reason": "made up", "status": "adopted", "evidence": ["x"], "tested_on": {"seeds": [101]}}],
        "questions": [],
    }
    (root / "papers/demo/ledger.yaml").write_text(yaml.safe_dump(ledger))
    code, data = freeze(capsys, root, "--seeds", "100-102")
    assert code == 2 and "ledger dev.a tested_on" in data["error"]
    code, data = freeze(capsys, root, "--seeds", "200-202")
    assert code == 0
    assert data["prereg"]["deviations"] == [{"id": "dev.a", "item": "eq.1", "value": 2.0, "source": "deviation"}]


def test_refreeze_needs_reason_and_keeps_history(root, capsys):
    freeze(capsys, root, "--seeds", "100-101")
    code, data = freeze(capsys, root, "--seeds", "102-103")
    assert code == 2 and "--reason" in data["error"]
    code, data = freeze(capsys, root, "--seeds", "102-103", "--reason", "made-up reason")
    assert code == 0
    rec = data["prereg"]
    assert rec["reason"] == "made-up reason"
    assert [h["seeds"]["reserved"] for h in rec["history"]] == ["100-101"]
    code, out, _ = call(capsys, "prereg", "show", "p1", "--project", str(root))
    assert code == 0 and "superseded:" in out and "made-up reason" in out


def test_freeze_unknown_config_or_missing_seeds(root, capsys):
    code, data = freeze(capsys, root, "--seeds", "100", "--config", "nope")
    assert code == 2 and "no config 'nope'" in data["error"]
    code, data = freeze(capsys, root)
    assert code == 2 and "--seeds" in data["error"]


# -- check -------------------------------------------------------------------------


def test_clean_final_runs_pass_the_check(root, capsys):
    jcall(capsys, root, "run", "p1", "--seed", "0")          # development run, before the freeze
    freeze(capsys, root, "--seeds", "100-102")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "prereg")
    jcall(capsys, root, "run", "p1", "--seed", "100", "--seed", "101")
    code, data = jcall(capsys, root, "prereg", "check")
    assert code == 0, problems(data)
    (res,) = data["panels"]
    assert [r["seed"] for r in res["final_runs"]] == [100, 101]
    assert all(r["pass"] for r in res["final_runs"])
    assert res["remaining_seeds"] == "102"
    assert problems(data) == []
    code, data = jcall(capsys, root, "lint")
    assert code == 0


def test_gate_change_after_freeze_is_an_error_in_check_and_lint(root, capsys):
    freeze(capsys, root, "--seeds", "100-101")
    p = root / PANEL
    p.write_text(p.read_text().replace("min: 5", "min: 4"))
    code, data = jcall(capsys, root, "prereg", "check", "p1")
    assert code == 1
    assert any(lvl == "error" and "gates changed since the preregistration" in m for lvl, m in problems(data))
    code, data = jcall(capsys, root, "lint")
    assert code == 1
    assert any("gates changed since the preregistration" in p["message"] for p in data["problems"])


def test_why_notes_dont_count_as_a_gate_change(root, capsys):
    freeze(capsys, root, "--seeds", "100-101")
    p = root / PANEL
    p.write_text(p.read_text().replace('why: "mean level is at least 5"', 'why: "reworded note"'))
    code, data = jcall(capsys, root, "prereg", "check", "p1")
    assert code == 0, problems(data)


def test_config_and_deviation_changes_are_errors(root, capsys):
    freeze(capsys, root, "--seeds", "100-101")
    p = root / PANEL
    p.write_text(p.read_text().replace("adopted: {a: 2.0}", "adopted: {a: 2.5}"))
    ledger = {
        "entries": [{"id": "dev.b", "item": "eq.1", "type": "deviation", "value": 1, "source": "deviation",
                     "reason": "made up", "status": "adopted", "evidence": ["x"], "tested_on": {"seeds": [9]}}],
        "questions": [],
    }
    (root / "papers/demo/ledger.yaml").write_text(yaml.safe_dump(ledger))
    _, data = jcall(capsys, root, "prereg", "check", "p1")
    msgs = [m for _, m in problems(data)]
    assert any("config 'adopted' changed" in m for m in msgs)
    assert any("adopted deviations changed" in m for m in msgs)


def test_reserved_seed_used_before_the_freeze(root, capsys):
    jcall(capsys, root, "run", "p1", "--seed", "100")
    freeze(capsys, root, "--seeds", "100-101", "--force")
    code, data = jcall(capsys, root, "prereg", "check", "p1")
    assert code == 1
    assert any("reserved seed 100 was used before the preregistration" in m for _, m in problems(data))


def test_outside_seeds_other_config_and_reuse(root, capsys):
    freeze(capsys, root, "--seeds", "100-101")
    jcall(capsys, root, "run", "p1", "--seed", "5")                                   # outside → warn
    jcall(capsys, root, "run", "p1", "--seed", "101", "--config", "paper_only")      # other config → error
    jcall(capsys, root, "run", "p1", "--seed", "100")
    jcall(capsys, root, "run", "p1", "--seed", "100", "--set", "a=2.0")             # same config hash → same id (rerun)
    (root / "panels_mod.py").write_text((root / "panels_mod.py").read_text() + "\n# edit\n")
    jcall(capsys, root, "run", "p1", "--seed", "100")                                 # dirty tree → a second final run
    code, data = jcall(capsys, root, "prereg", "check", "p1")
    assert code == 1
    msgs = problems(data)
    assert ("warn", "1 run(s) after the preregistration used seeds outside the reserved set (100-101) — they aren't final runs") in msgs
    assert any(lvl == "error" and "reserved seed 101 run with config 'paper_only'" in m for lvl, m in msgs)
    assert any(lvl == "warn" and "reserved seed 100 has 2 final runs" in m for lvl, m in msgs)
    assert any(lvl == "warn" and "dirty tree" in m for lvl, m in msgs)


def test_final_run_checked_with_other_gates(root, capsys):
    freeze(capsys, root, "--seeds", "100")
    jcall(capsys, root, "run", "p1", "--seed", "100")
    p = root / PANEL
    original = p.read_text()
    p.write_text(original.replace("min: 5", "min: 4"))
    jcall(capsys, root, "check", "p1")          # re-checks the latest run with the changed gates
    p.write_text(original)
    _, data = jcall(capsys, root, "prereg", "check", "p1")
    assert any("last checked with gates" in m for _, m in problems(data))


def test_hand_edited_file_is_caught(root, capsys):
    freeze(capsys, root, "--seeds", "100")
    f = root / "papers/demo/prereg/p1.yaml"
    f.write_text(f.read_text().replace("min: 5", "min: 4"))
    _, data = jcall(capsys, root, "prereg", "check", "p1")
    assert any("was edited" in m for _, m in problems(data))


def test_check_without_prereg_lists_nothing(root, capsys):
    code, out, _ = call(capsys, "prereg", "check", "--project", str(root))
    assert code == 0 and "no preregistered panels" in out
    code, data = jcall(capsys, root, "prereg", "check", "p1")
    assert data["panels"][0]["preregistered"] is False


def test_seed_stats_final_counts_only_final_runs(root, capsys):
    jcall(capsys, root, "modules", "enable", "seed-stats")
    jcall(capsys, root, "run", "p1", "--seed", "0", "--seed", "1")
    code, data = jcall(capsys, root, "seed-stats", "p1", "--final")
    assert code == 2 and "needs a preregistration" in data["error"]
    freeze(capsys, root, "--seeds", "100-102")
    jcall(capsys, root, "run", "p1", "--seed", "100", "--seed", "101", "--seed", "7")
    _, data = jcall(capsys, root, "seed-stats", "p1", "--final")
    (g,) = data["panels"][0]["configs"]
    assert [s["seed"] for s in g["seeds"]] == [100, 101]
    assert data["panels"][0]["hints"][0].startswith("final runs only: reserved seeds 100-102")


def test_final_records_filters(root):
    proj = Project.find(root)
    pr = {"seeds": {"reserved": "10-11"}, "frozen_at": "2026-01-02T00:00:00+00:00", "config_hash": "c"}
    base = {"source": "run", "config_hash": "c", "created_at": "2026-01-03T00:00:00+00:00"}
    recs = [
        {**base, "id": "ok", "seed": 10},
        {**base, "id": "early", "seed": 11, "created_at": "2026-01-01T00:00:00+00:00"},
        {**base, "id": "other-seed", "seed": 3},
        {**base, "id": "import", "seed": 10, "source": "import"},
        {**base, "id": "other-config", "seed": 11, "config_hash": "d"},
    ]
    assert [r["id"] for r in prereg.final_records(pr, recs)] == ["ok"]
    assert proj.name == "proj"
