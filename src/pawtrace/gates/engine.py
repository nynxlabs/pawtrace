"""Evaluate a panel's gate config (``metrics:`` + ``gates:``) against run data.

Names in a gate config resolve in this order:

1. ``run.<series>`` — a series from the run's output,
2. ``ref.<reference>`` — a digitized reference series,
3. a name under ``metrics:`` — computed once, on demand (cycles are an error).

Any argument listed in an op's/check's ``refs`` may also be a plain number.

Tolerances are explicit per gate. ``tolerance_hook(gate_id, check, args) -> args``
lets a later module (digitization error) widen or derive them; v1 passes args
through unchanged.

A metric that raises doesn't crash the report: every gate depending on it fails
with the error attached. A gate with ``required: false`` is reported but doesn't
count toward the panel verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..data import Series
from .library import CHECKS, OPS

GATE_META_KEYS = {"check", "why", "required", "added", "history", "notes", "source"}
TolHook = Callable[[str, str, dict[str, Any]], dict[str, Any]]


class GateConfigError(ValueError):
    pass


@dataclass
class GateResult:
    gate: str
    passed: bool
    check: str
    inputs: dict[str, Any]
    required: bool = True
    why: str | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        out = {
            "pass": self.passed,
            "check": self.check,
            "inputs": _jsonable(self.inputs),
            "required": self.required,
        }
        if self.why:
            out["why"] = self.why
        if self.error:
            out["error"] = self.error
        return out


@dataclass
class GateReport:
    results: dict[str, GateResult]
    metrics: dict[str, Any] = field(default_factory=dict)
    gates_hash: str = ""

    @property
    def passed(self) -> bool:
        req = [r for r in self.results.values() if r.required]
        return bool(req) and all(r.passed for r in req)

    @property
    def verdicts(self) -> dict[str, bool]:
        return {k: r.passed for k, r in self.results.items()}

    def to_json(self) -> dict[str, Any]:
        req = [r for r in self.results.values() if r.required]
        return {
            "pass": self.passed,
            "n_required": len(req),
            "n_passed": sum(r.passed for r in req),
            "gates_hash": self.gates_hash,
            "gates": {k: r.to_json() for k, r in self.results.items()},
            "metrics": _jsonable(self.metrics),
        }


def gates_hash(cfg: dict[str, Any]) -> str:
    """Stable hash of the metric + gate definitions (ignores why/history notes)."""
    gates = {
        k: {kk: vv for kk, vv in (v or {}).items() if kk not in {"why", "added", "history", "notes", "source"}}
        for k, v in (cfg.get("gates") or {}).items()
    }
    blob = json.dumps({"metrics": cfg.get("metrics") or {}, "gates": gates}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def validate(cfg: dict[str, Any]) -> list[str]:
    """Static problems in a gate config (unknown ops/checks/names, missing args)."""
    problems: list[str] = []
    metrics = cfg.get("metrics") or {}
    gates = cfg.get("gates") or {}
    if not gates:
        problems.append("no gates defined")
    known = set(metrics)

    def _refs_ok(where: str, refs: tuple[str, ...], args: dict[str, Any]) -> None:
        for r in refs:
            if r not in args:
                continue
            vals = args[r] if isinstance(args[r], list) else [args[r]]
            for v in vals:
                if isinstance(v, str) and not (v.startswith(("run.", "ref.")) or v in known):
                    problems.append(f"{where}: {r}={v!r} is not a metric, run.<series> or ref.<name>")

    for name, m in metrics.items():
        if not isinstance(m, dict) or "op" not in m:
            problems.append(f"metric {name}: needs op")
            continue
        spec = OPS.get(m["op"])
        if spec is None:
            problems.append(f"metric {name}: unknown op {m['op']!r}")
            continue
        _refs_ok(f"metric {name}", spec.refs, m)
    for gid, g in gates.items():
        if not isinstance(g, dict) or "check" not in g:
            problems.append(f"gate {gid}: needs check")
            continue
        spec = CHECKS.get(g["check"])
        if spec is None:
            problems.append(f"gate {gid}: unknown check {g['check']!r}")
            continue
        _refs_ok(f"gate {gid}", spec.refs, g)
    return problems


class _Resolver:
    def __init__(self, metrics_cfg: dict[str, Any], inputs: dict[str, Series]):
        self.cfg = metrics_cfg
        self.inputs = inputs
        self.values: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        self._stack: list[str] = []

    def get(self, name: Any) -> Any:
        if isinstance(name, list):
            return [self.get(n) for n in name]
        if not isinstance(name, str):
            return name
        if name.startswith(("run.", "ref.")):
            if name not in self.inputs:
                raise KeyError(f"missing input series {name!r} (have: {', '.join(sorted(self.inputs)) or 'none'})")
            return self.inputs[name]
        if name in self.errors:
            raise RuntimeError(self.errors[name])
        if name in self.values:
            return self.values[name]
        if name not in self.cfg:
            raise KeyError(f"unknown metric {name!r}")
        if name in self._stack:
            raise GateConfigError(f"metric cycle: {' -> '.join(self._stack + [name])}")
        self._stack.append(name)
        try:
            m = dict(self.cfg[name])
            spec = OPS[m.pop("op")]
            args = {k: (self.get(v) if k in spec.refs else v) for k, v in m.items()}
            val = spec.fn(**args)
        except GateConfigError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported per gate
            self.errors[name] = f"{name}: {exc}"
            raise RuntimeError(self.errors[name]) from exc
        finally:
            self._stack.pop()
        self.values[name] = val
        return val


def evaluate(
    cfg: dict[str, Any],
    inputs: dict[str, Series],
    *,
    tolerance_hook: TolHook | None = None,
) -> GateReport:
    """Run every gate in ``cfg`` against ``inputs`` (``run.*`` / ``ref.*`` series)."""
    problems = validate(cfg)
    if problems:
        raise GateConfigError("; ".join(problems))
    res = _Resolver(cfg.get("metrics") or {}, inputs)
    results: dict[str, GateResult] = {}
    for gid, g in (cfg.get("gates") or {}).items():
        spec = CHECKS[g["check"]]
        raw = {k: v for k, v in g.items() if k not in GATE_META_KEYS}
        required = bool(g.get("required", True))
        try:
            args = {k: (res.get(v) if k in spec.refs else v) for k, v in raw.items()}
            if tolerance_hook is not None:
                args = tolerance_hook(gid, g["check"], args)
            passed = bool(spec.fn(**args))
            shown = {k: (args[k] if k in spec.refs and not isinstance(args[k], Series) else v) for k, v in raw.items()}
            results[gid] = GateResult(gid, passed, g["check"], shown, required, g.get("why"))
        except GateConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            results[gid] = GateResult(gid, False, g["check"], raw, required, g.get("why"), error=str(exc))
    # Every metric, computed (gates may not touch them all; they're the panel's readout).
    for name in cfg.get("metrics") or {}:
        try:
            res.get(name)
        except Exception:  # noqa: BLE001
            pass
    shown_metrics = {k: v for k, v in res.values.items() if not isinstance(v, Series)}
    for k, err in res.errors.items():
        shown_metrics[k] = {"error": err}
    return GateReport(results, shown_metrics, gates_hash(cfg))


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.ndarray):
        return [_jsonable(x) for x in v.tolist()]
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, Series):
        return {"series": len(v)}
    return v
