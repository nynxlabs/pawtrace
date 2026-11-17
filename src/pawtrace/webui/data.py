"""JSON views for ``pawtrace serve``, built on :mod:`pawtrace.views` (shared with the TUI).

Every call re-reads the plain files, so the page always shows what's on disk. Image paths
are never sent to the page as paths it can ask for: :class:`Files` hands out opaque keys
and the server serves only registered files.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from .. import rights as rts
from ..panel import Panel
from ..project import CONFIG_NAME, Project
from ..questions import status_of
from ..runs import RunStore, last_report
from ..spec import CANDIDATE, ITEM_STATUS, Paper
from ..views import latest_png, load_paper, paper_figure
from . import notes as nts

MAX_FIGURE = 64 * 1024 * 1024


class Files:
    """Opaque keys for the files a view shows (overlays, crops, notes images)."""

    def __init__(self) -> None:
        self._by_key: dict[str, Path] = {}
        self._lock = threading.Lock()

    def key(self, path: Path) -> str:
        p = Path(path).resolve()
        k = hashlib.sha256(str(p).encode()).hexdigest()[:20]
        with self._lock:
            self._by_key[k] = p
        return k

    def get(self, key: str) -> Path | None:
        with self._lock:
            return self._by_key.get(key)

    def url(self, path: Path) -> str:
        try:
            v = int(Path(path).stat().st_mtime)
        except OSError:
            v = 0
        return f"/f/{self.key(path)}?v={v}"


# ---------------------------------------------------------------------------
# Checkouts
# ---------------------------------------------------------------------------


def checkouts(root: Path) -> list[dict[str, Any]]:
    """The project's git worktrees that hold a pawtrace project (the main checkout first)."""
    root = root.resolve()
    out: list[dict[str, Any]] = []
    try:
        r = subprocess.run(["git", "-C", str(root), "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, timeout=10)
        text = r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        text = ""
    cur: dict[str, Any] = {}
    for line in text.splitlines() + [""]:
        if not line.strip():
            if cur.get("path"):
                out.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line[9:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
        elif line.startswith("HEAD "):
            cur["head"] = line[5:][:7]
        elif line == "detached":
            cur["branch"] = None
    rel = None
    if out:
        # the project may sit in a subfolder of the repo: keep the same subfolder in each worktree
        for wt in out:
            if root == Path(wt["path"]).resolve() or Path(wt["path"]).resolve() in root.parents:
                rel = root.relative_to(Path(wt["path"]).resolve())
                break
    res = []
    for wt in out:
        p = Path(wt["path"]) / (rel or "")
        if (p / CONFIG_NAME).is_file():
            res.append({"path": str(p), "branch": wt.get("branch"), "head": wt.get("head")})
    if not any(Path(c["path"]).resolve() == root for c in res):
        res.insert(0, {"path": str(root), "branch": None, "head": None})
    for i, c in enumerate(res):
        c["id"] = str(i)
        c["label"] = c["branch"] or Path(c["path"]).name
    return res


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


def rights_summary(spec: dict[str, Any]) -> dict[str, Any]:
    r = rts.rights_of(spec) or {}
    return {
        "license": r.get("license") or "unknown",
        "access": r.get("access") or "unknown",
        "ai_tools": rts.ai_tools(spec),
        "redistribute": rts.may_redistribute(spec),
        "open_version": r.get("open_version") or None,
        "copy_url": r.get("copy_url") or None,
    }


def _report(rep: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rep:
        return None
    gates = []
    for gid, g in (rep.get("gates") or {}).items():
        g = g or {}
        gates.append({"id": gid, "pass": bool(g.get("pass")), "required": bool(g.get("required", True)),
                      "check": g.get("check"), "inputs": g.get("inputs"), "why": g.get("why"), "error": g.get("error")})
    return {"pass": bool(rep.get("pass")), "n_passed": rep.get("n_passed"), "n_required": rep.get("n_required"),
            "gates": gates, "metrics": rep.get("metrics") or {}}


def home(project: Project) -> dict[str, Any]:
    store = RunStore(project.runs_dir)
    papers = []
    for pid in project.papers():
        pv = load_paper(project, pid, panels=False)
        paper = Paper(pid, project.paper_dir(pid))
        s = pv.summary or paper.summary()
        n_pass = n_fail = n_unchecked = 0
        pids = project.panel_ids(pid)
        for panel_id in pids:
            rec = store.latest(panel_id)
            rep = last_report(rec) if rec else None
            if rep is None:
                n_unchecked += 1
            elif rep.get("pass"):
                n_pass += 1
            else:
                n_fail += 1
        papers.append({
            "id": pid, "title": pv.title, "year": pv.year,
            "stages": [st.to_json() for st in pv.stages],
            "items": s.get("items", 0), "items_by_status": s.get("items_by_status", {}),
            "candidates": s.get("candidates", 0), "open_items": len(s.get("open_items") or []),
            "open_questions": len(s.get("open_questions") or []),
            "panels": {"n": len(pids), "pass": n_pass, "fail": n_fail, "unchecked": n_unchecked},
            "rights": rights_summary(paper.spec) if paper.spec else None,
        })
    return {
        "project": project.name, "profile": project.profile,
        "modules_on": [k for k, v in project.modules().items() if v],
        "papers": papers,
    }


def _item(i: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "kind", "status", "section", "text", "value", "units", "unit", "note", "plain", "quote", "where")
    out = {k: i.get(k) for k in keep if i.get(k) is not None}
    out.setdefault("status", "stated")
    return out


def paper_view(project: Project, paper_id: str, files: Files) -> dict[str, Any]:
    if paper_id not in project.papers():
        raise KeyError(f"no paper {paper_id!r}")
    pv = load_paper(project, paper_id)
    paper = Paper(paper_id, project.paper_dir(paper_id))
    spec = paper.spec
    never = rts.ai_tools(spec) == "never" if spec else False
    entries = [dict(e) for e in paper.entries]
    by_item: dict[str, list[str]] = {}
    for e in entries:
        if e.get("item"):
            by_item.setdefault(str(e["item"]), []).append(str(e.get("id")))
    items = []
    for i in paper.items:
        it = _item(i)
        it["entries"] = by_item.get(str(i.get("id")), [])
        items.append(it)
    questions = []
    for q in paper.questions:
        qq = {k: q.get(k) for k in ("id", "to", "question", "why", "items", "panels", "answer", "answer_source", "asked") if q.get(k) is not None}
        qq["status"] = status_of(q)
        questions.append(qq)
    panels = []
    for p in pv.panels:
        try:
            cfg = Panel.load(project, p.id).cfg
        except Exception:
            cfg = {}
        latest = p.latest or {}
        panels.append({
            "id": p.id, "title": p.title, "runs": p.runs,
            "latest": {k: latest.get(k) for k in ("id", "source", "created", "date", "config", "seed") if latest.get(k) is not None} or None,
            "report": _report(p.report),
            "overlay": files.url(p.ours_png) if p.ours_png else None,
            "overlay_name": p.ours_png.name if p.ours_png else None,
            "overlay_path": str(p.ours_png) if p.ours_png else None,
            "paper_figure": files.url(p.paper_png) if p.paper_png else None,
            "paper_figure_note": p.paper_png_note,
            "references": sorted((cfg.get("references") or {}).keys()),
        })
    statuses = {s: sum(1 for i in items if i["status"] == s) for s in (*ITEM_STATUS, CANDIDATE)}
    return {
        "id": paper_id, "title": pv.title, "year": pv.year,
        "authors": (spec.get("paper") or {}).get("authors") if spec else None,
        "stages": [st.to_json() for st in pv.stages],
        "rights": rights_summary(spec) if spec else None,
        "rights_lines": rts.render_lines(spec) if spec else [],
        "ai_never": never,
        "items": items, "statuses": statuses,
        "entries": entries,
        "questions": questions,
        "panels": panels,
        "lint": pv.lint,
        "summary": pv.summary,
    }


def _provenance(path: Path) -> dict[str, Any]:
    """What a reference file records about how it was made (curves-json ``meta``), plus a sidecar README."""
    out: dict[str, Any] = {"exists": path.is_file()}
    if path.is_file() and path.suffix.lower() == ".json" and path.stat().st_size < 16 * 1024 * 1024:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            doc = None
        meta = (doc or {}).get("meta") or {}
        dig = meta.get("digitization") or {}
        rev = meta.get("review") or {}
        out["series"] = {k: (v or {}).get("n") or len(((v or {}).get("xy") or {}).get("y") or []) for k, v in ((doc or {}).get("series") or {}).items()}
        if dig:
            out["method"] = dig.get("method")
            out["tool"] = dig.get("tool")
            out["by"] = dig.get("name") or dig.get("by")
            out["date"] = dig.get("date")
            out["assist_used"] = (dig.get("assist") or {}).get("used")
            out["image"] = (dig.get("image") or {}).get("file")
        if rev:
            out["reviewed_by"] = rev.get("name") or rev.get("reviewed_by")
            out["review_date"] = rev.get("date")
            out["review_complete"] = rev.get("complete", True)
            res = {}
            for name, r in ((rev.get("residual") or {}).get("series") or {}).items():
                res[name] = {k: v for k, v in (r or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
            out["residual"] = res
        out["history"] = len(meta.get("history") or [])
    readme = path.parent / "README.md"
    if readme.is_file() and readme.stat().st_size < 256 * 1024:
        out["readme"] = readme.read_text(errors="replace")
    return out


def figures(project: Project, roots: list[nts.Root], files: Files) -> dict[str, Any]:
    refs = []
    for pid in project.papers():
        spec = Paper(pid, project.paper_dir(pid)).spec
        never = rts.ai_tools(spec) == "never" if spec else False
        for panel_id in project.panel_ids(pid):
            try:
                panel = Panel.load(project, panel_id)
            except Exception as exc:
                refs.append({"panel": panel_id, "error": str(exc)})
                continue
            for name, ref in (panel.cfg.get("references") or {}).items():
                ref = ref or {}
                path = project.path(ref["path"]) if ref.get("path") else None
                fig = ref.get("figure") or panel.cfg.get("figure")
                refs.append({
                    "panel": panel_id, "paper": pid, "reference": name, "ai_never": never,
                    "path": ref.get("path"), "abspath": str(path) if path else None,
                    "format": ref.get("format"), "series": ref.get("series"),
                    "digitized_from": ref.get("digitized_from"),
                    "figure": fig if isinstance(fig, (dict, str)) else None,
                    "provenance": _provenance(path) if path else {"exists": False},
                })
    figs = nts.list_figures(roots)
    for f in figs:
        if f["size"] <= MAX_FIGURE:
            f["url"] = files.url(Path(f["path"]))
    readmes = []
    dirs_with_figs = {(f["root"], str(Path(f["rel"]).parent)) for f in figs}
    for root in roots:
        for p in nts.walk(root, {".md"}):
            if p.name.lower() != "readme.md":
                continue
            rel = root.rel(p)
            parent = str(Path(rel).parent)
            if any(r == root.id and (d == parent or d.startswith(parent + "/") or parent == ".") for r, d in dirs_with_figs) \
                    and "figure" in rel.lower():
                readmes.append({"root": root.id, "rel": rel, "title": nts.title_of(nts.read_md(p), rel)})
    return {"references": refs, "figures": figs, "readmes": readmes}


def stamp(project: Project, roots: list[nts.Root]) -> str:
    """Changes when anything the views read changes (newest mtime + file count)."""
    best = 0.0
    n = 0
    for base in (project.papers_dir, project.runs_dir, project.images_dir):
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            try:
                if p.is_file():
                    n += 1
                    best = max(best, p.stat().st_mtime)
            except OSError:
                pass
    try:
        best = max(best, project.config_path.stat().st_mtime)
    except OSError:
        pass
    best = max(best, nts.stamp(roots))
    return f"{best:.3f}:{n}"
