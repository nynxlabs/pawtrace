"""The local web server behind ``pawtrace digitize``.

Stdlib only (``http.server``). It binds to loopback, serves one static page
(``static/digitize.html``), the figure image and a small JSON API, and writes the
curves-json file when the page saves. Every request needs the session token the
command prints in the URL, and the ``Host`` header must be loopback, so other
local pages can't drive it (DNS rebinding) and nothing listens on the network.

No model calls, no uploads: the image and the clicks stay on this machine.
"""
from __future__ import annotations

import json
import secrets
import subprocess
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import numpy as np

from .. import __version__, formats
from ..data import Series
from .core import (
    Calibration,
    DigitizeError,
    apply_review,
    build_document,
    load_document,
    residual,
    sha256_file,
    write_document,
)

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
MAX_BODY = 32 * 1024 * 1024


@dataclass
class Figure:
    """The figure the page shows: image bytes plus what provenance records about them."""

    data: bytes
    mime: str
    info: dict[str, Any]  # file name, sha256 of the source file, pdf page/crop/dpi

    @classmethod
    def load(cls, path: Path, *, page: int | None = None, crop: list[float] | None = None, dpi: int = 200) -> "Figure":
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f"figure image not found: {path}")
        ext = path.suffix.lower()
        info: dict[str, Any] = {"file": path.name, "sha256": sha256_file(path)}
        if ext == ".pdf":
            return cls(_render_pdf(path, page or 1, crop, dpi), "image/png", {**info, "page": page or 1, "crop": crop, "dpi": dpi})
        if page is not None or crop is not None:
            raise DigitizeError("--page and --crop are for PDF figures")
        if ext not in IMAGE_TYPES:
            raise DigitizeError(f"unsupported image type {ext!r} (png, jpg, gif, webp, or pdf with --page)")
        return cls(path.read_bytes(), IMAGE_TYPES[ext], info)


def _render_pdf(path: Path, page: int, crop: list[float] | None, dpi: int) -> bytes:
    """Render one PDF page (1-based) to PNG; ``crop`` = x0,y0,x1,y1 in PDF points from the top-left."""
    try:
        import io

        import pypdfium2 as pdfium
    except ImportError as exc:
        raise DigitizeError("PDF figures need the extract extra: pip install pawtrace[extract]") from exc
    doc = pdfium.PdfDocument(str(path))
    if not 1 <= page <= len(doc):
        raise DigitizeError(f"{path.name} has {len(doc)} pages; --page {page} is out of range")
    pg = doc[page - 1]
    w, h = pg.get_size()
    box = (0.0, 0.0, 0.0, 0.0)
    if crop:
        x0, y0, x1, y1 = crop
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise DigitizeError(f"--crop {crop} is outside the page ({w:.0f} x {h:.0f} pt)")
        box = (x0, h - y1, w - x1, y0)  # pdfium crop: left, bottom, right, top margins
    bitmap = pg.render(scale=dpi / 72.0, crop=box)
    buf = io.BytesIO()
    bitmap.to_pil().save(buf, format="PNG")
    return buf.getvalue()


@dataclass
class Session:
    """One digitize session: a new digitization, or a review of an existing reference."""

    figure: Figure
    out: Path
    mode: str = "new"  # new | review
    title: str = ""
    by: str | None = None
    note: str | None = None  # shown on the page (e.g. the paper's ai_tools rule)
    original: dict[str, Series] = field(default_factory=dict)
    original_info: dict[str, Any] = field(default_factory=dict)  # path, format, sha256, git
    previous: dict[str, Any] | None = None  # the curves-json doc a review overwrites in place
    focus: str | None = None  # the series the panel reference points at
    root: Path | None = None  # project root (for paths printed back)
    token: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    saves: list[dict[str, Any]] = field(default_factory=list)

    # -- what the page loads ---------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "title": self.title,
            "note": self.note,
            "out": self.out.name,
            "image": self.figure.info,
            "focus": self.focus,
            "original": {name: _series_json(s) for name, s in self.original.items()},
            "preload": self._preload(),
            "tool_version": __version__,
        }

    def _preload(self) -> dict[str, Any] | None:
        """Calibration + pixel points from an earlier ``pawtrace digitize`` save of the same image."""
        doc = self.previous if self.mode == "review" else None
        if not doc:
            return None
        dig = (doc.get("meta") or {}).get("digitization") or {}
        if not dig.get("calibration") or (dig.get("image") or {}).get("sha256") != self.figure.info.get("sha256"):
            return None
        try:
            cal = Calibration.from_json(dig["calibration"])
        except DigitizeError:
            return None
        series = []
        for name, obj in (doc.get("series") or {}).items():
            meta = obj.get("meta") or {}
            pts = _placed_points(cal, self.original.get(name), meta.get("px"), meta.get("err_px"))
            if pts is None:
                continue
            series.append({"name": name, "kind": obj.get("kind", "curve"), "color": obj.get("color_rgba"), "points": pts})
        return {"calibration": dig["calibration"], "series": series, "assist": (dig.get("assist") or {}).get("snap_to_color")}

    def place(self, calibration: dict[str, Any]) -> dict[str, Any]:
        """Pixel positions of the original series under a calibration (review mode)."""
        cal = Calibration.from_json(calibration)
        out = {}
        for name, s in self.original.items():
            out[name] = _placed_points(cal, s, None, None)
        return out

    # -- saving ----------------------------------------------------------------
    def build(self, body: dict[str, Any]) -> dict[str, Any]:
        state = body.get("state") or {}
        image = dict(self.figure.info)
        size = body.get("image_size")
        if isinstance(size, list) and len(size) == 2:
            image["width"], image["height"] = int(size[0]), int(size[1])
        doc = build_document(state, image=image, by=self.by, assist=body.get("assist"), tool_version=__version__,
                             original=self.original if self.mode == "review" else None)
        if self.mode == "review":
            res = residual(self.original, doc)
            doc = apply_review(doc, original=self.original_info, residual=res, by=self.by, previous=self.previous)
            if not body.get("reviewed"):
                doc["meta"]["review"]["reviewed_by"] = None
                doc["meta"]["review"]["complete"] = False
            else:
                doc["meta"]["review"]["complete"] = True
        return doc

    def save(self, body: dict[str, Any]) -> dict[str, Any]:
        doc = self.build(body)
        write_document(self.out, doc)
        info = {
            "path": str(self.out),
            "series": {k: v["n"] for k, v in doc["series"].items()},
            "reviewed": bool((doc["meta"].get("review") or {}).get("complete")),
        }
        if self.mode == "review":
            info["residual"] = doc["meta"]["review"]["residual"]
        self.saves.append(info)
        return info


def _series_json(s: Series) -> dict[str, Any]:
    return {
        "x": list(s.x) if s.categorical else s.xf.tolist(),
        "y": s.y.tolist(),
        "yerr": None if s.yerr is None else s.yerr.tolist(),
        "kind": "bars" if s.categorical and s.kind == "curve" else s.kind,
        "categorical": s.categorical,
        "color": s.meta.get("color_rgba"),
    }


def _placed_points(cal: Calibration, s: Series | None, px: Any, err_px: Any) -> list[dict[str, Any]] | None:
    """Page points for series ``s``: stored pixels when they match, else placed through the calibration.

    Label (bar/table) series have no x position in data units, so their points are
    spread evenly along the calibrated x axis for the person to drag onto the bars.
    """
    if s is None:
        return None
    n = len(s)
    if isinstance(px, list) and len(px) == n:
        pix = np.asarray(px, dtype=float)
    elif s.categorical:
        ax = cal.x
        t = (np.arange(n) + 0.5) / max(n, 1)
        xs = ax.p1[0] + t * (ax.p2[0] - ax.p1[0])
        ys = cal.to_pixel(np.column_stack([np.full(n, _mid_x(cal)), s.y]))[:, 1]
        pix = np.column_stack([xs, ys])
    else:
        pix = cal.to_pixel(np.column_stack([s.xf, s.y]))
    errs: list[Any] = [None] * n
    if isinstance(err_px, list) and len(err_px) == n:
        errs = err_px
    elif s.yerr is not None:
        xs_data = _mid_x(cal) if s.categorical else s.xf
        top = cal.to_pixel(np.column_stack([np.broadcast_to(xs_data, (n,)), s.y + s.yerr]))
        errs = [[float(pix[i, 0]), float(top[i, 1])] if np.isfinite(top[i]).all() and s.yerr[i] > 0 else None for i in range(n)]
    pts = []
    for i in range(n):
        p = {"px": [round(float(pix[i, 0]), 2), round(float(pix[i, 1]), 2)], "src": i}
        if not np.isfinite(pix[i]).all():
            continue  # e.g. a non-positive value on a log axis
        if s.categorical:
            p["label"] = s.x[i]
        if errs[i] is not None:
            p["err_px"] = errs[i]
        pts.append(p)
    return pts


def _mid_x(cal: Calibration) -> float:
    a, b = cal.x.v1, cal.x.v2
    return float(np.sqrt(a * b)) if cal.x.scale == "log" else (a + b) / 2.0


def git_info(path: Path) -> dict[str, Any]:
    """Commit that last touched ``path`` and whether it has uncommitted edits (None outside git)."""
    def run(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", str(path.parent), *args], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    commit = run("log", "-1", "--format=%H", "--", path.name)
    if not commit:
        return {"commit": None}
    dirty = run("status", "--porcelain", "--", path.name)
    return {"commit": commit, "dirty": bool(dirty)}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def page_html() -> bytes:
    return resources.files("pawtrace.digitize").joinpath("static/digitize.html").read_bytes()


class _Handler(BaseHTTPRequestHandler):
    server: "DigitizeServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        pass

    # -- guards -----------------------------------------------------------------
    def _allowed(self, token: str | None) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in ("127.0.0.1", "localhost", "::1"):
            self._send(HTTPStatus.FORBIDDEN, b"bad host", "text/plain")
            return False
        if not token or not secrets.compare_digest(token, self.server.session.token):
            self._send(HTTPStatus.FORBIDDEN, b"bad token", "text/plain")
            return False
        return True

    def _send(self, status: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(status, json.dumps(obj, default=str).encode(), "application/json")

    # -- routes -----------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        token = (parse_qs(url.query).get("t") or [None])[0] or self.headers.get("X-Pawtrace-Token")
        if not self._allowed(token):
            return
        sess = self.server.session
        if url.path == "/":
            csp = "default-src 'none'; img-src 'self' blob:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"
            self._send(200, page_html(), "text/html; charset=utf-8", {"Content-Security-Policy": csp})
        elif url.path == "/image":
            self._send(200, sess.figure.data, sess.figure.mime)
        elif url.path == "/api/session":
            self._json(sess.describe())
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        if not self._allowed(self.headers.get("X-Pawtrace-Token")):
            return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            self._send(415, b"json only", "text/plain")
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            self._send(413, b"too large", "text/plain")
            return
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
            return
        sess = self.server.session
        try:
            with self.server.lock:
                if url.path == "/api/place":
                    self._json({"series": sess.place(body.get("calibration") or {})})
                elif url.path == "/api/preview":
                    doc = sess.build(body)
                    self._json({"residual": (doc["meta"].get("review") or {}).get("residual"),
                                "series": {k: v["n"] for k, v in doc["series"].items()}})
                elif url.path == "/api/save":
                    self._json(sess.save(body))
                elif url.path == "/api/quit":
                    self._json({"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self._send(404, b"not found", "text/plain")
        except (DigitizeError, ValueError, KeyError, TypeError) as exc:
            self._json({"error": str(exc)}, 400)


class DigitizeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, session: Session, port: int = 0):
        self.session = session
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.session.token}"


def load_original(path: Path, fmt: str | None, options: dict[str, Any]) -> tuple[dict[str, Series], dict[str, Any] | None]:
    """The reference file's series (raw, no panel transform) and, for curves-json, the whole document."""
    fmt = fmt or formats.guess_format(path)
    series = formats.read(path, fmt, **options)
    doc = load_document(path) if fmt == "curves-json" else None
    return series, doc
