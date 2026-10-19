"""Panels: the one framework-like piece of pawtrace.

A panel YAML names the **panel contract** — a function in the project's own
repo, ``fn(config: dict, seed: int) -> PanelData`` (or a dict of series) — plus
the digitized references, metrics, gates and overlay settings. pawtrace never
owns the model code; it calls the function, records what came back, and checks it.

Panel YAML keys (see ``docs/panel-format.md``)::

    panel / title / claim / spec_items
    outputs:     {name: {kind, x, y}}                 # what the function returns
    run:         {entry: "pkg.mod:fn" | "path.py:fn", pythonpath: [...],
                  configs: {paper_only: {...}, adopted: {...}}, default_config, seeds}
    import:      {format: json-fields, series: {name: {y: field, x?: field}}, seed, config, commit}
    references:  {name: {path, format, series, bin_near_duplicates, transform, status}}
    metrics / gates                                   # gates as config (pawtrace.gates)
    overlay:     {series, references, xlabel, ylabel, xlim, ylim}
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from . import formats
from .data import PanelData, Series, coerce_panel_data, sort_and_bin
from .gates import GateReport, evaluate, validate
from .project import Project


class PanelError(RuntimeError):
    pass


@dataclass
class Reference:
    name: str
    series: Series
    path: Path
    status: str = "ok"


@dataclass
class Panel:
    id: str
    cfg: dict[str, Any]
    path: Path
    project: Project

    @classmethod
    def load(cls, project: Project, ref: str) -> "Panel":
        pid = project.resolve_panel_id(ref)
        path = project.panel_path(pid)
        cfg = yaml.safe_load(path.read_text()) or {}
        if not isinstance(cfg, dict):
            raise PanelError(f"{path}: expected a mapping")
        return cls(pid, cfg, path, project)

    @property
    def paper(self) -> str:
        return self.id.split("/", 1)[0]

    @property
    def title(self) -> str:
        return str(self.cfg.get("title") or self.id)

    def gate_cfg(self) -> dict[str, Any]:
        return {"metrics": self.cfg.get("metrics") or {}, "gates": self.cfg.get("gates") or {}}

    # -- references ------------------------------------------------------
    def references(self) -> dict[str, Reference]:
        out: dict[str, Reference] = {}
        for name, spec in (self.cfg.get("references") or {}).items():
            spec = dict(spec or {})
            path = self.project.path(spec["path"])
            if not path.exists():
                raise PanelError(f"reference {name}: {path} not found")
            all_series = formats.read(path, spec.get("format"), **(spec.get("options") or {}))
            key = spec.get("series")
            if key is None:
                if len(all_series) != 1:
                    raise PanelError(f"reference {name}: file has {len(all_series)} series; set series:")
                key = next(iter(all_series))
            if key not in all_series:
                raise PanelError(f"reference {name}: no series {key!r} in {path} (have: {', '.join(all_series)})")
            s = all_series[key]
            if spec.get("bin_near_duplicates") and not s.categorical:
                x, y = sort_and_bin(s.xf, s.y, rel_width=float(spec.get("bin_width", 0.0025)))
                s = Series(x=x, y=y, kind=s.kind)
            elif not s.categorical:
                order = np.argsort(s.xf, kind="mergesort")
                s = Series(x=s.xf[order], y=s.y[order], yerr=None if s.yerr is None else s.yerr[order], kind=s.kind)
            t = spec.get("transform") or {}
            if t and not s.categorical:
                s = Series(
                    x=s.xf * float(t.get("x_scale", 1.0)) + float(t.get("x_offset", 0.0)),
                    y=s.y * float(t.get("y_scale", 1.0)) + float(t.get("y_offset", 0.0)),
                    kind=s.kind,
                )
            out[name] = Reference(name, s, path, str(spec.get("status", "ok")))
        return out

    # -- the panel contract ---------------------------------------------------
    def configs(self) -> dict[str, dict[str, Any]]:
        return dict((self.cfg.get("run") or {}).get("configs") or {})

    def default_config_name(self) -> str | None:
        run = self.cfg.get("run") or {}
        if run.get("default_config"):
            return str(run["default_config"])
        cfgs = self.configs()
        return "adopted" if "adopted" in cfgs else (next(iter(cfgs)) if cfgs else None)

    def seeds(self) -> list[int]:
        return [int(s) for s in ((self.cfg.get("run") or {}).get("seeds") or [0])]

    def entry(self) -> Any:
        run = self.cfg.get("run") or {}
        target = run.get("entry")
        if not target:
            raise PanelError(f"{self.id}: no run.entry (panel contract) — import saved artifacts instead")
        for p in run.get("pythonpath") or ["."]:
            sp = str(self.project.path(p))
            if sp not in sys.path:
                sys.path.insert(0, sp)
        mod_name, _, fn_name = str(target).partition(":")
        if not fn_name:
            raise PanelError(f"run.entry must be 'module:function' or 'file.py:function', got {target!r}")
        if mod_name.endswith(".py"):
            file = self.project.path(mod_name)
            spec = importlib.util.spec_from_file_location(f"pawtrace_panel_{file.stem}", file)
            if spec is None or spec.loader is None:
                raise PanelError(f"can't load {file}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            module = importlib.import_module(mod_name)
        try:
            return getattr(module, fn_name)
        except AttributeError:
            raise PanelError(f"{mod_name} has no {fn_name}") from None

    def run(self, config_name: str | None = None, seed: int = 0, overrides: dict[str, Any] | None = None) -> tuple[PanelData, dict[str, Any]]:
        """Call the project's panel function; returns (data, the exact config used)."""
        name = config_name or self.default_config_name()
        cfgs = self.configs()
        if name is not None and name not in cfgs:
            raise PanelError(f"{self.id}: no config {name!r} (have: {', '.join(cfgs) or 'none'})")
        config = dict(cfgs.get(name, {}) if name else {})
        config.update(overrides or {})
        data = coerce_panel_data(self.entry()(config, seed))
        return data, config

    # -- importing saved artifacts --------------------------------------------
    def import_artifact(self, path: str | Path) -> tuple[PanelData, dict[str, Any]]:
        """Read a saved artifact through the panel's ``import:`` mapping.

        Returns (data, info) with ``info`` = {seed, config, commit} when the mapping names them.
        """
        spec = self.cfg.get("import") or {}
        fmt = spec.get("format", "json-fields")
        path = Path(path)
        if fmt == "panel-json":
            data, payload = PanelData.load(path), {}
        elif fmt == "json-fields":
            payload = json.loads(path.read_text())
            series: dict[str, Series] = {}
            for name, m in (spec.get("series") or {}).items():
                y = _field(payload, m["y"])
                if y is None:
                    raise PanelError(f"{path}: no field {m['y']!r} for series {name}")
                x = _field(payload, m["x"]) if m.get("x") else None
                yerr = _field(payload, m["yerr"]) if m.get("yerr") else None
                series[name] = Series(x=x, y=y, yerr=yerr) if x is not None else Series.from_y(y, yerr=yerr)
            data = PanelData(series=series)
        else:
            raise PanelError(f"unknown import format {fmt!r} (json-fields, panel-json)")
        info: dict[str, Any] = {}
        for key in ("seed", "config", "commit"):
            if spec.get(key):
                info[key] = _field(payload, spec[key])
        return data, info

    # -- checking --------------------------------------------------------
    def inputs(self, data: PanelData) -> dict[str, Series]:
        inp = {f"run.{k}": s for k, s in data.series.items()}
        inp.update({f"ref.{k}": r.series for k, r in self.references().items()})
        return inp

    def check(self, data: PanelData) -> GateReport:
        return evaluate(self.gate_cfg(), self.inputs(data))

    def lint(self) -> list[dict[str, str]]:
        """Problems (level error/warn) a person or agent should fix before trusting results."""
        out: list[dict[str, str]] = []

        def add(level: str, msg: str) -> None:
            out.append({"level": level, "panel": self.id, "message": msg})

        for p in validate(self.gate_cfg()):
            add("error", p)
        for gid, g in (self.cfg.get("gates") or {}).items():
            if isinstance(g, dict) and not g.get("why"):
                add("warn", f"gate {gid} has no why (what paper claim it checks)")
        if not self.cfg.get("references"):
            add("warn", "no digitized references — gates can only check internal shape")
        for name, spec in (self.cfg.get("references") or {}).items():
            if str((spec or {}).get("status", "ok")) != "ok":
                add("warn", f"reference {name} status is {spec.get('status')!r}")
            try:
                if not self.project.path(spec["path"]).exists():
                    add("error", f"reference {name}: {spec['path']} not found")
            except Exception as exc:  # noqa: BLE001
                add("error", f"reference {name}: {exc}")
        run = self.cfg.get("run") or {}
        if run and "paper_only" not in (run.get("configs") or {}):
            add("warn", "no paper_only config — keep a text-only config runnable next to the adopted one")
        if not run and not self.cfg.get("import"):
            add("error", "neither run.entry (panel contract) nor import mapping")
        return out


def _field(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in str(dotted).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            cur = cur[int(part)]
        else:
            return None
    return cur
