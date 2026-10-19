"""Tests for pawtrace.modules (module registry) and pawtrace.project (config, paths, panel ids)."""
from __future__ import annotations

import re

from pathlib import Path

import pytest
import yaml

from pawtrace import modules as mod
from pawtrace.project import CONFIG_NAME, Project, ProjectError


# ---------------------------------------------------------------------------
# modules
# ---------------------------------------------------------------------------


def test_minimal_profile_turns_on_only_core():
    state = mod.resolve("minimal")
    core = {m.name for m in mod.MODULES.values() if m.core}
    assert core == {"spec", "panels", "runs", "gates", "overlays"}
    assert {name for name, on in state.items() if on} == core


def test_standard_profile_turns_on_its_modules_and_core():
    state = mod.resolve("standard")
    on = {name for name, v in state.items() if v}
    expected = {"foundations", "scaffolding", "seed-stats", "dependency-graph", "paper-tables"}
    assert expected <= on
    assert {"spec", "panels", "runs", "gates", "overlays"} <= on
    assert "sensitivity" not in on
    assert mod.missing_dependencies(state) == {}


def test_strict_profile_turns_on_everything():
    state = mod.resolve("strict")
    assert all(state.values())
    assert set(state) == set(mod.MODULES)


def test_core_module_cannot_be_disabled():
    with pytest.raises(mod.ModuleError, match="core"):
        mod.resolve("minimal", {"spec": False})
    with pytest.raises(mod.ModuleError, match="core"):
        mod.resolve("strict", {"gates": False})


def test_core_module_may_be_restated_as_on():
    assert mod.resolve("minimal", {"runs": True})["runs"] is True


def test_unknown_profile_is_an_error():
    with pytest.raises(mod.ModuleError, match="unknown profile 'nope'"):
        mod.resolve("nope")


def test_unknown_module_override_is_an_error():
    with pytest.raises(mod.ModuleError, match="unknown module 'no-such-module'"):
        mod.resolve("minimal", {"no-such-module": True})


def test_override_turns_optional_module_on_and_off():
    assert mod.resolve("minimal", {"sensitivity": True})["sensitivity"] is True
    assert mod.resolve("strict", {"sensitivity": False})["sensitivity"] is False


def test_missing_dependencies_reports_paper_tables_without_seed_stats():
    state = mod.resolve("minimal", {"paper-tables": True})
    assert mod.missing_dependencies(state) == {"paper-tables": ["seed-stats"]}


def test_missing_dependencies_empty_when_dependency_on():
    state = mod.resolve("minimal", {"paper-tables": True, "seed-stats": True})
    assert mod.missing_dependencies(state) == {}


def test_missing_dependencies_ignores_disabled_modules():
    # paper-tables needs seed-stats, but paper-tables itself is off, so nothing is reported.
    assert mod.missing_dependencies(mod.resolve("minimal")) == {}


def test_with_dependencies_orders_dependencies_first():
    assert mod.with_dependencies("paper-tables") == ["seed-stats", "paper-tables"]


def test_with_dependencies_is_transitive():
    # scaffolding -> foundations; dependency-graph -> foundations.
    assert mod.with_dependencies("dependency-graph") == ["foundations", "dependency-graph"]
    assert mod.with_dependencies("scaffolding") == ["foundations", "scaffolding"]


def test_with_dependencies_of_leaf_module_is_just_itself():
    assert mod.with_dependencies("sensitivity") == ["sensitivity"]


def test_with_dependencies_unknown_module_is_an_error():
    with pytest.raises(mod.ModuleError):
        mod.with_dependencies("no-such-module")


# ---------------------------------------------------------------------------
# project: discovery
# ---------------------------------------------------------------------------


def _write_cfg(root: Path, text: str = "pawtrace: 1\nprofile: minimal\n") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_NAME).write_text(text)
    return root


def test_find_walks_up_from_a_subdirectory(tmp_path):
    root = _write_cfg(tmp_path / "proj")
    deep = root / "papers" / "x" / "panels"
    deep.mkdir(parents=True)
    proj = Project.find(deep)
    assert proj.root == root.resolve()
    assert proj.profile == "minimal"


def test_find_accepts_a_file_path_and_uses_its_directory(tmp_path):
    root = _write_cfg(tmp_path / "proj")
    f = root / "papers" / "spec.yaml"
    f.parent.mkdir(parents=True)
    f.write_text("paper: {}\n")
    assert Project.find(f).root == root.resolve()


def test_find_without_config_is_an_error(tmp_path):
    empty = tmp_path / "bare"
    empty.mkdir()
    with pytest.raises(ProjectError, match="pawtrace.yaml|init"):
        Project.find(empty)


def test_load_rejects_non_mapping_config(tmp_path):
    (tmp_path / CONFIG_NAME).write_text("- just\n- a list\n")
    with pytest.raises(ProjectError, match="expected a mapping"):
        Project.load(tmp_path / CONFIG_NAME)


def test_name_defaults_to_directory_name(tmp_path):
    root = _write_cfg(tmp_path / "my-study")
    assert Project.find(root).name == "my-study"


def test_default_directories_are_under_root(tmp_path):
    root = _write_cfg(tmp_path / "proj")
    proj = Project.find(root)
    assert proj.papers_dir == root.resolve() / "papers"
    assert proj.runs_dir == root.resolve() / "runs"
    assert proj.status_path == root.resolve() / "STATUS.md"


# ---------------------------------------------------------------------------
# project: ${var} and ~ expansion
# ---------------------------------------------------------------------------


def test_expand_uses_paths_vars(tmp_path):
    root = _write_cfg(tmp_path / "proj", "pawtrace: 1\npaths:\n  vars: {data: /srv/data}\n  papers: '${data}/papers'\n")
    proj = Project.find(root)
    assert proj.papers_dir == Path("/srv/data/papers")


def test_expand_var_can_reference_another_var(tmp_path):
    root = _write_cfg(
        tmp_path / "proj",
        "pawtrace: 1\npaths:\n  vars: {base: /srv, data: '${base}/data'}\n  runs: '${data}/runs'\n",
    )
    assert Project.find(root).runs_dir == Path("/srv/data/runs")


def test_expand_falls_back_to_prefixed_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWTRACE_VAR_RAWDATA", "/env/prefixed")
    root = _write_cfg(tmp_path / "proj", "pawtrace: 1\npaths:\n  papers: '${rawdata}/papers'\n")
    assert Project.find(root).papers_dir == Path("/env/prefixed/papers")


def test_expand_falls_back_to_plain_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWTRACE_TEST_PLAIN", "/env/plain")
    root = _write_cfg(tmp_path / "proj")
    proj = Project.find(root)
    assert proj.expand("${PAWTRACE_TEST_PLAIN}/x") == "/env/plain/x"


def test_paths_vars_win_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PAWTRACE_VAR_DATA", "/from/env")
    root = _write_cfg(tmp_path / "proj", "pawtrace: 1\npaths:\n  vars: {data: /from/config}\n")
    assert Project.find(root).expand("${data}") == "/from/config"


def test_unknown_var_raises_with_the_name(tmp_path, monkeypatch):
    monkeypatch.delenv("PAWTRACE_VAR_NOPE_NOT_SET", raising=False)
    monkeypatch.delenv("NOPE_NOT_SET", raising=False)
    root = _write_cfg(tmp_path / "proj")
    proj = Project.find(root)
    with pytest.raises(ProjectError, match=r"unknown path variable \$\{NOPE_NOT_SET\}"):
        proj.expand("${NOPE_NOT_SET}/x")


def test_tilde_expands_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = _write_cfg(tmp_path / "proj")
    assert Project.find(root).expand("~/data") == str(tmp_path / "home" / "data")


def test_relative_path_is_resolved_against_root(tmp_path):
    root = _write_cfg(tmp_path / "proj")
    proj = Project.find(root)
    assert proj.path("figures/x.png") == root.resolve() / "figures" / "x.png"


# ---------------------------------------------------------------------------
# project: set_module (edits pawtrace.yaml in place)
# ---------------------------------------------------------------------------

INIT_STYLE = """\
# pawtrace project config — the record of which checks were on.
pawtrace: 1
name: demo
profile: minimal   # minimal | standard | strict
modules: {}   # per-module overrides on top of the profile, e.g. sensitivity: true
paths:
  papers: papers
  runs: runs          # gitignore this
  status: STATUS.md   # generated by `pawtrace status --write`
  images: figures
  vars: {}          # ${name} substitutions for paths
"""


def test_set_module_into_modules_empty_flow_mapping(tmp_path):
    root = _write_cfg(tmp_path / "proj", INIT_STYLE)
    proj = Project.find(root)
    proj.set_module("seed-stats", True)
    text = (root / CONFIG_NAME).read_text()
    assert re.search(r"^modules:  # per-module overrides[^\n]*\n  seed-stats: true$", text, re.M)
    assert proj.modules()["seed-stats"] is True
    assert yaml.safe_load(text)["modules"] == {"seed-stats": True}


def test_set_module_keeps_comments_on_other_lines(tmp_path):
    root = _write_cfg(tmp_path / "proj", INIT_STYLE)
    Project.find(root).set_module("sensitivity", True)
    text = (root / CONFIG_NAME).read_text()
    for comment in (
        "# pawtrace project config — the record of which checks were on.",
        "# minimal | standard | strict",
        "# gitignore this",
        "# generated by `pawtrace status --write`",
        "# ${name} substitutions for paths",
    ):
        assert comment in text, comment


def test_set_module_keeps_trailing_comment_on_rewritten_line(tmp_path):
    root = _write_cfg(tmp_path / "proj", INIT_STYLE)
    Project.find(root).set_module("sensitivity", True)
    assert "# per-module overrides on top of the profile" in (root / CONFIG_NAME).read_text()


def test_set_module_when_no_modules_key_appends_block(tmp_path):
    root = _write_cfg(tmp_path / "proj", "pawtrace: 1\nprofile: minimal\npaths:\n  runs: runs\n")
    proj = Project.find(root)
    proj.set_module("seed-stats", True)
    text = (root / CONFIG_NAME).read_text()
    assert text.rstrip().endswith("modules:\n  seed-stats: true")
    assert "paths:\n  runs: runs" in text
    assert proj.modules()["seed-stats"] is True


def test_set_module_into_existing_block_updates_in_place(tmp_path):
    text = "pawtrace: 1\nprofile: minimal\nmodules:\n  seed-stats: false\n  sensitivity: true\npaths:\n  runs: runs\n"
    root = _write_cfg(tmp_path / "proj", text)
    proj = Project.find(root)
    proj.set_module("seed-stats", True)
    out = (root / CONFIG_NAME).read_text()
    assert out.count("modules:") == 1
    assert "  seed-stats: true\n  sensitivity: true\n" in out
    assert proj.modules()["seed-stats"] is True
    assert proj.modules()["sensitivity"] is True


def test_set_module_adds_new_entry_to_existing_block(tmp_path):
    text = "pawtrace: 1\nprofile: minimal\nmodules:\n  sensitivity: true\npaths:\n  runs: runs\n"
    root = _write_cfg(tmp_path / "proj", text)
    proj = Project.find(root)
    proj.set_module("seed-stats", True)
    out = (root / CONFIG_NAME).read_text()
    assert out.count("modules:") == 1
    assert proj.modules()["seed-stats"] is True
    assert proj.modules()["sensitivity"] is True
    assert "paths:\n  runs: runs" in out


def test_set_module_can_turn_a_module_back_off(tmp_path):
    root = _write_cfg(tmp_path / "proj", "pawtrace: 1\nprofile: strict\nmodules:\n  sensitivity: true\n")
    proj = Project.find(root)
    proj.set_module("sensitivity", False)
    assert proj.modules()["sensitivity"] is False
    assert "sensitivity: false" in (root / CONFIG_NAME).read_text()


def test_set_module_rejects_core_and_unknown(tmp_path):
    root = _write_cfg(tmp_path / "proj", INIT_STYLE)
    proj = Project.find(root)
    with pytest.raises(mod.ModuleError, match="core"):
        proj.set_module("spec", False)
    with pytest.raises(mod.ModuleError, match="unknown module"):
        proj.set_module("nope", True)
    # nothing was written
    assert (root / CONFIG_NAME).read_text() == INIT_STYLE


def test_set_module_keeps_existing_flow_mapping_entries(tmp_path):
    root = _write_cfg(tmp_path / "proj", "pawtrace: 1\nprofile: minimal\nmodules: {sensitivity: true}\n")
    proj = Project.find(root)
    proj.set_module("seed-stats", True)
    assert proj.modules()["sensitivity"] is True
    assert proj.modules()["seed-stats"] is True


# ---------------------------------------------------------------------------
# project: panel ids
# ---------------------------------------------------------------------------


def _panels_project(tmp_path: Path) -> Project:
    root = _write_cfg(tmp_path / "proj")
    for paper, panels in {"alpha": ["fig1"], "beta": ["fig1", "fig2"]}.items():
        d = root / "papers" / paper / "panels"
        d.mkdir(parents=True)
        for p in panels:
            (d / f"{p}.yaml").write_text("panel: x\n")
    return Project.find(root)


def test_resolve_panel_id_accepts_full_id(tmp_path):
    proj = _panels_project(tmp_path)
    assert proj.resolve_panel_id("alpha/fig1") == "alpha/fig1"
    assert proj.resolve_panel_id("beta/fig1") == "beta/fig1"


def test_resolve_panel_id_accepts_bare_unique_id(tmp_path):
    proj = _panels_project(tmp_path)
    assert proj.resolve_panel_id("fig2") == "beta/fig2"


def test_resolve_panel_id_ambiguous_bare_id_is_an_error(tmp_path):
    proj = _panels_project(tmp_path)
    with pytest.raises(ProjectError, match="ambiguous"):
        proj.resolve_panel_id("fig1")


def test_resolve_panel_id_unknown_lists_known_ids(tmp_path):
    proj = _panels_project(tmp_path)
    with pytest.raises(ProjectError, match="no panel 'nope'.*alpha/fig1"):
        proj.resolve_panel_id("nope")


def test_panel_path_maps_id_to_yaml(tmp_path):
    proj = _panels_project(tmp_path)
    assert proj.panel_path("beta/fig2") == proj.root / "papers" / "beta" / "panels" / "fig2.yaml"
