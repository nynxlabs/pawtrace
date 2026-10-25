"""The ``command`` backend: any PDF → markdown tool, run as a plain command.

pawtrace doesn't bundle or call models. For better equations and layout, point
``extract.command`` in ``pawtrace.yaml`` at a converter you installed — marker
(``marker_single``), MinerU (``mineru``), docling (``docling --to md``), or your own
script around a local OCR model — as an argv list with ``{pdf}`` and ``{out}``
placeholders. pawtrace runs it, finds the markdown it wrote under ``{out}``, and reads:

- ``#`` headings → sections (numbers like "2.1" / "III.B" are picked up from the title);
- ``$$ … $$`` and ``\\[ … \\]`` blocks → equations with LaTeX (number from ``\\tag{n}``
  or a trailing "(n)");
- ``![alt](path)`` images with a "Figure N" / "Fig. N" caption next to them → figures.

``formula_command`` (any backend) is the same idea for single equation crops: argv with
``{image}``; whatever it prints on stdout becomes that equation's ``latex``.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .common import CAPTION, Extraction, caption_panels, heading_parts, section_label, slug, unique_id

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
TAG = re.compile(r"\\tag\{([^}]+)\}")
TRAILING_NUM = re.compile(r"\(\s*(S?\d{1,3}[a-z]?)\s*\)\s*$")


class CommandError(RuntimeError):
    pass


def _argv(template: list[str] | str, **subs: str) -> list[str]:
    if isinstance(template, str):
        import shlex

        template = shlex.split(template)
    return [str(a).format(**subs) for a in template]


def run_command(template: list[str] | str, timeout: float, **subs: str) -> subprocess.CompletedProcess[str]:
    argv = _argv(template, **subs)
    if not argv:
        raise CommandError("empty command")
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise CommandError(f"command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(f"command timed out after {timeout:g}s: {' '.join(argv)}") from exc


def extract(pdf: Path, out: Path, options: dict[str, Any]) -> Extraction:
    template = options.get("command")
    if not template:
        raise CommandError("extract.command is empty — set it in pawtrace.yaml (argv with {pdf} and {out})")
    raw_dir = out / "backend"
    raw_dir.mkdir(parents=True, exist_ok=True)
    proc = run_command(template, float(options.get("timeout_s") or 1800), pdf=str(pdf), out=str(raw_dir))
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        raise CommandError(f"extract command exited {proc.returncode}: " + " | ".join(tail))
    mds = sorted(raw_dir.rglob("*.md"), key=lambda p: (-p.stat().st_size, str(p)))
    if not mds:
        raise CommandError(f"extract command wrote no markdown under {raw_dir}")
    return parse_markdown(mds[0].read_text(errors="replace"), mds[0].parent, out)


def parse_markdown(md: str, base: Path, out: Path) -> Extraction:
    """Read a converter's markdown into sections, equations and figures (images copied to ``out/figures``)."""
    ex = Extraction()
    sec: dict[str, Any] = {"id": "front", "number": None, "title": "front matter", "label": "front matter", "page": None, "paras": [[]]}
    sections = [sec]
    sec_ids, eq_ids, fig_ids = {"front"}, set(), set()
    roman: str | None = None
    lines = md.splitlines()
    pending_img: tuple[str, str] | None = None   # (alt, path) waiting for its caption
    i = 0

    def add_figure(caption: str, alt: str, path: str) -> None:
        m = CAPTION.match(caption) or CAPTION.match(alt)
        if not m:
            ex.warnings.append(f"image {path} has no Figure/Fig. caption next to it — skipped")
            return
        num = m.group(1)
        fid = unique_id(f"fig.{num}", fig_ids)
        src = (base / path).resolve()
        img = None
        if src.is_file():
            img = f"figures/fig-{slug(fid[4:])}{src.suffix.lower()}"
            (out / img).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out / img)
        else:
            ex.warnings.append(f"Fig. {num}: image {path} not found")
        cap = caption if CAPTION.match(caption) else alt
        ex.figures.append({"id": fid, "number": num, "page": None, "caption": cap, "image": img, "bbox": None,
                           "panels": [{"id": f"{fid}{k}", "label": k, "caption": v, "image": None, "bbox": None}
                                      for k, v in sorted(caption_panels(cap).items())]})

    while i < len(lines):
        line = lines[i].strip()
        if pending_img is not None and line:
            alt, path = pending_img
            pending_img = None
            if CAPTION.match(line):
                add_figure(line, alt, path)
                i += 1
                continue
            add_figure("", alt, path)
        h = HEADING.match(line)
        if h:
            title = h.group(2).replace("*", "").strip()
            hp = heading_parts(title, styled=True)
            number, name, kind = hp if hp else (None, title, "named")
            if kind == "roman":
                roman = number
            elif kind == "letter":
                number = f"{roman}.{number}" if roman else None
                name = title if roman is None else name
            sid = unique_id(f"s{number}" if number else f"s.{slug(name)}", sec_ids)
            sec = {"id": sid, "number": number, "title": name, "label": section_label(number, name), "page": None, "paras": [[]]}
            sections.append(sec)
        elif line.startswith("$$") or line.startswith("\\["):
            close = "$$" if line.startswith("$$") else "\\]"
            body = line[2:]
            if close in body:
                body, rest = body.split(close, 1)
            else:
                rest = ""
                buf = [body]
                i += 1
                while i < len(lines) and close not in lines[i]:
                    buf.append(lines[i])
                    i += 1
                if i < len(lines):
                    last, rest = lines[i].split(close, 1)
                    buf.append(last)
                body = "\n".join(buf)
            latex = body.strip()
            tag = TAG.search(latex) or TRAILING_NUM.search(rest)
            num = tag.group(1).strip() if tag else None
            latex = TAG.sub("", latex).strip()
            eid = unique_id(f"eq.{num}" if num else "eq.u", eq_ids)
            ex.equations.append({"id": eid, "number": num, "page": None, "section": sec["id"], "text": latex,
                                 "latex": latex, "image": None, "bbox": None})
            sec["paras"].append([f"[Eq. ({num}): {latex}]" if num else f"[Eq.: {latex}]"])
            sec["paras"].append([])
        elif IMAGE.search(line) and IMAGE.sub("", line).strip() == "":
            m = IMAGE.search(line)
            pending_img = (m.group(1), m.group(2))
        elif CAPTION.match(line) and i + 1 < len(lines) and IMAGE.search(lines[i + 1] if lines[i + 1].strip() else (lines[i + 2] if i + 2 < len(lines) else "")):
            j = i + 1 if lines[i + 1].strip() else i + 2
            m = IMAGE.search(lines[j])
            add_figure(line, m.group(1), m.group(2))
            i = j
        elif not line:
            if sec["paras"][-1]:
                sec["paras"].append([])
        else:
            sec["paras"][-1].append(line)
        i += 1
    if pending_img is not None:
        add_figure("", *pending_img)
    for s in sections:
        s["text"] = "\n\n".join(" ".join(p) for p in s.pop("paras") if p)
        if s["id"] == "front" and not s["text"]:
            continue
        ex.sections.append(s)
    return ex
