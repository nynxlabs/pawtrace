"""Digitization and data file formats → ``{series_name: Series}``.

Every reader takes a path plus reader options and returns named series. Add a
reader with ``@register("name")``; panels pick one with ``format:`` in their
reference block (``curves-json`` when omitted).

Built in:

- ``curves-json`` — the thin digitization schema: ``{"series": {name: {"n", "xy": {"x", "y"}, "color_rgba"?}}}``
  (extra per-series keys such as old convenience stats are ignored).
- ``panel-json`` — a saved :class:`~pawtrace.data.PanelData` (same shape, flat ``x``/``y``).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .data import Series

Reader = Callable[..., dict[str, Series]]
READERS: dict[str, Reader] = {}


def register(name: str) -> Callable[[Reader], Reader]:
    def deco(fn: Reader) -> Reader:
        READERS[name] = fn
        return fn

    return deco


def read(path: str | Path, fmt: str | None = None, **opts: Any) -> dict[str, Series]:
    fmt = fmt or guess_format(path)
    try:
        reader = READERS[fmt]
    except KeyError:
        raise ValueError(f"unknown format {fmt!r} (known: {', '.join(sorted(READERS))})") from None
    return reader(Path(path), **opts)


def guess_format(path: str | Path) -> str:
    name = Path(path).name.lower()
    if name.endswith(".wpd.json") and "wpd-json" in READERS:
        return "wpd-json"
    if name.endswith(".csv") and "wpd-csv" in READERS:
        return "wpd-csv"
    return "curves-json"


@register("curves-json")
def read_curves_json(path: Path) -> dict[str, Series]:
    payload = json.loads(path.read_text())
    out: dict[str, Series] = {}
    for name, obj in (payload.get("series") or {}).items():
        xy = obj.get("xy") or obj
        x, y = xy.get("x"), xy.get("y")
        if not y or (x is not None and len(x) != len(y)):
            continue
        out[str(name)] = Series.from_json(obj)
    return out


@register("panel-json")
def read_panel_json(path: Path) -> dict[str, Series]:
    from .data import PanelData

    return PanelData.load(path).series


from . import formats_extra  # noqa: E402,F401  (registers more readers)
