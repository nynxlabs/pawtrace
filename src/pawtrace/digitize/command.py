"""``pawtrace digitize``: set up a session from the command line and serve it until Done / Ctrl+C."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

from .. import rights as rts
from ..panel import Panel, PanelError
from ..project import Project, ProjectError
from ..spec import Paper
from .core import DigitizeError, load_document, sha256_file
from .server import DigitizeServer, Figure, Session, git_info, load_original

AI_NOTE = ("{paper}: rights.rules.ai_tools is never. This tool makes no model calls and uploads nothing; "
           "colour snap is plain image matching and stays off unless you turn it on for this figure "
           "(the saved file records it).")


def _project(args: Any) -> Project | None:
    try:
        return Project.find(args.project) if args.project else Project.find()
    except ProjectError:
        if args.review or args.panel:
            raise
        return None


def _crop(text: str | None) -> list[float] | None:
    if not text:
        return None
    parts = [float(v) for v in text.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise DigitizeError("--crop needs x0,y0,x1,y1 (PDF points from the page's top-left)")
    return parts


def _figure_spec(panel: Panel | None, ref_name: str | None) -> dict[str, Any]:
    """``figure:`` from the reference block, else the panel (``{image, page, crop, dpi}``)."""
    if panel is None:
        return {}
    ref = ((panel.cfg.get("references") or {}).get(ref_name) or {}) if ref_name else {}
    fig = ref.get("figure") or panel.cfg.get("figure") or {}
    return fig if isinstance(fig, dict) else {"image": fig}


def _paper_note(proj: Project | None, paper: str | None) -> str | None:
    if not proj or not paper:
        return None
    spec = Paper(paper, proj.paper_dir(paper)).spec
    if spec and rts.ai_tools(spec) == "never":
        return AI_NOTE.format(paper=paper)
    return None


def build_session(args: Any) -> Session:
    proj = _project(args)
    panel: Panel | None = None
    ref_name: str | None = None
    if args.review:
        panel_ref, ref_name = args.review
        panel = Panel.load(proj, panel_ref)
    elif args.panel:
        panel = Panel.load(proj, args.panel)
        ref_name = args.reference

    fig = _figure_spec(panel, ref_name)
    image = args.image or fig.get("image")
    if not image:
        raise DigitizeError("which figure? pass the image (or a PDF with --page), or set figure: in the panel YAML")
    image_path = proj.path(image) if (proj and not args.image) else Path(os.path.expanduser(image))
    page = args.page if args.page is not None else fig.get("page")
    crop = _crop(args.crop) if args.crop else fig.get("crop")
    dpi = args.dpi or int(fig.get("dpi") or 200)
    figure = Figure.load(image_path, page=page, crop=crop, dpi=dpi)

    paper = panel.paper if panel else args.paper
    note = _paper_note(proj, paper)

    if args.review:
        assert panel is not None and ref_name is not None and proj is not None
        refs = panel.cfg.get("references") or {}
        if ref_name not in refs:
            raise PanelError(f"{panel.id} has no reference {ref_name!r} (have: {', '.join(refs) or 'none'})")
        spec = dict(refs[ref_name] or {})
        src = proj.path(spec["path"])
        if not src.is_file():
            raise FileNotFoundError(f"reference {ref_name}: {src} not found")
        fmt = spec.get("format")
        options = spec.get("options") or {}
        if getattr(args, "options", None):
            try:
                options = json.loads(args.options)
            except json.JSONDecodeError as exc:
                raise DigitizeError(f"--options is not JSON: {exc}") from None
            if not isinstance(options, dict):
                raise DigitizeError("--options must be a JSON object")
        original, doc = load_original(src, fmt, options)
        fmt = fmt or "curves-json"
        if args.out:
            out = Path(args.out)
        elif fmt == "curves-json" and doc is not None:
            out = src  # in place; the old version goes into meta.history (and stays in git)
        else:
            out = src.with_suffix(".json")
            if out.exists() and out.resolve() != src.resolve():
                raise DigitizeError(f"{out} already exists; pass --out for the reviewed file")
        info = {"path": _rel(src, proj.root), "format": fmt, "sha256": sha256_file(src), "git": git_info(src),
                "series": spec.get("series"), "made_by": _made_by(doc)}
        return Session(
            figure=figure, out=out, mode="review", by=args.by, note=note,
            title=f"review {panel.id} · {ref_name}", original=original, original_info=info,
            previous=doc if out.resolve() == src.resolve() else None, focus=spec.get("series"), root=proj.root,
        )

    if not args.out:
        raise DigitizeError("pass --out FILE for a new digitization (curves-json)")
    out = Path(args.out)
    if out.exists() and not args.force:
        hint = " (it's a digitization: open it with --review <panel> <reference>)" if load_document(out) else ""
        raise DigitizeError(f"{out} already exists{hint}; --force to replace it")
    title = f"{panel.id} · {ref_name}" if panel and ref_name else (panel.id if panel else image_path.name)
    return Session(figure=figure, out=out, mode="new", by=args.by, note=note, title=f"digitize {title}")


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return path.name


def _made_by(doc: dict[str, Any] | None) -> Any:
    meta = (doc or {}).get("meta") or {}
    dig = meta.get("digitization") or {}
    return {k: dig[k] for k in ("tool", "method", "by", "date") if k in dig} or None


def run(args: Any, emit) -> int:
    sess = build_session(args)
    if sess.note:
        print(f"note: {sess.note}", file=sys.stderr)
    server = DigitizeServer(sess, port=args.port or 0)
    url = server.url
    print(f"pawtrace digitize: {url}", flush=True)
    print(f"  saves {sess.out}  ·  Done in the page or Ctrl+C here to stop", file=sys.stderr, flush=True)
    if args.open_cmd:
        port = str(server.server_address[1])
        argv = [a.replace("{url}", url).replace("{port}", port) for a in shlex.split(args.open_cmd)]
        try:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL)
        except OSError as exc:
            print(f"pawtrace digitize: --open-cmd failed: {exc}", file=sys.stderr)
    elif args.browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    result = {"out": str(sess.out), "mode": sess.mode, "saves": sess.saves}
    last = sess.saves[-1] if sess.saves else None
    text = "no save" if not last else f"saved {last['path']} ({', '.join(f'{k} {v}' for k, v in last['series'].items())})" + (
        ", reviewed" if last.get("reviewed") else "")
    if args.review and last and Path(last["path"]).suffix == ".json" and sess.original_info.get("format") != "curves-json":
        text += f"\nupdate the panel reference: path: {_rel(Path(last['path']), sess.root or Path.cwd())}, format: curves-json (drop options:)"
    emit(result, text)
    return 0
