"""Panel data: named series that both panel outputs and digitized references use.

A series is ``x`` + ``y`` (+ optional ``yerr``). ``x`` is numeric for curves and
scatter, or a list of string labels for bars and table rows, so gates written
against labels (``value_at``, ``ordering``) work on bar charts and tables the
same way window gates work on curves.

On disk a panel's data is one JSON object::

    {"series": {"<name>": {"x": [...], "y": [...], "yerr": [...]?, "kind": "curve"?}},
     "meta": {...}}

The thin digitization schema (``{"n": N, "xy": {"x": [...], "y": [...]}}``) reads
as the same thing, so references and run outputs share one loader.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

KINDS = ("curve", "scatter", "bars", "table")


@dataclass
class Series:
    """One named trace: numeric or categorical ``x``, numeric ``y``."""

    x: np.ndarray | list[str]
    y: np.ndarray
    yerr: np.ndarray | None = None
    kind: str = "curve"
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.y = np.asarray(self.y, dtype=float)
        if self.yerr is not None:
            self.yerr = np.asarray(self.yerr, dtype=float)
        if _is_labels(self.x):
            self.x = [str(v) for v in self.x]
        else:
            self.x = np.asarray(self.x, dtype=float)
        if len(self.x) != self.y.size:
            raise ValueError(f"x has {len(self.x)} values but y has {self.y.size}")
        if self.yerr is not None and self.yerr.size != self.y.size:
            raise ValueError("yerr must match y")

    @property
    def categorical(self) -> bool:
        return isinstance(self.x, list)

    @property
    def xf(self) -> np.ndarray:
        """Numeric x (raises for categorical series)."""
        if self.categorical:
            raise TypeError("series has label x; use a label-based op (value_at, ordering)")
        return self.x  # type: ignore[return-value]

    def __len__(self) -> int:
        return int(self.y.size)

    @classmethod
    def from_y(cls, y: Any, *, x0: float = 0.0, dx: float = 1.0, **kw: Any) -> "Series":
        """Series with evenly spaced x (``x0 + i*dx``), e.g. a per-step trace."""
        y = np.asarray(y, dtype=float)
        return cls(x=x0 + dx * np.arange(y.size, dtype=float), y=y, **kw)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "x": list(self.x) if self.categorical else self.xf.tolist(),
            "y": self.y.tolist(),
        }
        if self.yerr is not None:
            out["yerr"] = self.yerr.tolist()
        if self.kind != "curve":
            out["kind"] = self.kind
        if self.meta:
            out["meta"] = self.meta
        return out

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "Series":
        xy = obj.get("xy") or obj
        y = xy.get("y")
        if y is None:
            raise ValueError("series needs y")
        x = xy.get("x")
        if x is None:
            return cls.from_y(y, yerr=obj.get("yerr"), kind=obj.get("kind", "curve"))
        return cls(x=x, y=y, yerr=obj.get("yerr"), kind=obj.get("kind", "curve"), meta=obj.get("meta") or {})


@dataclass
class PanelData:
    """What a panel run returns: named series plus free-form metadata."""

    series: dict[str, Series]
    meta: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, name: str) -> Series:
        try:
            return self.series[name]
        except KeyError:
            raise KeyError(f"no series {name!r} (have: {', '.join(self.series) or 'none'})") from None

    def to_json(self) -> dict[str, Any]:
        return {"series": {k: s.to_json() for k, s in self.series.items()}, "meta": self.meta}

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "PanelData":
        return cls(
            series={str(k): Series.from_json(v) for k, v in (obj.get("series") or {}).items()},
            meta=dict(obj.get("meta") or {}),
        )

    @classmethod
    def load(cls, path: str | Path) -> "PanelData":
        return cls.from_json(json.loads(Path(path).read_text()))


def coerce_panel_data(obj: Any) -> PanelData:
    """Accept what a panel function returns: PanelData, ``{name: Series}``,
    ``{name: array}`` (per-index trace), or the JSON shape."""
    if isinstance(obj, PanelData):
        return obj
    if isinstance(obj, dict) and "series" in obj and isinstance(obj["series"], dict):
        return PanelData.from_json(obj)
    if isinstance(obj, dict):
        series: dict[str, Series] = {}
        for k, v in obj.items():
            if isinstance(v, Series):
                series[str(k)] = v
            elif isinstance(v, dict):
                series[str(k)] = Series.from_json(v)
            else:
                series[str(k)] = Series.from_y(v)
        return PanelData(series=series)
    raise TypeError(f"panel returned {type(obj).__name__}; expected PanelData or a dict of series")


def sort_and_bin(x: np.ndarray, y: np.ndarray, *, rel_width: float = 0.0025) -> tuple[np.ndarray, np.ndarray]:
    """Sort by x and median-combine points within ``rel_width`` of the x span.

    Cleans hand-digitization quirks (double passes, small backtracks). Leave it
    off for traces whose one-sample spikes matter: a spike digitized as
    top/bottom/top at nearly the same x collapses to its median.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 2:
        return x, y
    order = np.argsort(x, kind="mergesort")
    x, y = x[order], y[order]
    span = float(np.ptp(x))
    if span <= 0:
        return np.asarray([float(x[0])]), np.asarray([float(np.median(y))])
    width = max(span * rel_width, 1e-9)
    xs: list[float] = []
    ys: list[float] = []
    i, n = 0, int(x.size)
    while i < n:
        j = i + 1
        while j < n and (x[j] - x[i]) <= width:
            j += 1
        xs.append(float(np.mean(x[i:j])))
        ys.append(float(np.median(y[i:j])))
        i = j
    return np.asarray(xs), np.asarray(ys)


def _is_labels(x: Any) -> bool:
    if isinstance(x, np.ndarray):
        return x.dtype.kind in "USO" and x.size > 0 and isinstance(x.flat[0], str)
    if isinstance(x, (list, tuple)) and x:
        return any(isinstance(v, str) for v in x)
    return False
