"""Tests for pawtrace.webui (``pawtrace ui``) over real HTTP: the loopback and token guards,
the notes and review forms, the data views, figures, the theme, the digitize mount, and the
form parser.

Each test builds a small synthetic project in ``tmp_path``: a project root (``pawtrace.yaml``,
``papers/demo/spec.yaml`` with three items, panels ``fig1`` and ``fig2``) and a notes folder
beside it (a PNG and three markdown files). The server runs on 127.0.0.1 with an OS-picked
port, and every request has a 5 s socket timeout. No real papers, figures or notes are used.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import struct
import threading
import time
import zlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import yaml

from pawtrace.localweb import TOKEN_HEADER
from pawtrace.webui import notes as nts
from pawtrace.webui.server import UiConfig, UiServer, UiState
from pawtrace.webui.theme import FALLBACK_COLORS

TIMEOUT = 5  # seconds, per request

SPEC = {
    "paper": {"id": "demo", "title": "Demo", "authors": ["A. Author"], "year": 2024, "doi": "", "version": "published"},
    "rights": {
        "license": "CC-BY-4.0",
        "access": "open-access",
        "rules": {"redistribute": True, "quotes_in_spec": True, "ai_tools": "local-only"},
    },
    "items": [
        {"id": "i1", "kind": "value", "status": "stated", "text": "Model A has three states"},
        {"id": "i2", "kind": "value", "status": "missing", "text": "Model B step size"},
        {"id": "i3", "kind": "value", "status": "ambiguous", "text": "Model C noise level"},
    ],
}

REVIEW_MD = """# Review: demo

**`plant.dt`** · §2

Some text.

- [x] agree  - [ ] disagree  - [ ] unsure  note:

**`plant.gain`** · §3

Another text.

- [ ] agree  - [x] disagree  - [ ] unsure  note: the cap is wrong
second line of the note

**`plant.tau`** · §4

Third text.

- [ ] agree  - [ ] disagree  - [ ] unsure  note:
"""

INTERVIEW_MD = """# Intake

**A1** `plant.model` — Which model?
answer: Placeholder model

**A2** `plant.dt` — What is the time step?
answer:
"""

OTHER_MD = "# Other\n\nJust a plain note, no form here.\n"


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


def make_notes(tmp_path: Path) -> tuple[Path, bytes]:
    notes = tmp_path / "notes"
    (notes / "review").mkdir(parents=True)
    (notes / "intake").mkdir()
    blob = write_png(notes / "fig.png", 4, 3)
    (notes / "review" / "demo.md").write_text(REVIEW_MD, encoding="utf-8")
    (notes / "intake" / "interview.md").write_text(INTERVIEW_MD, encoding="utf-8")
    (notes / "other.md").write_text(OTHER_MD, encoding="utf-8")
    return notes, blob


def make_project(tmp_path: Path, png: Path) -> Path:
    root = tmp_path / "proj"
    pdir = root / "papers" / "demo"
    (pdir / "panels").mkdir(parents=True)
    (pdir / "d").mkdir()
    (root / "pawtrace.yaml").write_text("pawtrace: 1\nname: t\nprofile: minimal\n")
    (pdir / "spec.yaml").write_text(yaml.safe_dump(SPEC, sort_keys=False))
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
    return root


class FakePopen:
    """Stands in for subprocess.Popen; records the argv of each call."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        return None


@pytest.fixture
def env(tmp_path) -> SimpleNamespace:
    notes, png_blob = make_notes(tmp_path)
    root = make_project(tmp_path, notes / "fig.png")
    return SimpleNamespace(root=root, notes=notes, tmp=tmp_path, png=png_blob)


@pytest.fixture
def start(env):
    """Start a UiServer on a thread; tear every one down on exit."""
    servers: list[UiServer] = []

    def _start(*, edit_cmd=None, theme=None, popen=None, frame_ancestors=None, read_only=False, write_checkout=False,
               frontend=None) -> UiServer:
        cfg = UiConfig(project=env.root, notes=[str(env.notes)], edit_cmd=edit_cmd, theme=theme,
                       frame_ancestors=list(frame_ancestors or []), read_only=read_only,
                       write_checkout=write_checkout, state_dir=env.tmp / "state", frontend=frontend)
        srv = UiServer(UiState(cfg, popen=popen), port=0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv

    yield _start
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def _req(srv, method, path, *, token=None, host=None, headers=None, body=None):
    """One request with exactly the headers given (Host included, so a test can forge it).

    Returns (status, headers with lower-case names, body bytes).
    """
    port = srv.server_address[1]
    hdrs = {"Host": host or f"127.0.0.1:{port}", "Connection": "close"}
    if token is not None:
        hdrs[TOKEN_HEADER] = token
    hdrs.update(headers or {})
    payload = None
    if body is not None:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
        hdrs["Content-Length"] = str(len(payload))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in hdrs.items():
            conn.putheader(k, v)
        conn.endheaders(payload)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    finally:
        conn.close()


def _json(resp, status: int = 200):
    got, _, body = resp
    assert got == status, body[:300]
    return json.loads(body)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_api_needs_token_and_loopback_host(start):
    srv = start()
    assert _req(srv, "GET", "/api/meta")[0] == 403
    assert _req(srv, "GET", "/api/meta", token="wrong-token")[0] == 403
    status, headers, body = _req(srv, "GET", "/api/meta", token=srv.token)
    assert status == 200
    assert headers["content-type"] == "application/json"
    assert json.loads(body)["project"] == "t"


def test_right_token_with_foreign_host_is_refused(start):
    srv = start()
    port = srv.server_address[1]
    assert _req(srv, "GET", "/api/meta", token=srv.token, host="evil.example")[0] == 403
    assert _req(srv, "GET", "/api/meta", token=srv.token, host=f"localhost:{port}")[0] == 200
    assert _req(srv, "GET", "/api/meta", token=srv.token, host=f"127.0.0.1:{port}")[0] == 200


def test_every_response_carries_safe_headers(start):
    srv = start()
    responses = [
        _req(srv, "GET", "/"),  # 200 front end
        _req(srv, "GET", "/static/app.js"),  # 404
        _req(srv, "GET", "/api/meta"),  # 403, no token
        _req(srv, "GET", "/api/meta", token=srv.token),  # 200
        _req(srv, "POST", "/api/files/open", token=srv.token, body={"space": "notes:0", "path": "other.md"}),  # 409
    ]
    for status, headers, _ in responses:
        assert headers["cache-control"] == "no-store", status
        assert headers["referrer-policy"] == "no-referrer", status
        assert headers["x-content-type-options"] == "nosniff", status


def test_post_guards_json_only_object_and_token(start):
    srv = start(edit_cmd=None)
    body = {"space": "notes:0", "path": "review/demo.md"}
    # text/plain is refused before the body is read
    status, _, _ = _req(srv, "POST", "/api/files/open", token=srv.token,
                        headers={"Content-Type": "text/plain"}, body=json.dumps(body).encode())
    assert status == 415
    # valid JSON that isn't an object
    status, _, _ = _req(srv, "POST", "/api/files/open", token=srv.token, body=b"[]")
    assert status == 400
    # no token
    assert _req(srv, "POST", "/api/files/open", body=body)[0] == 403


# ---------------------------------------------------------------------------
# Notes and forms
# ---------------------------------------------------------------------------


def test_notes_list_kinds_and_counts(start):
    srv = start()
    data = _json(_req(srv, "GET", "/api/notes", token=srv.token))
    by = {n["rel"]: n for n in data["notes"]}
    assert set(by) == {"review/demo.md", "intake/interview.md", "other.md"}
    assert by["review/demo.md"]["kind"] == "review"
    assert by["review/demo.md"]["counts"] == {"agree": 1, "disagree": 1, "unmarked": 1}
    assert by["intake/interview.md"]["kind"] == "interview"
    assert by["intake/interview.md"]["counts"] == {"answered": 1, "unanswered": 1}
    assert by["other.md"]["kind"] == "note"
    assert data["roots"] == [{"id": 0, "label": "notes"}]


def test_note_returns_blocks_with_choices_and_multiline_notes(start):
    srv = start()
    query = urlencode({"root": 0, "rel": "review/demo.md"})
    form = _json(_req(srv, "GET", f"/api/note?{query}", token=srv.token))
    blocks = {b["id"]: b for b in form["blocks"]}
    assert set(blocks) == {"plant.dt", "plant.gain", "plant.tau"}
    assert blocks["plant.dt"]["choice"] == "agree"
    assert blocks["plant.gain"]["choice"] == "disagree"
    assert blocks["plant.gain"]["note"] == "the cap is wrong\nsecond line of the note"
    assert blocks["plant.tau"]["choice"] is None


def test_note_paths_cannot_leave_the_notes_folder(start, env):
    srv = start()
    secret = env.tmp / "secret.md"
    secret.write_text("# secret\n", encoding="utf-8")
    (env.notes / "escape.md").symlink_to(secret)
    for rel in ("../secret.md", str(secret), "escape.md"):
        query = urlencode({"root": 0, "rel": rel})
        status, _, _ = _req(srv, "GET", f"/api/note?{query}", token=srv.token)
        assert status == 403, rel


# ---------------------------------------------------------------------------
# Edit command
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Data views
# ---------------------------------------------------------------------------


def test_home_and_paper_views(start):
    srv = start()
    home = _json(_req(srv, "GET", "/api/home?co=0", token=srv.token))
    paper = next(p for p in home["papers"] if p["id"] == "demo")
    assert paper["rights"]["ai_tools"] == "local-only"
    assert paper["panels"]["n"] == 2
    assert paper["items"] == 3

    view = _json(_req(srv, "GET", "/api/paper?co=0&id=demo", token=srv.token))
    assert len(view["items"]) == 3
    assert view["statuses"]["stated"] == 1
    assert view["statuses"]["missing"] == 1
    assert view["statuses"]["ambiguous"] == 1

    assert _req(srv, "GET", "/api/paper?co=0&id=nope", token=srv.token)[0] == 404
    assert _req(srv, "GET", "/api/home?co=9", token=srv.token)[0] == 404
    assert _req(srv, "GET", "/api/paper?co=9&id=demo", token=srv.token)[0] == 404


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------


def test_figures_are_served_by_opaque_key(start, env):
    srv = start()
    fig = _json(_req(srv, "GET", "/api/figures?co=0", token=srv.token))
    entry = next(f for f in fig["figures"] if f["rel"] == "fig.png")
    url = entry["url"]
    assert url.startswith("/f/") and "?v=" in url

    status, headers, body = _req(srv, "GET", url, token=srv.token)
    assert status == 200
    assert headers["content-type"] == "image/png"
    assert "sandbox" in headers["content-security-policy"]
    assert body == env.png

    assert _req(srv, "GET", "/f/notakey", token=srv.token)[0] == 404
    assert _req(srv, "GET", url)[0] == 403  # a valid key still needs the token


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------


def test_theme_snapshot_without_a_file(start):
    srv = start()
    assert _json(_req(srv, "GET", "/api/theme", token=srv.token)) == {"available": False}


def test_theme_snapshot_from_a_file(start, env):
    f = env.tmp / "active.json"
    f.write_text(json.dumps({"id": "x", "colors": {"bg": "#000000"}, "syntax": None, "updatedAt": 5,
                             "text": {"desktop": {"ui": 13}}, "tabs": {"desktop": {"barHeight": "40px"}},
                             "oled": {"desktop": False, "laptop": True}}))
    srv = start(theme=str(f))
    snap = _json(_req(srv, "GET", "/api/theme", token=srv.token))
    assert snap["available"] is True and snap["rev"] == "5" and snap["theme"]["colors"]["bg"] == "#000000"
    assert snap["text"] == {"desktop": {"ui": 13}} and snap["tabs"]["desktop"]["barHeight"] == "40px"
    assert snap["oled"] == {"desktop": False, "laptop": True}
    f.write_text("{broken")
    assert _json(_req(srv, "GET", "/api/theme", token=srv.token)) == {"available": False}


def test_theme_file_lookup_order(env, monkeypatch):
    from pawtrace.webui.theme import default_path, resolve

    xdg = env.tmp / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("PAWTRACE_THEME", raising=False)
    assert default_path() is None and resolve(None) is None  # nothing: the built-in theme
    nynx = xdg / "nynx-theme" / "active.json"
    nynx.parent.mkdir(parents=True)
    nynx.write_text("{}")
    assert default_path() == nynx  # read if present
    ours = xdg / "pawtrace" / "theme.json"
    ours.parent.mkdir(parents=True)
    ours.write_text("{}")
    assert default_path() == ours  # pawtrace's own file first
    monkeypatch.setenv("PAWTRACE_THEME", str(env.tmp / "env.json"))
    assert default_path() == env.tmp / "env.json"  # then the environment
    assert resolve("/x/cli.json") == "/x/cli.json"  # --theme (or theme: in ui.yaml) before all of them
    assert resolve(None) == str(env.tmp / "env.json")


# ---------------------------------------------------------------------------
# Digitize mount
# ---------------------------------------------------------------------------


def test_digitize_mount_lifecycle_and_output_guard(start, env):
    srv = start()
    image = {"root": 0, "rel": "fig.png"}
    started = _json(_req(srv, "POST", "/api/digitize/start", token=srv.token, body={
        "co": "0", "mode": "new", "image": image, "out": "papers/demo/digitization/new.json",
    }))
    sid = started["id"]

    session = _json(_req(srv, "GET", f"/dz/{sid}/api/session", token=srv.token))
    assert session["mode"] == "new"

    status, headers, _ = _req(srv, "GET", f"/dz/{sid}/", token=srv.token)
    assert status == 200
    assert headers["content-type"].startswith("text/html")

    # an output file outside both the checkout and the notes folders is refused
    outside = env.tmp / "elsewhere.json"
    status, _, _ = _req(srv, "POST", "/api/digitize/start", token=srv.token, body={
        "co": "0", "mode": "new", "image": image, "out": str(outside),
    })
    assert status == 403

    _json(_req(srv, "POST", f"/dz/{sid}/api/quit", token=srv.token, body={}))
    # quit answers first and then drops the session; allow a moment for that to land
    deadline = time.monotonic() + TIMEOUT
    status = None
    while time.monotonic() < deadline:
        status = _req(srv, "GET", f"/dz/{sid}/api/session", token=srv.token)[0]
        if status == 404:
            break
        time.sleep(0.02)
    assert status == 404


# ---------------------------------------------------------------------------
# Form parser (no server)
# ---------------------------------------------------------------------------


def test_single_example_mark_line_makes_a_note_not_a_review():
    text = "# How to mark\n\nMark like this:\n\n- [ ] agree  - [ ] disagree  note:\n"
    assert nts.parse_form(text)["kind"] == "note"


def test_one_checkbox_task_is_not_a_mark_line():
    assert nts.parse_marks("- [ ] a task") is None


def test_mark_line_gives_options_and_first_checked():
    marks = nts.parse_marks("- [X] agree  - [ ] disagree  note: hi")
    assert [m["label"] for m in marks] == ["agree", "disagree"]
    assert marks[0]["checked"] is True
    assert marks[1]["checked"] is False


def test_several_checked_boxes_give_choice_several():
    form = nts.parse_form("**`plant.dt`** · §2\n\n- [x] agree  - [x] disagree  note:\n")
    assert form["blocks"][0]["choice"] == "several"


# ---------------------------------------------------------------------------
# Deep links, embedding, the open-file hook
# ---------------------------------------------------------------------------


def test_bad_frame_ancestor_is_refused(env):
    from pawtrace.webui.notes import NotesError

    with pytest.raises(NotesError):
        UiState(UiConfig(project=env.root, notes=[str(env.notes)], frame_ancestors=["*"]))




def test_root_is_the_front_end_and_data_needs_the_token(start):
    srv = start()
    status, _, body = _req(srv, "GET", "/")
    assert status == 200 and b"<html" in body.lower()
    for path in ("/api/meta", "/api/tree", "/f/x", "/dz/x/"):
        assert _req(srv, "GET", path)[0] == 403, path
    assert _req(srv, "GET", "/static/app.js")[0] == 404


def test_front_end_folder_is_public_but_host_checked(start, env):
    import pawtrace.webui.server as ws

    srv = start(frontend=Path(ws.__file__).with_name("frontend"))  # the placeholder, built front end or not
    status, headers, body = _req(srv, "GET", "/")
    assert status == 200 and b"placeholder.js" in body
    assert "frame-ancestors 'none'" in headers["content-security-policy"] and headers["cache-control"] == "no-store"
    assert _req(srv, "GET", "/placeholder.js")[1]["content-type"].startswith("text/javascript")
    assert _req(srv, "GET", "/app/")[0] == 308  # the old address
    assert _req(srv, "GET", "/paper/demo")[0] == 200  # app routes get index.html
    assert _req(srv, "GET", "/missing.js")[0] == 404
    assert _req(srv, "GET", "/../server.py")[0] == 404
    assert _req(srv, "GET", "/%2e%2e/server.py")[0] == 404
    assert _req(srv, "GET", "/", host="evil.example")[0] == 403


def test_custom_front_end_folder(start, env):
    fe = env.tmp / "fe"
    fe.mkdir()
    (fe / "index.html").write_text("<p>core</p>")
    (fe / ".secret").write_text("x")
    srv = start(frontend=fe)
    assert _req(srv, "GET", "/")[2] == b"<p>core</p>"
    assert _req(srv, "GET", "/.secret")[0] == 404
    with pytest.raises(Exception):
        start(frontend=env.tmp)  # no index.html


def test_digitize_page_frame_ancestors(start, env):
    srv = start(frame_ancestors=["http://127.0.0.1:8795", "https://notes.example.ts.net"])
    assert _json(_req(srv, "GET", "/api/meta", token=srv.token))["frame_ancestors"] == ["http://127.0.0.1:8795", "https://notes.example.ts.net"]
    sid = _json(_req(srv, "POST", "/api/digitize/start", token=srv.token,
                     body={"co": "0", "mode": "new", "image": {"root": 0, "rel": "fig.png"},
                           "out": "papers/demo/digitization/embed.json"}))["id"]
    _, h, _ = _req(srv, "GET", f"/dz/{sid}/", token=srv.token)
    assert "frame-ancestors 'self' http://127.0.0.1:8795 https://notes.example.ts.net;" in h["content-security-policy"]
    srv2 = start()
    sid2 = _json(_req(srv2, "POST", "/api/digitize/start", token=srv2.token,
                      body={"co": "0", "mode": "new", "image": {"root": 0, "rel": "fig.png"},
                            "out": "papers/demo/digitization/embed2.json"}))["id"]
    _, h2, _ = _req(srv2, "GET", f"/dz/{sid2}/", token=srv2.token)
    assert "frame-ancestors 'self';" in h2["content-security-policy"]


# ---------------------------------------------------------------------------
# Host interface: spaces, files, open, state
# ---------------------------------------------------------------------------


def _q(**kw):
    from urllib.parse import urlencode

    return urlencode(kw)


def test_spaces_and_their_write_flags(start, env):
    srv = start()
    spaces = {sp["id"]: sp for sp in _json(_req(srv, "GET", "/api/spaces", token=srv.token))["spaces"]}
    assert spaces["co:0"]["kind"] == "checkout" and spaces["co:0"]["writable"] is False
    assert spaces["notes:0"]["kind"] == "notes" and spaces["notes:0"]["writable"] is True
    srv_ro = start(read_only=True, write_checkout=True)
    ro = {sp["id"]: sp for sp in _json(_req(srv_ro, "GET", "/api/spaces", token=srv_ro.token))["spaces"]}
    assert ro["co:0"]["writable"] is False and ro["notes:0"]["writable"] is False
    srv_w = start(write_checkout=True)
    w = {sp["id"]: sp for sp in _json(_req(srv_w, "GET", "/api/spaces", token=srv_w.token))["spaces"]}
    assert w["co:0"]["writable"] is True


def test_list_stat_read(start, env):
    srv = start()
    listing = _json(_req(srv, "GET", "/api/files/list?" + _q(space="notes:0", path=""), token=srv.token))
    names = {e["name"]: e for e in listing["entries"]}
    assert names["review"]["type"] == "dir" and names["fig.png"]["kind"] == "image" and names["other.md"]["kind"] == "markdown"
    (env.notes / ".hidden.md").write_text("x")
    listing = _json(_req(srv, "GET", "/api/files/list?" + _q(space="notes:0", path=""), token=srv.token))
    assert ".hidden.md" not in {e["name"] for e in listing["entries"]}
    st = _json(_req(srv, "GET", "/api/files/stat?" + _q(space="notes:0", path="review/demo.md"), token=srv.token))
    raw = (env.notes / "review" / "demo.md").read_bytes()
    assert st["sha256"] == hashlib.sha256(raw).hexdigest() and st["writable"] is True and st["abspath"].endswith("demo.md")
    status, headers, body = _req(srv, "GET", "/api/files/read?" + _q(space="notes:0", path="review/demo.md"), token=srv.token)
    assert status == 200 and body == raw
    assert headers["content-type"].startswith("text/markdown") and headers["x-pawtrace-sha256"] == st["sha256"]
    assert "sandbox" in headers["content-security-policy"]
    code = _json(_req(srv, "GET", "/api/files/list?" + _q(space="co:0", path="papers/demo"), token=srv.token))
    assert {"spec.yaml", "panels", "d"} <= {e["name"] for e in code["entries"]}


def test_file_paths_stay_inside_their_space(start, env):
    srv = start()
    secret = env.tmp / "secret.md"
    secret.write_text("# secret\n")
    (env.notes / "escape.md").symlink_to(secret)
    for path in ("../secret.md", str(secret), "escape.md", ".hidden/x.md"):
        status = _req(srv, "GET", "/api/files/read?" + _q(space="notes:0", path=path), token=srv.token)[0]
        assert status in (400, 403), (path, status)
    listing = _json(_req(srv, "GET", "/api/files/list?" + _q(space="notes:0", path=""), token=srv.token))
    assert "escape.md" not in {e["name"] for e in listing["entries"]}
    assert _req(srv, "GET", "/api/files/read?" + _q(space="notes:9", path="x"), token=srv.token)[0] == 404
    assert _req(srv, "GET", "/api/files/read?" + _q(space="evil", path="x"), token=srv.token)[0] == 400


def test_write_compare_and_swap(start, env):
    srv = start()
    target = env.notes / "review" / "demo.md"
    base = hashlib.sha256(target.read_bytes()).hexdigest()
    w = lambda **b: _req(srv, "POST", "/api/files/write", token=srv.token, body={"space": "notes:0", "path": "review/demo.md", **b})
    assert w(text="new", base_sha256=None)[0] == 409  # exists: needs its sha
    assert w(text="new", base_sha256="0" * 64)[0] == 409  # stale
    got = _json(w(text="- [x] agree\n", base_sha256=base))
    assert target.read_text() == "- [x] agree\n" and got["sha256"] == hashlib.sha256(b"- [x] agree\n").hexdigest()
    assert w(text="again", base_sha256=base)[0] == 409  # base is now stale
    # create a new file; refuse to create over an existing one or in a missing folder
    created = _req(srv, "POST", "/api/files/write", token=srv.token, body={"space": "notes:0", "path": "new.md", "text": "hi", "base_sha256": None})
    assert created[0] == 200 and (env.notes / "new.md").read_text() == "hi"
    assert _req(srv, "POST", "/api/files/write", token=srv.token, body={"space": "notes:0", "path": "nope/new.md", "text": "x", "base_sha256": None})[0] == 404
    assert _req(srv, "POST", "/api/files/write", token=srv.token, body={"space": "notes:0", "path": "../x.md", "text": "x", "base_sha256": None})[0] == 403
    assert _req(srv, "POST", "/api/files/write", token=srv.token, body={"space": "notes:0", "path": "new.md", "text": 5, "base_sha256": None})[0] == 400
    assert not list(env.notes.rglob(".*.tmp"))


def test_write_through_a_symlink_keeps_the_link(start, env):
    srv = start()
    real = env.notes / "intake" / "real.md"
    real.write_text("one")
    link = env.notes / "linked.md"
    link.symlink_to(real)
    base = hashlib.sha256(b"one").hexdigest()
    status, _, body = _req(srv, "POST", "/api/files/write", token=srv.token,
                           body={"space": "notes:0", "path": "linked.md", "text": "two", "base_sha256": base})
    assert status == 200, body
    assert link.is_symlink() and real.read_text() == "two"


def test_checkout_writes_need_the_flag_and_spare_generated_files(start, env):
    spec = env.root / "papers" / "demo" / "spec.yaml"
    base = hashlib.sha256(spec.read_bytes()).hexdigest()
    body = {"space": "co:0", "path": "papers/demo/spec.yaml", "text": spec.read_text() + "\n", "base_sha256": base}
    srv = start()
    assert _req(srv, "POST", "/api/files/write", token=srv.token, body=body)[0] == 403
    srv_w = start(write_checkout=True)
    assert _req(srv_w, "POST", "/api/files/write", token=srv_w.token, body=body)[0] == 200
    for gen in ("STATUS.md", "papers/demo/SPEC.md", "QUESTIONS.md"):
        (env.root / gen).write_text("generated")
        h = hashlib.sha256(b"generated").hexdigest()
        r = _req(srv_w, "POST", "/api/files/write", token=srv_w.token, body={"space": "co:0", "path": gen, "text": "x", "base_sha256": h})
        assert r[0] == 403, gen
    srv_ro = start(read_only=True)
    note_base = hashlib.sha256((env.notes / "other.md").read_bytes()).hexdigest()
    assert _req(srv_ro, "POST", "/api/files/write", token=srv_ro.token,
                body={"space": "notes:0", "path": "other.md", "text": "x", "base_sha256": note_base})[0] == 403


def test_open_file_runs_edit_cmd_inside_spaces_only(start, env):
    fake = FakePopen()
    srv = start(edit_cmd="ed {path}", popen=fake)
    ok = _req(srv, "POST", "/api/files/open", token=srv.token, body={"space": "co:0", "path": "papers/demo/spec.yaml"})
    assert ok[0] == 200
    ok2 = _req(srv, "POST", "/api/files/open", token=srv.token, body={"space": "notes:0", "path": "review/demo.md"})
    assert ok2[0] == 200
    assert fake.calls == [["ed", str((env.root / "papers/demo/spec.yaml").resolve())],
                          ["ed", str((env.notes / "review" / "demo.md").resolve())]]
    outside = env.tmp / "outside.txt"
    outside.write_text("x")
    assert _req(srv, "POST", "/api/files/open", token=srv.token, body={"space": "co:0", "path": "../outside.txt"})[0] == 403
    assert _req(srv, "POST", "/api/files/open", token=srv.token, body={"space": "co:0", "path": "nope.txt"})[0] == 404
    assert len(fake.calls) == 2
    srv2 = start(edit_cmd=None, popen=fake)
    assert _req(srv2, "POST", "/api/files/open", token=srv2.token, body={"space": "notes:0", "path": "other.md"})[0] == 409


def test_state_store_round_trip_and_limits(start, env):
    srv = start()
    assert _json(_req(srv, "GET", "/api/state?key=tabs", token=srv.token))["value"] is None
    tabs = {"open": [{"route": "paper/demo"}], "active": 0}
    assert _req(srv, "POST", "/api/state", token=srv.token, body={"key": "tabs", "value": tabs})[0] == 200
    assert _json(_req(srv, "GET", "/api/state?key=tabs", token=srv.token))["value"] == tabs
    assert _json(_req(srv, "GET", "/api/state", token=srv.token))["state"] == {"tabs": tabs}
    files = list((env.tmp / "state").glob("*.json"))
    assert len(files) == 1 and (files[0].stat().st_mode & 0o777) == 0o600
    assert _req(srv, "POST", "/api/state", token=srv.token, body={"key": "Bad Key", "value": 1})[0] == 400
    assert _req(srv, "POST", "/api/state", token=srv.token, body={"key": "big", "value": "x" * 300_000})[0] == 413
    assert _req(srv, "POST", "/api/state", token=srv.token, body={"key": "tabs", "value": None})[0] == 200
    assert _json(_req(srv, "GET", "/api/state", token=srv.token))["state"] == {}


def test_views_carry_file_refs(start, env):
    srv = start()
    notes = _json(_req(srv, "GET", "/api/notes", token=srv.token))["notes"]
    assert {"space": "notes:0", "path": "review/demo.md", "tree": "t/notes/review/demo.md"} in [n["file"] for n in notes]
    figs = _json(_req(srv, "GET", "/api/figures?co=0", token=srv.token))
    refs = {r["reference"]: r for r in figs["references"]}
    assert refs["paper"]["file"] == {"space": "co:0", "path": "papers/demo/d/power.json", "tree": "t/proj/papers/demo/d/power.json"}
    assert {"space": "notes:0", "path": "fig.png", "tree": "t/notes/fig.png"} in [f["file"] for f in figs["figures"]]
    note = _json(_req(srv, "GET", "/api/note?root=0&rel=review/demo.md", token=srv.token))
    assert note["file"] == {"space": "notes:0", "path": "review/demo.md", "tree": "t/notes/review/demo.md"}


# ---------------------------------------------------------------------------
# densk's file API (what a densk-core front end calls)
# ---------------------------------------------------------------------------


def _tree_paths(nodes):
    out = []
    for n in nodes:
        out.append(n["path"])
        out += _tree_paths(n.get("dir") or [])
    return out


def test_tree_lists_notes_then_checkouts(start, env):
    srv = start()
    nodes = _json(_req(srv, "GET", "/api/tree", token=srv.token))
    assert [n["path"] for n in nodes] == ["t"]  # no settings file configured: only the project's folder
    assert [n["path"] for n in nodes[0]["dir"]] == ["t/notes", "t/proj"]
    assert all("dir" in n for n in nodes[0]["dir"])
    paths = _tree_paths(nodes)
    assert "t/notes/review/demo.md" in paths and "t/proj/papers/demo/spec.yaml" in paths
    (env.notes / ".hidden.md").write_text("x")
    assert "t/notes/.hidden.md" not in _tree_paths(_json(_req(srv, "GET", "/api/tree", token=srv.token)))
    meta = _json(_req(srv, "GET", "/api/meta", token=srv.token))
    assert {s["id"]: s["tree"] for s in meta["spaces"]} == {"co:0": "t/proj", "notes:0": "t/notes"}


def test_tree_hides_configured_names(start, env):
    srv = start()
    pkg = env.notes / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "model.py").write_text("x = 1\n")
    (env.notes / "build").mkdir()
    (env.notes / "build" / "out.md").write_text("x")
    tree = lambda: _tree_paths(_json(_req(srv, "GET", "/api/tree", token=srv.token)))
    find = lambda q: [h["path"] for h in _json(_req(srv, "GET", "/api/find?q=" + q, token=srv.token))]
    assert "t/notes/pkg/__init__.py" in tree() and find("__init__")  # nothing hidden by default
    srv.state.cfg.hide = ["__init__.py", "bui?d"]  # globs on the name; applies on the next request
    paths = tree()
    assert "t/notes/pkg/__init__.py" not in paths and "t/notes/pkg/model.py" in paths
    assert not any(p.startswith("t/notes/build") for p in paths)  # a hidden folder hides what's in it
    assert find("__init__") == [] and find("out") == []
    listing = _json(_req(srv, "GET", "/api/files/list?space=notes:0&path=pkg", token=srv.token))
    assert [e["name"] for e in listing["entries"]] == ["model.py"]
    assert _req(srv, "GET", "/api/file?path=t/notes/pkg/__init__.py", token=srv.token)[0] == 200  # still opens by path


def test_file_read_versions_and_ranges(start, env):
    srv = start()
    raw = (env.notes / "review" / "demo.md").read_bytes()
    status, h, body = _req(srv, "GET", "/api/file?path=t/notes/review/demo.md", token=srv.token)
    assert status == 200 and body == raw
    assert h["x-version"] == hashlib.sha256(raw).hexdigest() and h["x-densk-writable"] == "1"
    status, h, body = _req(srv, "GET", "/api/file?path=t/proj/papers/demo/spec.yaml", token=srv.token)
    assert status == 200 and h["x-densk-writable"] == "0"  # checkouts are read-only by default
    status, h, body = _req(srv, "GET", "/api/file?path=t/notes/fig.png", token=srv.token)
    assert status == 200 and "x-version" not in h and h["content-type"] == "image/png"
    status, h, body = _req(srv, "GET", "/api/file?path=t/notes/review/demo.md", token=srv.token, headers={"Range": "bytes=0-9"})
    assert status == 206 and body == raw[:10] and h["content-range"] == f"bytes 0-9/{len(raw)}"
    status, h, body = _req(srv, "GET", "/api/file?path=t/notes/review/demo.md", token=srv.token, headers={"Range": "bytes=-5"})
    assert status == 206 and body == raw[-5:]
    assert _req(srv, "GET", "/api/file?path=t/notes/review/demo.md", token=srv.token, headers={"Range": f"bytes={len(raw)}-"})[0] == 416
    for bad in ("t/notes/../secret.md", "nope/x.md", "t/notes/.hidden/x", "/etc/passwd"):
        assert _req(srv, "GET", "/api/file?path=" + bad, token=srv.token)[0] in (400, 403, 404), bad


def test_file_put_conflicts_and_rules(start, env):
    srv = start()
    target = env.notes / "review" / "demo.md"
    v0 = hashlib.sha256(target.read_bytes()).hexdigest()
    put = lambda path, text, base: _req(srv, "PUT", "/api/file?path=" + path, token=srv.token,
                                        headers={"x-base-version": base, "Content-Type": "text/plain"}, body=text.encode())
    status, _, body = put("t/notes/review/demo.md", "- [x] agree  note: ok\n", v0)
    assert status == 200 and json.loads(body)["version"] == hashlib.sha256(b"- [x] agree  note: ok\n").hexdigest()
    status, _, body = put("t/notes/review/demo.md", "stale", v0)
    assert status == 409 and json.loads(body)["version"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert target.read_text() == "- [x] agree  note: ok\n"
    assert put("t/notes/new.md", "hello", "new")[0] == 200 and (env.notes / "new.md").read_text() == "hello"
    assert put("t/notes/new.md", "again", "new")[0] == 409
    assert put("t/proj/papers/demo/spec.yaml", "x", "whatever")[0] == 403  # read-only checkout
    assert put("t/notes/../x.md", "x", "new")[0] in (403, 404)
    assert _req(srv, "PUT", "/api/file?path=t/notes/new.md", body=b"x", headers={"x-base-version": "new"})[0] == 403  # no token
    assert _req(srv, "PUT", "/api/file?path=t/notes/new.md", token=srv.token, body=b"x")[0] == 400  # no base version


def test_settings_file_in_the_tree(env, tmp_path):
    cfg_file = tmp_path / "cfg" / "pawtrace" / "ui.yaml"
    state = UiState(UiConfig(project=env.root, notes=[str(env.notes)], settings=cfg_file, state_dir=tmp_path / "st"))
    srv = UiServer(state)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        nodes = _json(_req(srv, "GET", "/api/tree", token=srv.token))
        assert [n["path"] for n in nodes] == ["pawtrace", "t"]
        assert nodes[0]["dir"] == [{"name": "settings.yaml", "path": "pawtrace/settings.yaml"}]
        status, h, body = _req(srv, "GET", "/api/file?path=pawtrace/settings.yaml", token=srv.token)
        assert status == 200 and h["x-version"] == "new" and body.startswith(b"# pawtrace's settings")
        put = lambda text, base: _req(srv, "PUT", "/api/file?path=pawtrace/settings.yaml", token=srv.token,
                                      headers={"x-base-version": base}, body=text.encode())
        status, _, out = put("by: Me\n", "new")
        assert status == 200 and cfg_file.read_text() == "by: Me\n"
        assert put("by: You\n", json.loads(out)["version"])[0] == 200 and cfg_file.read_text() == "by: You\n"
        (cfg_file.parent / "secret.json").write_text("{}")
        for bad in ("pawtrace/secret.json", "pawtrace/ui.yaml", "pawtrace/../ui.yaml"):
            assert _req(srv, "GET", "/api/file?path=" + bad, token=srv.token)[0] == 404, bad
    finally:
        srv.shutdown()
        srv.server_close()


def test_pawtrace_docs_in_the_tree(env, tmp_path):
    from pawtrace.webui import fileapi as fa
    docs, source = fa.pawtrace_docs()
    assert "playbooks/intake.md" in docs and (not source or "README.md" in docs)
    cfg_file = tmp_path / "cfg" / "ui.yaml"
    state = UiState(UiConfig(project=env.root, notes=[str(env.notes)], settings=cfg_file, docs=True,
                             state_dir=tmp_path / "st"))
    srv = UiServer(state)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        own = _json(_req(srv, "GET", "/api/tree", token=srv.token))[0]
        assert own["path"] == "pawtrace" and own["dir"][-1]["path"] == "pawtrace/settings.yaml"
        flat = lambda ns: [p for n in ns for p in (flat(n["dir"]) if "dir" in n else [n["path"]])]
        assert "pawtrace/playbooks/intake.md" in flat(own["dir"])
        status, h, body = _req(srv, "GET", "/api/file?path=pawtrace/playbooks/intake.md", token=srv.token)
        assert status == 200 and body == docs["playbooks/intake.md"].read_bytes()
        assert h["x-densk-writable"] == ("1" if source else "0")
        for bad in ("pawtrace/playbooks/__init__.py", "pawtrace/playbooks/../cli.py", "pawtrace/docs/nope.md"):
            assert _req(srv, "GET", "/api/file?path=" + bad, token=srv.token)[0] == 404, bad
    finally:
        srv.shutdown()
        srv.server_close()


def test_settings_api(env, tmp_path):
    cfg_file = tmp_path / "cfg" / "pawtrace" / "ui.yaml"
    state = UiState(UiConfig(project=env.root, notes=[str(env.notes)], settings=cfg_file, state_dir=tmp_path / "st"))
    srv = UiServer(state)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = _json(_req(srv, "GET", "/api/settings", token=srv.token))
        assert got["exists"] is False and got["version"] == "new" and got["writable"] and got["project"] == "t"
        assert got["values"]["notes"] == [] and got["pending"] == []  # unset: serve keeps its flag
        assert got["running"]["notes"] == [str(env.notes)]
        post = lambda values, version: _req(srv, "POST", "/api/settings", token=srv.token, body={"values": values, "version": version})
        values = {**got["values"], "by": "Me", "edit_cmd": "ed {path}", "branches": True, "notes": [str(env.notes)],
                  "frame_ancestors": ["http://127.0.0.1:8795"]}
        out = _json(post(values, "new"))
        saved = yaml.safe_load(cfg_file.read_text())
        assert saved == {"edit_cmd": "ed {path}", "by": "Me", "frame_ancestors": ["http://127.0.0.1:8795"],
                         "branches": True, "projects": {"t": {"notes": [str(env.notes)]}}}
        assert state.cfg.by == "Me" and state.cfg.edit_cmd == "ed {path}"  # live
        assert out["pending"] == ["branches", "frame_ancestors"]  # need a restart
        out = _json(post({**out["values"], "hide": ["__init__.py", " "]}, out["version"]))
        assert yaml.safe_load(cfg_file.read_text())["hide"] == ["__init__.py"]
        assert state.cfg.hide == ["__init__.py"] and "hide" not in out["pending"]  # live
        assert post(values, "new")[0] == 409  # stale version
        # other keys and projects survive; an empty value drops its key
        cfg_file.write_text(cfg_file.read_text() + "frontend: /x\n")
        cfg_file.write_text(cfg_file.read_text().replace("projects:\n", "projects:\n  other:\n    notes: [/o]\n"))
        cur = _json(_req(srv, "GET", "/api/settings", token=srv.token))
        out = _json(post({**cur["values"], "by": "", "notes": []}, cur["version"]))
        saved = yaml.safe_load(cfg_file.read_text())
        assert "by" not in saved and saved["frontend"] == "/x" and saved["projects"] == {"other": {"notes": ["/o"]}}
        assert state.cfg.by is None
        for bad in ({"edit_cmd": "ed"}, {"frame_ancestors": ["nope"]}, {"branches": "yes"}, {"notes": "x"}):
            assert post({**out["values"], **bad}, out["version"])[0] == 400, bad
        cfg_file.write_text("by: [unclosed\n")
        assert _req(srv, "GET", "/api/settings", token=srv.token)[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_settings_read_only(env, tmp_path):
    cfg_file = tmp_path / "ui.yaml"
    state = UiState(UiConfig(project=env.root, settings=cfg_file, read_only=True, state_dir=tmp_path / "st"))
    srv = UiServer(state)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = _json(_req(srv, "GET", "/api/settings", token=srv.token))
        assert got["writable"] is False
        assert _req(srv, "POST", "/api/settings", token=srv.token, body={"values": got["values"], "version": "new"})[0] == 403
        assert not cfg_file.exists()
    finally:
        srv.shutdown()
        srv.server_close()


def test_tree_map_shows_named_folders(env, tmp_path):
    (env.root / "code").mkdir()
    (env.root / "code" / "ctl.py").write_text("x = 1\n")
    (tmp_path / "outside").mkdir()
    (env.root / "pawtrace.yaml").write_text(
        "pawtrace: 1\nname: t\nprofile: minimal\ntree:\n  papers: papers\n  code: code\n  away: ../outside\n  gone: nope\n")
    papers = env.root / "papers"
    (papers / "demo" / "notes.md").write_text("# notes\n")
    state = UiState(UiConfig(project=env.root, notes=[str(env.notes), str(papers)], state_dir=tmp_path / "st"))
    srv = UiServer(state)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        nodes = _json(_req(srv, "GET", "/api/tree", token=srv.token))
        # the map's folders in its order (missing and outside ones skipped), then notes; papers/ is listed once
        assert [n["path"] for n in nodes[0]["dir"]] == ["t/papers", "t/code", "t/notes"]
        notes = _json(_req(srv, "GET", "/api/notes", token=srv.token))["notes"]
        demo = next(n for n in notes if n["rel"] == "demo/notes.md")
        assert demo["file"]["tree"] == "t/papers/demo/notes.md"
        status, h, _ = _req(srv, "GET", "/api/file?path=t/papers/demo/notes.md", token=srv.token)
        assert status == 200 and h["x-densk-writable"] == "1"  # inside the project folder
        status, h, _ = _req(srv, "GET", "/api/file?path=t/papers/demo/spec.yaml", token=srv.token)
        assert status == 200
        put = _req(srv, "PUT", "/api/file?path=t/papers/demo/SPEC.md", token=srv.token, headers={"x-base-version": "new"}, body=b"x")
        assert put[0] == 403  # generated
        home = _json(_req(srv, "GET", "/api/home", token=srv.token))
        assert {p["id"]: p["folder"] for p in home["papers"]} == {"demo": "t/papers/demo"}  # the paper page's place in the tree
    finally:
        srv.shutdown()
        srv.server_close()


def test_find_matches_names_first(start, env):
    srv = start()
    hits = [h["path"] for h in _json(_req(srv, "GET", "/api/find?q=demo&limit=10", token=srv.token))]
    assert hits[0] == "t/notes/review/demo.md" and "t/proj/papers/demo/spec.yaml" in hits


def test_events_stream_reports_changes(start, env):
    srv = start()
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.putrequest("GET", "/api/events", skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        conn.putheader(TOKEN_HEADER, srv.token)
        conn.endheaders()
        r = conn.getresponse()
        assert r.status == 200 and r.getheader("Content-Type") == "text/event-stream"
        assert r.fp.readline().startswith(b"retry:")
        import time as _t
        _t.sleep(0.5)
        (env.notes / "other.md").write_text("changed on disk\n")
        (env.notes / "added.md").write_text("new\n")
        event = data = None
        deadline = _t.time() + 8
        while _t.time() < deadline:
            line = r.fp.readline().decode()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "change":
                data = json.loads(line.split(":", 1)[1])
                break
        assert data is not None
        assert "t/notes/other.md" in data["paths"] and "t/notes/added.md" in data["paths"] and data["tree"] is True
    finally:
        conn.close()
    assert _req(srv, "GET", "/api/events")[0] == 403


def test_events_stream_reports_folder_only_changes(start, env):
    """A folder appearing or going away is a tree change even when no shown file did: an empty
    folder, or one holding only hidden things (here __pycache__), left in the sidebar otherwise."""
    import shutil
    stale = env.notes / "stale"
    (stale / "__pycache__").mkdir(parents=True)
    (stale / "__pycache__" / "m.cpython-314.pyc").write_bytes(b"x")
    srv = start()
    assert "t/notes/stale" in srv.state.tree().files(dirs=True)
    assert not any("__pycache__" in p for p in srv.state.tree().files(dirs=True))
    assert "t/notes/stale" not in srv.state.tree().files()  # find lists files only
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)

    def next_change(r):
        event = None
        deadline = time.time() + 8
        while time.time() < deadline:
            line = r.fp.readline().decode()
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event == "change":
                return json.loads(line.split(":", 1)[1])
        return None

    try:
        conn.putrequest("GET", "/api/events", skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", f"127.0.0.1:{port}")
        conn.putheader(TOKEN_HEADER, srv.token)
        conn.endheaders()
        r = conn.getresponse()
        assert r.status == 200 and r.fp.readline().startswith(b"retry:")
        time.sleep(0.5)
        shutil.rmtree(stale)
        data = next_change(r)
        assert data is not None and data["tree"] is True and "t/notes/stale" in data["paths"]
        (env.notes / "fresh").mkdir()
        data = next_change(r)
        assert data is not None and data["tree"] is True and data["paths"] == ["t/notes/fresh"]
    finally:
        conn.close()


def test_page_load_with_token_sets_an_httponly_cookie(start, env):
    srv = start()
    status, h, _ = _req(srv, "GET", f"/?t={srv.token}&x=1")
    assert status == 303 and h["location"] == "/?x=1"
    cookie = h["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Max-Age" not in cookie and "Expires" not in cookie
    pair = cookie.split(";", 1)[0]
    assert _req(srv, "GET", "/api/meta", headers={"Cookie": pair})[0] == 200
    assert _req(srv, "GET", "/api/meta", headers={"Cookie": pair.split("=")[0] + "=wrong"})[0] == 403
    assert _req(srv, "GET", "/api/meta", headers={"Cookie": "pawtrace_1=" + srv.token})[0] == 403  # another port's name
    assert _req(srv, "GET", "/?t=wrong")[0] == 403
