"""Calibration, curves-json build, review residuals and document IO for the manual digitizer.

Calibration uses four reference points, two per axis, so rotated and skewed
axes work. Let ``u`` be an axis's transformed value: the value itself on a
linear axis, ``log10(value)`` on a log axis. X1, X2 are the x-axis pixels with
values x1, x2, and Y1, Y2 the y-axis pixels with values y1, y2. The x-axis
points lie on a line of constant data-y and the y-axis points on a line of
constant data-x. With the pixel basis ``dX = X2 - X1``, ``dY = Y2 - Y1``:

- ``P - X1 = a*dX + b*dY`` gives ``u_x = ux1 + a*(ux2 - ux1)``;
- ``P - Y1 = c*dX + d*dY`` gives ``u_y = uy1 + d*(uy2 - uy1)``.

The inverse (data to pixel) solves for ``a`` and ``d`` from the data, then
``P = X1 + a*dX + (d + k)*dY`` where ``k`` is the ``dY`` coefficient of
``Y1 - X1`` in the same basis. Axis-aligned figures reduce to ordinary linear
or log interpolation. Pixel y grows downward; the basis math needs no special
case for that.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import numbers
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..data import Series

SCALES = ("linear", "log")
KINDS = ("curve", "scatter", "bars")
_EPS = 1e-9


class DigitizeError(ValueError):
    """Bad calibration or click data. The message is meant for the person clicking."""


# --- small validators -------------------------------------------------------------

def _num(v: Any, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, numbers.Real) or not math.isfinite(float(v)):
        raise DigitizeError(f"{what} must be a finite number")
    return float(v)


def _pair(v: Any, what: str) -> tuple[float, float]:
    if not isinstance(v, (list, tuple, np.ndarray)) or len(v) != 2:
        raise DigitizeError(f"{what} must be [x, y]")
    return _num(v[0], what), _num(v[1], what)


def _int(v: Any, what: str) -> int:
    f = _num(v, what)
    if not f.is_integer():
        raise DigitizeError(f"{what} must be an integer")
    return int(f)


def _points(arr_like: Any, what: str) -> np.ndarray:
    try:
        arr = np.asarray(arr_like, dtype=float)
    except (TypeError, ValueError):
        raise DigitizeError(f"{what} points must be pairs of numbers") from None
    if arr.size == 0:
        return np.zeros((0, 2))
    if arr.ndim == 1 and arr.size == 2:
        arr = arr.reshape(1, 2)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise DigitizeError(f"{what} points must be an (N, 2) array")
    if not np.all(np.isfinite(arr)):
        raise DigitizeError(f"{what} points must be finite")
    return arr


def _ut(scale: str, v: float) -> float:
    if scale == "log":
        if not v > 0:
            raise DigitizeError("log axis needs values > 0")
        return math.log10(v)
    return float(v)


def _ua(scale: str, v: Any) -> np.ndarray:
    arr = np.asarray(v, dtype=float)
    if not np.all(np.isfinite(arr)):
        raise DigitizeError("values must be finite numbers")
    if scale == "log":
        if np.any(arr <= 0):
            raise DigitizeError("log axis needs values > 0")
        return np.log10(arr)
    return arr


def _from_u(scale: str, u: np.ndarray) -> np.ndarray:
    return np.power(10.0, u) if scale == "log" else u


def _sig(v: Any) -> float:
    return float(f"{float(v):.6g}")


def _px(v: Any) -> float:
    return round(float(v), 2)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _color(v: Any) -> list[int | float] | None:
    if v is None:
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        raise DigitizeError("color must be [r, g, b, a]")
    out: list[int | float] = []
    for c in v:
        f = _num(c, "color channel")
        out.append(int(f) if f.is_integer() else f)
    return out


# --- calibration ------------------------------------------------------------------

@dataclass
class Axis:
    """One axis: two calibration ticks, each a pixel (x, y) and its value."""

    scale: str  # "linear" | "log" (log10)
    p1: tuple[float, float]
    v1: float
    p2: tuple[float, float]
    v2: float

    def __post_init__(self) -> None:
        if self.scale not in SCALES:
            raise DigitizeError(f"axis scale must be one of {', '.join(SCALES)}, got {self.scale!r}")
        self.p1 = _pair(self.p1, "axis point 1 px")
        self.p2 = _pair(self.p2, "axis point 2 px")
        self.v1 = _num(self.v1, "axis point 1 value")
        self.v2 = _num(self.v2, "axis point 2 value")
        if self.v1 == self.v2:
            raise DigitizeError("the two calibration values on an axis must differ (v1 == v2)")
        if self.scale == "log" and (self.v1 <= 0 or self.v2 <= 0):
            raise DigitizeError("log axis calibration values must be > 0")


def _axis_from_json(obj: Any, name: str) -> Axis:
    if not isinstance(obj, dict):
        raise DigitizeError(f"calibration.{name} must be an object")
    scale = obj.get("scale", "linear")
    pts = obj.get("points")
    if not isinstance(pts, list) or len(pts) != 2:
        raise DigitizeError(f"{name} axis needs exactly two calibration points")
    parsed = []
    for i, pt in enumerate(pts, start=1):
        if not isinstance(pt, dict):
            raise DigitizeError(f"{name} axis point {i} must be an object")
        px = _pair(pt.get("px"), f"{name} axis point {i} px")
        value = _num(pt.get("value"), f"{name} axis point {i} value")
        parsed.append((px, value))
    return Axis(scale=scale, p1=parsed[0][0], v1=parsed[0][1], p2=parsed[1][0], v2=parsed[1][1])


@dataclass
class Calibration:
    x: Axis
    y: Axis

    def __post_init__(self) -> None:
        if not isinstance(self.x, Axis) or not isinstance(self.y, Axis):
            raise DigitizeError("calibration needs an x and a y axis")
        dx = np.subtract(self.x.p2, self.x.p1)
        dy = np.subtract(self.y.p2, self.y.p1)
        nx, ny = float(np.hypot(*dx)), float(np.hypot(*dy))
        if nx <= _EPS:
            raise DigitizeError("the two x-axis calibration points are identical; click two different ticks")
        if ny <= _EPS:
            raise DigitizeError("the two y-axis calibration points are identical; click two different ticks")
        cross = float(dx[0] * dy[1] - dx[1] * dy[0])
        if abs(cross) <= _EPS * nx * ny:
            raise DigitizeError("the x and y axes are parallel; the two axes must not be parallel")

    @classmethod
    def from_json(cls, obj: Any) -> "Calibration":
        if not isinstance(obj, dict):
            raise DigitizeError("calibration must be an object with x and y axes")
        return cls(x=_axis_from_json(obj.get("x"), "x"), y=_axis_from_json(obj.get("y"), "y"))

    def to_json(self) -> dict[str, Any]:
        def axis(a: Axis) -> dict[str, Any]:
            return {
                "scale": a.scale,
                "points": [
                    {"px": [a.p1[0], a.p1[1]], "value": a.v1},
                    {"px": [a.p2[0], a.p2[1]], "value": a.v2},
                ],
            }

        return {"x": axis(self.x), "y": axis(self.y)}

    def _basis(self) -> np.ndarray:
        x1, x2 = np.asarray(self.x.p1, dtype=float), np.asarray(self.x.p2, dtype=float)
        y1, y2 = np.asarray(self.y.p1, dtype=float), np.asarray(self.y.p2, dtype=float)
        return np.column_stack([x2 - x1, y2 - y1])  # columns: dX, dY

    def to_data(self, px: Any) -> np.ndarray:
        """Pixel (N, 2) to data (N, 2) of (x, y)."""
        arr = _points(px, "pixel")
        if arr.shape[0] == 0:
            return np.zeros((0, 2))
        m = self._basis()
        x1, y1 = np.asarray(self.x.p1, dtype=float), np.asarray(self.y.p1, dtype=float)
        ab = np.linalg.solve(m, (arr - x1).T)  # row 0: alpha (x), row 1: beta
        dl = np.linalg.solve(m, (arr - y1).T)  # row 1: delta (y)
        ux1 = _ut(self.x.scale, self.x.v1)
        ux2 = _ut(self.x.scale, self.x.v2)
        uy1 = _ut(self.y.scale, self.y.v1)
        uy2 = _ut(self.y.scale, self.y.v2)
        ux = ux1 + ab[0] * (ux2 - ux1)
        uy = uy1 + dl[1] * (uy2 - uy1)
        return np.column_stack([_from_u(self.x.scale, ux), _from_u(self.y.scale, uy)])

    def to_pixel(self, data: Any) -> np.ndarray:
        """Data (N, 2) of (x, y) to pixel (N, 2). Inverse of :meth:`to_data`."""
        arr = _points(data, "data")
        if arr.shape[0] == 0:
            return np.zeros((0, 2))
        m = self._basis()
        x1, y1 = np.asarray(self.x.p1, dtype=float), np.asarray(self.y.p1, dtype=float)
        u = _ua(self.x.scale, arr[:, 0])
        w = _ua(self.y.scale, arr[:, 1])
        ux1 = _ut(self.x.scale, self.x.v1)
        ux2 = _ut(self.x.scale, self.x.v2)
        uy1 = _ut(self.y.scale, self.y.v1)
        uy2 = _ut(self.y.scale, self.y.v2)
        alpha = (u - ux1) / (ux2 - ux1)
        delta = (w - uy1) / (uy2 - uy1)
        c = np.linalg.solve(m, y1 - x1)  # Y1 - X1 = c1*dX + c2*dY
        return x1 + alpha[:, None] * m[:, 0] + (delta + c[1])[:, None] * m[:, 1]


# --- curves-json document ---------------------------------------------------------

def _series_entry(
    name: str, kind: str, cal: Calibration, points: list[Any], color: Any, orig: Series | None = None
) -> tuple[dict[str, Any], bool]:
    n = len(points)
    px: list[tuple[float, float]] = []
    err_px: list[tuple[float, float] | None] = []
    src: list[int | None] = []
    snapped: list[bool] = []
    labels: list[str] = []
    for i, p in enumerate(points, start=1):
        where = f"{name!r} point {i}"
        if not isinstance(p, dict):
            raise DigitizeError(f"{where} must be an object")
        px.append(_pair(p.get("px"), f"{where} px"))
        e = p.get("err_px")
        err_px.append(None if e is None else _pair(e, f"{where} err_px"))
        s = p.get("src")
        src.append(None if s is None else _int(s, f"{where} src"))
        snapped.append(bool(p.get("snapped", False)))
        if kind == "bars":
            lab = p.get("label")
            if not isinstance(lab, str) or not lab.strip():
                raise DigitizeError(f"{where}: bars points need a non-empty label")
            labels.append(lab.strip())

    data = cal.to_data(px)
    if not np.all(np.isfinite(data)):
        raise DigitizeError(f"series {name!r}: a point falls outside the calibrated range")
    xs, ys = data[:, 0], data[:, 1]

    yerr = np.zeros(n)
    has_err = any(e is not None for e in err_px)
    if has_err:
        idx = [i for i, e in enumerate(err_px) if e is not None]
        ey = cal.to_data([err_px[i] for i in idx])[:, 1]
        yerr[idx] = np.abs(ey - ys[idx])

    # review: points carried over from the original and never moved keep its exact values
    kept = [False] * n
    if orig is not None:
        for i, p in enumerate(points):
            s = src[i]
            if s is None or p.get("moved") or not 0 <= s < len(orig):
                continue
            if orig.categorical != (kind == "bars"):
                continue
            kept[i] = True
            ys[i] = float(orig.y[s])
            if kind == "bars":
                labels[i] = str(orig.x[s])
            else:
                xs[i] = float(orig.xf[s])
            if orig.yerr is not None and err_px[i] is not None:
                yerr[i] = float(orig.yerr[s])
    if kind == "bars":
        seen: set[str] = set()
        for lab in labels:
            if lab in seen:
                raise DigitizeError(f"series {name!r}: duplicate bars label {lab!r}")
            seen.add(lab)

    order = list(range(n))
    if kind == "curve":
        order.sort(key=lambda i: xs[i])  # stable: ties keep click order

    def r(v: float, i: int) -> float:
        return float(v) if kept[i] else _sig(v)

    x_out: list[Any] = [labels[i] for i in order] if kind == "bars" else [r(xs[i], i) for i in order]
    entry: dict[str, Any] = {"n": n, "xy": {"x": x_out, "y": [r(ys[i], i) for i in order]}}
    if kind != "curve":
        entry["kind"] = kind
    if has_err:
        entry["yerr"] = [r(yerr[i], i) for i in order]
    col = _color(color)
    if col is not None:
        entry["color_rgba"] = col
    entry["meta"] = {
        "px": [[_px(px[i][0]), _px(px[i][1])] for i in order],
        "err_px": [
            None if err_px[i] is None else [_px(err_px[i][0]), _px(err_px[i][1])]  # type: ignore[index]
            for i in order
        ],
        "src": [src[i] for i in order],
        "snapped": [snapped[i] for i in order],
    }
    return entry, any(snapped)


def build_document(
    state: dict,
    *,
    image: dict,
    by: str | None = None,
    assist: dict | None = None,
    now: str | None = None,
    tool_version: str = "",
    original: dict[str, Series] | None = None,
) -> dict:
    """Turn the page's clicks (pixels) into a curves-json document with provenance.

    ``original`` (review): a point with ``src`` and no ``moved`` flag keeps the original
    series' exact x, y (and yerr) instead of values read back from its pixel.
    """
    if not isinstance(state, dict):
        raise DigitizeError("digitize state must be an object")
    cal = Calibration.from_json(state.get("calibration"))
    specs = state.get("series") or []
    if not isinstance(specs, list):
        raise DigitizeError("series must be a list")

    out: dict[str, Any] = {}
    names: set[str] = set()
    any_snapped = False
    for i, spec in enumerate(specs, start=1):
        if not isinstance(spec, dict):
            raise DigitizeError(f"series {i} must be an object")
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            raise DigitizeError(f"series {i} needs a non-empty name")
        if name in names:
            raise DigitizeError(f"duplicate series name {name!r}")
        names.add(name)
        kind = spec.get("kind", "curve")
        if kind not in KINDS:
            raise DigitizeError(f"series {name!r}: kind must be one of {', '.join(KINDS)}, got {kind!r}")
        points = spec.get("points") or []
        if not isinstance(points, list):
            raise DigitizeError(f"series {name!r}: points must be a list")
        if not points:
            continue
        entry, snapped = _series_entry(name, kind, cal, points, spec.get("color"), (original or {}).get(name))
        any_snapped = any_snapped or snapped
        out[name] = entry

    meta: dict[str, Any] = {
        "tool": "pawtrace digitize",
        "tool_version": tool_version,
        "method": "manual",
        "by": "human",
    }
    if by is not None:
        meta["name"] = by
    meta["date"] = now or _utc_now()
    meta["image"] = copy.deepcopy(image)
    meta["calibration"] = cal.to_json()
    meta["assist"] = {"used": any_snapped, "snap_to_color": copy.deepcopy(assist)}
    return {"series": out, "meta": {"digitization": meta}}


# --- residuals --------------------------------------------------------------------

def _tol(v: float) -> float:
    return _EPS * max(1.0, abs(float(v)))


def _rms(vals: list[float]) -> float:
    return float(math.sqrt(sum(v * v for v in vals) / len(vals))) if vals else 0.0


def _maxabs(vals: list[float]) -> float:
    return max(abs(v) for v in vals) if vals else 0.0


def _round_out(obj: Any) -> Any:
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, float):
        return _sig(obj)
    if isinstance(obj, list):
        return [_round_out(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _round_out(v) for k, v in obj.items()}
    return obj


def _series_residual(name: str, orig: Series, ent: dict) -> dict[str, Any]:
    try:
        xs = list(ent["xy"]["x"])
        ys = [float(v) for v in ent["xy"]["y"]]
    except (KeyError, TypeError, ValueError):
        raise DigitizeError(f"series {name!r} has no usable xy data") from None
    meta = ent.get("meta") or {}
    src_raw = meta.get("src") or [None] * len(ys)
    if len(src_raw) != len(ys) or len(xs) != len(ys):
        raise DigitizeError(f"series {name!r}: x, y and meta.src lengths differ")
    src = [None if s is None else _int(s, f"series {name!r} src") for s in src_raw]

    n_o, n_e = len(orig), len(ys)
    for s in src:
        if s is not None and not 0 <= s < n_o:
            raise DigitizeError(f"series {name!r}: src {s} is outside the original's {n_o} points")
    edited_is_labels = any(isinstance(v, str) for v in xs)
    if orig.categorical != edited_is_labels:
        raise DigitizeError(f"series {name!r}: x changed between labels and numbers")

    matched = [(i, s) for i, s in enumerate(src) if s is not None]
    referenced = {s for _, s in matched}
    oy = np.asarray(orig.y, dtype=float)
    out: dict[str, Any] = {
        "n_original": n_o,
        "n_edited": n_e,
        "added": n_e - len(matched),
        "removed": n_o - len(referenced),
        "y_range": float(np.ptp(oy)) if n_o else 0.0,
    }

    if orig.categorical:
        ox = list(orig.x)
        moved = 0
        dys_all: list[float] = []
        for i, s in matched:
            dy = ys[i] - float(oy[s])
            if xs[i] != ox[s] or abs(dy) > _tol(oy[s]):
                moved += 1
        first: dict[str, int] = {}
        for j, lab in enumerate(ox):
            first.setdefault(lab, j)
        for i in range(n_e):
            lab = xs[i]
            if lab in first:
                dys_all.append(ys[i] - float(oy[first[lab]]))
        edited_labels = [str(v) for v in xs]
        present = set(edited_labels)
        labels_added: list[str] = []
        for lab in edited_labels:
            if lab not in first and lab not in labels_added:
                labels_added.append(lab)
        out.update(
            moved=moved,
            max_abs_dy=_maxabs(dys_all),
            rms_dy=_rms(dys_all),
            labels_added=labels_added,
            labels_removed=[lab for lab in first if lab not in present],
        )
        return out

    ox = np.asarray(orig.xf, dtype=float)
    moved = 0
    dxs: list[float] = []
    dys: list[float] = []
    for i, s in matched:
        dx = float(xs[i]) - float(ox[s])
        dy = ys[i] - float(oy[s])
        if abs(dx) > _tol(ox[s]) or abs(dy) > _tol(oy[s]):
            moved += 1
        dxs.append(dx)
        dys.append(dy)

    curve_rms: float | None = None
    if n_e >= 2:
        ex = np.asarray([float(v) for v in xs], dtype=float)
        ey = np.asarray(ys, dtype=float)
        order = np.argsort(ex, kind="stable")
        exs, eys = ex[order], ey[order]
        sel = (ox >= exs[0]) & (ox <= exs[-1])
        if bool(sel.any()):
            resid = np.interp(ox[sel], exs, eys) - oy[sel]
            curve_rms = float(math.sqrt(float(np.mean(resid * resid))))
    out.update(
        moved=moved,
        max_abs_dy=_maxabs(dys),
        rms_dy=_rms(dys),
        max_abs_dx=_maxabs(dxs),
        curve_rms=curve_rms,
    )
    return out


def residual(original: dict[str, Series], doc: dict) -> dict:
    """Compare original series with an edited build_document result.

    ``meta.src[i]`` indexes the original series as passed in (file order);
    numeric curves are interpolated on a sorted copy.
    """
    edited = doc.get("series") if isinstance(doc, dict) else None
    if not isinstance(edited, dict):
        raise DigitizeError("document has no series")
    series_out = {
        name: _series_residual(name, original[name], ent)
        for name, ent in edited.items()
        if name in original
    }
    result = {
        "series": series_out,
        "added_series": [n for n in edited if n not in original],
        "removed_series": [n for n in original if n not in edited],
    }
    return _round_out(result)


# --- review and IO ----------------------------------------------------------------

def apply_review(
    doc: dict,
    *,
    original: dict,
    residual: dict,
    by: str | None = None,
    now: str | None = None,
    previous: dict | None = None,
) -> dict:
    """Return ``doc`` with ``meta.review`` (and ``meta.history`` when overwriting a file)."""
    out = copy.deepcopy(doc)
    stamp = now or _utc_now()
    meta = out.setdefault("meta", {})
    if previous is not None:
        prev = copy.deepcopy(previous)
        prev_meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
        history = list(prev_meta.pop("history", None) or [])
        history.append({"replaced": stamp, "meta": prev_meta, "series": prev.get("series")})
        meta["history"] = history
    review: dict[str, Any] = {"reviewed_by": "human"}
    if by is not None:
        review["name"] = by
    review["date"] = stamp
    review["original"] = copy.deepcopy(original)
    review["residual"] = copy.deepcopy(residual)
    meta["review"] = review
    return out


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_document(path: str | Path, doc: dict) -> None:
    """Write atomically: a ``.tmp`` sibling, then ``os.replace``."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    text = json.dumps(doc, indent=1, allow_nan=False) + "\n"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def load_document(path: str | Path) -> dict | None:
    """Raw JSON if the file is a dict with a ``series`` dict; None when missing or not one.

    Unparseable JSON raises DigitizeError rather than returning None, so an
    in-place overwrite never silently drops a file that could not be read.
    """
    p = Path(path)
    if not p.is_file():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise DigitizeError(f"{p} is not valid JSON: {e}") from None
    if isinstance(payload, dict) and isinstance(payload.get("series"), dict):
        return payload
    return None
