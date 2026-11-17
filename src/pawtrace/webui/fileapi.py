"""densk's file API, so a front end built on densk-core (``createHttpHost()``) runs on pawtrace serve.

One file tree over every space (:mod:`pawtrace.webui.files`), two folders at the top:

- ``pawtrace/`` holds pawtrace's own docs when the server shows them (``README.md``,
  ``docs/*.md``, ``playbooks/*.md``; :func:`pawtrace_docs`) and ``settings.yaml``, the user config
  (``ui.yaml``) when the server has one (the front end shows its Settings page there instead and
  opens the file from it);
- ``<project>/`` (the project's name) holds each notes folder and each checkout, named by its
  label and addressed by a slug: ``<project>/<slug>/<path in the space>``.

- ``GET /api/tree`` → ``[{name, path, dir?}]``
- ``GET /api/file?path=P`` → the bytes. Text files get ``x-version`` (their sha256) and
  ``x-densk-writable``; a single ``Range`` is honoured (206 / 416) for pdf.js.
- ``PUT /api/file?path=P`` with ``x-base-version`` (or ``new``) → ``{version}``, or 409
  ``{version}`` when the file changed since it was read. Writes go through
  :meth:`Space.write` (compare-and-swap, atomic, symlinks written at their target, generated
  files and read-only spaces refused).
- ``GET /api/events`` → server-sent events: ``change`` ``{paths, tree, all?}`` from a polling
  watcher, ``theme`` snapshots when the theme file changes.
- ``GET /api/find?q=&limit=`` → ``[{path}]``; ``GET /api/theme`` → the theme snapshot.

Everything stays inside the spaces: paths resolve through :meth:`Space.resolve`.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
from pathlib import Path
from typing import Any

from .files import FilesError, Space, hidden_name, kind_of

TREE_MAX = 20000
TEXT_KINDS = ("markdown", "code", "data", "text")
SKIP = {"node_modules", "__pycache__"}
# A folder in Tree.files(dirs=True): no mtime or size worth comparing, only there or not.
DIR_MARK = (0, -1)
POLL_S = 1.5


def slugify(label: str) -> str:
    s = re.sub(r"^(feature|fix|refs/heads)/", "", label)
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s).strip("-.") or "space"
    return s


SETTINGS_DIR = "pawtrace"
SETTINGS_PATH = f"{SETTINGS_DIR}/settings.yaml"


def pawtrace_docs() -> tuple[dict[str, Path], bool]:
    """pawtrace's own docs as ``{path under pawtrace/: file}``, and whether they're a source checkout.

    The playbooks ship in the package; ``README.md`` and ``docs/*.md`` only exist in a source
    checkout (the repo around ``src/pawtrace``), which is also the only place they're editable."""
    pkg = Path(__file__).resolve().parents[1]
    repo = pkg.parents[1]
    source = (repo / "pyproject.toml").is_file() and (repo / "src" / "pawtrace") == pkg
    out: dict[str, Path] = {}
    if source:
        if (repo / "README.md").is_file():
            out["README.md"] = repo / "README.md"
        for f in sorted((repo / "docs").glob("*.md")):
            out[f"docs/{f.name}"] = f
    for f in sorted((pkg / "playbooks").glob("*.md")):
        out[f"playbooks/{f.name}"] = f
    return out, source


def _doc_nodes(docs: dict[str, Path]) -> list[dict[str, Any]]:
    """The docs as tree nodes: files first, then each folder (docs/, playbooks/)."""
    files: list[dict[str, Any]] = []
    dirs: dict[str, list[dict[str, Any]]] = {}
    for rel in docs:
        head, _, name = rel.rpartition("/")
        node = {"name": name, "path": f"{SETTINGS_DIR}/{rel}"}
        (dirs.setdefault(head, []) if head else files).append(node)
    return files + [{"name": d, "path": f"{SETTINGS_DIR}/{d}", "dir": kids} for d, kids in dirs.items()]


class Tree:
    """Tree paths ↔ spaces. Built from the server's current spaces on each call (they're few)."""

    def __init__(self, spaces: list[Space], project: str = "project", settings: Space | None = None,
                 docs: dict[str, Path] | None = None, docs_writable: bool = False,
                 hide: tuple[str, ...] | list[str] = ()):
        self.hide = tuple(hide)  # ui.yaml's hide: name globs the tree and find skip
        self.label = project
        self.docs = docs or {}  # path under pawtrace/ → file (pawtrace_docs)
        self.docs_writable = docs_writable
        self.project = slugify(project)
        if self.project == SETTINGS_DIR:
            self.project = f"{SETTINGS_DIR}-project"
        self.settings = settings  # root = the config folder, label = the config file: only that file is served
        ordered = [s for s in spaces if s.alias is None]  # in the server's order
        self.by_slug: dict[str, Space] = {}
        self.slug_of: dict[str, str] = {}
        for sp in ordered:
            base = slugify(sp.label)
            slug, n = base, 2
            while slug in self.by_slug:
                slug, n = f"{base}-{n}", n + 1
            self.by_slug[slug] = sp
            self.slug_of[sp.id] = f"{self.project}/{slug}"
        for sp in spaces:  # a hidden space's files have the tree paths of the space shown for its folder
            if sp.alias is not None and sp.alias in self.slug_of:
                self.slug_of[sp.id] = self.slug_of[sp.alias]

    def split(self, path: str) -> tuple[Space, str]:
        path = str(path or "").strip().lstrip("/")
        if path == SETTINGS_PATH and self.settings is not None:
            return self.settings, self.settings.label
        head, _, rest = path.partition("/")
        if head == SETTINGS_DIR and rest in self.docs:
            f = self.docs[rest]  # one file per space: nothing else in its folder is reachable
            return Space(f"doc:{rest}", "docs", f.parent, f.name, self.docs_writable), f.name
        slug, _, rel = rest.partition("/")
        sp = self.by_slug.get(slug) if head == self.project else None
        if sp is None:
            raise FilesError("not found", 404)
        return sp, rel

    def path_of(self, space_id: str, rel: str) -> str | None:
        slug = self.slug_of.get(space_id)
        return f"{slug}/{rel}" if slug else None

    def nodes(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        own = _doc_nodes(self.docs)
        if self.settings is not None:
            own.append({"name": "settings.yaml", "path": SETTINGS_PATH})
        if own:
            out.append({"name": SETTINGS_DIR, "path": SETTINGS_DIR, "dir": own})
        spaces = []
        for slug, sp in self.by_slug.items():
            count = [0]
            path = f"{self.project}/{slug}"
            spaces.append({"name": sp.label, "path": path, "dir": _walk(sp.root.resolve(), sp.root.resolve(), path, count, self.hide)})
        out.append({"name": self.label, "path": self.project, "dir": spaces})
        return out

    def files(self, dirs: bool = False) -> dict[str, tuple[int, int]]:
        """Every file the tree shows: tree path → (mtime_ns, size). ``dirs=True`` (the change
        watcher) adds every folder the tree shows as ``DIR_MARK``, so a folder appearing or going
        away is a tree change even when no shown file did (an empty folder, or one holding only
        hidden things like ``__pycache__``)."""
        out: dict[str, tuple[int, int]] = {}
        if self.settings is not None:
            try:
                st = (self.settings.root / self.settings.label).stat()
                out[SETTINGS_PATH] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
        for rel, f in self.docs.items():
            try:
                st = f.stat()
                out[f"{SETTINGS_DIR}/{rel}"] = (st.st_mtime_ns, st.st_size)
            except OSError:
                pass
        for slug, sp in self.by_slug.items():
            base = sp.root.resolve()
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if not hidden_name(d, self.hide) and d not in SKIP]
                if dirs:
                    for d in dirnames:
                        out[f"{self.project}/{slug}/{(Path(dirpath) / d).relative_to(base).as_posix()}"] = DIR_MARK
                for name in filenames:
                    if hidden_name(name, self.hide):
                        continue
                    p = Path(dirpath) / name
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    out[f"{self.project}/{slug}/{p.relative_to(base).as_posix()}"] = (st.st_mtime_ns, st.st_size)
                    if len(out) > TREE_MAX * 4:
                        return out
        return out


def _walk(base: Path, d: Path, prefix: str, count: list[int], hide: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    try:
        entries = sorted(d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for child in entries:
        if hidden_name(child.name, hide) or child.name in SKIP or count[0] >= TREE_MAX:
            continue
        try:
            rp = child.resolve()
        except OSError:
            continue
        if rp != base and base not in rp.parents:
            continue  # a symlink that leaves the space
        count[0] += 1
        path = f"{prefix}/{child.relative_to(base).as_posix()}"
        if child.is_dir():
            out.append({"name": child.name, "path": path, "dir": _walk(base, child, prefix, count, hide)})
        else:
            out.append({"name": child.name, "path": path})
    return out


def version_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | str:
    """``bytes=a-b`` → (start, end inclusive); None = no range; "bad" = unsatisfiable."""
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header)
    if not m or (not m.group(1) and not m.group(2)):
        return "bad"
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:  # suffix: the last N bytes
        n = int(m.group(2))
        if n == 0:
            return "bad"
        start, end = max(0, size - n), size - 1
    if start >= size or start > end:
        return "bad"
    return start, min(end, size - 1)


def find(tree: Tree, query: str, limit: int = 50) -> list[dict[str, str]]:
    q = query.strip().lower()
    if not q:
        return []
    words = q.split()
    scored = []
    for path in tree.files():  # hidden names already left out: hidden from the tree, so from find too
        low = path.lower()
        name = low.rsplit("/", 1)[-1]
        if not all(w in low for w in words):
            continue
        score = (0 if name.startswith(words[0]) else 1 if all(w in name for w in words) else 2, len(path))
        scored.append((score, path))
    scored.sort()
    return [{"path": p} for _, p in scored[:limit]]


# ---------------------------------------------------------------------------
# Live changes: one polling watcher, any number of event-stream clients
# ---------------------------------------------------------------------------


class Hub:
    def __init__(self, tree_fn, theme_rev_fn, theme_snapshot_fn):
        self.tree_fn = tree_fn
        self.theme_rev_fn = theme_rev_fn
        self.theme_snapshot_fn = theme_snapshot_fn
        self.clients: set[queue.Queue] = set()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self.lock:
            self.clients.add(q)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True, name="pawtrace-watch")
                self.thread.start()
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients.discard(q)

    def publish(self, event: str, data: Any) -> None:
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait((event, data))
            except queue.Full:  # a stuck client: tell it to recheck everything instead
                try:
                    while True:
                        q.get_nowait()
                except queue.Empty:
                    pass
                q.put_nowait(("change", {"paths": [], "tree": True, "all": True}))

    def _run(self) -> None:
        last = self.tree_fn().files(dirs=True)
        rev = self.theme_rev_fn()
        while not self.stop.wait(POLL_S):
            try:
                now = self.tree_fn().files(dirs=True)
            except Exception:
                continue
            added = now.keys() - last.keys()
            removed = last.keys() - now.keys()
            changed = [p for p in now.keys() & last.keys() if now[p] != last[p]]
            if added or removed or changed:
                self.publish("change", {"paths": sorted([*changed, *added, *removed]), "tree": bool(added or removed)})
            last = now
            r = self.theme_rev_fn()
            if r != rev:
                rev = r
                self.publish("theme", self.theme_snapshot_fn())


def theme_snapshot(theme_path: Path | None) -> dict[str, Any]:
    """densk-core's theme snapshot from a theme file (docs/api.md § Theme file); ``{available: false}`` without one."""
    if not theme_path:
        return {"available": False}
    try:
        raw = json.loads(Path(theme_path).read_text())
        mtime = Path(theme_path).stat().st_mtime_ns
    except (OSError, ValueError):
        return {"available": False}
    if not isinstance(raw, dict) or not isinstance(raw.get("colors"), dict):
        return {"available": False}
    out: dict[str, Any] = {
        "available": True,
        "rev": str(raw.get("updatedAt") or mtime),
        "theme": {"id": raw.get("id") or "external", "colors": raw["colors"], "syntax": raw.get("syntax")},
    }
    for key in ("text", "layout", "tabs", "window", "oled"):
        if isinstance(raw.get(key), dict):
            out[key] = raw[key]
    return out


def theme_rev(theme_path: Path | None) -> str:
    if not theme_path:
        return "none"
    try:
        return str(Path(theme_path).stat().st_mtime_ns)
    except OSError:
        return "missing"


def text_kind(path: Path) -> bool:
    return kind_of(path) in TEXT_KINDS


__all__ = ["Tree", "Hub", "find", "parse_range", "theme_snapshot", "theme_rev", "version_of", "text_kind"]
