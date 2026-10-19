"""Overlay plots: our run series drawn over the digitized references.

``plot_panel`` needs matplotlib (the ``plot`` extra). ``next_versioned`` picks the
next ``{stem}_v{N}{ext}`` name so images are never overwritten: densk and nook
cache images by filename.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .data import PanelData, Series
    from .panel import Panel, Reference


def next_versioned(directory: Path | str, stem: str, ext: str = ".png") -> tuple[Path, int]:
    """Path for ``{stem}_v{N+1}{ext}`` where N is the highest existing version (0 if none)."""
    directory = Path(directory)
    pat = re.compile(rf"^{re.escape(stem)}_v(\d+){re.escape(ext)}$")
    top = 0
    if directory.is_dir():
        for p in directory.iterdir():
            m = pat.match(p.name)
            if m:
                top = max(top, int(m.group(1)))
    n = top + 1
    while (directory / f"{stem}_v{n}{ext}").exists():
        n += 1
    return directory / f"{stem}_v{n}{ext}", n


def plot_panel(
    panel: "Panel",
    data: "PanelData",
    out: Path | str,
    *,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Draw the panel's run series over its references and save a PNG at ``out``.

    Settings come from ``panel.cfg["overlay"]``: ``series``, ``references`` (lists of
    names; default all), ``xlabel``, ``ylabel``, ``xlim``, ``ylim``.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("install the plot extra: pip install pawtrace[plot]") from exc

    cfg = panel.cfg.get("overlay") or {}
    warnings: list[str] = []

    # run series -----------------------------------------------------------
    wanted_runs = cfg.get("series")
    if wanted_runs is None:
        wanted_runs = list(data.series)
    runs: list[tuple[str, "Series"]] = []
    for name in wanted_runs:
        if name in data.series:
            runs.append((name, data.series[name]))
        else:
            warnings.append(f"run series {name!r} not in data (have: {', '.join(data.series) or 'none'})")

    # references -----------------------------------------------------------
    refs: list["Reference"] = []
    wanted_refs = cfg.get("references")
    if wanted_refs != []:
        all_refs = panel.references()
        for name in (list(all_refs) if wanted_refs is None else wanted_refs):
            if name in all_refs:
                refs.append(all_refs[name])
            else:
                warnings.append(f"reference {name!r} not defined in panel (have: {', '.join(all_refs) or 'none'})")

    # one chart type: bars if any shown series is categorical, else lines
    bars = any(s.categorical for _, s in runs) or any(r.series.categorical for r in refs)
    kept_runs = []
    for name, s in runs:
        if s.categorical == bars:
            kept_runs.append((name, s))
        else:
            warnings.append(f"run series {name!r} skipped: {'numeric' if bars else 'categorical'} series can't share this chart")
    kept_refs = []
    for r in refs:
        if r.series.categorical == bars:
            kept_refs.append(r)
        else:
            warnings.append(f"reference {r.name!r} skipped: {'numeric' if bars else 'categorical'} series can't share this chart")
    runs, refs = kept_runs, kept_refs
    if not runs and not refs:
        warnings.append("nothing to plot")

    # figure ---------------------------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 4.5))
    try:
        if bars:
            _draw_bars(ax, runs, refs)
        else:
            _draw_lines(ax, runs, refs)

        title = panel.title
        if report:
            verdict = "PASS" if report.get("pass") else "FAIL"
            title = f"{title} — gates {report.get('n_passed')}/{report.get('n_required')}, {verdict}"
        ax.set_title(title)
        ax.set_xlabel(str(cfg.get("xlabel", "")))
        ax.set_ylabel(str(cfg.get("ylabel", "")))
        if cfg.get("xlim") is not None:
            ax.set_xlim(*cfg["xlim"])
        if cfg.get("ylim") is not None:
            ax.set_ylim(*cfg["ylim"])
        handles, labels = ax.get_legend_handles_labels()
        if labels:
            ax.legend(loc="best", fontsize=9)

        captions = [f"reference {r.name}: {r.status}" for r in refs if r.status != "ok"]
        if captions:
            fig.text(0.01, 0.01, "\n".join(captions), fontsize=8, va="bottom")
            fig.tight_layout(rect=(0, 0.04 + 0.035 * len(captions), 1, 1))
        else:
            fig.tight_layout()

        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, dpi=150)
    finally:
        plt.close(fig)

    return {
        "out": str(out),
        "series": [name for name, _ in runs],
        "references": [r.name for r in refs],
        "warnings": warnings,
    }


def _color(j: int) -> str:
    return f"C{j % 10}"


def _draw_bars(ax: Any, runs: list[tuple[str, "Series"]], refs: list["Reference"]) -> None:
    items: list[tuple[str, "Series", bool]] = [(n, s, False) for n, s in runs]
    items += [(r.name, r.series, True) for r in refs]
    order: dict[str, int] = {}
    for _, s, _ in items:
        for lab in s.x:
            order.setdefault(lab, len(order))
    width = 0.8 / max(len(items), 1)
    for j, (name, s, is_ref) in enumerate(items):
        pos = [order[lab] - 0.4 + width * (j + 0.5) for lab in s.x]
        kw: dict[str, Any] = {"yerr": s.yerr} if s.yerr is not None else {}
        if is_ref:
            ax.bar(pos, s.y, width, facecolor="none", edgecolor=_color(j), hatch="//", linewidth=1.2,
                   label=f"{name} (digitized)", **kw)
        else:
            ax.bar(pos, s.y, width, color=_color(j), label=name, **kw)
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels(list(order))


def _draw_lines(ax: Any, runs: list[tuple[str, "Series"]], refs: list["Reference"]) -> None:
    for j, (name, s) in enumerate(runs):
        x = s.xf
        ax.plot(x, s.y, color=_color(j), linewidth=1.8, label=name)
        if s.yerr is not None:
            ax.fill_between(x, s.y - s.yerr, s.y + s.yerr, color=_color(j), alpha=0.2, linewidth=0)
    for j, r in enumerate(refs, start=len(runs)):
        ax.plot(r.series.xf, r.series.y, color=_color(j), linewidth=1.4, linestyle="--",
                label=f"{r.name} (digitized)")
