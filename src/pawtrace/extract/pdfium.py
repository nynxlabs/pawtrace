"""Lightweight backend on PDFium (``pypdfium2``, Apache-2.0 / BSD-3-Clause): offline, CPU only.

- **text by section**: text lines in reading order (one or two columns), page headers and
  footers dropped, headings found by numbering + font (bigger, bold or all caps);
- **equations**: numbered display equations ("… (3)") as text plus a PNG crop — PDF text
  of math is often garbled, so ``latex`` stays empty unless ``formula_command`` fills it;
- **figures**: captions ("Fig. 3." / "Figure 3:") and the graphics above them, cropped to
  PNG; panel letters inside the figure split it into per-panel crops.

It's heuristic. Scanned PDFs (no text layer), unnumbered equations and unusual layouts
need the ``command`` backend (marker, MinerU, docling, a local OCR model, ...).
"""
from __future__ import annotations

import ctypes
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    CAPTION, EQ_ALONE, EQ_NUMBER, MATHY, PANEL_LABEL, Extraction, caption_panels, heading_parts, section_label, slug, unique_id,
)

GRAPHIC_TYPES = (2, 3, 4, 5)   # FPDF_PAGEOBJ_PATH, IMAGE, SHADING, FORM


def available() -> bool:
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True


def version() -> str:
    import pypdfium2

    return f"pypdfium2 {pypdfium2.version.PYPDFIUM_INFO}"


@dataclass
class Line:
    page: int
    left: float
    bottom: float
    right: float
    top: float
    text: str
    size: float
    bold: bool
    col: str = "full"   # full | left | right

    @property
    def height(self) -> float:
        return max(self.top - self.bottom, 1.0)

    def inside(self, box: tuple[float, float, float, float], pad: float = 2.0) -> bool:
        l, b, r, t = box
        return self.left >= l - pad and self.right <= r + pad and self.bottom >= b - pad and self.top <= t + pad


def _font(tp: Any, raw: Any, idx: int) -> tuple[float, bool]:
    if idx is None or idx < 0:
        return 0.0, False
    size = float(raw.FPDFText_GetFontSize(tp, idx))
    n = raw.FPDFText_GetFontInfo(tp, idx, None, 0, None)
    name = ""
    if n:
        buf = ctypes.create_string_buffer(n)
        raw.FPDFText_GetFontInfo(tp, idx, buf, n, None)
        name = buf.value.decode("latin-1", "replace")
    weight = raw.FPDFText_GetFontWeight(tp, idx)
    return size, ("bold" in name.lower() or "black" in name.lower() or weight >= 600)


def _clean(s: str) -> str:
    return s.replace("\r", " ").replace("\n", " ").replace("\x02", "").replace("\xad", "").replace("￾", "").strip()


def _page_lines(page: Any, pno: int, raw: Any) -> list[Line]:
    tp = page.get_textpage()
    segs = []
    for i in range(tp.count_rects()):
        l, b, r, t = tp.get_rect(i)
        s = _clean(tp.get_text_bounded(l, b, r, t))
        if not s:
            continue
        size, bold = _font(tp, raw, tp.get_index(l + 0.5, (b + t) / 2, 2.0, (t - b) / 2 + 1))
        if (t - b) > max(3 * size, 30) and (r - l) < (t - b):
            continue   # rotated text (e.g. the arXiv stamp in the margin)
        segs.append(Line(pno, l, b, r, t, s, size or (t - b), bold))
    # merge segments that sit on the same baseline and nearly touch (one visual line)
    segs.sort(key=lambda s: (-round(s.top, 0), s.left))
    rows: list[list[Line]] = []
    for s in segs:
        for row in rows:
            ref = row[0]
            overlap = min(ref.top, s.top) - max(ref.bottom, s.bottom)
            if overlap > 0.5 * min(ref.height, s.height):
                row.append(s)
                break
        else:
            rows.append([s])
    lines: list[Line] = []
    for row in rows:
        row.sort(key=lambda s: s.left)
        cur = row[0]
        for s in row[1:]:
            if s.left - cur.right <= 0.9 * max(cur.height, s.height):
                cur = Line(pno, min(cur.left, s.left), min(cur.bottom, s.bottom), max(cur.right, s.right), max(cur.top, s.top),
                           f"{cur.text} {s.text}" if not cur.text.endswith(" ") else cur.text + s.text,
                           cur.size, cur.bold and s.bold)
            else:
                lines.append(cur)
                cur = s
        lines.append(cur)
    for ln in lines:
        ln.text = " ".join(ln.text.split())
    return _attach_equation_numbers(lines, page.get_size()[0])


def _attach_equation_numbers(lines: list[Line], width: float) -> list[Line]:
    """A right-aligned "(3)" usually sits apart from its equation: join it to the pieces on its row."""
    nums = [ln for ln in lines if EQ_ALONE.match(ln.text)]
    if not nums:
        return lines
    mid = width / 2
    long = [ln for ln in lines if len(ln.text) > 30]
    two_col = bool(long) and sum(1 for ln in long if ln.left < mid - 10 and ln.right > mid + 10) < 0.3 * len(long)
    gone: set[int] = set()
    for num in nums:
        row = [ln for ln in lines if ln is not num and id(ln) not in gone and ln.right <= num.left + 1
               and min(ln.top, num.top) - max(ln.bottom, num.bottom) > -0.5 * num.height
               and num.left - ln.right < 0.6 * (num.right - min(x.left for x in lines))
               and not (two_col and num.left > mid and ln.left < mid - 10)]   # stay in the number's column
        row = [ln for ln in row if not ln.text.endswith(".") or MATHY.search(ln.text)]
        if not row:
            continue
        row.sort(key=lambda x: x.left)
        gone.update(id(x) for x in row)
        num.text = " ".join(x.text for x in row) + " " + num.text
        num.left, num.bottom, num.top = row[0].left, min(num.bottom, *(x.bottom for x in row)), max(num.top, *(x.top for x in row))
        num.size, num.bold = row[0].size, row[0].bold
    return [ln for ln in lines if id(ln) not in gone]


def _columns(lines: list[Line], width: float) -> None:
    mid = width / 2
    for ln in lines:
        if ln.left < mid - 10 and ln.right > mid + 10:
            ln.col = "full"
        else:
            ln.col = "left" if (ln.left + ln.right) / 2 < mid else "right"


def _reading_order(lines: list[Line]) -> list[Line]:
    """Top to bottom; two-column stretches read left column then right, broken by full-width lines."""
    out: list[Line] = []
    left: list[Line] = []
    right: list[Line] = []
    for ln in sorted(lines, key=lambda x: (-x.top, x.left)):
        if ln.col == "full":
            out += left + right + [ln]
            left, right = [], []
        else:
            (left if ln.col == "left" else right).append(ln)
    return out + left + right


def _drop_running_heads(pages: list[list[Line]], heights: list[float]) -> None:
    """Remove page numbers and running headers/footers (same text near the edge on many pages)."""
    def key(ln: Line) -> str:
        return "".join(c for c in ln.text.lower() if not c.isdigit()).strip()

    def edge(ln: Line, h: float) -> bool:
        return ln.top > h - 55 or ln.bottom < 55

    counts: dict[str, int] = {}
    for lines, h in zip(pages, heights):
        for k in {key(ln) for ln in lines if edge(ln, h)}:
            counts[k] = counts.get(k, 0) + 1
    many = max(2, len(pages) // 2)
    for lines, h in zip(pages, heights):
        lines[:] = [ln for ln in lines if not (edge(ln, h) and (not key(ln).strip(" .-–—|") or (counts.get(key(ln), 0) >= many and len(pages) > 1)))]


def _graphics(page: Any, width: float, height: float) -> list[tuple[float, float, float, float]]:
    out = []
    for obj in page.get_objects(max_depth=1):
        if obj.type not in GRAPHIC_TYPES:
            continue
        l, b, r, t = obj.get_bounds()
        w, h = r - l, t - b
        if w <= 0 and h <= 0:
            continue
        if w * h > 0.85 * width * height:          # page background
            continue
        if h < 1.5 and w > 0.6 * width:            # horizontal rules
            continue
        out.append((l, b, r, t))
    return out


def _union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _near(a: tuple[float, ...], b: tuple[float, ...], gap: float) -> bool:
    return not (a[2] + gap < b[0] or b[2] + gap < a[0] or a[3] + gap < b[1] or b[3] + gap < a[1])


def _figure_box(cap: Line, captions: list[Line], graphics: list[tuple[float, float, float, float]],
                lines: list[Line], width: float, height: float, body_size: float) -> tuple[tuple[float, float, float, float] | None, str]:
    """The figure's crop box next to its caption, and how it was found (``graphics`` | ``text-gap`` | ``none``)."""
    mid = width / 2
    xr = (0.0, width) if cap.col == "full" else ((0.0, mid + 5) if cap.col == "left" else (mid - 5, width))

    def in_cols(g: tuple[float, ...]) -> bool:
        cx = (g[0] + g[2]) / 2
        return xr[0] <= cx <= xr[1]

    above = [c.bottom for c in captions if c is not cap and c.bottom > cap.top and (c.col == cap.col or "full" in (c.col, cap.col))]
    ceiling = min(above) if above else height
    below = [c.top for c in captions if c is not cap and c.top < cap.bottom and (c.col == cap.col or "full" in (c.col, cap.col))]
    floor = max(below) if below else 0.0
    for side in ("above", "below"):
        if side == "above":
            pool = [g for g in graphics if in_cols(g) and g[1] >= cap.top - 3 and g[3] <= ceiling + 3]
            seed = [g for g in pool if g[1] - cap.top <= 0.2 * height]
        else:
            pool = [g for g in graphics if in_cols(g) and g[3] <= cap.bottom + 3 and g[1] >= floor - 3]
            seed = [g for g in pool if cap.bottom - g[3] <= 0.2 * height]
        if not seed:
            continue
        cluster = list(seed)
        box = _union(cluster)
        grown = True
        while grown:
            grown = False
            for g in pool:
                if g not in cluster and _near(box, g, 15):
                    cluster.append(g)
                    box = _union(cluster)
                    grown = True
        # axis labels, tick labels and panel letters around the graphics
        short = [ln for ln in lines if ln is not cap and len(ln.text) <= 40 and not CAPTION.match(ln.text)
                 and (ln.bottom >= cap.top if side == "above" else ln.top <= cap.bottom) and not _prose(ln, body_size)]
        taken: set[int] = set()
        grown = True
        while grown:
            grown = False
            for ln in short:
                lb = (ln.left, ln.bottom, ln.right, ln.top)
                if id(ln) not in taken and _near(box, lb, 10):
                    taken.add(id(ln))
                    box = _union([box, lb])
                    grown = True
        l, b, r, t = box
        b, t = max(0.0, b - 3), min(height, t + 3)
        if side == "above":
            b = max(b, cap.top + 1)      # never into the caption
        else:
            t = min(t, cap.bottom - 1)
        # trim a sentence of running text (a table caption, a paragraph end) caught at the far edge
        for ln in lines:
            if ln is cap or len(ln.text) <= 40 or ln.right < l or ln.left > r:
                continue
            mid_y = (ln.top + ln.bottom) / 2
            if side == "above" and t - 0.15 * (t - b) <= mid_y <= t:
                t = min(t, ln.bottom - 1)
            elif side == "below" and b <= mid_y <= b + 0.15 * (t - b):
                b = max(b, ln.top + 1)
        return (max(0.0, l - 3), b, min(width, r + 3), t), "graphics"
    # no graphics objects (figure drawn as text, or nested too deep): crop the gap between
    # the caption and the nearest body paragraph above it
    body_above = [ln.bottom for ln in lines if ln.bottom > cap.top and ln.top <= ceiling and _is_body(ln, body_size, xr)]
    top = min([ceiling, *body_above]) if body_above else min(ceiling, height - 50)
    if top - cap.top < 40:
        return None, "none"
    return (xr[0], cap.top + 2, xr[1], top - 2), "text-gap"


def _prose(ln: Line, body_size: float) -> bool:
    """A fragment of running text (body-size font, several words, ends like a sentence part)."""
    return abs(ln.size - body_size) <= 0.06 * body_size and len(ln.text.split()) >= 3 and ln.text[-1:] in ".,;:"


def _is_body(ln: Line, body_size: float, xr: tuple[float, float]) -> bool:
    return (abs(ln.size - body_size) <= 0.06 * body_size and not ln.bold and len(ln.text) >= 40
            and (ln.right - ln.left) >= 0.35 * (xr[1] - xr[0]))


def _equation_box(ln: Line, lines: list[Line], width: float, height: float) -> tuple[float, float, float, float]:
    """The equation's row plus up to one line height above and below (fractions, sums), stopping at text lines."""
    pad = 1.0 * ln.height
    over = [x for x in lines if x is not ln and x.right > ln.left and x.left < ln.right]
    above = [x.bottom for x in over if x.bottom >= ln.top - 1 and len(x.text) > 25]
    below = [x.top for x in over if x.top <= ln.bottom + 1 and len(x.text) > 25]
    top = min([ln.top + pad, *[a - 0.5 for a in above if a - 0.5 > ln.top]])
    bottom = max([ln.bottom - pad, *[b + 0.5 for b in below if b + 0.5 < ln.bottom]])
    return (max(0.0, ln.left - 6), max(0.0, bottom), min(width, ln.right + 6), min(height, top))


def _panel_boxes(labels: list[Line], fig: tuple[float, float, float, float]) -> dict[str, tuple[float, float, float, float]]:
    rows: list[list[Line]] = []
    for ln in sorted(labels, key=lambda x: -x.top):
        if rows and abs(rows[-1][0].top - ln.top) <= 0.6 * ln.height + 8:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    out: dict[str, tuple[float, float, float, float]] = {}
    for ri, row in enumerate(rows):
        row.sort(key=lambda x: x.left)
        top = min(fig[3], max(x.top for x in row) + 3)
        bottom = (max(x.top for x in rows[ri + 1]) + 3) if ri + 1 < len(rows) else fig[1]
        for ci, ln in enumerate(row):
            left = max(fig[0], ln.left - 3)
            right = (row[ci + 1].left - 3) if ci + 1 < len(row) else fig[2]
            letter = PANEL_LABEL.match(ln.text).group(1).lower()
            if letter not in out and right > left and top > bottom:
                out[letter] = (left, bottom, right, top)
    return out


def _render(page: Any, box: tuple[float, float, float, float], width: float, height: float, dpi: int, out: Path) -> None:
    l, b, r, t = box
    bitmap = page.render(scale=dpi / 72.0, crop=(l, b, width - r, height - t))
    out.parent.mkdir(parents=True, exist_ok=True)
    bitmap.to_pil().save(out, format="PNG")


def extract(pdf: Path, out: Path, options: dict[str, Any]) -> Extraction:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    dpi = int(options.get("dpi") or 200)
    doc = pdfium.PdfDocument(str(pdf))
    ex = Extraction(pages=len(doc))
    pages, sizes = [], []
    for pno in range(len(doc)):
        page = doc[pno]
        w, h = page.get_size()
        lines = _page_lines(page, pno + 1, raw)
        _columns(lines, w)
        pages.append(lines)
        sizes.append((w, h))
    _drop_running_heads(pages, [h for _, h in sizes])
    if not any(pages):
        ex.warnings.append("no text layer (scanned PDF?) — use the command backend with an OCR tool")
        return ex
    weighted = [round(ln.size, 1) for lines in pages for ln in lines for _ in range(max(1, len(ln.text) // 10))]
    body = statistics.mode(weighted) if weighted else 10.0

    # figures first, so their inner labels stay out of the section text
    fig_ids: set[str] = set()
    inner: set[int] = set()
    caption_starts: dict[int, dict[str, Any]] = {}
    found: dict[str, list[tuple[int, int, Line, Any, str]]] = {}
    for pno, lines in enumerate(pages):
        w, h = sizes[pno]
        caps = [ln for ln in lines if CAPTION.match(ln.text)]
        if not caps:
            continue
        graphics = _graphics(doc[pno], w, h)
        for k, cap in enumerate(caps):
            box, how = _figure_box(cap, caps, graphics, lines, w, h, body)
            found.setdefault(CAPTION.match(cap.text).group(1), []).append((pno, k, cap, box, how))
    rank = {"graphics": 0, "text-gap": 1, "none": 2}
    chosen = []
    for num, cands in found.items():
        # a body sentence can start with "Fig. 4." too: prefer the one with graphics, then the smaller font
        best = min(cands, key=lambda c: (rank[c[4]], round(c[2].size, 1), c[0], c[1]))
        chosen.append(best)
        for c in cands:
            if c is not best:
                ex.warnings.append(f"page {c[0] + 1}: another line starting like the Fig. {num} caption, skipped: {c[2].text[:60]!r}")
    for pno, _, cap, box, how in sorted(chosen, key=lambda c: (c[0], c[1])):
        lines = pages[pno]
        page = doc[pno]
        w, h = sizes[pno]
        caps = [c for p2, _, c, _, _ in chosen if p2 == pno]
        num = CAPTION.match(cap.text).group(1)
        fid = unique_id(f"fig.{num}", fig_ids)
        if how == "text-gap":
            ex.warnings.append(f"Fig. {num} (page {pno + 1}): no graphics objects; cropped the gap above the caption — check the crop")
        fig: dict[str, Any] = {"id": fid, "number": num, "page": pno + 1, "caption": cap.text, "image": None, "bbox": None, "panels": []}
        if box is None:
            ex.warnings.append(f"Fig. {num} (page {pno + 1}): no graphics found next to the caption — crop it by hand")
        else:
            fig["bbox"] = [round(v, 1) for v in box]
            fig["image"] = f"figures/fig-{slug(num)}.png"
            _render(page, box, w, h, dpi, out / fig["image"])
            members = [ln for ln in lines if ln.inside(box) and all(ln is not c for c in caps)]
            inner.update(id(ln) for ln in members)
            labels = [ln for ln in members if PANEL_LABEL.match(ln.text)]
            if len({PANEL_LABEL.match(ln.text).group(1).lower() for ln in labels}) >= 2:
                for letter, pbox in sorted(_panel_boxes(labels, box).items()):
                    img = f"figures/fig-{slug(num)}{letter}.png"
                    _render(page, pbox, w, h, dpi, out / img)
                    fig["panels"].append({"id": f"{fid}{letter}", "label": letter, "caption": None,
                                          "image": img, "bbox": [round(v, 1) for v in pbox]})
        ex.figures.append(fig)
        caption_starts[id(cap)] = fig

    # one pass in reading order: sections, paragraphs, equations, caption continuations
    sec: dict[str, Any] = {"id": "front", "number": None, "title": "front matter", "label": "front matter", "page": 1, "paras": [[]]}
    sections = [sec]
    sec_ids = {"front"}
    eq_ids: set[str] = set()
    roman: str | None = None
    open_cap: tuple[dict[str, Any], Line] | None = None
    prev: Line | None = None
    for pno, lines in enumerate(pages):
        for ln in _reading_order(lines):
            if id(ln) in inner:
                continue
            if id(ln) in caption_starts:
                open_cap = (caption_starts[id(ln)], ln)
                prev = None
                continue
            if open_cap is not None:
                fig, last = open_cap
                if (ln.page == last.page and ln.col == last.col and 0 <= last.bottom - ln.top <= 1.5 * ln.height
                        and ln.size <= last.size * 1.05 and not heading_parts(ln.text, ln.size >= body * 1.08 or ln.bold)):
                    fig["caption"] += " " + ln.text
                    open_cap = (fig, ln)
                    continue
                open_cap = None
            styled = ln.size >= body * 1.08 or ln.bold or (ln.text.isupper() and len(ln.text) > 3)
            hp = heading_parts(ln.text, styled)
            if hp:
                number, title, kind = hp
                if kind == "roman":
                    roman = number
                elif kind == "letter":
                    if roman is None:
                        hp = None
                    else:
                        number = f"{roman}.{number}"
                if hp:
                    sid = unique_id(f"s{number}" if number else f"s.{slug(title)}", sec_ids)
                    sec = {"id": sid, "number": number, "title": title, "label": section_label(number, title), "page": ln.page, "paras": [[]]}
                    sections.append(sec)
                    prev = None
                    continue
            m = EQ_NUMBER.match(ln.text)
            if m and MATHY.search(m.group("body")) and len(m.group("body")) < 200:
                num = m.group("num")
                eid = unique_id(f"eq.{num}", eq_ids)
                w, h = sizes[pno]
                box = _equation_box(ln, lines, w, h)
                img = f"equations/eq-{slug(eid[3:])}.png"
                _render(doc[pno], box, w, h, dpi, out / img)
                ex.equations.append({"id": eid, "number": num, "page": ln.page, "section": sec["id"], "text": m.group("body"),
                                     "latex": None, "image": img, "bbox": [round(v, 1) for v in box]})
                sec["paras"].append([f"[Eq. ({num}): {m.group('body')}]"])
                sec["paras"].append([])
                prev = None
                continue
            if prev is not None and prev.page == ln.page and prev.col == ln.col and prev.bottom - ln.top > 1.2 * ln.height:
                sec["paras"].append([])
            para = sec["paras"][-1]
            if para and para[-1].endswith("-") and ln.text[:1].islower():
                para[-1] = para[-1][:-1] + ln.text
            else:
                para.append(ln.text)
            prev = ln

    for f in ex.figures:
        texts = caption_panels(f["caption"])
        for p in f["panels"]:
            p["caption"] = texts.get(p["label"])
    for s in sections:
        paras = [" ".join(p) for p in s.pop("paras") if p]
        s["text"] = "\n\n".join(paras)
        if s["id"] == "front" and not s["text"]:
            continue
        ex.sections.append(s)
    return ex
