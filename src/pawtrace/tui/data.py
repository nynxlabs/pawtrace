"""What the TUI shows, read from the same files the CLI uses (no state of its own)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import rights as rts
from ..panel import Panel
from ..project import Project
from ..runs import RunStore, last_report
from ..spec import Paper
from ..stages import Stage, paper_stages


@dataclass
class PanelView:
    id: str
    title: str
    runs: int
    latest: dict[str, Any] | None
    report: dict[str, Any] | None
    ours_png: Path | None
    paper_png: Path | None
    paper_png_note: str

    @property
    def verdict(self) -> bool | None:
        return bool(self.report["pass"]) if self.report else None

    @property
    def score(self) -> str:
        if not self.report:
            return "—"
        return f"{self.report.get('n_passed', '?')}/{self.report.get('n_required', '?')}"


@dataclass
class PaperView:
    id: str
    title: str
    year: Any
    stages: list[Stage]
    panels: list[PanelView] = field(default_factory=list)
    spec: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    lint: list[dict[str, str]] = field(default_factory=list)
    source_dir: Path | None = None


def _version_key(p: Path) -> int:
    m = re.search(r"_v(\d+)$", p.stem)
    return int(m.group(1)) if m else -1


def latest_png(directory: Path, stem: str = "replication") -> Path | None:
    """Newest ``<stem>_vN.png`` that ``pawtrace overlay`` wrote, if any."""
    if not directory.is_dir():
        return None
    hits = sorted(directory.glob(f"{stem}_v*.png"), key=_version_key)
    return hits[-1] if hits else None


def paper_figure(project: Project, panel: Panel, spec: dict[str, Any]) -> tuple[Path | None, str]:
    """The paper's own crop for a panel, from the local extract folder, and a rights note.

    Crops live in ``papers/<paper>/source/`` (gitignored) and only ever show on
    this machine. ``digitized_from`` on a reference wins; then ``fig-<panel>``,
    then the whole figure ``fig-<number>``.
    """
    src = project.paper_dir(panel.paper) / rts.SOURCE_DIR
    lic = (rts.rights_of(spec) or {}).get("license") or "unknown"
    share = "license allows sharing" if rts.may_redistribute(spec) else f"local copy only ({lic})"
    cands: list[Path] = []
    for ref in (panel.cfg.get("references") or {}).values():
        df = str((ref or {}).get("digitized_from") or "").split()
        if df:
            cands.append(src / df[-1])
    pid = panel.id.split("/", 1)[1]
    cands += [src / "figures" / f"fig-{pid}.png"]
    m = re.match(r"(\d+)", pid)
    if m:
        cands.append(src / "figures" / f"fig-{m.group(1)}.png")
    for c in cands:
        if c.is_file():
            return c, f"{c.relative_to(project.paper_dir(panel.paper))} · {share}"
    if not src.is_dir():
        return None, "no extraction yet: `pawtrace extract run " + panel.paper + "` puts the paper's figures here"
    return None, f"no crop for {pid} in {src.name}/figures"


def load_panel(project: Project, pid: str, spec: dict[str, Any], store: RunStore) -> PanelView:
    try:
        panel = Panel.load(project, pid)
        title = panel.title
    except Exception as exc:
        return PanelView(pid, str(exc), 0, None, None, None, None, "")
    recs = store.list(pid)
    rec = recs[-1] if recs else None
    paper_png, note = paper_figure(project, panel, spec)
    paper, pnl = pid.split("/", 1)
    return PanelView(
        id=pid,
        title=title,
        runs=len(recs),
        latest=rec,
        report=last_report(rec) if rec else None,
        ours_png=latest_png(project.images_dir / paper / pnl),
        paper_png=paper_png,
        paper_png_note=note,
    )


def load_paper(project: Project, paper_id: str, *, panels: bool = True) -> PaperView:
    store = RunStore(project.runs_dir)
    paper = Paper(paper_id, project.paper_dir(paper_id))
    meta = paper.spec.get("paper") or {}
    pv = PaperView(
        id=paper_id,
        title=str(meta.get("title") or ""),
        year=meta.get("year") or "",
        stages=paper_stages(project, paper_id, store),
        spec=paper.spec,
        summary=paper.summary() if paper.spec else {},
    )
    if panels:
        pv.panels = [load_panel(project, pid, paper.spec, store) for pid in project.panel_ids(paper_id)]
        pv.lint = paper.lint()
        src = project.paper_dir(paper_id) / rts.SOURCE_DIR
        pv.source_dir = src if src.is_dir() else None
    return pv


def extraction(source_dir: Path | None) -> dict[str, Any] | None:
    if not source_dir:
        return None
    f = source_dir / "manifest.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None
