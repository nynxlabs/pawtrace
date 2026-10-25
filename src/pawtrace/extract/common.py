"""Shared pieces of the extraction backends: the result shape, heading / caption /
equation patterns, and the candidate spec items an extraction seeds.

A backend returns an :class:`Extraction`::

    sections:  [{id, number, title, label, page, text}]
    equations: [{id, number, page, section, text, latex, image, bbox}]
    figures:   [{id, number, page, caption, image, bbox, panels: [{id, label, caption, image, bbox}]}]
    warnings:  ["..."]

``image`` paths are relative to the extraction folder. Nothing here decides whether
a detail is stated — candidates are for a person or agent to review.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

KNOWN_HEADINGS = (
    "abstract", "introduction", "background", "related work", "methods", "materials and methods", "results", "discussion", "conclusion", "conclusions", "references", "bibliography",
    "acknowledgment", "acknowledgments", "acknowledgement", "acknowledgements", "appendix",
    "supplementary material", "supplementary information", "data availability", "code availability",
)
NO_CANDIDATES = ("references", "bibliography", "acknowledgment", "acknowledgments", "acknowledgement", "acknowledgements")

ARABIC = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-Z][^\n]{0,85})$")
ROMAN = re.compile(r"^([IVX]{1,6})\.\s+([A-Z][^\n]{0,85})$")
LETTER = re.compile(r"^([A-H])\.\s+([A-Z][^\n]{0,85})$")
CAPTION = re.compile(r"^(?:Fig\.|Figure|FIG\.|FIGURE|Supplementary Figure)\s*(S?\d{1,3})\s*(?:[.:|]|—|–)\s*(.*)$")
EQ_NUMBER = re.compile(r"^(?P<body>.*\S)\s*\((?P<num>(?:S?\d{1,3}|[A-Z]\.\d{1,3})[a-z]?)\)$")
EQ_ALONE = re.compile(r"^\((?:S?\d{1,3}|[A-Z]\.\d{1,3})[a-z]?\)$")
MATHY = re.compile(r"[=+\-−×·∑∏∫∂√≤≥≈∝∞^_/αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ]")
PANEL_LABEL = re.compile(r"^\(?([a-hA-H])\)?\.?$")
CAPTION_PANELS = re.compile(r"\(([a-hA-H])\)")
PARAM_EQ = re.compile(
    r"(?<![\w.])([A-Za-zͰ-Ͽ][\wͰ-Ͽ]{0,12})\s*=\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
)
PARAM_SET = re.compile(r"\b(?:set to|fixed at|chosen as|initiali[sz]ed (?:to|at)|equal to)\s+(-?\d+(?:\.\d+)?)", re.I)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")
EQ_MARKER = re.compile(r"\[Eq\.[^\]]*\]")   # how backends mark display equations inside section text
QUOTE_MAX = 300   # candidates carry short quotes, not passages


@dataclass
class Extraction:
    sections: list[dict[str, Any]] = field(default_factory=list)
    equations: list[dict[str, Any]] = field(default_factory=list)
    figures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: int | None = None


def heading_parts(text: str, styled: bool) -> tuple[str | None, str, str] | None:
    """``(number, title, kind)`` when ``text`` reads like a section heading, else None.

    ``kind`` is ``arabic`` | ``roman`` | ``letter`` | ``named``. Numbered headings need
    ``styled`` (bigger or bold font, or all caps) so body lines starting with a number
    aren't taken for headings.
    """
    t = " ".join(text.split())
    if not (2 <= len(t) <= 90):
        return None
    bare = t.rstrip(":.").strip()
    if bare.lower() in KNOWN_HEADINGS and bare[:1].isupper():
        return (None, bare, "named")
    if not styled or len(t.split()) > 12 or t.endswith((",", ";")):
        return None
    for rx, kind in ((ROMAN, "roman"), (ARABIC, "arabic"), (LETTER, "letter")):
        m = rx.match(t)
        if m:
            return (m.group(1), m.group(2).rstrip(":. "), kind)
    return None


def section_label(number: str | None, title: str) -> str:
    return f"§{number}" if number else title


def slug(s: str) -> str:
    s = "".join(_greek(c) for c in s)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


def _greek(c: str) -> str:
    name = unicodedata.name(c, "")
    return name.rsplit(" ", 1)[-1].lower() if name.startswith("GREEK") else c


def short(s: str, n: int = QUOTE_MAX) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def caption_panels(caption: str) -> dict[str, str]:
    """``{"a": "text after (a) …", ...}`` from a caption that labels its panels in parentheses."""
    parts = CAPTION_PANELS.split(caption)
    out: dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        out.setdefault(parts[i].lower(), parts[i + 1].strip(" ,;."))
    return out


def unique_id(base: str, seen: set[str]) -> str:
    cand, n = base, 2
    while cand in seen:
        cand, n = f"{base}-{n}", n + 1
    seen.add(cand)
    return cand


def candidates(ex: Extraction, extraction_id: str) -> list[dict[str, Any]]:
    """Candidate spec items: one per numbered equation, figure panel, and sentence that sets a value.

    Each carries ``status: candidate`` — the reviewer reads it against the paper and sets
    the real status. ``extracted`` says which extraction and object it came from.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(base: str, kind: str, text: str, section: str, ref: str, page: Any, image: str | None = None) -> None:
        ext: dict[str, Any] = {"extraction": extraction_id, "ref": ref}
        if page is not None:
            ext["page"] = page
        if image:
            ext["image"] = image
        out.append({"id": unique_id(base, seen), "kind": kind, "text": short(text), "section": section,
                    "status": "candidate", "extracted": ext})

    labels = {s["id"]: s.get("label") for s in ex.sections}
    for e in ex.equations:
        where = labels.get(e.get("section"))
        num = e.get("number")
        sec = ", ".join(x for x in (where, f"Eq. ({num})" if num else None) if x) or "?"
        add(f"eq.{num}" if num else "eq", "equation", e.get("latex") or e.get("text") or "", sec, e["id"], e.get("page"), e.get("image"))
    for f in ex.figures:
        panels = f.get("panels") or []
        if panels:
            for p in panels:
                add(f"panel.{f['number']}{p['label']}", "panel", p.get("caption") or f.get("caption") or "",
                    f"Fig. {f['number']}{p['label']}", p["id"], f.get("page"), p.get("image"))
        else:
            add(f"panel.{f['number']}", "panel", f.get("caption") or "", f"Fig. {f['number']}", f["id"], f.get("page"), f.get("image"))
    for s in ex.sections:
        if (s.get("title") or "").lower() in NO_CANDIDATES:
            continue
        prose = EQ_MARKER.sub("\n\n", s.get("text") or "")
        sents = [x for para in prose.split("\n\n") for x in SENTENCE_SPLIT.split(para.strip()) if x.strip()]
        for sent in sents:
            m = PARAM_EQ.search(sent)
            if not (m or PARAM_SET.search(sent)):
                continue
            base = f"param.{slug(m.group(1))}" if m else "param"
            add(base, "parameter", sent, s.get("label") or "?", s["id"], s.get("page"))
    return out
