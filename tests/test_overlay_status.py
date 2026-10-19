"""Tests for pawtrace.overlay (plots, versioned filenames) and pawtrace.status."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pawtrace.data import PanelData, Series
from pawtrace.overlay import next_versioned, plot_panel
from pawtrace.panel import Panel
from pawtrace.project import Project
from pawtrace.runs import RunStore
from pawtrace.status import collect, render

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

SPEC = """\
paper:
  title: Demo paper
policy:
  when_silent: [convention]
items:
  - id: eq1
    kind: equation
    status: stated
    section: "2.1"
    text: "y = x + 1"
  - id: p_beta
    kind: parameter
    status: missing
    text: "beta is not given"
"""

PANEL = """\
title: Demo panel
claim: trace rises
references:
  paper:
    path: ref.json
    series: s
import:
  format: json-fields
  series:
    y: {y: trace}
metrics:
  m: {op: window_mean, of: run.y}
gates:
  g: {check: at_least, value: m, min: 0, why: test}
"""


@pytest.fixture
def project(tmp_path: Path) -> Project:
    (tmp_path / "pawtrace.yaml").write_text("name: demo\nprofile: minimal\n")
    paper = tmp_path / "papers" / "demo"
    (paper / "panels").mkdir(parents=True)
    (paper / "spec.yaml").write_text(SPEC)
    (paper / "panels" / "p1.yaml").write_text(PANEL)
    (tmp_path / "ref.json").write_text(
        json.dumps({"series": {"s": {"n": 3, "xy": {"x": [0, 1, 2], "y": [1, 2, 3]}}}})
    )
    (tmp_path / "artifact.json").write_text(json.dumps({"trace": [1, 2, 3]}))
    return Project.load(tmp_path / "pawtrace.yaml")


@pytest.fixture
def panel(project: Project) -> Panel:
    return Panel.load(project, "demo/p1")


def test_next_versioned_empty_dir(tmp_path: Path) -> None:
    d = tmp_path / "figs"
    assert next_versioned(d, "trace") == (d / "trace_v1.png", 1)  # directory doesn't exist yet
    d.mkdir()
    assert next_versioned(d, "trace") == (d / "trace_v1.png", 1)


def test_next_versioned_skips_existing(tmp_path: Path) -> None:
    d = tmp_path / "figs"
    d.mkdir()
    (d / "trace_v1.png").write_bytes(b"a")
    (d / "trace_v3.png").write_bytes(b"c")
    (d / "other_v9.png").write_bytes(b"x")  # different stem, ignored
    path, n = next_versioned(d, "trace")
    assert (path, n) == (d / "trace_v4.png", 4)
    assert not path.exists()


def test_plot_numeric_with_reference_and_report(project: Project, panel: Panel, tmp_path: Path) -> None:
    data = PanelData({"y": Series.from_y([1, 2, 3])})
    report = panel.check(data).to_json()
    assert report["pass"] is True

    out, _ = next_versioned(tmp_path / "figs", "p1")
    res = plot_panel(panel, data, out, report=report)
    assert out.exists() and out.read_bytes()[:8] == PNG_MAGIC
    assert res["out"] == str(out)
    assert res["series"] == ["y"]
    assert res["references"] == ["paper"]
    assert res["warnings"] == []

    # a second plot goes to v2 and leaves v1 untouched
    before = out.read_bytes()
    out2, n = next_versioned(tmp_path / "figs", "p1")
    assert n == 2
    plot_panel(panel, data, out2, report=report)
    assert out2.exists() and out.read_bytes() == before


def test_plot_reference_status_caption_and_missing_names(panel: Panel, tmp_path: Path) -> None:
    panel.cfg["references"]["paper"]["status"] = "approximate"
    panel.cfg["overlay"] = {"series": ["y", "nope"], "references": ["paper", "ghost"]}
    data = PanelData({"y": Series.from_y([1, 2, 3])})
    res = plot_panel(panel, data, tmp_path / "figs" / "caption_v1.png")
    assert res["series"] == ["y"]
    assert res["references"] == ["paper"]
    assert any("nope" in w for w in res["warnings"])
    assert any("ghost" in w for w in res["warnings"])
    assert (tmp_path / "figs" / "caption_v1.png").read_bytes()[:8] == PNG_MAGIC


def test_plot_categorical_bars_without_references(panel: Panel, tmp_path: Path) -> None:
    panel.cfg["overlay"] = {"references": []}
    data = PanelData({"bars": Series(x=["a", "b"], y=[1, 2], yerr=[0.1, 0.2], kind="bars")})
    out = tmp_path / "bars_v1.png"
    res = plot_panel(panel, data, out)
    assert out.read_bytes()[:8] == PNG_MAGIC
    assert res["series"] == ["bars"]
    assert res["references"] == []
    assert res["warnings"] == []


def test_status_after_import_and_render_is_stable(project: Project, panel: Panel, tmp_path: Path) -> None:
    src = tmp_path / "artifact.json"
    data, info = panel.import_artifact(src)
    store = RunStore(project.runs_dir)
    rec = store.record_import(panel.id, data, source=src, info=info)
    rec = store.add_check(rec, panel.check(data))

    st = collect(project)
    assert st["project"] == "demo"
    assert st["profile"] == "minimal"
    assert "spec" in st["modules_on"]
    (paper,) = st["papers"]
    assert paper["items"] == 2
    assert paper["open_items"] == ["p_beta"]
    (p,) = st["panels"]
    assert p["panel"] == "demo/p1"
    assert p["title"] == "Demo panel"
    assert p["runs"] == 1
    assert p["latest_run"] == rec["id"]
    assert p["latest_source"] == "import"
    assert p["pass"] is True
    assert (p["n_passed"], p["n_required"]) == (1, 1)
    assert p["failing"] == []

    text = render(project)
    assert text.startswith("<!-- generated by `pawtrace status`; do not edit")
    assert text.splitlines()[1] == ""
    assert "# demo status" in text
    assert "demo/p1" in text
    assert "PASS" in text
    assert render(project) == text
