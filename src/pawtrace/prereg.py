"""preregistration module: freeze a panel's gates before its final runs.

``pawtrace prereg freeze <panel> --seeds 100-119`` writes
``papers/<paper>/prereg/<panel>.yaml`` — commit it before the final runs::

    panel, frozen_at, git: {commit, dirty}
    gates_hash, gate_config: {metrics, gates}      # what the final runs are judged by
    config_name, config_hash, config               # the run config the final runs use
    seeds: {reserved: "100-119"}                   # fresh seeds kept for the final runs
    deviations_hash, deviations: [{id, item, value, source}]   # adopted deviations at freeze time
    reason?, history: [{frozen_at, gates_hash, config_hash, seeds, deviations_hash, reason}]

The file is written by the tool, never by hand (``check`` recomputes the hash of the stored
gate config). Freezing again supersedes it only with ``--reason``; the old freeze moves to
``history``, so a later gate change is always visible with its reason.

Reserved seeds must be fresh: ``freeze`` refuses seeds that already appear in the panel's run
records or in a ledger entry's ``tested_on.seeds`` (``--force`` overrides, and says so in
the file). ``check`` (also part of ``pawtrace lint`` when the module is on) flags:

* errors — gates, the frozen config, or the adopted-deviation list changed since the freeze;
  a reserved seed used before the freeze; a final run checked with other gates or run with
  another config; the file edited by hand
* warnings — runs after the freeze on seeds outside the reserved set (not final runs);
  a reserved seed run more than once (pick one and say which); imported records on reserved
  seeds (an import can't show when it ran); final runs from a dirty tree or never checked
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .gates import gates_hash, validate
from .panel import Panel
from .project import Project
from .runs import RunStore, config_hash, git_state, now
from .spec import Paper

HEADER = (
    "# preregistration for {panel}: written by `pawtrace prereg freeze`; do not edit.\n"
    "# Commit it before the final runs. To supersede it, freeze again with --reason (the old one moves to history).\n"
)


class PreregError(ValueError):
    pass


# -- seeds -------------------------------------------------------------------


def parse_seeds(spec: str | int | list[Any]) -> list[int]:
    """``"100-119,130"`` / ``[1, 2]`` / ``7`` → sorted unique non-negative ints."""
    if isinstance(spec, int):
        parts: list[Any] = [spec]
    elif isinstance(spec, list):
        parts = spec
    else:
        parts = [p for p in re.split(r"[,\s]+", str(spec).strip()) if p]
    out: set[int] = set()
    for p in parts:
        if isinstance(p, int):
            if p < 0:
                raise PreregError(f"seed {p} is negative")
            out.add(p)
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", str(p))
        if not m:
            raise PreregError(f"bad seed spec {p!r} (use e.g. 100-119 or 100,101,105)")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        if b < a:
            raise PreregError(f"bad seed range {p!r} (end before start)")
        out.update(range(a, b + 1))
    if not out:
        raise PreregError("no seeds reserved")
    return sorted(out)


def format_seeds(seeds: str | list[int]) -> str:
    """Compact ranges: ``[1, 2, 3, 7]`` → ``"1-3,7"``."""
    xs = parse_seeds(seeds) if not isinstance(seeds, list) else sorted(set(seeds))
    runs: list[str] = []
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[j] + 1:
            j += 1
        runs.append(str(xs[i]) if i == j else f"{xs[i]}-{xs[j]}")
        i = j + 1
    return ",".join(runs)


# -- files -------------------------------------------------------------------


def path_for(project: Project, panel_id: str) -> Path:
    paper, panel = panel_id.split("/", 1)
    return project.paper_dir(paper) / "prereg" / f"{panel}.yaml"


def load(project: Project, panel_id: str) -> dict[str, Any] | None:
    p = path_for(project, panel_id)
    if not p.exists():
        return None
    obj = yaml.safe_load(p.read_text()) or {}
    if not isinstance(obj, dict):
        raise PreregError(f"{p}: expected a mapping")
    return obj


def _deviations(paper: Paper) -> list[dict[str, Any]]:
    return [
        {"id": e.get("id"), "item": e.get("item"), "value": e.get("value"), "source": e.get("source")}
        for e in paper.entries
        if e.get("type") == "deviation" and e.get("status") == "adopted"
    ]


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def used_seeds(project: Project, panel_id: str) -> dict[int, list[str]]:
    """Seeds already spent on this panel: ``{seed: [where]}`` (run records + ledger tested_on)."""
    used: dict[int, list[str]] = {}
    for r in RunStore(project.runs_dir).list(panel_id):
        if r.get("seed") is not None:
            used.setdefault(int(r["seed"]), []).append(f"run {r['id']}")
    paper = Paper(panel_id.split("/", 1)[0], project.paper_dir(panel_id.split("/", 1)[0]))
    for e in paper.entries:
        for s in ((e.get("tested_on") or {}).get("seeds") or []):
            try:
                used.setdefault(int(s), []).append(f"ledger {e.get('id')} tested_on")
            except (TypeError, ValueError):
                continue
    return used


def freeze(
    project: Project,
    panel_ref: str,
    seeds: str,
    *,
    config_name: str | None = None,
    reason: str | None = None,
    force: bool = False,
    when: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    panel = Panel.load(project, panel_ref)
    problems = validate(panel.gate_cfg())
    if problems:
        raise PreregError(f"{panel.id}: fix the gate config first: {'; '.join(problems)}")
    if not panel.gate_cfg()["gates"]:
        raise PreregError(f"{panel.id} has no gates to freeze")
    reserved = parse_seeds(seeds)

    cfgs = panel.configs()
    name = config_name or panel.default_config_name()
    if cfgs and name not in cfgs:
        raise PreregError(f"{panel.id}: no config {name!r} (have: {', '.join(cfgs)})")
    config = cfgs.get(name) if cfgs else None

    old = load(project, panel.id)
    if old is not None and not reason:
        raise PreregError(f"{panel.id} is already preregistered ({old.get('frozen_at')}); freezing again needs --reason")

    used = used_seeds(project, panel.id)
    clash = {s: used[s] for s in reserved if s in used}
    if clash and not force:
        shown = "; ".join(f"{s}: {', '.join(w[:2])}" for s, w in list(clash.items())[:5])
        raise PreregError(
            f"{len(clash)} reserved seed(s) are already used ({shown}) — pick fresh seeds, or --force (recorded in the file)"
        )

    paper = Paper(panel.paper, project.paper_dir(panel.paper))
    devs = _deviations(paper)
    gcfg = panel.gate_cfg()
    rec: dict[str, Any] = {
        "panel": panel.id,
        "frozen_at": when or now(),
        "git": {k: v for k, v in git_state(project.root).items() if k in ("commit", "dirty")},
        "gates_hash": gates_hash(gcfg),
        "gate_config": gcfg,
        "config_name": name,
        "config_hash": config_hash(config) if config is not None else None,
        "config": config,
        "seeds": {"reserved": format_seeds(reserved)},
        "deviations_hash": _hash(devs),
        "deviations": devs,
    }
    if clash:
        rec["forced_seeds"] = format_seeds(sorted(clash))
    if reason:
        rec["reason"] = reason
    history = list((old or {}).get("history") or [])
    if old is not None:
        history.append({k: old.get(k) for k in ("frozen_at", "gates_hash", "config_hash", "seeds", "deviations_hash", "reason") if k in old})
    rec["history"] = history

    out = path_for(project, panel.id)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".yaml.tmp")
    tmp.write_text(HEADER.format(panel=panel.id) + yaml.safe_dump(rec, sort_keys=False, allow_unicode=True))
    tmp.replace(out)
    return out, rec


# -- checking ----------------------------------------------------------------


def _ts(s: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def _latest_check(rec: dict[str, Any]) -> dict[str, Any] | None:
    checks = rec.get("checks") or []
    return checks[-1] if checks else None


def final_records(pr: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Records that count as final runs: run (not imported) on a reserved seed after the freeze,
    with the frozen config."""
    reserved = set(parse_seeds(pr["seeds"]["reserved"]))
    t0 = _ts(pr.get("frozen_at"))
    out = []
    for r in records:
        t = _ts(r.get("created_at"))
        if r.get("source") != "run" or r.get("seed") is None or int(r["seed"]) not in reserved:
            continue
        if t0 is None or t is None or t < t0:
            continue
        if pr.get("config_hash") is not None and r.get("config_hash") != pr["config_hash"]:
            continue
        out.append(r)
    return out


def check(project: Project, panel_id: str, pr: dict[str, Any] | None = None) -> dict[str, Any]:
    """Problems with a panel's preregistration and its runs, plus a summary of the final runs."""
    pr = pr if pr is not None else load(project, panel_id)
    problems: list[dict[str, str]] = []

    def add(level: str, msg: str) -> None:
        problems.append({"level": level, "panel": panel_id, "message": msg})

    if pr is None:
        return {"panel": panel_id, "preregistered": False, "problems": [], "final_runs": [], "remaining_seeds": None}

    frozen = pr.get("gates_hash")
    if gates_hash(pr.get("gate_config") or {}) != frozen:
        add("error", "preregistration file was edited: its gate_config doesn't match its gates_hash — restore it from git")
    try:
        reserved = parse_seeds(pr["seeds"]["reserved"])
    except (KeyError, TypeError, PreregError) as exc:
        add("error", f"preregistration has no valid seeds.reserved ({exc})")
        reserved = []
    t0 = _ts(pr.get("frozen_at"))
    if t0 is None:
        add("error", f"preregistration frozen_at {pr.get('frozen_at')!r} isn't an ISO timestamp")

    panel = Panel.load(project, panel_id)
    now_hash = panel.check_hash()
    if now_hash != frozen:
        add("error", f"gates changed since the preregistration ({frozen} → {now_hash}) — revert, or `pawtrace prereg freeze {panel_id} --reason …` to supersede it on the record")
    name = pr.get("config_name")
    cfgs = panel.configs()
    if pr.get("config_hash") is not None:
        if name not in cfgs:
            add("error", f"frozen config {name!r} is gone from the panel")
        elif config_hash(cfgs[name]) != pr["config_hash"]:
            add("error", f"config {name!r} changed since the preregistration")
    paper = Paper(panel.paper, project.paper_dir(panel.paper))
    if _hash(_deviations(paper)) != pr.get("deviations_hash"):
        add("error", "the adopted deviations changed since the preregistration — re-freeze with --reason, or revert the ledger")

    rset = set(reserved)
    records = RunStore(project.runs_dir).list(panel_id)
    final: dict[int, list[dict[str, Any]]] = {}
    outside = []
    for r in records:
        seed = r.get("seed")
        t = _ts(r.get("created_at"))
        after = t0 is not None and t is not None and t >= t0
        if seed is None or int(seed) not in rset:
            if after:
                outside.append(r["id"])
            continue
        seed = int(seed)
        if r.get("source") == "import":
            add("warn", f"run {r['id']}: imported record on reserved seed {seed} — an import can't show it ran after the freeze")
            continue
        if not after:
            add("error", f"run {r['id']}: reserved seed {seed} was used before the preregistration — it isn't fresh")
            continue
        if pr.get("config_hash") is not None and r.get("config_hash") != pr["config_hash"]:
            add("error", f"run {r['id']}: reserved seed {seed} run with config {r.get('config_name')!r} ({r.get('config_hash')}), not the frozen {name!r} — that seed is spent")
            continue
        chk = _latest_check(r)
        if chk is None:
            add("warn", f"final run {r['id']} was never checked")
        elif chk.get("gates_hash") != frozen:
            add("error", f"final run {r['id']}: last checked with gates {chk.get('gates_hash')}, preregistered {frozen}")
        if (r.get("git") or {}).get("dirty"):
            add("warn", f"final run {r['id']} ran from a dirty tree")
        final.setdefault(seed, []).append(r)
    for seed, rs in sorted(final.items()):
        if len(rs) > 1:
            add("warn", f"reserved seed {seed} has {len(rs)} final runs ({', '.join(r['id'] for r in rs)}) — report one and say which")
    if outside:
        add("warn", f"{len(outside)} run(s) after the preregistration used seeds outside the reserved set ({format_seeds(pr['seeds']['reserved']) if reserved else '?'}) — they aren't final runs")

    rows = []
    for seed, rs in sorted(final.items()):
        r = rs[-1]
        chk = _latest_check(r) or {}
        rep = chk.get("report") or {}
        rows.append({"seed": seed, "run": r["id"], "pass": rep.get("pass") if rep else None, "gates_hash": chk.get("gates_hash")})
    return {
        "panel": panel_id,
        "preregistered": True,
        "frozen_at": pr.get("frozen_at"),
        "gates_hash": frozen,
        "config_name": name,
        "reserved": format_seeds(reserved) if reserved else None,
        "final_runs": rows,
        "remaining_seeds": format_seeds([s for s in reserved if s not in final]) if len(final) < len(reserved) else "",
        "outside_reserved": outside,
        "problems": problems,
    }


def format_check(res: dict[str, Any]) -> str:
    if not res["preregistered"]:
        return f"{res['panel']}: not preregistered"
    n_pass = sum(1 for r in res["final_runs"] if r["pass"])
    lines = [
        f"{res['panel']}: preregistered {res['frozen_at']} · gates {res['gates_hash']} · config {res['config_name']} · reserved seeds {res['reserved']}",
        f"  final runs: {len(res['final_runs'])} ({n_pass} pass); seeds left: {res['remaining_seeds'] or 'none'}",
    ]
    lines += [f"  {p['level']:5} {p['message']}" for p in res["problems"]]
    return "\n".join(lines)
