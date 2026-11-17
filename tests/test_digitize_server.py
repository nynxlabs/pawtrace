"""Tests for pawtrace.digitize.server and command: the local digitize page's guards and API,
new digitizations, reviews of curves-json and table-csv references, and the CLI parse.

Each test builds a small project in ``tmp_path`` (``pawtrace.yaml``, ``papers/demo/spec.yaml``,
panels ``fig1`` (curves-json reference) and ``fig2`` (table-csv reference)) and a tiny PNG.
The server runs on 127.0.0.1 with an OS-picked port; every request has a 5 s socket timeout.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import struct
import threading
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from pawtrace import cli, formats
from pawtrace.digitize.command import build_session
from pawtrace.digitize.core import DigitizeError, write_document
from pawtrace.digitize.server import DigitizeServer

TIMEOUT = 5  # seconds, per request

# x: px 100 -> 0, px 500 -> 100 (4 px per unit); y: py 400 -> 0, py 0 -> 40 (10 px per unit)
CAL = {
    "x": {"scale": "linear", "points": [{"px": [100, 400], "value": 0}, {"px": [500, 400], "value": 100}]},
    "y": {"scale": "linear", "points": [{"px": [100, 400], "value": 0}, {"px": [100, 0], "value": 40}]},
}

SPEC = {
    "paper": {"id": "demo", "title": "Demo", "authors": [], "year": 2024, "doi": "", "version": "published"},
    "rights": {
        "license": "CC-BY-4.0",
        "access": "open-access",
        "rules": {"redistribute": True, "quotes_in_spec": True, "ai_tools": "never"},
    },
}


def px_of(x: float, y: float) -> list[float]:
    """Pixel position of data point (x, y) under CAL."""
    return [100 + 4 * x, 400 - 10 * y]


def write_png(path: Path, w: int, h: int) -> bytes:
    """A small RGB PNG written with the stdlib only."""
    rows = [b"\x00" + bytes(v for x in range(w) for v in (x * 60, y * 80, 200)) for y in range(h)]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    blob = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(blob)
    return blob


def make_project(tmp_path: Path) -> tuple[Path, Path, bytes]:
    """Project root, the figure PNG path, and the PNG bytes."""
    root = tmp_path / "proj"
    pdir = root / "papers" / "demo"
    (pdir / "panels").mkdir(parents=True)
    (pdir / "d").mkdir()
    (root / "pawtrace.yaml").write_text("pawtrace: 1\nname: t\nprofile: minimal\n")
    (pdir / "spec.yaml").write_text(yaml.safe_dump(SPEC, sort_keys=False))
    png = tmp_path / "fig.png"
    blob = write_png(png, 4, 3)
    (pdir / "d" / "power.json").write_text(json.dumps({"series": {"paper": {"x": [0, 50, 100], "y": [10, 20, 15]}}}))
    (pdir / "d" / "rates.csv").write_text("label,healthy,pd,healthy_se\nA,1,2,0.5\nB,3,4,1\n")
    fig1 = {
        "figure": {"image": str(png)},
        "references": {"paper": {"path": "papers/demo/d/power.json", "format": "curves-json", "series": "paper"}},
    }
    fig2 = {
        "figure": {"image": str(png)},
        "references": {
            "h": {
                "path": "papers/demo/d/rates.csv",
                "format": "table-csv",
                "series": "healthy",
                "options": {"value": ["healthy", "pd"], "err": {"healthy": "healthy_se"}},
            }
        },
    }
    (pdir / "panels" / "fig1.yaml").write_text(yaml.safe_dump(fig1, sort_keys=False))
    (pdir / "panels" / "fig2.yaml").write_text(yaml.safe_dump(fig2, sort_keys=False))
    return root, png, blob


def _args(**kw) -> SimpleNamespace:
    base = dict(image=None, out=None, review=None, panel=None, reference=None, paper=None, page=None, crop=None,
                dpi=None, by=None, port=None, browser=False, open_cmd=None, force=False, project=None, json=False)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def serve():
    """Start DigitizeServer(session, port=0) on a thread; tear down on exit."""
    started: list[DigitizeServer] = []

    def _start(session) -> DigitizeServer:
        srv = DigitizeServer(session, port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return srv

    yield _start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def _req(srv: DigitizeServer, method: str, path: str, *, headers=None, body: bytes | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=TIMEOUT)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        r = conn.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        conn.close()


def _post(srv: DigitizeServer, path: str, obj, token: str | None = None):
    tok = srv.session.token if token is None else token
    body = json.dumps(obj).encode()
    return _req(srv, "POST", path, headers={"Content-Type": "application/json", "X-Pawtrace-Token": tok}, body=body)


def _json(resp) -> dict:
    status, _, body = resp
    assert status == 200, body[:200]
    return json.loads(body)


def _pts_for_save(placed: list[dict], moved: dict[int, list[float]] | None = None) -> list[dict]:
    """Placed points as the page sends them back; ``moved`` maps a src index to its new px."""
    out = []
    for p in placed:
        q = dict(p)
        if moved and p.get("src") in moved:
            q["px"] = moved[p["src"]]
            q["moved"] = True
        out.append(q)
    return out


# ---------------------------------------------------------------------------
# 1. guards and routes
# ---------------------------------------------------------------------------


def test_guards_and_routes(tmp_path, serve):
    root, png, blob = make_project(tmp_path)
    sess = build_session(_args(image=str(png), out=str(tmp_path / "g.json"), paper="demo", project=str(root)))
    srv = serve(sess)
    tok = sess.token

    status, _, _ = _req(srv, "GET", "/")
    assert status == 403  # no token

    status, headers, body = _req(srv, "GET", f"/?t={tok}")
    assert status == 200
    assert "pawtrace digitize" in body.decode("utf-8")
    assert headers.get("Content-Type", "").startswith("text/html")

    port = srv.server_address[1]
    status, _, _ = _req(srv, "GET", f"/?t={tok}", headers={"Host": f"evil.example:{port}"})
    assert status == 403  # right token, foreign Host (DNS rebinding)

    status, _, _ = _req(
        srv, "POST", "/api/save",
        headers={"Content-Type": "text/plain", "X-Pawtrace-Token": tok},
        body=b'{"state": {}}',
    )
    assert status == 415

    status, headers, body = _req(srv, "GET", f"/image?t={tok}")
    assert status == 200
    assert body == blob
    assert headers.get("Content-Type") == "image/png"

    status, _, _ = _req(srv, "GET", f"/nope?t={tok}")
    assert status == 404


# ---------------------------------------------------------------------------
# 2. new digitization
# ---------------------------------------------------------------------------


def test_new_digitization_saves_curves_json_with_provenance(tmp_path, serve):
    root, png, blob = make_project(tmp_path)
    out = tmp_path / "new.json"
    sess = build_session(_args(image=str(png), out=str(out), paper="demo", project=str(root)))
    assert "ai_tools is never" in (sess.note or "")
    srv = serve(sess)

    state = {
        "calibration": CAL,
        "series": [{
            "name": "paper", "kind": "curve",
            "points": [{"px": px_of(0, 10)}, {"px": px_of(50, 20)}, {"px": px_of(100, 15)}],
        }],
    }
    info = _json(_post(srv, "/api/save", {"state": state, "image_size": [4, 3]}))
    assert info["path"] == str(out)
    assert info["series"] == {"paper": 3}
    assert info["reviewed"] is False

    s = formats.read(out, "curves-json")["paper"]
    assert list(s.xf) == pytest.approx([0, 50, 100])
    assert list(s.y) == pytest.approx([10, 20, 15])

    doc = json.loads(out.read_text())
    dig = doc["meta"]["digitization"]
    assert dig["tool"] == "pawtrace digitize"
    assert dig["method"] == "manual"
    assert dig["by"] == "human"
    assert dig["image"]["sha256"] == hashlib.sha256(blob).hexdigest()
    assert dig["image"]["width"] == 4
    assert dig["image"]["height"] == 3
    assert dig["calibration"]["x"]["points"][0]["px"] == [100, 400]
    assert dig["assist"]["used"] is False


# ---------------------------------------------------------------------------
# 3. build_session refusals
# ---------------------------------------------------------------------------


def test_new_mode_needs_out(tmp_path):
    root, png, _ = make_project(tmp_path)
    with pytest.raises(DigitizeError, match="--out"):
        build_session(_args(image=str(png), paper="demo", project=str(root)))


def test_new_mode_refuses_existing_out_unless_forced(tmp_path):
    root, png, _ = make_project(tmp_path)
    out = tmp_path / "exists.json"
    write_document(out, {"series": {"s": {"n": 1, "xy": {"x": [0], "y": [1]}}}, "meta": {}})
    # it is a digitization-shaped file, so the message points at --review
    with pytest.raises(DigitizeError, match="--review"):
        build_session(_args(image=str(png), out=str(out), paper="demo", project=str(root)))
    sess = build_session(_args(image=str(png), out=str(out), paper="demo", project=str(root), force=True))
    assert sess.out == out and sess.mode == "new"


# ---------------------------------------------------------------------------
# 4. review of a curves-json reference, in place
# ---------------------------------------------------------------------------


def _review_session(root: Path):
    return build_session(_args(review=["demo/fig1", "paper"], project=str(root)))


def _edit_and_save(srv: DigitizeServer, *, reviewed: bool):
    placed = _json(_post(srv, "/api/place", {"calibration": CAL}))["series"]["paper"]
    assert [p["src"] for p in placed] == [0, 1, 2]
    for p, (x, y) in zip(placed, [(0, 10), (50, 20), (100, 15)]):
        assert p["px"] == pytest.approx(px_of(x, y), abs=0.01)
    pts = _pts_for_save(placed, moved={1: [300, 180]})  # point 1 dragged to y = 22
    pts.append({"px": [450, 300]})  # an added point at (87.5, 10), no src
    state = {"calibration": CAL, "series": [{"name": "paper", "kind": "curve", "points": pts}]}
    return _post(srv, "/api/save", {"state": state, "image_size": [4, 3], "reviewed": reviewed, "assist": None})


def test_review_curves_json_in_place(tmp_path, serve):
    root, png, _ = make_project(tmp_path)
    src = root / "papers" / "demo" / "d" / "power.json"
    original_bytes = src.read_bytes()
    original_sha = hashlib.sha256(original_bytes).hexdigest()

    sess = _review_session(root)
    assert sess.out == src
    assert sess.previous is not None
    srv = serve(sess)

    status, _, body = _edit_and_save(srv, reviewed=True)
    assert status == 200, body[:200]

    doc = json.loads(src.read_text())
    ser = doc["series"]["paper"]
    assert ser["n"] == 4
    idx = {s: i for i, s in enumerate(ser["meta"]["src"]) if s is not None}
    xs, ys = ser["xy"]["x"], ser["xy"]["y"]
    # unmoved points keep the original values exactly
    assert xs[idx[0]] == 0.0 and ys[idx[0]] == 10.0
    assert xs[idx[2]] == 100.0 and ys[idx[2]] == 15.0
    # the moved point and the added one
    assert xs[idx[1]] == pytest.approx(50.0) and ys[idx[1]] == pytest.approx(22.0)
    added = [i for i, s in enumerate(ser["meta"]["src"]) if s is None]
    assert len(added) == 1 and xs[added[0]] == pytest.approx(87.5) and ys[added[0]] == pytest.approx(10.0)

    rev = doc["meta"]["review"]
    assert rev["reviewed_by"] == "human"
    assert rev["complete"] is True
    assert rev["residual"]["series"]["paper"]["moved"] == 1
    assert rev["residual"]["series"]["paper"]["added"] == 1
    assert rev["residual"]["series"]["paper"]["removed"] == 0
    assert rev["original"]["format"] == "curves-json"
    assert rev["original"]["sha256"] == original_sha

    hist = doc["meta"]["history"]
    assert len(hist) == 1
    assert hist[0]["series"] == {"paper": {"x": [0, 50, 100], "y": [10, 20, 15]}}


def test_review_saved_unreviewed_is_not_complete(tmp_path, serve):
    root, _, _ = make_project(tmp_path)
    srv = serve(_review_session(root))
    status, _, body = _edit_and_save(srv, reviewed=False)
    assert status == 200, body[:200]
    rev = json.loads((root / "papers" / "demo" / "d" / "power.json").read_text())["meta"]["review"]
    assert rev["reviewed_by"] is None
    assert rev["complete"] is False


# ---------------------------------------------------------------------------
# 5. preload from the earlier save
# ---------------------------------------------------------------------------


def test_review_preload_after_save(tmp_path, serve):
    root, png, blob = make_project(tmp_path)
    src = root / "papers" / "demo" / "d" / "power.json"
    srv = serve(_review_session(root))
    status, _, body = _edit_and_save(srv, reviewed=True)
    assert status == 200, body[:200]
    saved = json.loads(src.read_text())
    stored_px = saved["series"]["paper"]["meta"]["px"]

    again = _review_session(root)
    pre = again.describe()["preload"]
    assert pre is not None
    assert pre["calibration"] == saved["meta"]["digitization"]["calibration"]
    assert pre["calibration"]["x"]["points"][0]["px"] == [100, 400]
    series = {s["name"]: s for s in pre["series"]}
    pts = series["paper"]["points"]
    assert len(pts) == len(stored_px) == 4
    for p, want in zip(pts, stored_px):
        assert p["px"] == pytest.approx(want, abs=0.01)
    assert hashlib.sha256(blob).hexdigest() == saved["meta"]["digitization"]["image"]["sha256"]


# ---------------------------------------------------------------------------
# 6. review of a table-csv reference
# ---------------------------------------------------------------------------


def test_review_table_csv_writes_json_beside_source(tmp_path, serve):
    root, _, _ = make_project(tmp_path)
    d = root / "papers" / "demo" / "d"
    sess = build_session(_args(review=["demo/fig2", "h"], project=str(root)))
    assert sess.out == d / "rates.json"
    assert sess.previous is None
    assert set(sess.original) == {"healthy", "pd"}
    assert list(sess.original["healthy"].x) == ["A", "B"]
    assert list(sess.original["healthy"].yerr) == pytest.approx([0.5, 1.0])
    srv = serve(sess)

    place = _json(_post(srv, "/api/place", {"calibration": CAL}))["series"]
    healthy, pd = place["healthy"], place["pd"]
    assert [p["label"] for p in healthy] == ["A", "B"]
    assert [p["src"] for p in healthy] == [0, 1]
    assert all("err_px" in p for p in healthy)
    assert all("err_px" not in p for p in pd)

    state = {"calibration": CAL, "series": [
        {"name": "healthy", "kind": "bars", "points": healthy},
        {"name": "pd", "kind": "bars", "points": pd},
    ]}
    status, _, body = _post(srv, "/api/save", {"state": state, "image_size": [4, 3], "reviewed": True})
    assert status == 200, body[:200]

    out = d / "rates.json"
    series = formats.read(out, "curves-json")
    h = series["healthy"]
    assert list(h.x) == ["A", "B"]
    assert list(h.y) == [1.0, 3.0]
    assert list(h.yerr) == [0.5, 1.0]
    assert h.kind == "bars"
    rev = json.loads(out.read_text())["meta"]["review"]
    assert rev["residual"]["series"]["healthy"]["moved"] == 0
    assert rev["residual"]["series"]["pd"]["moved"] == 0
    assert rev["original"]["format"] == "table-csv"


def test_review_table_csv_refuses_existing_json(tmp_path):
    root, _, _ = make_project(tmp_path)
    d = root / "papers" / "demo" / "d"
    (d / "rates.json").write_text('{"series": {}}')
    with pytest.raises(DigitizeError, match="already exists"):
        build_session(_args(review=["demo/fig2", "h"], project=str(root)))
    other = tmp_path / "other.json"
    sess = build_session(_args(review=["demo/fig2", "h"], project=str(root), out=str(other)))
    assert sess.out == other


# ---------------------------------------------------------------------------
# 7. CLI parse
# ---------------------------------------------------------------------------


def test_cli_parses_review_pair():
    ns = cli.build_parser().parse_args(["digitize", "--review", "demo/fig1", "paper", "--by", "x"])
    assert ns.review == ["demo/fig1", "paper"]
    assert ns.by == "x"
