"""Where each paper stands in the seven stages, worked out from the plain files.

Nothing here is stored: every state comes from ``spec.yaml``, ``ledger.yaml``,
the panel configs and the run records, so it can't drift from them. Used by
``pawtrace tui``; deterministic, so a CLI view can share it.

States: ``done``, ``active`` (started, something left), ``todo`` (not started),
``off`` (the stage's module is turned off in ``pawtrace.yaml``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from . import rights as rts
from .panel import Panel
from .project import Project
from .runs import RunStore, last_report
from .spec import Paper

STAGES = ("intake", "foundations", "gaps", "build", "run", "check", "audit")
STATES = ("done", "active", "todo", "off")


@dataclass
class Stage:
    name: str
    state: str
    note: str

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _rights_open(spec: dict[str, Any]) -> list[str]:
    """What's still unset in the rights block (empty when it's filled in)."""
    r = rts.rights_of(spec)
    if not r:
        return ["rights block"]
    out = []
    if str(r.get("license") or "unknown").lower() == "unknown":
        out.append("license")
    if str(r.get("access") or "unknown") == "unknown":
        out.append("access")
    if not str(r.get("open_version") or "").strip():
        out.append("open version")
    return out


def paper_stages(project: Project, paper_id: str, store: RunStore | None = None) -> list[Stage]:
    paper = Paper(paper_id, project.paper_dir(paper_id))
    modules = project.modules()
    store = store or RunStore(project.runs_dir)
    s = paper.summary()
    items = paper.items
    out: list[Stage] = []

    # 1 intake: spec with items, rights filled, policy set, candidates reviewed
    if not paper.spec:
        out.append(Stage("intake", "todo", "no spec.yaml"))
    else:
        left = _rights_open(paper.spec)
        if not paper.spec.get("policy"):
            left.append("code-use policy")
        real = [i for i in items if not str(i.get("id", "")).startswith("example.")]
        if not real:
            left.append("spec items")
        if s["candidates"]:
            left.append(f"{s['candidates']} candidates to review")
        note = f"{len(real)} items, ai_tools: {rts.ai_tools(paper.spec)}"
        out.append(Stage("intake", "active" if left else "done", note + ("; to do: " + ", ".join(left) if left else "")))

    # 2 foundations: foundation items, each with its own rights
    found = [i for i in items if i.get("kind") == "foundation"]
    if not modules.get("foundations"):
        out.append(Stage("foundations", "off", f"module off ({len(found)} foundation items)"))
    elif not found:
        out.append(Stage("foundations", "todo", "no foundation items"))
    else:
        no_rights = [i["id"] for i in found if not i.get("rights")]
        out.append(Stage("foundations", "active" if no_rights else "done",
                         f"{len(found)} foundations" + (f", {len(no_rights)} without rights" if no_rights else "")))

    # 3 gaps: every missing / ambiguous / contradicted item has a live ledger entry
    gaps = sum(s["items_by_status"].get(k, 0) for k in ("missing", "ambiguous", "contradicted"))
    open_items = s["open_items"]
    if not gaps:
        out.append(Stage("gaps", "done" if items else "todo", "no gaps in the spec" if items else "no spec items yet"))
    else:
        out.append(Stage("gaps", "active" if open_items else "done",
                         f"{gaps - len(open_items)}/{gaps} filled" + (f", open: {', '.join(open_items[:4])}" + ("…" if len(open_items) > 4 else "") if open_items else "")))

    # 4–6 per panel: contract, runs, latest verdicts
    pids = project.panel_ids(paper_id)
    with_entry = imported = runs = passed = checked = 0
    for pid in pids:
        try:
            panel = Panel.load(project, pid)
            cfg = panel.cfg
        except Exception:
            cfg = {}
        if (cfg.get("run") or {}).get("entry"):
            with_entry += 1
        elif cfg.get("import"):
            imported += 1
        rec = store.latest(pid)
        if rec:
            runs += 1
            rep = last_report(rec)
            if rep:
                checked += 1
                passed += bool(rep.get("pass"))
    n = len(pids)
    if not n:
        out += [Stage("build", "todo", "no panels"), Stage("run", "todo", "no panels"), Stage("check", "todo", "no panels")]
    else:
        build_note = f"{with_entry}/{n} panels with a contract" + (f", {imported} import-only" if imported else "")
        out.append(Stage("build", "done" if with_entry == n else "active" if with_entry or imported else "todo", build_note))
        out.append(Stage("run", "done" if runs == n else "active" if runs else "todo", f"{runs}/{n} panels have runs"))
        out.append(Stage("check", "done" if checked and passed == n else "active" if checked else "todo",
                         f"{passed}/{n} panels pass" + (f" ({checked} checked)" if checked != n else "")))

    # 7 audit: failing panels and open author questions
    failing = checked - passed
    qs = s["open_questions"]
    if not checked:
        out.append(Stage("audit", "todo", "nothing checked yet"))
    else:
        bits = []
        if failing:
            bits.append(f"{failing} failing panel{'s' if failing != 1 else ''} to trace")
        if qs:
            bits.append(f"{len(qs)} open author question{'s' if len(qs) != 1 else ''}")
        out.append(Stage("audit", "active" if bits else "done", ", ".join(bits) or "nothing open"))
    return out

