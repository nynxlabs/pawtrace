"""More digitization and table readers → ``{series_name: Series}``.

Registered on import (``formats.py`` imports this module at its bottom), so
``read(path, fmt=...)`` finds them:

- ``wpd-json`` — WebPlotDigitizer project file. Points come from the ``value``
  fields WPD writes at click/export time; series names and ``colorRGB`` are kept
  as WPD gives them (the color lands in ``Series.meta["color_rgba"]``).
- ``wpd-csv`` — WebPlotDigitizer multi-series CSV: a row of series names, then
  ``X,Y,X,Y,...`` columns.
- ``engauge-csv`` — Engauge Digitizer CSV in any of its three export layouts
  (shared-x columns, raw ``x`` blocks per curve, or a plain two-column file).
- ``table-csv`` — a plain table (bar charts, tables): one ``bars`` series per
  value column, categorical x from a label column, optional error columns.

The numeric readers take ``series_map={"src": "dst"}`` to rename series. Empty
cells and non-finite values are dropped (per x/y pair), and numeric-x series are
sorted by x with a stable sort.
"""
from __future__ import annotations

import csv
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .data import Series
from .formats import register

Traces = dict[str, tuple[list[float], list[float]]]


# --- cell helpers -----------------------------------------------------------


def _is_number(text: str) -> bool:
    try:
        float(text.strip().replace(",", "."))
    except ValueError:
        return False
    return True


def _cell(text: str) -> float | None:
    """Parse one CSV cell. ``None`` for empty or non-finite; ValueError for text."""
    s = text.strip()
    if not s:
        return None
    v = float(s.replace(",", "."))
    return v if math.isfinite(v) else None


def _cell_lenient(text: str) -> float | None:
    """Like :func:`_cell`, but non-numeric text (sub-headers, notes) is also ``None``."""
    try:
        return _cell(text)
    except ValueError:
        return None


def _at(row: Sequence[str], i: int) -> str:
    return row[i] if i < len(row) else ""


def _finite(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if math.isfinite(v) else None


def _csv_rows(path: Path) -> list[list[str]]:
    """Rows of a CSV with blank lines and ``#`` comment lines removed."""
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.reader(fh) if r and any(c.strip() for c in r) and not r[0].lstrip().startswith("#")]


def _build(
    traces: Traces,
    series_map: dict[str, str] | None = None,
    colors: dict[str, list[int]] | None = None,
) -> dict[str, Series]:
    """Turn ``{name: (xs, ys)}`` into sorted, renamed :class:`Series`."""
    out: dict[str, Series] = {}
    for name, (xs, ys) in traces.items():
        if not xs:
            continue
        key = (series_map or {}).get(name, name)
        if key in out:
            raise ValueError(f"series {name!r} and another series both map to {key!r}")
        x = np.asarray(xs, dtype=float)
        y = np.asarray(ys, dtype=float)
        order = np.argsort(x, kind="stable")
        meta: dict[str, Any] = {}
        if colors and name in colors:
            meta["color_rgba"] = colors[name]
        out[key] = Series(x=x[order], y=y[order], meta=meta)
    return out


def _add(traces: Traces, name: str, x: float | None, y: float | None) -> None:
    if x is None or y is None:
        return
    xs, ys = traces.setdefault(name, ([], []))
    xs.append(x)
    ys.append(y)


# --- WebPlotDigitizer -------------------------------------------------------


@register("wpd-json")
def read_wpd_json(path: Path, series_map: dict[str, str] | None = None) -> dict[str, Series]:
    """WPD project JSON: ``{"datasetColl": [{"name", "colorRGB"?, "data": [{"value": [x, y]}]}]}``."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    traces: Traces = {}
    colors: dict[str, list[int]] = {}
    seen: set[str] = set()
    for ds in payload.get("datasetColl") or []:
        name = str(ds.get("name", "series"))
        if name in seen:
            raise ValueError(f"duplicate WPD series name {name!r} in {path}")
        seen.add(name)
        for p in ds.get("data") or []:
            v = p.get("value") if isinstance(p, dict) else None
            if not isinstance(v, list) or len(v) < 2:
                continue
            _add(traces, name, _finite(v[0]), _finite(v[1]))
        col = ds.get("colorRGB")
        if name in traces and isinstance(col, list) and len(col) >= 3:
            colors[name] = [int(col[0]), int(col[1]), int(col[2]), int(col[3]) if len(col) > 3 else 255]
    return _build(traces, series_map, colors)


@register("wpd-csv")
def read_wpd_csv(path: Path, series_map: dict[str, str] | None = None) -> dict[str, Series]:
    """WPD multi-series CSV: names on row 1 (every other cell), ``X,Y`` pairs after.

    The ``X,Y`` sub-header row and any non-numeric cells are skipped.
    """
    rows = _csv_rows(path)
    if not rows:
        raise ValueError(f"empty WPD CSV: {path}")
    head = rows[0]
    columns = list(range(0, len(head), 2))
    traces: Traces = {}
    names: dict[int, str] = {}
    for i in columns:
        name = head[i].strip() or f"series{i // 2 + 1}"
        if name in traces:
            raise ValueError(f"duplicate WPD series name {name!r} in {path}")
        names[i] = name
        traces[name] = ([], [])
    for r in rows[1:]:
        for i, name in names.items():
            _add(traces, name, _cell_lenient(_at(r, i)), _cell_lenient(_at(r, i + 1)))
    return _build(traces, series_map)


# --- Engauge Digitizer ------------------------------------------------------


@register("engauge-csv")
def read_engauge_csv(
    path: Path,
    series_map: dict[str, str] | None = None,
    curve: str = "series",
) -> dict[str, Series]:
    """Engauge CSV. ``curve`` names the single curve of a plain two-column file.

    Layouts, detected from the first row:

    1. Shared-x: ``x,Curve1,Curve2,...`` then one row of x and a y per curve.
    2. Raw blocks: ``x,CurveName`` starts a curve; ``x,y`` rows follow until the next header.
    3. Two columns: ``x,y`` (header optional).
    """
    rows = _csv_rows(path)
    if not rows:
        raise ValueError(f"empty Engauge CSV: {path}")
    head = [c.strip() for c in rows[0]]
    if len(head) >= 2 and head[0].lower() == "x":
        named = [c for c in head[1:] if c and not _is_number(c)]
        if len(named) >= 2:
            return _build(_shared_x(rows, head), series_map)
        if len(named) == 1 and not (len(head) == 2 and head[1].lower() == "y"):
            return _build(_raw_blocks(rows), series_map)
    if len(head) < 2:
        raise ValueError(f"unrecognized Engauge CSV layout: {path}")
    traces: Traces = {curve: ([], [])}
    start = 0 if _is_number(rows[0][0]) else 1
    for r in rows[start:]:
        if len(r) >= 2:
            _add(traces, curve, _cell_lenient(r[0]), _cell_lenient(r[1]))
    if not traces[curve][0]:
        raise ValueError(f"could not parse Engauge CSV layout: {path}")
    return _build(traces, series_map)


def _shared_x(rows: list[list[str]], head: list[str]) -> Traces:
    names = head[1:]
    traces: Traces = {n: ([], []) for n in names if n}
    for r in rows[1:]:
        x = _cell_lenient(_at(r, 0))
        if x is None:
            continue
        for i, n in enumerate(names):
            if n:
                _add(traces, n, x, _cell_lenient(_at(r, i + 1)))
    return traces


def _raw_blocks(rows: list[list[str]]) -> Traces:
    traces: Traces = {}
    current: str | None = None
    for r in rows:
        cells = [c.strip() for c in r]
        if cells[0].lower() == "x" and len(cells) >= 2 and cells[1] and not _is_number(cells[1]):
            current = cells[1]
            traces.setdefault(current, ([], []))
            continue
        if current is not None and len(cells) >= 2:
            _add(traces, current, _cell_lenient(cells[0]), _cell_lenient(cells[1]))
    return traces


# --- plain table ------------------------------------------------------------


@register("table-csv")
def read_table_csv(
    path: Path,
    label: str | None = None,
    value: str | list[str] | None = None,
    err: dict[str, str] | None = None,
) -> dict[str, Series]:
    """Header row, then one row per category.

    - ``label``: column with the category names (categorical x); default the first column.
    - ``value``: one column name or a list; default every column except ``label``.
      Each becomes a ``bars`` series named by its column.
    - ``err``: ``{value_column: error_column}`` → that series' ``yerr``.

    A row is dropped from a series when its label or value (or its error, when
    one is mapped) is empty. Non-numeric text in a value column is an error.
    """
    rows = _csv_rows(path)
    if len(rows) < 2:
        raise ValueError(f"table CSV needs a header and at least one row: {path}")
    head = [c.strip() for c in rows[0]]
    label_col = head[0] if label is None else label
    label_i = _column(head, label_col, path)

    if value is None:
        values = [h for h in head if h and h != label_col]
    elif isinstance(value, str):
        values = [value]
    else:
        values = list(value)
    for v in values:
        _column(head, v, path)
    err = dict(err or {})
    for v, e in err.items():
        if v not in values:
            raise ValueError(f"err maps {v!r}, which is not one of the value columns")
        _column(head, e, path)

    out: dict[str, Series] = {}
    for v in values:
        vi = head.index(v)
        ei = head.index(err[v]) if v in err else None
        labels: list[str] = []
        ys: list[float] = []
        es: list[float] = []
        for n, r in enumerate(rows[1:], start=1):
            lab = _at(r, label_i).strip()
            y = _strict_cell(_at(r, vi), path, n, v)
            e = _strict_cell(_at(r, ei), path, n, err[v]) if ei is not None else None
            if not lab or y is None or (ei is not None and e is None):
                continue
            labels.append(lab)
            ys.append(y)
            if e is not None:
                es.append(e)
        if labels:
            out[v] = Series(
                x=labels,
                y=ys,
                yerr=es if ei is not None else None,
                kind="bars",
            )
    return out


def _strict_cell(text: str, path: Path, row: int, column: str) -> float | None:
    try:
        return _cell(text)
    except ValueError:
        raise ValueError(f"{path}: data row {row}, column {column!r}: not a number: {text.strip()!r}") from None


def _column(head: list[str], name: str, path: Path) -> int:
    if name not in head:
        cols = ", ".join(h for h in head if h) or "none"
        raise ValueError(f"{path}: no column {name!r} (columns: {cols})")
    return head.index(name)
