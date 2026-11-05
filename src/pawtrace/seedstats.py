"""seed-stats module: pass rates across seeds, with confidence intervals.

Reads only run records (``runs/<paper>/<panel>/*.json``) and the latest check stored on
each. Nothing is re-run or re-checked here: a record whose latest check used other gates
than the panel has now (a different ``gates_hash``) is counted as *stale* and left out,
with a hint to re-check (``pawtrace check <panel> --all``). Pass ``include_stale`` to
count it anyway.

Per panel and config (``config_name``; imports without one group as ``(imported)``):

* per seed: the verdict of the latest record for that seed (older records for the same
  seed are counted as superseded, never double-counted)
* the panel pass rate k/n with a Wilson score interval
* per gate: pass rate k/n with a Wilson interval
* per metric (the scalar values the gates measured): n, mean, sample std, min, median, max
* guidance: the interval's half-width, and how many seeds would bring it under a target

Warnings say when a group mixes commits or config hashes, or has records without a seed.
Pure Python: no scipy.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from statistics import NormalDist
from typing import Any, Iterable

IMPORTED = "(imported)"


def z_for(confidence: float) -> float:
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    return NormalDist().inv_cdf(0.5 + confidence / 2)


def wilson(k: int, n: int, confidence: float = 0.95) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials (``None`` when n == 0)."""
    if n <= 0:
        return None
    if not 0 <= k <= n:
        raise ValueError(f"need 0 <= k <= n, got k={k}, n={n}")
    z = z_for(confidence)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if k == 0 else max(0.0, centre - half)   # exact at the ends (no float dust)
    hi = 1.0 if k == n else min(1.0, centre + half)
    return lo, hi


def seeds_for_halfwidth(p: float, halfwidth: float, confidence: float = 0.95, max_n: int = 100_000) -> int | None:
    """Smallest n whose Wilson interval at rate ``p`` has half-width <= ``halfwidth``."""
    if not 0 < halfwidth < 0.5:
        raise ValueError(f"halfwidth must be in (0, 0.5), got {halfwidth}")
    p = min(max(p, 0.0), 1.0)
    lo, hi = 1, max_n
    if _halfwidth(p, hi, confidence) > halfwidth:
        return None

    while lo < hi:  # half-width shrinks monotonically in n for a fixed rate
        mid = (lo + hi) // 2
        if _halfwidth(p, mid, confidence) <= halfwidth:
            hi = mid
        else:
            lo = mid + 1
    return lo


def _halfwidth(p: float, n: int, confidence: float) -> float:
    z = z_for(confidence)
    return z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)


def _rate(k: int, n: int, confidence: float) -> dict[str, Any]:
    ci = wilson(k, n, confidence)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci": list(ci) if ci else None}


def _is_scalar(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def summarize(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "std": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def _latest_check(rec: dict[str, Any]) -> dict[str, Any] | None:
    checks = rec.get("checks") or []
    return checks[-1] if checks else None


def panel_stats(
    panel_id: str,
    records: Iterable[dict[str, Any]],
    *,
    gates_hash: str | None,
    config: str | None = None,
    include_stale: bool = False,
    confidence: float = 0.95,
    target_halfwidth: float | None = None,
) -> dict[str, Any]:
    """Seed statistics for one panel from its run records.

    ``gates_hash`` is the panel's current one; ``None`` accepts any check (no staleness test).
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unchecked: list[str] = []
    stale: list[str] = []
    for rec in records:
        name = rec.get("config_name") or (IMPORTED if rec.get("source") == "import" else "(none)")
        if config is not None and name != config:
            continue
        chk = _latest_check(rec)
        if chk is None:
            unchecked.append(rec["id"])
            continue
        if gates_hash is not None and chk.get("gates_hash") != gates_hash and not include_stale:
            stale.append(rec["id"])
            continue
        groups[name].append(rec)

    out_groups = []
    for name in sorted(groups):
        out_groups.append(_group(name, groups[name], confidence, target_halfwidth))
    hints = []
    if stale:
        hints.append(f"{len(stale)} record(s) were last checked with other gates — `pawtrace check {panel_id} --all` re-checks them")
    if unchecked:
        hints.append(f"{len(unchecked)} record(s) were never checked — `pawtrace check {panel_id} --all`")
    return {
        "panel": panel_id,
        "gates_hash": gates_hash,
        "confidence": confidence,
        "configs": out_groups,
        "stale": stale,
        "unchecked": unchecked,
        "hints": hints,
    }


def _group(name: str, recs: list[dict[str, Any]], confidence: float, target: float | None) -> dict[str, Any]:
    recs = sorted(recs, key=lambda r: (r.get("created_at") or "", r["id"]))
    by_seed: dict[Any, dict[str, Any]] = {}
    superseded: list[str] = []
    no_seed = 0
    for r in recs:
        key = r.get("seed")
        if key is None:
            no_seed += 1
            key = ("no-seed", r["id"])
        if key in by_seed:
            superseded.append(by_seed[key]["id"])
        by_seed[key] = r
    chosen = sorted(by_seed.values(), key=lambda r: (r.get("seed") is None, r.get("seed") if r.get("seed") is not None else 0, r["id"]))

    seeds = []
    gate_k: dict[str, int] = defaultdict(int)
    gate_n: dict[str, int] = defaultdict(int)
    gate_required: dict[str, bool] = {}
    metric_vals: dict[str, list[float]] = defaultdict(list)
    for r in chosen:
        chk = _latest_check(r) or {}
        rep = chk.get("report") or {}
        failing = []
        for gid, g in (rep.get("gates") or {}).items():
            gate_n[gid] += 1
            gate_k[gid] += int(bool(g.get("pass")))
            gate_required[gid] = bool(g.get("required", True))
            if g.get("required", True) and not g.get("pass"):
                failing.append(gid)
        for mname, v in (rep.get("metrics") or {}).items():
            if _is_scalar(v):
                metric_vals[mname].append(float(v))
        seeds.append({
            "seed": r.get("seed"),
            "run": r["id"],
            "source": r.get("source"),
            "commit": (r.get("git") or {}).get("commit"),
            "pass": bool(rep.get("pass")),
            "n_passed": rep.get("n_passed"),
            "n_required": rep.get("n_required"),
            "failing": failing,
            "gates_hash": chk.get("gates_hash"),
        })

    k = sum(s["pass"] for s in seeds)
    n = len(seeds)
    overall = _rate(k, n, confidence)
    guidance: dict[str, Any] = {"halfwidth": None, "target_halfwidth": target, "seeds_needed": None}
    if overall["ci"]:
        guidance["halfwidth"] = (overall["ci"][1] - overall["ci"][0]) / 2
    if target is not None and n:
        guidance["seeds_needed"] = seeds_for_halfwidth(k / n, target, confidence)

    warnings = []
    commits = sorted({s["commit"] or "?" for s in seeds})
    if len(commits) > 1:
        warnings.append(f"seeds come from {len(commits)} commits — pass rates mix code versions")
    hashes = sorted({str(r.get("config_hash")) for r in chosen})
    if len(hashes) > 1:
        warnings.append(f"{len(hashes)} different config hashes under config {name!r} (overrides?)")
    if no_seed:
        warnings.append(f"{no_seed} record(s) have no seed; each counts once")
    if len({s["gates_hash"] for s in seeds}) > 1:
        warnings.append("seeds were checked with different gates — rates mix gate versions")

    return {
        "config": name,
        "pass": overall,
        "guidance": guidance,
        "seeds": seeds,
        "superseded": superseded,
        "gates": {g: {**_rate(gate_k[g], gate_n[g], confidence), "required": gate_required[g]} for g in gate_n},
        "metrics": {m: summarize(v) for m, v in sorted(metric_vals.items())},
        "commits": commits,
        "warnings": warnings,
    }


# -- text --------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{100 * x:.0f}%"


def _ci(r: dict[str, Any]) -> str:
    return f"[{_pct(r['ci'][0])}, {_pct(r['ci'][1])}]" if r.get("ci") else "[—]"


def _num(v: Any) -> str:
    return "—" if v is None else f"{v:.4g}"


def format_text(stats: dict[str, Any]) -> str:
    conf = f"{100 * stats['confidence']:.0f}%"
    lines = [f"{stats['panel']} (gates {stats['gates_hash'] or 'any'})"]
    if not stats["configs"]:
        lines.append("  no checked runs")
    for g in stats["configs"]:
        p = g["pass"]
        lines.append(f"  config {g['config']}: {p['k']}/{p['n']} seeds pass ({_pct(p['rate'])}, {conf} CI {_ci(p)})")
        gd = g["guidance"]
        if gd["halfwidth"] is not None:
            msg = f"    ±{_pct(gd['halfwidth'])} half-width"
            if gd["target_halfwidth"] is not None:
                need = gd["seeds_needed"]
                msg += f"; ~{need} seeds for ±{_pct(gd['target_halfwidth'])} at this rate" if need else "; target half-width not reachable"
            lines.append(msg)
        lines.append("    seeds: " + ", ".join(
            f"{s['seed'] if s['seed'] is not None else '?'}:{'PASS' if s['pass'] else 'FAIL'}" for s in g["seeds"]))
        lines.append("    gates:")
        for gid, r in g["gates"].items():
            opt = "" if r["required"] else " (info)"
            lines.append(f"      {gid + opt:<36} {r['k']}/{r['n']} {_ci(r)}")
        if g["metrics"]:
            lines.append("    metrics (n, mean ± std, min / median / max):")
            for m, s in g["metrics"].items():
                lines.append(f"      {m:<36} {s['n']}, {_num(s['mean'])} ± {_num(s['std'])}, {_num(s['min'])} / {_num(s['median'])} / {_num(s['max'])}")
        if g["superseded"]:
            lines.append(f"    {len(g['superseded'])} older record(s) for the same seeds not counted")
        lines += [f"    warning: {w}" for w in g["warnings"]]
    lines += [f"  note: {h}" for h in stats["hints"]]
    return "\n".join(lines)
