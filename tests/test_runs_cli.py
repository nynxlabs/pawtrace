"""Run records, the panel contract, and the `pawtrace` CLI end to end.

Each test builds a small project in ``tmp_path``: a git repo with one paper (``demo``) and one
panel (``p1``) whose panel function is ``panels_mod.py:make``. The panel returns
``y_i = a * (i + seed)`` for ``i`` in 0..9, so the window mean is ``a * (4.5 + seed)``:

* ``adopted`` (a=2.0): mean 9 + 2*seed  -> gate ``at_least 5`` passes for seed 0 and 1
* ``paper_only`` (a=1.0): mean 4.5 + seed -> fails at seed 0, passes at seed 1
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from pawtrace import cli
from pawtrace.runs import RunStore, config_hash

PANELS_MOD = """\
def make(config, seed):
    return {"y": [config.get("a", 1.0) * (i + seed) for i in range(10)]}
"""

PANEL_P1 = """\
panel: p1
title: "Panel one"
claim: "y grows with a"
spec_items: [eq.1]
run:
  entry: "panels_mod.py:make"
  pythonpath: ["."]
  configs:
    paper_only: {a: 1.0}
    adopted: {a: 2.0}
import:
  format: json-fields
  series:
    y: {y: y}
  seed: seed
  commit: commit
metrics:
  m: {op: window_mean, of: run.y}
gates:
  g: {check: at_least, value: m, min: 5, why: "mean level is at least 5"}
"""

SPEC = {
    "paper": {"id": "demo", "title": "Demo", "authors": [], "year": 2024, "doi": "", "version": "published"},
    "policy": {
        "when_silent": ["authors-code", "convention", "author-question", "deviation"],
        "authors_code": {"url": "", "license": "unknown", "use": "reference-only"},
        "off_limits": [],
    },
    "items": [{"id": "eq.1", "kind": "equation", "text": "y = a (i + seed)", "section": "§1", "status": "stated"}],
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def make_project(tmp_path: Path, name: str = "proj") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "pawtrace.yaml").write_text("pawtrace: 1\nname: proj\nprofile: minimal\nmodules: {}\n")
    pdir = root / "papers" / "demo"
    (pdir / "panels").mkdir(parents=True)
    (pdir / "spec.yaml").write_text(yaml.safe_dump(SPEC, sort_keys=False))
    (pdir / "ledger.yaml").write_text("entries: []\nquestions: []\n")
    (pdir / "panels" / "p1.yaml").write_text(PANEL_P1)
    (root / "panels_mod.py").write_text(PANELS_MOD)
    (root / ".gitignore").write_text("runs/\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test Runner")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def call(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = cli.main(list(argv))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def jcall(capsys: pytest.CaptureFixture[str], root: Path, *argv: str) -> tuple[int, Any]:
    code, out, _ = call(capsys, *argv, "--project", str(root), "--json")
    return code, json.loads(out)


def commit_of(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").strip()


def run_files(root: Path, panel: str = "demo/p1") -> list[Path]:
    return sorted((root / "runs" / panel).glob("*.json"))


# ---------------------------------------------------------------------------
# the panel contract: run, pass/fail, exit codes
# ---------------------------------------------------------------------------


def test_run_adopted_passes_and_exits_zero(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "run", "p1")
    assert code == 0
    assert data["panel"] == "demo/p1"
    (run,) = data["runs"]
    assert run["seed"] == 0
    assert run["report"]["pass"] is True
    assert run["report"]["metrics"]["m"] == pytest.approx(9.0)
    rec = json.loads(RunStore(root / "runs").path("demo/p1", run["run"]).read_text())
    assert rec["config_name"] == "adopted"
    assert rec["config"] == {"a": 2.0}
    assert rec["source"] == "run"
    assert len(rec["checks"]) == 1


def test_run_paper_only_fails_and_exits_one(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "run", "p1", "--config", "paper_only")
    assert code == 1
    rep = data["runs"][0]["report"]
    assert rep["pass"] is False
    assert rep["n_passed"] == 0 and rep["n_required"] == 1
    assert rep["metrics"]["m"] == pytest.approx(4.5)
    assert rep["gates"]["g"]["pass"] is False


def test_run_bare_panel_id_and_full_id_both_work(tmp_path, capsys):
    root = make_project(tmp_path)
    code1, d1 = jcall(capsys, root, "run", "demo/p1")
    code2, d2 = jcall(capsys, root, "run", "p1")
    assert code1 == code2 == 0
    assert d1["runs"][0]["run"] == d2["runs"][0]["run"]


def test_run_multiple_seeds_records_each(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "run", "p1", "--config", "adopted", "--seed", "0", "--seed", "1")
    assert code == 0
    seeds = [r["seed"] for r in data["runs"]]
    assert seeds == [0, 1]
    ids = [r["run"] for r in data["runs"]]
    assert len(set(ids)) == 2
    assert ids[0].endswith("-s0") and ids[1].endswith("-s1")
    assert len(run_files(root)) == 2


def test_run_seed_defaults_to_zero(tmp_path, capsys):
    root = make_project(tmp_path)
    _, data = jcall(capsys, root, "run", "p1")
    assert [r["seed"] for r in data["runs"]] == [0]


def test_run_set_overrides_config_and_changes_run_id(tmp_path, capsys):
    root = make_project(tmp_path)
    _, base = jcall(capsys, root, "run", "p1", "--config", "adopted")
    code, over = jcall(capsys, root, "run", "p1", "--config", "adopted", "--set", "a=0.5")
    assert code == 1  # 0.5 * 4.5 = 2.25 < 5
    assert over["runs"][0]["report"]["metrics"]["m"] == pytest.approx(2.25)
    assert over["runs"][0]["run"] != base["runs"][0]["run"]


def test_run_unknown_config_is_bad_input(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "run", "p1", "--config", "nope")
    assert code == 2
    assert "no config 'nope'" in data["error"]
    assert data["type"] == "PanelError"


def test_run_text_output_has_verdict_line(tmp_path, capsys):
    root = make_project(tmp_path)
    code, out, _ = call(capsys, "run", "p1", "--project", str(root))
    assert code == 0
    assert out.splitlines()[0].startswith("demo/p1 @ ")
    assert "PASS (1/1 gates)" in out.splitlines()[0]
    assert "ok   g: at_least" in out


# ---------------------------------------------------------------------------
# run ids
# ---------------------------------------------------------------------------


def test_same_commit_config_seed_gives_same_run_id(tmp_path, capsys):
    root = make_project(tmp_path)
    _, first = jcall(capsys, root, "run", "p1", "--config", "adopted")
    _, second = jcall(capsys, root, "run", "p1", "--config", "adopted")
    assert first["runs"][0]["run"] == second["runs"][0]["run"]
    assert len(run_files(root)) == 1


def test_run_id_format_is_commit_confighash_seed(tmp_path, capsys):
    root = make_project(tmp_path)
    _, data = jcall(capsys, root, "run", "p1", "--config", "adopted")
    rid = data["runs"][0]["run"]
    expected = f"{commit_of(root)[:12]}-{config_hash({'a': 2.0})}-s0"
    assert rid == expected


def test_different_config_gives_different_run_id(tmp_path, capsys):
    root = make_project(tmp_path)
    _, a = jcall(capsys, root, "run", "p1", "--config", "adopted")
    _, b = jcall(capsys, root, "run", "p1", "--config", "paper_only")
    assert a["runs"][0]["run"] != b["runs"][0]["run"]


def test_dirty_tree_adds_plus_d_suffix(tmp_path, capsys):
    root = make_project(tmp_path)
    _, clean = jcall(capsys, root, "run", "p1", "--config", "adopted")
    (root / "panels_mod.py").write_text(PANELS_MOD + "\n# local edit\n")
    _, dirty = jcall(capsys, root, "run", "p1", "--config", "adopted")
    rid = dirty["runs"][0]["run"]
    assert re.fullmatch(r"[0-9a-f]{12}-[0-9a-f]{8}-s0\+d[0-9a-f]{8}", rid)
    assert rid != clean["runs"][0]["run"]
    rec = json.loads(RunStore(root / "runs").path("demo/p1", rid).read_text())
    assert rec["git"]["dirty"] is True
    assert len(rec["git"]["diff_sha256"]) == 64


def test_dirty_tree_same_edit_same_id_different_edit_new_id(tmp_path, capsys):
    root = make_project(tmp_path)
    (root / "panels_mod.py").write_text(PANELS_MOD + "\n# edit one\n")
    _, one = jcall(capsys, root, "run", "p1", "--config", "adopted")
    _, one_again = jcall(capsys, root, "run", "p1", "--config", "adopted")
    assert one["runs"][0]["run"] == one_again["runs"][0]["run"]
    (root / "panels_mod.py").write_text(PANELS_MOD + "\n# edit two\n")
    _, two = jcall(capsys, root, "run", "p1", "--config", "adopted")
    assert two["runs"][0]["run"] != one["runs"][0]["run"]


def test_untracked_files_do_not_mark_tree_dirty(tmp_path, capsys):
    root = make_project(tmp_path)
    (root / "scratch_notes.txt").write_text("not committed\n")
    _, data = jcall(capsys, root, "run", "p1", "--config", "adopted")
    rid = data["runs"][0]["run"]
    assert "+d" not in rid


# ---------------------------------------------------------------------------
# runs, check, panels
# ---------------------------------------------------------------------------


def _seed_runs(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """adopted s0, s1 (pass, pass); paper_only s0, s1 (fail, pass)."""
    assert jcall(capsys, root, "run", "p1", "--config", "adopted", "--seed", "0", "--seed", "1")[0] == 0
    assert jcall(capsys, root, "run", "p1", "--config", "paper_only", "--seed", "0", "--seed", "1")[0] == 1


def test_runs_lists_every_record(tmp_path, capsys):
    root = make_project(tmp_path)
    _seed_runs(root, capsys)
    code, data = jcall(capsys, root, "runs", "p1")
    assert code == 0
    assert data["panel"] == "demo/p1"
    assert len(data["runs"]) == 4
    assert {(r["config"], r["seed"], r["pass"]) for r in data["runs"]} == {
        ("adopted", 0, True),
        ("adopted", 1, True),
        ("paper_only", 0, False),
        ("paper_only", 1, True),
    }
    assert all(r["source"] == "run" for r in data["runs"])
    assert {r["gates"] for r in data["runs"] if r["pass"]} == {"1/1"}
    assert {r["gates"] for r in data["runs"] if not r["pass"]} == {"0/1"}


def test_runs_empty_panel(tmp_path, capsys):
    root = make_project(tmp_path)
    code, out, _ = call(capsys, "runs", "p1", "--project", str(root))
    assert code == 0
    assert out.strip() == "no runs"


def test_check_all_summarises_pass_counts(tmp_path, capsys):
    root = make_project(tmp_path)
    _seed_runs(root, capsys)
    code, data = jcall(capsys, root, "check", "p1", "--all")
    assert code == 1  # one run fails
    assert data["summary"] == {"runs": 4, "pass": 3, "per_gate_pass": {"g": 3}}
    assert len(data["runs"]) == 4


def test_check_all_all_passing_exits_zero(tmp_path, capsys):
    root = make_project(tmp_path)
    assert jcall(capsys, root, "run", "p1", "--config", "adopted", "--seed", "0", "--seed", "1")[0] == 0
    code, data = jcall(capsys, root, "check", "p1", "--all")
    assert code == 0
    assert data["summary"] == {"runs": 2, "pass": 2, "per_gate_pass": {"g": 2}}


def test_check_run_by_id_prefix(tmp_path, capsys):
    root = make_project(tmp_path)
    _seed_runs(root, capsys)
    _, runs = jcall(capsys, root, "runs", "p1")
    target = next(r for r in runs["runs"] if r["config"] == "paper_only" and r["seed"] == 0)
    code, data = jcall(capsys, root, "check", "p1", "--run", target["run"])
    assert code == 1
    assert [r["run"] for r in data["runs"]] == [target["run"]]
    # the paper_only s0 and s1 records share everything but the seed suffix, so a prefix
    # that stops before "-s" is ambiguous and must not silently pick one
    ambiguous = target["run"].rsplit("-s", 1)[0]
    code2, data2 = jcall(capsys, root, "check", "p1", "--run", ambiguous)
    assert code2 == 2
    assert "no single run" in data2["error"]
    # a prefix that only matches one record works
    code3, data3 = jcall(capsys, root, "check", "p1", "--run", target["run"][:-1] + "0")
    assert code3 == 1
    assert data3["runs"][0]["run"] == target["run"]


def test_check_unknown_run_id_is_bad_input(tmp_path, capsys):
    root = make_project(tmp_path)
    _seed_runs(root, capsys)
    code, data = jcall(capsys, root, "check", "p1", "--run", "zzzz")
    assert code == 2
    assert "no single run" in data["error"]


def test_check_without_runs_is_bad_input(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "check", "p1")
    assert code == 2
    assert "no runs for demo/p1" in data["error"]


def test_check_saves_result_to_record(tmp_path, capsys):
    root = make_project(tmp_path)
    _seed_runs(root, capsys)
    jcall(capsys, root, "check", "p1", "--all")
    jcall(capsys, root, "check", "p1", "--all", "--no-save")
    recs = [json.loads(p.read_text()) for p in run_files(root)]
    # run once, then one check appended by `run`, one by `check --all`
    assert all(len(r["checks"]) == 2 for r in recs)


def test_check_data_file_is_not_recorded(tmp_path, capsys):
    root = make_project(tmp_path)
    artifact = root / "artifact.json"
    artifact.write_text(json.dumps({"y": [2 * i for i in range(10)]}))
    before = len(run_files(root))
    code, data = jcall(capsys, root, "check", "p1", "--data", str(artifact))
    assert code == 0
    assert data["runs"][0]["run"] == f"file:{artifact}"
    assert data["runs"][0]["report"]["pass"] is True
    assert len(run_files(root)) == before


def test_panels_lists_latest_verdict(tmp_path, capsys):
    root = make_project(tmp_path)
    code0, data0 = jcall(capsys, root, "panels")
    assert code0 == 0
    assert data0["panels"] == [
        {"panel": "demo/p1", "title": "Panel one", "runs": 0, "latest": None, "pass": None},
    ]
    jcall(capsys, root, "run", "p1", "--config", "paper_only")
    _, data1 = jcall(capsys, root, "panels")
    row = data1["panels"][0]
    assert row["runs"] == 1
    assert row["pass"] is False
    assert row["latest"]


def test_panels_latest_is_the_most_recent_run(tmp_path, capsys):
    """`latest` must be the run made last, even if two runs land in the same second."""
    root = make_project(tmp_path)
    jcall(capsys, root, "run", "p1", "--config", "adopted")
    jcall(capsys, root, "run", "p1", "--config", "paper_only")  # made last: fails
    _, data = jcall(capsys, root, "panels")
    assert data["panels"][0]["pass"] is False
    _, checked = jcall(capsys, root, "check", "p1")
    assert checked["runs"][0]["report"]["pass"] is False


def test_check_default_uses_latest(tmp_path, capsys):
    root = make_project(tmp_path)
    jcall(capsys, root, "run", "p1", "--config", "adopted")
    _, data = jcall(capsys, root, "check", "p1")
    assert data["runs"][0]["run"] == jcall(capsys, root, "runs", "p1")[1]["runs"][-1]["run"]


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def _artifact(path: Path) -> Path:
    path.write_text(json.dumps({"seed": 3, "commit": "deadbeef", "y": [2 * i for i in range(10)]}))
    return path


def test_import_same_file_twice_gives_one_record(tmp_path, capsys):
    root = make_project(tmp_path)
    art = _artifact(tmp_path / "saved_run.json")
    sha12 = hashlib.sha256(art.read_bytes()).hexdigest()[:12]
    code1, d1 = jcall(capsys, root, "import", "p1", str(art))
    code2, d2 = jcall(capsys, root, "import", "p1", str(art))
    assert code1 == code2 == 0
    assert d1["imported"][0]["run"] == d2["imported"][0]["run"] == f"imp-{sha12}"
    files = run_files(root)
    assert [p.name for p in files] == [f"imp-{sha12}.json"]
    rec = json.loads(files[0].read_text())
    assert rec["source"] == "import"
    assert rec["seed"] == 3
    assert rec["git"]["commit"] == "deadbeef"
    assert rec["config"] is None
    assert rec["imported_sha256"] == hashlib.sha256(art.read_bytes()).hexdigest()
    _, listed = jcall(capsys, root, "runs", "p1")
    assert len(listed["runs"]) == 1
    assert listed["runs"][0]["source"] == "import"


def test_import_passes_and_reports_through_gates(tmp_path, capsys):
    root = make_project(tmp_path)
    art = _artifact(tmp_path / "saved_run.json")
    code, data = jcall(capsys, root, "import", "p1", str(art))
    assert code == 0
    assert data["imported"][0]["report"]["pass"] is True
    assert data["imported"][0]["report"]["metrics"]["m"] == pytest.approx(9.0)


def test_import_no_check_skips_gates(tmp_path, capsys):
    root = make_project(tmp_path)
    art = _artifact(tmp_path / "saved_run.json")
    code, out, _ = call(capsys, "import", "p1", str(art), "--no-check", "--project", str(root))
    assert code == 0
    assert out.strip().startswith("imported ")
    rec = json.loads(run_files(root)[0].read_text())
    assert rec["checks"] == []


def test_import_failing_artifact_exits_one(tmp_path, capsys):
    root = make_project(tmp_path)
    art = tmp_path / "small.json"
    art.write_text(json.dumps({"y": [0.5, 0.5, 0.5]}))
    code, data = jcall(capsys, root, "import", "p1", str(art))
    assert code == 1
    assert data["imported"][0]["report"]["pass"] is False


# ---------------------------------------------------------------------------
# status, lint, modules, gates, playbook, init, error handling
# ---------------------------------------------------------------------------


def test_status_text_and_json(tmp_path, capsys):
    root = make_project(tmp_path)
    jcall(capsys, root, "run", "p1", "--config", "paper_only")
    code, out, _ = call(capsys, "status", "--project", str(root))
    assert code == 0
    assert "## Panels" in out
    assert "| demo/p1 | Panel one |" in out
    assert "FAIL" in out
    code2, data = jcall(capsys, root, "status")
    assert code2 == 0
    assert data["project"] == "proj"
    assert data["profile"] == "minimal"
    assert data["papers"][0]["paper"] == "demo"
    assert data["panels"][0]["panel"] == "demo/p1"
    assert data["panels"][0]["pass"] is False
    assert data["panels"][0]["failing"] == ["g"]


def test_status_write_creates_generated_file_and_is_idempotent(tmp_path, capsys):
    root = make_project(tmp_path)
    jcall(capsys, root, "run", "p1", "--config", "adopted")
    code, data = jcall(capsys, root, "status", "--write")
    assert code == 0
    target = root / "STATUS.md"
    assert Path(data["wrote"]) == target.resolve()
    first = target.read_text()
    assert first.startswith("<!-- generated")
    call(capsys, "status", "--write", "--project", str(root))
    assert target.read_text() == first


def test_lint_clean_project_exits_zero(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "lint")
    assert code == 0
    assert data["errors"] == 0
    # the panel has no digitized references, which is only a warning
    assert any("no digitized references" in p["message"] for p in data["problems"])
    assert all(p["level"] == "warn" for p in data["problems"])


def test_lint_reports_broken_gate_as_error(tmp_path, capsys):
    root = make_project(tmp_path)
    bad = PANEL_P1.replace("check: at_least", "check: no_such_check")
    (root / "papers" / "demo" / "panels" / "p1.yaml").write_text(bad)
    code, data = jcall(capsys, root, "lint")
    assert code == 1
    assert data["errors"] >= 1
    assert any(p["level"] == "error" and "unknown check 'no_such_check'" in p["message"] for p in data["problems"])


def test_lint_reports_paper_without_policy(tmp_path, capsys):
    root = make_project(tmp_path)
    spec = dict(SPEC)
    del spec["policy"]
    (root / "papers" / "demo" / "spec.yaml").write_text(yaml.safe_dump(spec))
    code, data = jcall(capsys, root, "lint", "demo")
    assert code == 1
    assert any("no policy" in p["message"] for p in data["problems"])


def test_modules_enable_with_deps_turns_both_on(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "modules", "enable", "paper-tables", "--with-deps")
    assert code == 0
    by_name = {m["module"]: m for m in data["modules"]}
    assert by_name["seed-stats"]["on"] is True
    assert by_name["paper-tables"]["on"] is True
    assert by_name["spec"]["core"] is True and by_name["spec"]["on"] is True
    assert by_name["sensitivity"]["on"] is False
    assert data["missing_dependencies"] == {}
    cfg = yaml.safe_load((root / "pawtrace.yaml").read_text())
    assert cfg["modules"] == {"seed-stats": True, "paper-tables": True}


def test_modules_enable_without_deps_reports_missing(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "modules", "enable", "paper-tables")
    assert code == 1
    assert data["missing_dependencies"] == {"paper-tables": ["seed-stats"]}
    code2, _ = jcall(capsys, root, "modules", "enable", "seed-stats")
    assert code2 == 0


def test_modules_list_json_shape(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "modules")
    assert code == 0
    assert data["profile"] == "minimal"
    names = {m["module"] for m in data["modules"]}
    assert {"spec", "panels", "runs", "gates", "overlays", "seed-stats", "paper-tables"} <= names
    row = next(m for m in data["modules"] if m["module"] == "paper-tables")
    assert row["depends"] == ["seed-stats"]
    assert row["implemented"] is False


def test_modules_enable_core_is_a_quiet_no_op(tmp_path, capsys):
    root = make_project(tmp_path)
    before = (root / "pawtrace.yaml").read_text()
    code, data = jcall(capsys, root, "modules", "enable", "runs")
    assert code == 0
    assert (root / "pawtrace.yaml").read_text() == before


def test_modules_enable_unknown_is_bad_input(tmp_path, capsys):
    root = make_project(tmp_path)
    code, data = jcall(capsys, root, "modules", "enable", "no-such-module")
    assert code == 2
    assert "unknown module" in data["error"]


def test_gates_lists_ops_and_checks(tmp_path, capsys):
    code, data = jcall(capsys, tmp_path, "gates")  # no project needed
    assert code == 0
    assert data["ops"] and data["checks"]
    for row in data["ops"] + data["checks"]:
        assert set(row) == {"name", "refs", "doc"}
    assert "window_mean" in {r["name"] for r in data["ops"]}
    assert "at_least" in {r["name"] for r in data["checks"]}


def test_playbook_listing(tmp_path, capsys):
    code, data = jcall(capsys, tmp_path, "playbook")
    assert code == 0
    assert [s["stage"] for s in data["stages"]] == ["intake", "foundations", "gaps", "build", "run", "check", "audit"]
    assert all(s["file"] == f"{s['stage']}.md" for s in data["stages"])


def test_playbook_unknown_stage_is_bad_input(tmp_path, capsys):
    code, data = jcall(capsys, tmp_path, "playbook", "nonsense")
    assert code == 2
    assert "unknown stage" in data["error"]


def test_init_scaffolds_project_and_paper(tmp_path, capsys):
    target = (tmp_path / "fresh").resolve()
    code, data = jcall(capsys, tmp_path, "init", str(target), "--paper", "demo")
    assert code == 0
    assert (target / "pawtrace.yaml").is_file()
    spec_p = target / "papers" / "demo" / "spec.yaml"
    ledger_p = target / "papers" / "demo" / "ledger.yaml"
    panel_p = target / "papers" / "demo" / "panels" / "example.yaml"
    assert spec_p.is_file() and ledger_p.is_file() and panel_p.is_file()
    assert "id: demo" in spec_p.read_text()
    assert "runs/" in (target / ".gitignore").read_text()
    assert str(spec_p) in data["created"]
    assert str(panel_p) in data["created"]
    cfg = yaml.safe_load((target / "pawtrace.yaml").read_text())
    assert cfg["profile"] == "minimal"


def test_init_twice_is_bad_input(tmp_path, capsys):
    target = tmp_path / "fresh"
    assert cli.main(["init", str(target)]) == 0
    capsys.readouterr()
    code, out, err = call(capsys, "init", str(target))
    assert code == 2
    assert "already exists" in err


def test_init_unknown_profile_is_bad_input(tmp_path, capsys):
    code, out, err = call(capsys, "init", str(tmp_path / "x"), "--profile", "huge")
    assert code == 2
    assert "unknown profile" in err


def test_unknown_panel_exits_two_with_json_error(tmp_path, capsys):
    root = make_project(tmp_path)
    code, out, err = call(capsys, "check", "nosuch", "--project", str(root), "--json")
    assert code == 2
    data = json.loads(out)
    assert "error" in data
    assert "no panel 'nosuch'" in data["error"]
    assert data["type"] == "ProjectError"
    assert err == ""


def test_error_without_json_goes_to_stderr_as_text(tmp_path, capsys):
    root = make_project(tmp_path)
    code, out, err = call(capsys, "check", "nosuch", "--project", str(root))
    assert code == 2
    assert out == ""
    assert err.startswith("pawtrace: no panel 'nosuch'")


def test_no_project_found_exits_two(tmp_path, capsys, monkeypatch):
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(bare)
    code, out, err = call(capsys, "panels")
    assert code == 2
    assert "no pawtrace.yaml" in err


def test_project_is_found_from_cwd_without_flag(tmp_path, capsys, monkeypatch):
    root = make_project(tmp_path)
    sub = root / "papers" / "demo" / "panels"
    monkeypatch.chdir(sub)
    code, out, _ = call(capsys, "panels", "--json")
    assert code == 0
    assert json.loads(out)["panels"][0]["panel"] == "demo/p1"
