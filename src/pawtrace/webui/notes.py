"""Notes folders for ``pawtrace serve``: intake forms, review forms and figure folders kept beside a project.

A project's notes can live outside its repo (a private notes vault, a shared drive), so the
UI takes them as folders from the command line or the user config, never from the
project's own files. Everything here is read-only: the UI shows the files and their marks;
marking happens in whatever editor the person uses (``--edit-cmd``).

Two form shapes are recognised, both plain markdown that reads fine in any editor:

- **Review forms** — each item is a block that starts with a bold head (``**`plant.dt`** · §2``)
  and ends with a mark line of checkboxes and a note::

      - [ ] agree  - [ ] disagree  - [ ] unsure  note:

  ``[x]`` (any case) marks a choice; everything after ``note:`` up to the next block is the
  note. The labels are free text, so other mark lines (``still open / answered``) count too.
- **Interviews** — each question is a bold head line followed by ``answer:``; the answer is
  the text after it up to the next question.

Anything else is listed as a plain note.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MD_MAX = 2 * 1024 * 1024
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
             ".webp": "image/webp", ".svg": "image/svg+xml"}
FIGURE_EXT = set(IMAGE_EXT) | {".pdf"}
SKIP_DIRS = {"node_modules", "__pycache__"}

_BOX = re.compile(r"-\s*\[([ xX])\]\s*(.*?)(?=\s+-\s*\[[ xX]\]|\s+note:|\s*$)")
_MARK_LINE = re.compile(r"^\s*-\s*\[[ xX]\]")
_HEAD = re.compile(r"^\*\*(.+?)\*\*(.*)$")
_TICKED = re.compile(r"`([^`]+)`")
_ANSWER = re.compile(r"^\s*answer:\s*(.*)$", re.I)
_HEADING = re.compile(r"^#{1,6}\s")


class NotesError(ValueError):
    pass


@dataclass(frozen=True)
class Root:
    """One notes folder. ``id`` is its index in the server's list (stable for a session)."""

    id: int
    path: Path
    label: str

    def resolve(self, rel: str) -> Path:
        """``rel`` inside this root, or NotesError. Symlinks that lead outside are refused."""
        if not rel or rel.startswith(("/", "\\")) or "\x00" in rel:
            raise NotesError("bad path")
        base = self.path.resolve()
        target = (base / rel).resolve()
        if target != base and base not in target.parents:
            raise NotesError("outside the notes folder")
        return target

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.path.resolve()).as_posix()


def make_roots(paths: list[str | Path]) -> list[Root]:
    out: list[Root] = []
    seen: set[Path] = set()
    for p in paths:
        path = Path(os.path.expanduser(str(p)))
        if not path.is_dir():
            raise NotesError(f"notes folder not found: {path}")
        r = path.resolve()
        if r in seen:
            continue
        seen.add(r)
        out.append(Root(len(out), path, path.name))
    return out


def walk(root: Root, exts: set[str]) -> list[Path]:
    """Files under ``root`` with one of ``exts``; hidden folders and symlinks that leave the root are skipped."""
    base = root.path.resolve()
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.startswith(".") or Path(name).suffix.lower() not in exts:
                continue
            p = Path(dirpath) / name
            try:
                rp = p.resolve()
            except OSError:
                continue
            if base in rp.parents and rp.is_file():
                out.append(p)
    return out


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _head_id(line: str) -> tuple[str, str] | None:
    """``**`id`** · rest`` → (id, rest); ``**A1** `id` — q`` → (id, A1 + rest)."""
    m = _HEAD.match(line.strip())
    if not m:
        return None
    inner, rest = m.group(1).strip(), m.group(2).strip()
    t = _TICKED.fullmatch(inner)
    if t:
        return t.group(1), rest.lstrip("·").strip()
    t2 = _TICKED.search(rest)
    if t2:
        return t2.group(1), f"{inner} {rest}".strip()
    return inner, rest.lstrip("·").strip()


def parse_marks(line: str) -> list[dict[str, Any]] | None:
    """The checkboxes on a mark line, or None when ``line`` isn't one.

    A mark line starts with a checkbox and has either two or more boxes or a ``note:``,
    so an ordinary one-item task list doesn't count.
    """
    if not _MARK_LINE.match(line):
        return None
    body = line.split("note:", 1)[0]
    boxes = [{"label": m.group(2).strip(), "checked": m.group(1) in "xX"} for m in _BOX.finditer(body)]
    if len(boxes) < 2 and "note:" not in line:
        return None
    return boxes or None


def parse_form(text: str) -> dict[str, Any]:
    """Blocks, marks and counts of one markdown file (see the module docstring)."""
    lines = text.splitlines()
    blocks: list[dict[str, Any]] = []
    head: tuple[str, str, int] | None = None  # (id, rest, line index)
    current: dict[str, Any] | None = None  # the block whose note/answer is still collecting

    def close() -> None:
        nonlocal current
        if current is not None:
            current["note"] = "\n".join(current.pop("_note")).strip()
            blocks.append(current)
            current = None

    def add(line: str, i: int) -> None:
        assert current is not None
        current["_note"].append(line)
        if line.strip():
            current["end"] = i + 1

    for i, line in enumerate(lines):
        h = _head_id(line) if line.startswith("**") else None
        if h or _HEADING.match(line):
            close()
            head = (h[0], h[1], i) if h else None
            continue
        marks = parse_marks(line)
        if marks is not None:
            close()
            ticked = [b["label"] for b in marks if b["checked"]]
            current = {
                "kind": "mark", "id": head[0] if head else f"line {i + 1}", "head": head[1] if head else "",
                "start": head[2] + 1 if head else i + 1, "line": i + 1, "end": i + 1, "options": marks, "choice": ticked[0] if len(ticked) == 1 else ("several" if ticked else None),
                "_note": [line.split("note:", 1)[1]] if "note:" in line else [],
            }
            head = None
            continue
        a = _ANSWER.match(line)
        if a and head is not None:
            close()
            current = {"kind": "answer", "id": head[0], "head": head[1], "start": head[2] + 1, "line": i + 1, "end": i + 1,
                       "_note": [a.group(1)]}
            head = None
            continue
        if current is not None:
            add(line, i)
    close()

    for b in blocks:
        if b["kind"] == "answer":
            b["choice"] = "answered" if b["note"] else None
    marks_ = [b for b in blocks if b["kind"] == "mark"]
    answers = [b for b in blocks if b["kind"] == "answer"]
    counts: dict[str, int] = {}
    for b in blocks:
        key = b["choice"] or ("unanswered" if b["kind"] == "answer" else "unmarked")
        counts[key] = counts.get(key, 0) + 1
    # one stray example line (a README showing the format) doesn't make a form
    kind = "review" if len(marks_) >= 2 else "interview" if len(answers) >= 2 else "note"
    return {"kind": kind, "blocks": blocks, "counts": counts, "total": len(blocks)}


def title_of(text: str, fallback: str) -> str:
    for line in text.splitlines()[:40]:
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def read_md(path: Path) -> str:
    if path.suffix.lower() != ".md":
        raise NotesError("not a markdown file")
    if path.stat().st_size > MD_MAX:
        raise NotesError("file too large to show")
    return path.read_text(encoding="utf-8", errors="replace")


def list_notes(roots: list[Root]) -> list[dict[str, Any]]:
    """Every markdown file under the roots, with its form kind and mark counts."""
    out = []
    for root in roots:
        for p in walk(root, {".md"}):
            try:
                text = read_md(p)
            except (OSError, NotesError):
                continue
            form = parse_form(text)
            st = p.stat()
            out.append({
                "root": root.id, "rel": root.rel(p), "title": title_of(text, p.stem), "kind": form["kind"],
                "counts": form["counts"], "total": form["total"], "mtime": st.st_mtime,
            })
    return out


def list_figures(roots: list[Root]) -> list[dict[str, Any]]:
    """Images and PDFs under the roots (for the figures page and digitize)."""
    out = []
    for root in roots:
        for p in walk(root, FIGURE_EXT):
            out.append({"root": root.id, "rel": root.rel(p), "name": p.name, "ext": p.suffix.lower(),
                        "size": p.stat().st_size, "path": str(p)})
    return out


def stamp(roots: list[Root]) -> float:
    """Newest mtime under the roots (markdown and figures), so the page knows when to reload."""
    best = 0.0
    for root in roots:
        for p in walk(root, FIGURE_EXT | {".md", ".json", ".csv", ".yaml"}):
            try:
                best = max(best, p.stat().st_mtime)
            except OSError:
                pass
    return best
