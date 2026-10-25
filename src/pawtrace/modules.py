"""Module registry: core (always on), optional modules, dependencies, profiles.

The project config's ``profile`` picks a preset; ``modules:`` turns individual
modules on or off on top of it. Every module reads and writes the same plain
files (spec, ledger, panel YAML, run records), so turning one on later backfills
from what's already there.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Module:
    name: str
    group: str
    summary: str
    depends: tuple[str, ...] = ()
    implemented: bool = False
    core: bool = False
    extra: dict[str, str] = field(default_factory=dict)


CORE = (
    Module("spec", "core", "paper spec (every stated/missing detail with its section) + gap ledger", implemented=True, core=True),
    Module("panels", "core", "panel contract: project function, config + seed in, data out", implemented=True, core=True),
    Module("runs", "core", "run records keyed by commit + config hash + seed", implemented=True, core=True),
    Module("gates", "core", "shared gate library; each panel's gates written as config", implemented=True, core=True),
    Module("overlays", "core", "digitized references drawn on the replication axes", implemented=True, core=True),
)

OPTIONAL = (
    Module("extract", "intake", "text by section, equations and figure panels from the paper PDF into a local, gitignored source/ folder; seeds candidate spec items", implemented=True),
    Module("foundations", "build", "foundation survey, license check, reuse/port/rebuild route, reference-equivalence tests"),
    Module("scaffolding", "build", "interfaces, config wiring, spec tests for the paper's environment/model/controller", depends=("foundations",)),
    Module("sensitivity", "finding what matters", "sweep each filled gap, show which panels move"),
    Module("text-claims", "finding what matters", "numeric claims from the paper's text become gates"),
    Module("digitization-error", "finding what matters", "reading precision feeds gate tolerances"),
    Module("seed-stats", "finding what matters", "pass rates with confidence intervals, seed-count guidance"),
    Module("preregistration", "honesty", "gates + deviation list frozen (hashed) before final runs"),
    Module("source-log", "honesty", "what each agent/person read; blocks off-limits sources"),
    Module("paper-versions", "honesty", "version, errata, follow-up papers"),
    Module("dependency-graph", "change", "foundation change → affected panels re-checked", depends=("foundations",)),
    Module("panel-claims", "change", "a lock file: a person or agent claims a panel before working on it"),
    Module("compute-budget", "change", "measured run times, batch cost before queueing"),
    Module("author-questions", "output", "email-ready drafts from open ledger items (templates over the ledger)"),
    Module("paper-tables", "output", "templated tables: text-only vs. code-informed, author questions, pass rates", depends=("seed-stats",)),
    Module("site-pages", "output", "per-panel pages templated from the plain files"),
    Module("release-bundle", "output", "tag, DOI, one-command rerun"),
    Module("beyond-the-paper", "output", "separate area for own improvements/comparisons, never mixed with the faithful replication"),
)

MODULES: dict[str, Module] = {m.name: m for m in (*CORE, *OPTIONAL)}

PROFILES: dict[str, tuple[str, ...]] = {
    "minimal": (),
    "standard": ("extract", "foundations", "scaffolding", "seed-stats", "dependency-graph", "paper-tables"),
    "strict": tuple(m.name for m in OPTIONAL),
}


class ModuleError(ValueError):
    pass


def resolve(profile: str, overrides: dict[str, bool] | None = None) -> dict[str, bool]:
    """Enabled state of every module for a profile + per-module overrides."""
    if profile not in PROFILES:
        raise ModuleError(f"unknown profile {profile!r} (known: {', '.join(PROFILES)})")
    state = {m.name: (m.core or m.name in PROFILES[profile]) for m in MODULES.values()}
    for name, on in (overrides or {}).items():
        if name not in MODULES:
            raise ModuleError(f"unknown module {name!r}")
        if MODULES[name].core and not on:
            raise ModuleError(f"{name} is core and can't be turned off")
        state[name] = bool(on)
    return state


def missing_dependencies(state: dict[str, bool]) -> dict[str, list[str]]:
    """``{enabled_module: [disabled dependencies]}`` — reported, never silently fixed."""
    out: dict[str, list[str]] = {}
    for name, on in state.items():
        if not on:
            continue
        miss = [d for d in MODULES[name].depends if not state.get(d)]
        if miss:
            out[name] = miss
    return out


def with_dependencies(name: str) -> list[str]:
    """``name`` plus everything it depends on (transitively), dependencies first."""
    if name not in MODULES:
        raise ModuleError(f"unknown module {name!r}")
    order: list[str] = []

    def visit(n: str) -> None:
        for d in MODULES[n].depends:
            visit(d)
        if n not in order:
            order.append(n)

    visit(name)
    return order
