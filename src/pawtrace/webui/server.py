"""The local server behind ``pawtrace serve``: the backend a front end talks to.

Stdlib only, loopback only, the same guards as ``pawtrace digitize``
(:mod:`pawtrace.localweb`): a loopback ``Host`` and the session token on every request,
JSON-only POSTs. It serves two kinds of things (``docs/api.md``):

- **pawtrace views**: overview, paper, notes and forms, figures and provenance, built from
  the project's plain files on each request (:mod:`pawtrace.webui.data`);
- **a host interface** for a front end's adapter: spaces (checkouts, notes folders),
  list / stat / read / write files with compare-and-swap, open a file in the person's
  editor, and a small state store for tabs and preferences.

It writes only when asked: a file through the host interface (where the space allows it),
an overlay PNG (``pawtrace overlay``), or a digitization (``pawtrace digitize``, mounted
under ``/dz/<id>/``). No model calls, no uploads, nothing leaves the machine.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import queue
import re
import secrets
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlencode, urlparse

from .. import __version__
from ..localweb import TOKEN_HEADER, BadRequest, GuardedHandler, new_token
from ..project import CONFIG_NAME, Project, ProjectError, git_top
from . import data as dv
from . import notes as nts
from . import fileapi as fa
from .files import WRITE_MAX, FilesError, Space, mime_of, parse_space, sha256_bytes
from .state import StateError, StateStore
from .theme import Theme

ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-\[\]:]+$")
FILE_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
# Data routes need the token; every other path is the front end's static files.
DATA_PREFIXES = ("/api/", "/api", "/f/", "/dz/")
# The front end at /: static files from a folder (the built web/ app, the bundled placeholder, or
# --frontend). They hold no project data, so they load without the token; every API call needs it.
APP_CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
           "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'self'; "
           "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
APP_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
             ".ico": "image/x-icon", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".wasm": "application/wasm",
             ".map": "application/json", ".txt": "text/plain; charset=utf-8"}


def default_frontend() -> Path:
    """The committed build of ``web/`` (``app/``) when it's there, else the bundled placeholder."""
    built = Path(__file__).with_name("app")
    return built if (built / "index.html").is_file() else Path(__file__).with_name("frontend")
FILE_TYPES = {**nts.IMAGE_EXT, ".pdf": "application/pdf"}


def slugify_entry(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(name)).strip("-.") or "folder"


def tree_entries(proj: Project) -> list[tuple[str, Path]]:
    """The project's ``tree:`` map as (name, folder), skipping folders that are missing or that
    leave the repo the project sits in (its own repo, or the one around it when it has its own)."""
    raw = proj.cfg.get("tree")
    if not isinstance(raw, dict):
        return []
    root = proj.root.resolve()
    bound = git_top(root)  # None outside git, or when the repo around it ignores the project
    if bound == root:  # the project is its own repo, nested in another: allow that one
        bound = _repo_top(root.parent) or root
    bound = bound or root
    out = []
    for name, rel in raw.items():
        if not isinstance(rel, str) or not str(name).strip():
            continue
        p = (root / os.path.expanduser(rel)).resolve()
        if (p == bound or bound in p.parents) and p.is_dir():
            out.append((str(name), p))
    return out


@functools.lru_cache(maxsize=64)
def _repo_top(d: Path) -> Path | None:
    try:
        r = subprocess.run(["git", "-C", str(d), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


@dataclass
class UiConfig:
    project: Path
    notes: list[str] = field(default_factory=list)
    edit_cmd: str | None = None
    frame_ancestors: list[str] = field(default_factory=list)
    theme: str | None = None
    by: str | None = None
    read_only: bool = False  # notes folders read-only too
    write_checkout: bool = False  # let the host interface write inside checkouts
    state_dir: Path | None = None  # default $XDG_STATE_HOME/pawtrace/ui
    frontend: Path | None = None  # folder served at / (default: the committed web/ build, else the placeholder)
    settings: Path | None = None  # the user config file, shown as pawtrace/settings.yaml (None: not shown)
    docs: bool = False  # pawtrace's own docs (README, docs/, playbooks/) in the pawtrace/ folder
    branches: bool = False  # with a tree: map, also show the project's other checkouts
    hide: list[str] = field(default_factory=list)  # name globs the file tree and find skip, e.g. __init__.py


class UiState:
    """What one ``pawtrace serve`` session knows: the project's checkouts, notes folders, open digitize sessions."""

    def __init__(self, cfg: UiConfig, *, popen: Callable[..., Any] | None = None):
        self.cfg = cfg
        self.root = Project.find(cfg.project).root
        self.roots = nts.make_roots(cfg.notes)
        self.theme = Theme(cfg.theme)
        self.files = dv.Files()
        self.checkouts = dv.checkouts(self.root)
        self.digitize: dict[str, Any] = {}  # id -> digitize Session
        self.dz_locks: dict[str, threading.Lock] = {}  # id -> that session's save lock
        self.lock = threading.Lock()  # digitize sessions + overlay (matplotlib isn't thread-safe)
        self.popen = popen or subprocess.Popen
        bad = [o for o in cfg.frame_ancestors if not ORIGIN_RE.match(o)]
        if bad:
            raise nts.NotesError(f"--frame-ancestor takes origins like http://127.0.0.1:8795, not {bad[0]!r}")
        self.ancestors = tuple(o.rstrip("/") for o in cfg.frame_ancestors)
        self.frontend = Path(cfg.frontend).expanduser() if cfg.frontend else default_frontend()
        if not (self.frontend / "index.html").is_file():
            raise nts.NotesError(f"--frontend: no index.html in {self.frontend}")
        self.state = StateStore.for_project(Project.load(self.root / CONFIG_NAME).name, cfg.state_dir)
        self.project_name = Project.load(self.root / CONFIG_NAME).name
        self.settings = None
        if cfg.settings:
            sp = Path(cfg.settings).expanduser()
            # the folder is the space and the label names the one file the tree serves from it
            self.settings = Space("settings", "settings", sp.parent, sp.name, not cfg.read_only)
        self.docs, source = fa.pawtrace_docs() if cfg.docs else ({}, False)
        self.docs_writable = source and not cfg.read_only
        self.hub = fa.Hub(self.tree, lambda: fa.theme_rev(self.theme.path), lambda: fa.theme_snapshot(self.theme.path))

    def tree(self) -> fa.Tree:
        return fa.Tree(self.spaces(), self.project_name, self.settings, self.docs, self.docs_writable, self.cfg.hide)

    # -- spaces (host interface) -------------------------------------------------
    def spaces(self) -> list[Space]:
        """The folders the file API serves, in tree order.

        A project whose ``pawtrace.yaml`` has a ``tree:`` map shows those folders for the
        checkout ``serve`` was started in (``tree: {papers: papers, code: ../src}``, paths from
        the project folder), and other checkouts only when the user config sets
        ``branches: true``. Without a map, each notes folder and each checkout is one folder.
        A notes folder that is also a tree folder is listed once, as the tree folder."""
        mapped: list[Space] = []
        whole: list[Space] = []
        try:
            has_tree = bool(tree_entries(Project.load(self.root / CONFIG_NAME)))
        except ProjectError:
            has_tree = False
        for c in self.checkouts:
            try:
                proj = Project.load(Path(c["path"]) / CONFIG_NAME)
            except ProjectError:
                continue
            main = proj.root.resolve() == self.root.resolve()
            entries = tree_entries(proj) if main else []
            if entries:
                for name, path in entries:
                    inside = path == proj.root.resolve() or proj.root.resolve() in path.parents
                    writable = not self.cfg.read_only and (inside or self.cfg.write_checkout)
                    mapped.append(Space(f"co:{c['id']}:{slugify_entry(name)}", "checkout", path, name, writable, proj))
            elif main or self.cfg.branches or not has_tree:
                whole.append(Space(f"co:{c['id']}", "checkout", proj.root, c["label"],
                                   self.cfg.write_checkout and not self.cfg.read_only, proj))
        notes = []
        for r in self.roots:
            same = next((m.id for m in mapped if m.root == r.path.resolve()), None)
            notes.append(Space(f"notes:{r.id}", "notes", r.path, r.label, not self.cfg.read_only, alias=same))
        return mapped + notes + whole

    def space(self, sid: Any) -> Space:
        parse_space(sid)
        for sp in self.spaces():
            if sp.id == sid:
                return sp
        raise FilesError("unknown space", 404)

    def file_ref(self, path: Path | None, co: str | None = None) -> dict[str, str] | None:
        """``{space, path}`` for a file inside a checkout (``co``) or a notes folder, else None."""
        if path is None:
            return None
        rp = Path(path).resolve()
        for sp in self.spaces():
            if sp.kind == "checkout" and co is not None and sp.id.split(":")[1] != str(co):
                continue
            base = sp.root.resolve()
            if base in rp.parents:
                rel = rp.relative_to(base).as_posix()
                return {"space": sp.id, "path": rel, "tree": self.tree().path_of(sp.id, rel)}
        return None

    # -- lookups ---------------------------------------------------------------
    def project(self, co: str | None) -> Project:
        if co in (None, ""):
            co = "0"
        for c in self.checkouts:
            if c["id"] == co:
                try:
                    return Project.load(Path(c["path"]) / CONFIG_NAME)
                except ProjectError as exc:
                    raise BadRequest(str(exc), 404) from None
        raise BadRequest("unknown checkout", 404)

    def root_of(self, rid: Any) -> nts.Root:
        try:
            return self.roots[int(rid)]
        except (TypeError, ValueError, IndexError):
            raise BadRequest("unknown notes folder", 404) from None

    def note_path(self, rid: Any, rel: Any) -> Path:
        try:
            return self.root_of(rid).resolve(str(rel or ""))
        except nts.NotesError as exc:
            raise BadRequest(str(exc), 403) from None

    def writable(self, path: Path, project: Project) -> bool:
        """Digitize output must land in the checkout or a notes folder."""
        p = path.resolve()
        bases = [project.root.resolve(), *(r.path.resolve() for r in self.roots)]
        return any(p == b or b in p.parents for b in bases)

    # -- views ------------------------------------------------------------------
    def meta(self) -> dict[str, Any]:
        self.checkouts = dv.checkouts(self.root)
        return {
            "version": __version__,
            "project": Project.load(self.root / CONFIG_NAME).name,
            "checkouts": self.checkouts,
            "notes": [{"id": r.id, "label": r.label, "path": str(r.path)} for r in self.roots],
            "can_edit": bool(self.cfg.edit_cmd),
            "spaces": [{**sp.to_json(), "tree": self.tree().slug_of.get(sp.id)} for sp in self.spaces()],
            "frame_ancestors": list(self.ancestors),
            "by": self.cfg.by,
        }

    def note(self, rid: Any, rel: Any) -> dict[str, Any]:
        p = self.note_path(rid, rel)
        if not p.is_file():
            raise BadRequest("not found", 404)
        try:
            text = nts.read_md(p)
        except nts.NotesError as exc:
            raise BadRequest(str(exc), 415) from None
        form = nts.parse_form(text)
        root = self.root_of(rid)
        # images the note links to, relative to its folder: hand out keys for the ones inside the root
        images = {}
        for ref in _image_refs(text):
            try:
                ip = root.resolve(str((Path(root.rel(p)).parent / ref).as_posix()))
            except nts.NotesError:
                continue
            if ip.is_file() and ip.suffix.lower() in nts.IMAGE_EXT:
                images[ref] = self.files.url(ip)
        return {"root": root.id, "rel": root.rel(p), "path": str(p),
                "file": {"space": f"notes:{root.id}", "path": root.rel(p), "tree": self.tree().path_of(f"notes:{root.id}", root.rel(p))},
                "title": nts.title_of(text, p.stem),
                "text": text, "images": images, **form}

    def open_file(self, body: dict[str, Any]) -> dict[str, Any]:
        """Open ``{space, path}`` in the person's editor: run ``--edit-cmd`` (argv, no shell)."""
        p = self.space(body.get("space")).resolve(str(body.get("path") or ""))
        if not p.is_file():
            raise FilesError("not a file", 400)
        if not self.cfg.edit_cmd:
            raise BadRequest("no --edit-cmd configured", 409)
        argv = [a.replace("{path}", str(p)) for a in shlex.split(self.cfg.edit_cmd)]
        try:
            self.popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise BadRequest(f"edit command failed: {exc}", 500) from None
        return {"ok": True, "path": str(p)}

    def overlay(self, co: Any, panel_id: Any) -> dict[str, Any]:
        from ..data import PanelData
        from ..overlay import next_versioned, plot_panel
        from ..panel import Panel
        from ..runs import RunStore, last_report

        proj = self.project(co)
        panel = Panel.load(proj, str(panel_id))
        rec = RunStore(proj.runs_dir).latest(panel.id)
        if not rec:
            raise BadRequest(f"no runs for {panel.id}", 409)
        out, _ = next_versioned(proj.images_dir / panel.paper / panel.id.split("/", 1)[1], "replication")
        with self.lock:
            try:
                info = plot_panel(panel, PanelData.from_json(rec["data"]), out, report=last_report(rec))
            except RuntimeError as exc:
                raise BadRequest(str(exc), 409) from None
        return {"out": Path(info["out"]).name, "run": rec["id"], "url": self.files.url(Path(info["out"])),
                "file": self.file_ref(Path(info["out"]), str(co or "0")), "warnings": info.get("warnings", [])}

    def start_digitize(self, body: dict[str, Any]) -> dict[str, Any]:
        from ..digitize.command import build_session
        from ..digitize.core import DigitizeError
        from ..panel import PanelError

        proj = self.project(body.get("co"))
        mode = body.get("mode") or "new"
        image = None
        if isinstance(body.get("image"), dict):
            image = str(self.note_path(body["image"].get("root"), body["image"].get("rel")))
        elif body.get("image"):
            raise BadRequest("image must be a figure from a notes folder", 400)
        out = body.get("out") or None
        if out:
            outp = Path(out).expanduser()
            outp = outp if outp.is_absolute() else proj.root / outp
            if not self.writable(outp, proj):
                raise BadRequest("the output file must be inside the checkout or a notes folder", 403)
            out = str(outp)
        args = argparse.Namespace(
            project=str(proj.root), image=image, out=out,
            review=(str(body.get("panel")), str(body.get("reference"))) if mode == "review" else None,
            panel=(body.get("panel") or None) if mode != "review" else None,
            reference=(body.get("reference") or None) if mode != "review" else None,
            paper=body.get("paper") or None, page=_int_or_none(body.get("page")), crop=body.get("crop") or None,
            dpi=_int_or_none(body.get("dpi")), by=body.get("by") or self.cfg.by, options=body.get("options") or None,
            force=bool(body.get("force")),
        )
        try:
            sess = build_session(args)
        except (DigitizeError, PanelError, ProjectError, FileNotFoundError, KeyError, ValueError) as exc:
            raise BadRequest(str(exc), 400) from None
        if not self.writable(sess.out, proj):
            raise BadRequest("the output file must be inside the checkout or a notes folder", 403)
        sid = new_token()[:12]
        with self.lock:
            self.digitize[sid] = sess
            self.dz_locks[sid] = threading.Lock()
        return {"id": sid, "title": sess.title, "out": str(sess.out), "note": sess.note}

    def digitize_list(self) -> list[dict[str, Any]]:
        with self.lock:
            return [{"id": k, "title": s.title, "out": str(s.out), "mode": s.mode, "saves": len(s.saves)}
                    for k, s in self.digitize.items()]


def _int_or_none(v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise BadRequest(f"not a number: {v!r}") from None


def _image_refs(text: str) -> list[str]:
    import re

    refs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text) + re.findall(r"!\[\[([^\]|]+)", text)
    return [r for r in dict.fromkeys(refs) if "://" not in r and not r.startswith("/")]


class _Handler(GuardedHandler):
    server: "UiServer"

    # -- GET -------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not path.startswith(DATA_PREFIXES):
            self._app(path)
            return
        if not self.allowed(self.get_token()):
            return
        st = self.server.state
        q = self.query()
        try:
            if path.startswith("/f/"):
                self._file(path[3:])
            elif path == "/api/spaces":
                self.send_json({"spaces": [sp.to_json() for sp in st.spaces()]})
            elif path == "/api/files/list":
                self.send_json(st.space(q.get("space")).list(q.get("path", ""), st.cfg.hide))
            elif path == "/api/files/stat":
                self.send_json(st.space(q.get("space")).stat(q.get("path", "")))
            elif path == "/api/files/read":
                data, fp = st.space(q.get("space")).read(q.get("path", ""))
                self.send_bytes(200, data, mime_of(fp), {"Content-Security-Policy": FILE_CSP,
                                                        "X-Pawtrace-Sha256": sha256_bytes(data),
                                                        "X-Pawtrace-Mtime": str(fp.stat().st_mtime)})
            elif path == "/api/state":
                key = q.get("key")
                self.send_json({"key": key, "value": st.state.get(key)} if key else {"state": st.state.get()})
            elif path.startswith("/dz/"):
                self._dz_get(path)
            elif path == "/api/meta":
                self.send_json(st.meta())
            elif path == "/api/theme":
                self.send_json(fa.theme_snapshot(st.theme.path))
            elif path == "/api/tree":
                if q.get("path"):
                    self.send_json({"error": "no lazy folders"}, 404)
                else:
                    self.send_json(st.tree().nodes())
            elif path == "/api/file":
                self._read_file(q.get("path", ""))
            elif path == "/api/find":
                self.send_json(fa.find(st.tree(), q.get("q", ""), int(q.get("limit") or 50)))
            elif path == "/api/events":
                self._events()
            elif path == "/api/home":
                co = q.get("co") or "0"
                proj = st.project(co)
                home = dv.home(proj)
                for p in home["papers"]:  # where the paper's folder is in the file tree (None: not shown)
                    p["folder"] = (st.file_ref(proj.paper_dir(p["id"]), co) or {}).get("tree")
                self.send_json(home)
            elif path == "/api/settings":
                from . import settings as us

                self.send_json(us.view(st))
            elif path == "/api/paper":
                co = q.get("co") or "0"
                view = dv.paper_view(st.project(co), q.get("id", ""), st.files)
                for pn in view["panels"]:
                    pn["overlay_file"] = st.file_ref(Path(pn["overlay_path"]), co) if pn.get("overlay_path") else None
                self.send_json(view)
            elif path == "/api/notes":
                notes = nts.list_notes(st.roots)
                tree = st.tree()
                for n in notes:
                    n["file"] = {"space": f"notes:{n['root']}", "path": n["rel"], "tree": tree.path_of(f"notes:{n['root']}", n["rel"])}
                self.send_json({"notes": notes, "roots": [{"id": r.id, "label": r.label} for r in st.roots]})
            elif path == "/api/note":
                self.send_json(st.note(q.get("root"), q.get("rel")))
            elif path == "/api/figures":
                co = q.get("co") or "0"
                figs = dv.figures(st.project(co), st.roots, st.files)
                for r in figs["references"]:
                    r["file"] = st.file_ref(Path(r["abspath"]), co) if r.get("abspath") else None
                tree = st.tree()
                for f in figs["figures"]:
                    f["file"] = {"space": f"notes:{f['root']}", "path": f["rel"], "tree": tree.path_of(f"notes:{f['root']}", f["rel"])}
                for r in figs["readmes"]:
                    r["file"] = {"space": f"notes:{r['root']}", "path": r["rel"], "tree": tree.path_of(f"notes:{r['root']}", r["rel"])}
                self.send_json({**figs, "sessions": st.digitize_list()})
            elif path == "/api/stamp":
                self.send_json({"stamp": dv.stamp(st.project(q.get("co")), st.roots), "theme": fa.theme_rev(st.theme.path)})
            else:
                self.send_bytes(404, b"not found", "text/plain")
        except (BadRequest, FilesError, StateError) as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except KeyError as exc:
            self.send_json({"error": str(exc).strip("'\"")}, 404)
        except Exception as exc:  # a broken panel or run file: say what, keep serving
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _read_file(self, tree_path: str) -> None:
        sp, rel = self.server.state.tree().split(tree_path)
        if sp.kind == "settings" and not (sp.root / rel).is_file():
            # no config yet: offer the commented example; saving it creates the file (x-version new)
            from .command import SETTINGS_EXAMPLE
            self.send_bytes(200, SETTINGS_EXAMPLE.encode(), "text/plain; charset=utf-8",
                            {"Content-Security-Policy": FILE_CSP, "x-version": "new", "x-densk-writable": "1" if sp.writable else "0"})
            return
        data, fp = sp.read(rel)
        headers = {"Content-Security-Policy": FILE_CSP, "Accept-Ranges": "bytes"}
        if fa.text_kind(fp):
            headers["x-version"] = fa.version_of(data)
            headers["x-densk-writable"] = "1" if sp.can_write(fp) else "0"
        rng = fa.parse_range(self.headers.get("Range"), len(data))
        if rng == "bad":
            self.send_bytes(416, b"", "text/plain", {**headers, "Content-Range": f"bytes */{len(data)}"})
            return
        if rng is not None:
            start, end = rng  # type: ignore[misc]
            self.send_bytes(206, data[start:end + 1], mime_of(fp), {**headers, "Content-Range": f"bytes {start}-{end}/{len(data)}"})
            return
        self.send_bytes(200, data, mime_of(fp), headers)

    def _events(self) -> None:
        """Server-sent events until the client goes away: ``change`` batches and ``theme`` snapshots."""
        hub = self.server.state.hub
        q = hub.subscribe()
        self.close_connection = True
        try:
            self.send_response(200)
            for k, v in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                         ("Referrer-Policy", "no-referrer"), ("Connection", "close")):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while not getattr(self.server, "closing", False):
                try:
                    event, data = q.get(timeout=15)
                    self.wfile.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            hub.unsubscribe(q)

    def _app(self, path: str) -> None:
        """A front-end file (host check only): /, /assets/…, /pdfjs/…. Unknown paths without an
        extension get index.html (app routes)."""
        if not self.host_ok():
            self.send_bytes(403, b"bad host", "text/plain")
            return
        if path == "/app" or path.startswith("/app/"):  # the old address
            self.send_response(308)
            self.send_header("Location", "/" + path[len("/app/"):] if path.startswith("/app/") else "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # A page load with ?t=<token>: keep the token in an HttpOnly cookie and drop it from the URL.
        q = self.query()
        if q.get("t"):
            if not secrets.compare_digest(q["t"], self.server.token):
                self.send_bytes(403, b"bad token", "text/plain")
                return
            rest = {k: v for k, v in q.items() if k != "t"}
            loc = path + ("?" + urlencode(rest) if rest else "")
            self.send_response(303)
            self.send_header("Set-Cookie", self.token_cookie_header())
            self.send_header("Location", loc)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        base = self.server.state.frontend.resolve()
        rel = unquote(path.lstrip("/")) or "index.html"
        target = (base / rel).resolve()
        if (target != base and base not in target.parents) or any(part.startswith(".") for part in Path(rel).parts):
            self.send_bytes(404, b"not found", "text/plain")
            return
        if not target.is_file():
            if Path(rel).suffix:
                self.send_bytes(404, b"not found", "text/plain")
                return
            target = base / "index.html"
        ctype = APP_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_bytes(200, target.read_bytes(), ctype, {"Content-Security-Policy": APP_CSP})

    def _file(self, key: str) -> None:
        p = self.server.state.files.get(key.split("?", 1)[0])
        if p is None or not p.is_file():
            self.send_bytes(404, b"not found", "text/plain")
            return
        ctype = FILE_TYPES.get(p.suffix.lower())
        if not ctype or p.stat().st_size > dv.MAX_FIGURE:
            self.send_bytes(415, b"not shown", "text/plain")
            return
        self.send_bytes(200, p.read_bytes(), ctype, {"Content-Security-Policy": FILE_CSP})

    def _dz(self, path: str) -> tuple[str, Any, str] | None:
        rest = path[len("/dz/"):]
        sid, _, sub = rest.partition("/")
        with self.server.state.lock:
            sess = self.server.state.digitize.get(sid)
        if sess is None:
            self.send_bytes(404, b"no such digitize session", "text/plain")
            return None
        return sid, sess, "/" + sub

    def _dz_get(self, path: str) -> None:
        from ..digitize.server import route_get

        got = self._dz(path)
        if got and not route_get(self, got[1], got[2], self.server.state.ancestors):
            self.send_bytes(404, b"not found", "text/plain")

    # -- POST ------------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        if not self.allowed(self.headers.get(TOKEN_HEADER)):
            return
        body = self.read_json()
        if body is None:
            return
        path = urlparse(self.path).path
        st = self.server.state
        try:
            if path.startswith("/dz/"):
                from ..digitize.server import route_post

                got = self._dz(path)
                if not got:
                    return
                sid, sess, sub = got

                def quit_() -> None:
                    with st.lock:
                        st.digitize.pop(sid, None)
                        st.dz_locks.pop(sid, None)

                with st.lock:
                    lock = st.dz_locks.get(sid) or threading.Lock()
                if not route_post(self, sess, sub, body, lock, quit_):
                    self.send_bytes(404, b"not found", "text/plain")
            elif path == "/api/overlay":
                self.send_json(st.overlay(body.get("co"), body.get("panel")))
            elif path == "/api/files/open":
                self.send_json(st.open_file(body))
            elif path == "/api/files/write":
                self.send_json(st.space(body.get("space")).write(str(body.get("path") or ""), body.get("text"), body.get("base_sha256")))
            elif path == "/api/state":
                st.state.set(body.get("key"), body.get("value"))
                self.send_json({"ok": True})
            elif path == "/api/settings":
                from . import settings as us

                self.send_json(us.save(st, body))
            elif path == "/api/digitize/start":
                self.send_json(st.start_digitize(body))
            elif path == "/api/quit":
                self.send_json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_bytes(404, b"not found", "text/plain")
        except (BadRequest, FilesError, StateError) as exc:
            self.send_json({"error": str(exc)}, exc.status)
        except Exception as exc:  # a broken panel or run file: say what, keep serving
            self.send_json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_PUT(self) -> None:  # noqa: N802
        """``PUT /api/file?path=P``: densk's save, against ``x-base-version`` (or ``new``)."""
        if not self.allowed(self.headers.get(TOKEN_HEADER)):
            return
        if urlparse(self.path).path != "/api/file":
            self.send_bytes(404, b"not found", "text/plain")
            return
        st = self.server.state
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > WRITE_MAX:
            self.send_json({"error": "too large"}, 413)
            return
        body = self.rfile.read(n)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            self.send_json({"error": "text must be UTF-8"}, 400)
            return
        base = self.headers.get("x-base-version")
        if not base:
            self.send_json({"error": "x-base-version is required (the version read, or new)"}, 400)
            return
        try:
            sp, rel = st.tree().split(self.query().get("path", ""))
            if sp.kind == "settings" and sp.writable:
                sp.root.mkdir(parents=True, exist_ok=True)
            out = sp.write(rel, text, None if base == "new" else base)
            self.send_json({"version": out["sha256"]})
        except FilesError as exc:
            if exc.status == 409:
                current = ""
                try:
                    data, _ = sp.read(rel)
                    current = fa.version_of(data)
                except FilesError:
                    pass
                self.send_json({"error": str(exc), "version": current}, 409)
            else:
                self.send_json({"error": str(exc)}, exc.status)

    do_HEAD = do_GET  # noqa: N815


class UiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: UiState, port: int = 0, token: str | None = None):
        self.state = state
        self.token = token or new_token()
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"
