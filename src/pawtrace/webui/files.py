"""File access for a front end's host adapter: spaces, list, stat, read, write.

A front end (e.g. one built on a shared editor/viewer core) asks its host for files. In
``pawtrace serve`` the host adapter calls these through the JSON API. Every file lives in a
**space**: ``co:<id>`` (one checkout of the project) or ``notes:<id>`` (a notes folder given
with ``--notes``), addressed by a path relative to the space's root.

- Paths are resolved (symlinks followed) and must stay inside the space's root; anything
  else is refused. Hidden files and folders are never listed.
- Notes folders are writable unless the server runs ``--read-only``; checkouts are
  read-only unless it runs ``--write-checkout``. Generated files (``STATUS.md``,
  ``QUESTIONS.md``, each paper's ``SPEC.md``) are never writable: pawtrace rewrites them.
- Writes are text (UTF-8), compare-and-swap on the file's sha256 (``base_sha256``; ``None``
  creates a new file and fails if one exists), and atomic: a temp file beside the real one,
  then a rename. A symlinked path is written at its target, so the link stays a link.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from fnmatch import fnmatchcase
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..project import Project

READ_MAX = 64 * 1024 * 1024
WRITE_MAX = 8 * 1024 * 1024
KINDS = {
    "markdown": {".md", ".markdown"},
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"},
    "data": {".json", ".yaml", ".yml", ".csv", ".tsv", ".toml", ".npz", ".npy"},
    "code": {".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".rs", ".c", ".h", ".cpp", ".jl", ".r", ".m", ".ipynb", ".css", ".html"},
    "text": {".txt", ".rst", ".tex", ".bib", ".log", ""},
}
MIME = {
    ".md": "text/markdown; charset=utf-8", ".markdown": "text/markdown; charset=utf-8", ".pdf": "application/pdf",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".svg": "image/svg+xml", ".json": "application/json", ".csv": "text/csv; charset=utf-8",
}
SPACE_RE = re.compile(r"^(co|notes):(\d+)(?::[A-Za-z0-9._-]+)?$")  # co:<id>:<entry> = one folder of the tree: map


class FilesError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def hidden_name(name: str, hide: tuple[str, ...] | list[str] = ()) -> bool:
    """A name the file tree skips: dotfiles, caches, and the ``hide:`` patterns of ui.yaml
    (shell globs on the file or folder name, e.g. ``__init__.py``). Hidden from the tree and
    find only: a hidden file still opens by its path."""
    if name.startswith(".") or name in ("__pycache__", "node_modules"):
        return True
    return any(fnmatchcase(name, pat) for pat in hide)


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    for kind, exts in KINDS.items():
        if ext in exts:
            return kind
    return "binary"


def mime_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MIME:
        return MIME[ext]
    return "text/plain; charset=utf-8" if kind_of(path) in ("code", "text", "data") else "application/octet-stream"


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass(frozen=True)
class Space:
    id: str  # "co:0", "notes:1"
    kind: str  # checkout | notes
    root: Path
    label: str
    writable: bool
    project: Project | None = None  # checkouts only, for the generated-file rule
    alias: str | None = None  # the space the tree shows instead (same folder): this one isn't listed

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label, "path": str(self.root), "writable": self.writable}

    # -- paths ------------------------------------------------------------------
    def resolve(self, rel: str, *, must_exist: bool = True) -> Path:
        rel = str(rel or "").strip()
        if "\x00" in rel or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
            raise FilesError("paths are relative to the space", 400)
        base = self.root.resolve()
        target = (base / rel).resolve() if rel not in ("", ".") else base
        if target != base and base not in target.parents:
            raise FilesError("outside the space", 403)
        if any(part.startswith(".") for part in Path(rel).parts if part not in (".", "..")):
            raise FilesError("hidden paths are not served", 403)
        if must_exist and not target.exists():
            raise FilesError("not found", 404)
        return target

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def generated(self, path: Path) -> bool:
        if self.project is None:
            return False
        p = path.resolve()
        fixed = {self.project.status_path.resolve()}
        qp = (self.project.cfg.get("paths") or {}).get("questions", "QUESTIONS.md")
        fixed.add(self.project.path(qp).resolve())
        if p in fixed:
            return True
        papers = self.project.papers_dir.resolve()
        return p.name == "SPEC.md" and p.parent.parent == papers

    # -- operations -------------------------------------------------------------
    def entry(self, p: Path) -> dict[str, Any]:
        st = p.stat()
        out = {"name": p.name, "path": self.rel(p), "type": "dir" if p.is_dir() else "file", "mtime": st.st_mtime}
        if p.is_file():
            out.update(size=st.st_size, kind=kind_of(p))
        return out

    def list(self, rel: str = "", hide: tuple[str, ...] | list[str] = ()) -> dict[str, Any]:
        d = self.resolve(rel)
        if not d.is_dir():
            raise FilesError("not a folder", 400)
        base = self.root.resolve()
        entries = []
        for child in sorted(d.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if hidden_name(child.name, hide):
                continue
            try:
                rp = child.resolve()
            except OSError:
                continue
            if rp != base and base not in rp.parents:
                continue  # a symlink that leaves the space
            try:
                entries.append(self.entry(child))
            except OSError:
                continue
        return {"space": self.id, "path": "" if d == base else self.rel(d), "entries": entries}

    def stat(self, rel: str) -> dict[str, Any]:
        p = self.resolve(rel)
        out = self.entry(p)
        out.update(space=self.id, abspath=str(p), writable=self.can_write(p))
        if p.is_file() and p.stat().st_size <= READ_MAX:
            out["sha256"] = sha256_bytes(p.read_bytes())
        return out

    def read(self, rel: str) -> tuple[bytes, Path]:
        p = self.resolve(rel)
        if not p.is_file():
            raise FilesError("not a file", 400)
        if p.stat().st_size > READ_MAX:
            raise FilesError("file too large to serve", 413)
        return p.read_bytes(), p

    def can_write(self, p: Path) -> bool:
        return self.writable and not self.generated(p)

    def write(self, rel: str, text: Any, base_sha256: Any) -> dict[str, Any]:
        if not isinstance(text, str):
            raise FilesError("text must be a string", 400)
        data = text.encode("utf-8")
        if len(data) > WRITE_MAX:
            raise FilesError("too large to write", 413)
        if not self.writable:
            raise FilesError(f"{self.id} is read-only", 403)
        p = self.resolve(rel, must_exist=False)
        if self.generated(p):
            raise FilesError("generated file: pawtrace rewrites it, edit its sources instead", 403)
        if p.exists():
            if not p.is_file():
                raise FilesError("not a file", 400)
            if base_sha256 is None:
                raise FilesError("file exists: pass its base_sha256 to replace it", 409)
            current = sha256_bytes(p.read_bytes())
            if base_sha256 != current:
                raise FilesError("the file changed since it was read", 409)
            mode = p.stat().st_mode & 0o777
        else:
            if base_sha256 is not None:
                raise FilesError("the file no longer exists", 409)
            if not p.parent.is_dir():
                raise FilesError("the folder doesn't exist", 404)
            mode = 0o644
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=f".{p.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, mode)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return {"space": self.id, "path": self.rel(p), "sha256": sha256_bytes(data), "mtime": p.stat().st_mtime}


def parse_space(sid: Any) -> tuple[str, str]:
    m = SPACE_RE.fullmatch(str(sid or ""))
    if not m:
        raise FilesError("space must look like co:<id> or notes:<id>", 400)
    return m.group(1), m.group(2)
