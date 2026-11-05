"""The shared gate library: metric ops and checks that panel gate configs name.

A panel's gates are config, not code. ``metrics:`` builds named values from the
run's series and the digitized references with **ops**; ``gates:`` tests those
values with **checks**. Both live in registries here so every paper shares one
vetted set instead of hand-coding ``<paper>_gates.py``.

Values flowing between ops are one of:

- a :class:`~pawtrace.data.Series` (numeric or label x),
- a vector (1-D float array, e.g. per-episode means; NaN marks a missing entry),
- a scalar float (NaN when undefined — every check fails on NaN).

Windows slice by **real x** (steps, seconds, Hz), never by sample fraction.
``head_mean`` / ``tail_mean`` are the only index-based ops, for "first/last N
samples" rules.

Adding an op: ``@op("name", refs=("of",))`` where ``refs`` lists the arguments
that name another metric or series (everything else is a literal). Adding a
check: ``@check("name", refs=(...))`` returning ``bool``.
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..data import Series


@dataclass(frozen=True)
class Spec:
    fn: Callable[..., Any]
    refs: tuple[str, ...]
    doc: str


OPS: dict[str, Spec] = {}
CHECKS: dict[str, Spec] = {}

NAN = float("nan")


def op(name: str, refs: tuple[str, ...] = ("of",)) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        OPS[name] = Spec(fn, refs, (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "")
        return fn

    return deco


def check(name: str, refs: tuple[str, ...]) -> Callable[[Callable[..., bool]], Callable[..., bool]]:
    def deco(fn: Callable[..., bool]) -> Callable[..., bool]:
        CHECKS[name] = Spec(fn, refs, (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "")
        return fn

    return deco


def _finite(*vals: Any) -> bool:
    try:
        return all(v is not None and math.isfinite(float(v)) for v in vals)
    except (TypeError, ValueError):
        return False


def _series(v: Any, opname: str) -> Series:
    if not isinstance(v, Series):
        raise TypeError(f"{opname}: expected a series, got {type(v).__name__}")
    return v


def _vector(v: Any, opname: str) -> np.ndarray:
    if isinstance(v, Series):
        return v.y
    arr = np.asarray(v, dtype=float)
    if arr.ndim != 1:
        raise TypeError(f"{opname}: expected a vector")
    return arr


def _mask(x: np.ndarray, lo: float | None, hi: float | None, closed: str) -> np.ndarray:
    m = np.ones(x.shape, dtype=bool)
    if lo is not None:
        m &= x >= lo
    if hi is not None:
        m &= (x <= hi) if closed == "both" else (x < hi)
    return m


def _mean(y: np.ndarray) -> float:
    return float(np.mean(y)) if y.size else NAN


# ---------------------------------------------------------------------------
# ops: series -> scalar
# ---------------------------------------------------------------------------


@op("length")
def length(of: Any) -> float:
    """Number of samples in a series or entries in a vector."""
    return float(len(of) if isinstance(of, Series) else _vector(of, "length").size)


@op("window_mean")
def window_mean(of: Series, lo: float | None = None, hi: float | None = None, closed: str = "both") -> float:
    """Mean of y over lo <= x <= hi (closed: both) or lo <= x < hi (closed: left); open ends allowed."""
    s = _series(of, "window_mean")
    return _mean(s.y[_mask(s.xf, lo, hi, closed)])


@op("window_stat")
def window_stat(
    of: Series,
    stat: str,
    lo: float | None = None,
    hi: float | None = None,
    closed: str = "both",
    q: float | None = None,
) -> float:
    """min / max / median / std / mean / percentile (with ``q`` in 0-100) of y over an x window.

    ``percentile`` uses numpy's default linear interpolation, so ``q: 25`` / ``q: 75``
    are the usual box-plot quartiles of a sample.
    """
    s = _series(of, "window_stat")
    y = s.y[_mask(s.xf, lo, hi, closed)]
    if not y.size:
        return NAN
    if stat == "percentile":
        if q is None:
            raise TypeError("window_stat: stat percentile needs q (0-100)")
        return float(np.percentile(y, float(q)))
    fns = {"min": np.min, "max": np.max, "median": np.median, "std": np.std, "mean": np.mean}
    return float(fns[stat](y))


@op("window_fraction")
def window_fraction(
    of: Series,
    lo: float | None = None,
    hi: float | None = None,
    closed: str = "both",
    at_least: float | None = None,
    at_most: float | None = None,
) -> float:
    """Fraction of samples in an x window with y >= at_least (or y <= at_most); NaN if empty.

    E.g. the share of late episodes that ran into the step limit.
    """
    s = _series(of, "window_fraction")
    if (at_least is None) == (at_most is None):
        raise TypeError("window_fraction needs exactly one of at_least / at_most")
    y = s.y[_mask(s.xf, lo, hi, closed)]
    if not y.size:
        return NAN
    hit = y >= float(at_least) if at_least is not None else y <= float(at_most)  # type: ignore[arg-type]
    return float(np.mean(hit))


@op("head_mean")
def head_mean(of: Series, n: int) -> float:
    """Mean of the first n samples (index-based)."""
    y = _series(of, "head_mean").y
    return _mean(y[: min(int(n), y.size)])


@op("tail_mean")
def tail_mean(of: Series, n: int) -> float:
    """Mean of the last n samples (index-based)."""
    y = _series(of, "tail_mean").y
    return _mean(y[max(0, y.size - int(n)) :])


@op("within_block_std")
def within_block_std(of: Series, lo: float, hi: float, block: float, min_n: int = 4) -> float:
    """Std of y minus its own block mean (block = floor(x / block)) over lo <= x < hi.

    Measures step-to-step spread inside episodes/trials without the slow trend.
    """
    s = _series(of, "within_block_std")
    m = (s.xf >= lo) & (s.xf < hi)
    x, y = s.xf[m], s.y[m]
    if y.size < min_n:
        return NAN
    blk = np.floor(x / block)
    resid = np.empty_like(y)
    for b in np.unique(blk):
        g = blk == b
        resid[g] = y[g] - np.mean(y[g])
    return float(np.std(resid))


@op("value_at")
def value_at(of: Series, label: str | None = None, x: float | None = None) -> float:
    """y at a category label (bars/tables) or interpolated at numeric x (curves)."""
    s = _series(of, "value_at")
    if label is not None:
        if not s.categorical:
            raise TypeError("value_at: label given but series has numeric x")
        try:
            return float(s.y[s.x.index(str(label))])  # type: ignore[union-attr]
        except ValueError:
            return NAN
    if x is None:
        raise TypeError("value_at needs label or x")
    if s.categorical or len(s) == 0:
        return NAN
    if x < s.xf.min() or x > s.xf.max():
        return NAN
    return float(np.interp(x, s.xf, s.y))


# ---------------------------------------------------------------------------
# ops: series -> series
# ---------------------------------------------------------------------------


@op("resample", refs=("of", "at"))
def resample(of: Series, at: Series) -> Series:
    """`of` linearly interpolated at `at`'s x (compare two curves at the same x)."""
    a, b = _series(of, "resample"), _series(at, "resample")
    return Series(x=b.xf.copy(), y=np.interp(b.xf, a.xf, a.y))


@op("smooth")
def smooth(of: Series, k: int, edges: str = "pad") -> Series:
    """Centered k-point running mean (samples i - k//2 ... i - k//2 + k - 1).

    ``edges: pad`` repeats the end samples to fill the window; ``edges: shrink``
    averages only the samples that exist, so the window shrinks at the ends.
    """
    s = _series(of, "smooth")
    y = s.y
    k = int(k)
    if k <= 1 or y.size < 2:
        return Series(x=s.xf.copy(), y=y.copy())
    if edges == "pad":
        padded = np.pad(y, (k // 2, k - 1 - k // 2), mode="edge")
        return Series(x=s.xf.copy(), y=np.convolve(padded, np.ones(k) / k, mode="valid"))
    if edges != "shrink":
        raise ValueError("smooth: edges must be pad or shrink")
    n = y.size
    csum = np.concatenate([[0.0], np.cumsum(y)])
    idx = np.arange(n)
    lo = np.clip(idx - k // 2, 0, n)
    hi = np.clip(idx - k // 2 + k, 0, n)
    return Series(x=s.xf.copy(), y=(csum[hi] - csum[lo]) / (hi - lo))


@op("on_grid")
def on_grid(of: Series, lo: float, hi: float, n: int) -> Series:
    """`of` linearly interpolated at n evenly spaced x from lo to hi (both included).

    Outside the series' x range the end values are held (numpy ``interp``), so a
    digitized curve that stops short of ``hi`` stays flat there.
    """
    s = _series(of, "on_grid")
    grid = np.linspace(float(lo), float(hi), int(n))
    if len(s) == 0:
        return Series(x=grid, y=np.full(grid.size, NAN))
    return Series(x=grid, y=np.interp(grid, s.xf, s.y))


@op("progress", refs=("of", "start", "end"))
def progress(of: Series, start: float, end: float, direction: str | None = None) -> Series:
    """How far each sample has moved from `start` toward `end`: (y - start) / (end - start).

    0 = still at the start level, 1 = reached the end level, whichever way the curve
    moves. ``direction: down`` / ``up`` require end < start / end > start; otherwise
    (and when start ~= end) every value is NaN, so milestones only count when the
    curve moves the expected way.
    """
    s = _series(of, "progress")
    nan = Series(x=s.xf.copy(), y=np.full(len(s), NAN))
    if not _finite(start, end):
        return nan
    a, b = float(start), float(end)
    if abs(b - a) <= 1e-9 * max(abs(a), 1.0):
        return nan
    if direction == "down" and not b < a:
        return nan
    if direction == "up" and not b > a:
        return nan
    if direction not in (None, "up", "down"):
        raise ValueError("progress: direction must be up or down")
    return Series(x=s.xf.copy(), y=(s.y - a) / (b - a))


@op("first_x")
def first_x(
    of: Series,
    at_least: float | None = None,
    at_most: float | None = None,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    """First x (inside an optional lo..hi window) where y >= at_least (or y <= at_most); NaN if never.

    With :func:`progress`, ``at_least: 0.5`` is the time or episode at which a curve
    has covered half its drop or rise.
    """
    s = _series(of, "first_x")
    if (at_least is None) == (at_most is None):
        raise TypeError("first_x needs exactly one of at_least / at_most")
    m = _mask(s.xf, lo, hi, "both")
    x, y = s.xf[m], s.y[m]
    hit = np.flatnonzero(y >= float(at_least) if at_least is not None else y <= float(at_most))  # type: ignore[arg-type]
    return float(x[hit[0]]) if hit.size else NAN


@op("select")
def select(of: Series, lo: float | None = None, hi: float | None = None, closed: str = "both") -> Series:
    """The part of a series inside an x window."""
    s = _series(of, "select")
    m = _mask(s.xf, lo, hi, closed)
    return Series(x=s.xf[m], y=s.y[m])


# ---------------------------------------------------------------------------
# ops: series -> vector, vector -> scalar/vector
# ---------------------------------------------------------------------------


@op("block_means")
def block_means(of: Series, width: float, count: int, start: float = 0.0, min_count: int = 1) -> np.ndarray:
    """Means over consecutive x blocks [start + i*width, start + (i+1)*width), i < count.

    A block with fewer than ``min_count`` samples is NaN (missing), so an
    incomplete trailing episode doesn't count as an episode.
    """
    s = _series(of, "block_means")
    out = np.full(int(count), NAN)
    for i in range(int(count)):
        lo = start + i * width
        y = s.y[(s.xf >= lo) & (s.xf < lo + width)]
        if y.size >= max(1, int(min_count)):
            out[i] = float(np.mean(y))
    return out


@op("values")
def values(of: Series) -> np.ndarray:
    """A series' y as a vector (e.g. bar heights in label order)."""
    return _series(of, "values").y.copy()


@op("slice_mean")
def slice_mean(of: Any, start: int | None = None, stop: int | None = None) -> float:
    """Mean of vector[start:stop]; NaN if any entry in the slice is missing."""
    v = _vector(of, "slice_mean")[start:stop]
    return float(np.mean(v)) if v.size else NAN


@op("pearson", refs=("a", "b"))
def pearson(a: Any, b: Any, min_n: int = 4) -> float:
    """Pearson r of two vectors/series y.

    Trailing missing entries are dropped (an incomplete run hasn't reached them);
    a missing entry anywhere else makes r undefined (broken data, not a shorter run).
    """
    va, vb = _vector(a, "pearson"), _vector(b, "pearson")
    n = min(va.size, vb.size)
    va, vb = va[:n], vb[:n]
    ok = np.isfinite(va) & np.isfinite(vb)
    last = int(np.flatnonzero(ok)[-1]) + 1 if ok.any() else 0
    if not ok[:last].all():
        return NAN
    va, vb = va[:last], vb[:last]
    if va.size < min_n or np.std(va) <= 1e-12 or np.std(vb) <= 1e-12:
        return NAN
    return float(np.corrcoef(va, vb)[0, 1])


@op("pearson_curves", refs=("a", "b"))
def pearson_curves(a: Series, b: Series, lo: float | None = None, hi: float | None = None, n: int = 200) -> float:
    """Pearson r of two curves on a uniform grid over their x overlap (density-independent)."""
    sa, sb = _series(a, "pearson_curves"), _series(b, "pearson_curves")
    if len(sa) < 4 or len(sb) < 4:
        return NAN
    lo_ = max(float(sa.xf.min()), float(sb.xf.min()), -np.inf if lo is None else lo)
    hi_ = min(float(sa.xf.max()), float(sb.xf.max()), np.inf if hi is None else hi)
    if not hi_ > lo_:
        return NAN
    grid = np.linspace(lo_, hi_, int(n))
    ya, yb = np.interp(grid, sa.xf, sa.y), np.interp(grid, sb.xf, sb.y)
    if np.std(ya) < 1e-12 or np.std(yb) < 1e-12:
        return NAN
    return float(np.corrcoef(ya, yb)[0, 1])


@op("half_crossing")
def half_crossing(of: Any, head: int = 4, tail: int = 4, min_n: int = 8) -> float:
    """First index reaching half the drop from mean(first `head`) to mean(last `tail`).

    Uses the leading run of present entries; NaN if shorter than ``min_n`` or never reached.
    """
    v = _vector(of, "half_crossing")
    bad = np.flatnonzero(~np.isfinite(v))
    v = v[: bad[0]] if bad.size else v
    if v.size < min_n:
        return NAN
    hi, lo = float(np.mean(v[:head])), float(np.mean(v[-tail:]))
    hit = np.flatnonzero(v <= hi - 0.5 * (hi - lo))
    return float(hit[0]) if hit.size else NAN


@op("rel_err", refs=("a", "b"))
def rel_err(a: Any, b: Any) -> np.ndarray:
    """Elementwise (a - b) / |b|."""
    va, vb = _vector(a, "rel_err"), _vector(b, "rel_err")
    n = min(va.size, vb.size)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (va[:n] - vb[:n]) / np.abs(vb[:n])


@op("max_abs")
def max_abs(of: Any) -> float:
    """max |v|; NaN if any entry is missing."""
    v = _vector(of, "max_abs")
    if not v.size or not np.all(np.isfinite(v)):
        return NAN
    return float(np.max(np.abs(v)))


# ---------------------------------------------------------------------------
# ops: scalar arithmetic
# ---------------------------------------------------------------------------


def _elementwise(a: Any, b: Any) -> bool:
    return isinstance(a, (Series, np.ndarray, list)) or isinstance(b, (Series, np.ndarray, list))


def _arith(a: Any, b: Any, fn: Callable[[Any, Any], Any], opname: str) -> Any:
    """Scalars give a float; a series or vector on either side works elementwise.

    Two series must share the same x; the result keeps it. A scalar broadcasts.
    """
    if not _elementwise(a, b):
        return float(fn(float(a), float(b)))
    x = None
    for v in (a, b):
        if isinstance(v, Series):
            if v.categorical:
                raise TypeError(f"{opname}: label series aren't supported elementwise")
            if x is not None and (x.size != v.xf.size or not np.array_equal(x, v.xf)):
                raise ValueError(f"{opname}: series have different x (resample them onto one grid first)")
            x = v.xf
    va = a.y if isinstance(a, Series) else np.asarray(a, dtype=float)
    vb = b.y if isinstance(b, Series) else np.asarray(b, dtype=float)
    if va.ndim and vb.ndim and va.size != vb.size:
        raise ValueError(f"{opname}: lengths differ ({va.size} vs {vb.size})")
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.asarray(fn(va, vb), dtype=float)
    return Series(x=x.copy(), y=out) if x is not None else out


@op("sub", refs=("a", "b"))
def sub(a: Any, b: Any) -> Any:
    """a - b (scalars; or elementwise for series on the same x / vectors)."""
    return _arith(a, b, lambda p, q: p - q, "sub")


@op("add", refs=("a", "b"))
def add(a: Any, b: Any) -> Any:
    """a + b (scalars; or elementwise for series on the same x / vectors)."""
    return _arith(a, b, lambda p, q: p + q, "add")


@op("mul", refs=("a", "b"))
def mul(a: Any, b: Any) -> Any:
    """a * b (scalars; or elementwise for series on the same x / vectors)."""
    return _arith(a, b, lambda p, q: p * q, "mul")


@op("ratio", refs=("a", "b"))
def ratio(a: Any, b: Any, eps: float = 1e-12) -> Any:
    """a / b; NaN when |b| < eps or either is missing (elementwise for series / vectors)."""
    if _elementwise(a, b):
        return _arith(a, b, lambda p, q: np.where(np.abs(q) < eps, NAN, p / q), "ratio")
    if not _finite(a, b) or abs(float(b)) < eps:
        return NAN
    return float(a) / float(b)


@op("abs")
def abs_(of: Any) -> Any:
    """|value| (a scalar, or elementwise for a series / vector)."""
    if isinstance(of, Series):
        return Series(x=_series(of, "abs").xf.copy(), y=np.abs(of.y))
    if isinstance(of, (np.ndarray, list)):
        return np.abs(np.asarray(of, dtype=float))
    return abs(float(of))


@op("across")
def across(of: list[Any], stat: str) -> float:
    """mean / min / max / median / std / ptp (max - min) across several scalars.

    ``of`` is a list of metric names or numbers, e.g. the pre-onset level of every
    condition (``ptp`` = their spread) or ``[our_value, 25]`` with ``min`` for a cap.
    Any missing entry makes the result NaN.
    """
    if not isinstance(of, (list, tuple)):
        raise TypeError("across: of must be a list of metrics or numbers")
    v = np.asarray([float(x) for x in of], dtype=float)
    if not v.size or not np.all(np.isfinite(v)):
        return NAN
    fns = {"mean": np.mean, "min": np.min, "max": np.max, "median": np.median, "std": np.std, "ptp": np.ptp}
    return float(fns[stat](v))


@op("only_if", refs=("value", "of"))
def only_if(value: Any, of: float, min: float | None = None, max: float | None = None) -> Any:  # noqa: A002
    """`value` when the precondition metric `of` is within [min, max]; NaN otherwise.

    E.g. late-window levels only count once the run reached the late window:
    ``{op: only_if, value: late_mean, of: n_episodes, min: 500}``.
    """
    ok = _finite(of) and (min is None or float(of) >= float(min)) and (max is None or float(of) <= float(max))
    if ok:
        return value
    if isinstance(value, Series):
        return Series(x=value.x, y=np.full(len(value), NAN), kind=value.kind)
    if isinstance(value, np.ndarray):
        return np.full(value.shape, NAN)
    return NAN


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


@check("equals", refs=("value",))
def equals(value: float, expected: float) -> bool:
    """value == expected (counts, lengths)."""
    return _finite(value) and float(value) == float(expected)


@check("less_than", refs=("a", "b"))
def less_than(a: float, b: float) -> bool:
    """a < b."""
    return _finite(a, b) and float(a) < float(b)


@check("greater_than", refs=("a", "b"))
def greater_than(a: float, b: float) -> bool:
    """a > b."""
    return _finite(a, b) and float(a) > float(b)


@check("at_least", refs=("value",))
def at_least(value: float, min: float) -> bool:  # noqa: A002
    """value >= min."""
    return _finite(value) and float(value) >= float(min)


@check("at_most", refs=("value",))
def at_most(value: float, max: float) -> bool:  # noqa: A002
    """value <= max."""
    return _finite(value) and float(value) <= float(max)


@check("in_range", refs=("value",))
def in_range(value: float, lo: float, hi: float, closed: str = "both") -> bool:
    """lo <= value <= hi; ``closed: right`` makes it lo < value <= hi, ``left`` lo <= value < hi, ``neither`` both strict."""
    if not _finite(value):
        return False
    v, a, b = float(value), float(lo), float(hi)
    if closed not in ("both", "left", "right", "neither"):
        raise ValueError("in_range: closed must be both, left, right or neither")
    lo_ok = v >= a if closed in ("both", "left") else v > a
    hi_ok = v <= b if closed in ("both", "right") else v < b
    return lo_ok and hi_ok


@check("rel_close", refs=("value", "ref"))
def rel_close(value: float, ref: float, tol: float) -> bool:
    """|value - ref| / |ref| <= tol."""
    if not _finite(value, ref):
        return False
    return abs(float(value) - float(ref)) / max(abs(float(ref)), 1e-9) <= float(tol)


@check("ratio_in_band", refs=("value", "ref"))
def ratio_in_band(value: float, ref: float, band: list[float], ref_positive: bool = False) -> bool:
    """band[0] <= value / ref <= band[1] (optionally requiring ref > 0)."""
    if not _finite(value, ref) or abs(float(ref)) < 1e-12:
        return False
    if ref_positive and float(ref) <= 0:
        return False
    q = float(value) / float(ref)
    return float(band[0]) <= q <= float(band[1])


@check("at_least_fraction", refs=("value", "ref"))
def at_least_fraction(value: float, ref: float, frac: float) -> bool:
    """value >= frac * ref (e.g. our drop is at least 70% of the paper's)."""
    return _finite(value, ref) and float(value) >= float(frac) * float(ref)


@check("abs_diff_at_most", refs=("a", "b"))
def abs_diff_at_most(a: float, b: float, max: float) -> bool:  # noqa: A002
    """|a - b| <= max."""
    return _finite(a, b) and abs(float(a) - float(b)) <= float(max)


@check("ordered", refs=("values",))
def ordered(values: list[float], direction: str = "decreasing", strict: bool = True) -> bool:
    """values are monotone in the given direction (bar/condition ordering)."""
    v = [float(x) for x in values]
    if len(v) < 2 or not _finite(*v):
        return False
    pairs = list(zip(v, v[1:]))
    if direction == "decreasing":
        return all((a > b) if strict else (a >= b) for a, b in pairs)
    if direction == "increasing":
        return all((a < b) if strict else (a <= b) for a, b in pairs)
    raise ValueError("direction must be increasing or decreasing")


@check("label_order", refs=("of",))
def label_order(of: Series, order: list[str], strict: bool = True) -> bool:
    """A label series' values follow ``order`` from highest to lowest."""
    vals = [value_at(of, label=lab) for lab in order]
    return ordered(vals, direction="decreasing", strict=strict)
