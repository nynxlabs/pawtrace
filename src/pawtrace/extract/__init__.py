"""The ``extract`` module: text by section, equations and figures from the paper PDF.

Everything goes into the paper's local ``source/`` folder, which carries its own
``.gitignore`` (``*``) so the PDF and its extracted content never reach git unless the
paper's rights allow it::

    papers/<paper>/source/
      .gitignore          # "*" — local only
      paper.pdf           # the copy we work from (rights.copy_url)
      manifest.json       # extraction id, backend + version + options, PDF sha256, file hashes
      text.md             # readable text by section (generated)
      sections.json  equations.json  figures.json
      equations/eq-3.png  figures/fig-4.png  figures/fig-4a.png
      candidates.yaml     # candidate spec items (status: candidate) for `pawtrace extract seed`

The extraction id hashes the PDF, backend, options and every output file, so spec items
(``extracted.extraction``) and run records can cite exactly which extraction they used.

Backends: ``pdfium`` (built in, offline, CPU — ``uv sync --extra extract``) and
``command`` (any PDF → markdown converter you run; see ``command.py``). Extraction never
marks anything ``stated``: seeded items start as ``status: candidate``.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import yaml

from .. import __version__
from .. import rights as rts
from ..spec import load_yaml
from . import command as cmd_backend
from . import pdfium as pdfium_backend
from .common import Extraction, candidates

BACKENDS = ("pdfium", "command")
CANDIDATE_KINDS = ("equation", "panel", "parameter")
GENERATED = ("text.md", "sections.json", "equations.json", "figures.json", "candidates.yaml", "manifest.json",
             "figures", "equations", "backend")
DEFAULTS: dict[str, Any] = {"backend": "pdfium", "dpi": 200, "command": [], "formula_command": [], "timeout_s": 1800}
GITIGNORE = "# pawtrace: the paper and everything extracted from it stay local (see rights: in spec.yaml)\n*\n"


class ExtractError(RuntimeError):
    pass


def source_dir(paper_dir: Path) -> Path:
    return paper_dir / rts.SOURCE_DIR


def options(cfg: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    opts = dict(DEFAULTS)
    opts.update({k: v for k, v in (cfg.get("extract") or {}).items() if v is not None})
    opts.update({k: v for k, v in overrides.items() if v is not None})
    if opts["backend"] not in BACKENDS:
        raise ExtractError(f"unknown extract backend {opts['backend']!r} (known: {', '.join(BACKENDS)})")
    return opts


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_rules(spec: dict[str, Any], out: Path, default_out: Path, opts: dict[str, Any], confirm_no_ai: bool) -> list[str]:
    """Refuse what the paper's rights don't allow; return notes for the user."""
    if not rts.rights_of(spec):
        raise ExtractError("no rights: in spec.yaml — record the paper's license, access and rules first (`pawtrace playbook intake`, step 2)")
    notes: list[str] = []
    runs_command = opts["backend"] == "command" or bool(opts.get("formula_command"))
    ai = rts.ai_tools(spec)
    if runs_command and ai == "never" and not confirm_no_ai:
        raise ExtractError(
            "rights.rules.ai_tools is never, and command backends usually run ML models — use --backend pdfium "
            "without formula_command, or pass --confirm-no-ai if your command uses no AI tool"
        )
    if runs_command and ai == "local-only":
        notes.append("rights.rules.ai_tools is local-only: the extract command must run on this machine, not a hosted service")
    if out.resolve() != default_out.resolve() and not rts.may_redistribute(spec):
        ignored = rts.dir_ignored(out)
        if ignored is False:
            raise ExtractError(
                f"refusing to write the paper's content to {out}: git doesn't ignore it and the paper's rights don't allow "
                f"redistribution — gitignore it, or use the default {rts.SOURCE_DIR}/ folder"
            )
    return notes


def run(paper_dir: Path, paper_id: str, cfg: dict[str, Any], pdf: str | Path | None = None, *,
        out: str | Path | None = None, confirm_no_ai: bool = False, **overrides: Any) -> dict[str, Any]:
    """Extract ``pdf`` (or the copy already in ``source/``) and write the outputs; returns the manifest."""
    spec = load_yaml(paper_dir / "spec.yaml")
    if not spec:
        raise ExtractError(f"no spec.yaml for {paper_id} — `pawtrace add-paper {paper_id}` first")
    opts = options(cfg, **overrides)
    default_out = source_dir(paper_dir)
    out = Path(out) if out else default_out
    notes = check_rules(spec, out, default_out, opts, confirm_no_ai)
    local = out / "paper.pdf"
    src = Path(pdf) if pdf else local
    if not src.is_file():
        raise ExtractError(f"no PDF: pass one (`pawtrace extract run {paper_id} <paper.pdf>`)" if not pdf else f"no such file: {src}")
    out.mkdir(parents=True, exist_ok=True)
    if out.resolve() == default_out.resolve() and not (out / ".gitignore").exists():
        (out / ".gitignore").write_text(GITIGNORE)
    if src.resolve() != local.resolve():
        shutil.copyfile(src, local)
    for name in GENERATED:
        p = out / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    if opts["backend"] == "pdfium":
        if not pdfium_backend.available():
            raise ExtractError("the pdfium backend needs pypdfium2 + pillow: uv sync --extra extract")
        ex = pdfium_backend.extract(local, out, opts)
        backend_version = pdfium_backend.version()
    else:
        try:
            ex = cmd_backend.extract(local, out, opts)
        except cmd_backend.CommandError as exc:
            raise ExtractError(str(exc)) from exc
        backend_version = str(_argv0(opts["command"]))
    if ex.pages is None and pdfium_backend.available():
        import pypdfium2

        ex.pages = len(pypdfium2.PdfDocument(str(local)))
    if opts.get("formula_command"):
        _formulas(ex, out, opts)

    pdf_sha = _sha256(local)
    _write_data(ex, out)
    files = {str(p.relative_to(out)): _sha256(p) for p in sorted(out.rglob("*"))
             if p.is_file() and p.name not in (".gitignore", "paper.pdf")}
    used = {k: opts[k] for k in ("dpi", "command", "formula_command") if opts.get(k)}
    ident = hashlib.sha256(json.dumps({"pdf": pdf_sha, "backend": opts["backend"], "version": backend_version,
                                       "options": used, "files": files}, sort_keys=True).encode()).hexdigest()[:12]
    cands = _write_derived(ex, out, paper_id, pdf_sha, ident)
    for name in ("candidates.yaml", "text.md"):
        files[name] = _sha256(out / name)
    rights = rts.rights_of(spec)
    manifest = {
        "pawtrace": __version__,
        "paper": paper_id,
        "extraction": ident,
        "backend": {"name": opts["backend"], "version": backend_version, "options": used},
        "pdf": {"file": "paper.pdf", "sha256": pdf_sha, "bytes": local.stat().st_size, "pages": ex.pages,
                "copied_from": src.name},
        "rights": {"license": rights.get("license"), "redistribute": rts.may_redistribute(spec), "ai_tools": rts.ai_tools(spec)},
        "counts": {"sections": len(ex.sections), "equations": len(ex.equations),
                   "equations_with_latex": sum(1 for e in ex.equations if e.get("latex")),
                   "figures": len(ex.figures), "panels": sum(len(f.get("panels") or []) for f in ex.figures),
                   "candidates": len(cands)},
        "files": dict(sorted(files.items())),
        "warnings": ex.warnings,
        "notes": notes,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    manifest["dir"] = str(out)
    return manifest


def _argv0(template: Any) -> str:
    if isinstance(template, str):
        return template.split()[0] if template.split() else ""
    return str(template[0]) if template else ""


def _formulas(ex: Extraction, out: Path, opts: dict[str, Any]) -> None:
    for e in ex.equations:
        if e.get("latex") or not e.get("image"):
            continue
        try:
            proc = cmd_backend.run_command(opts["formula_command"], float(opts.get("timeout_s") or 1800), image=str(out / e["image"]))
        except cmd_backend.CommandError as exc:
            ex.warnings.append(f"{e['id']}: formula_command failed: {exc}")
            continue
        if proc.returncode == 0 and proc.stdout.strip():
            e["latex"] = proc.stdout.strip()
        else:
            ex.warnings.append(f"{e['id']}: formula_command exited {proc.returncode} with no LaTeX")


def _write_data(ex: Extraction, out: Path) -> None:
    for name, obj in (("sections.json", ex.sections), ("equations.json", ex.equations), ("figures.json", ex.figures)):
        (out / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def _write_derived(ex: Extraction, out: Path, paper_id: str, pdf_sha: str, ident: str) -> list[dict[str, Any]]:
    """Files that name the extraction id (written after it's computed from the data files)."""
    cands = candidates(ex, ident)
    (out / "candidates.yaml").write_text(
        f"# candidate spec items from extraction {ident} — `pawtrace extract seed {paper_id}` adds them to spec.yaml\n"
        "# as status: candidate; read each against the paper and set its real status.\n"
        + yaml.safe_dump({"candidates": cands}, sort_keys=False, allow_unicode=True, width=120)
    )
    (out / "text.md").write_text(render_text(ex, paper_id, pdf_sha, ident))
    return cands


def render_text(ex: Extraction, paper_id: str, pdf_sha: str, ident: str) -> str:
    lines = [f"<!-- extracted by pawtrace from {paper_id}'s paper.pdf (sha256 {pdf_sha[:16]}…), extraction {ident}; "
             "generated, local only — never commit it unless the paper's rights allow -->", ""]
    for s in ex.sections:
        head = f"{s['number']} {s['title']}" if s.get("number") else s["title"]
        lines += [f"## {head}" + (f"  <!-- page {s['page']} -->" if s.get("page") else ""), "", s.get("text") or "", ""]
    if ex.figures:
        lines += ["## figures (extracted)", ""]
        for f in ex.figures:
            lines.append(f"- **{f['id']}** (page {f.get('page') or '?'}): {f.get('caption') or ''}" + (f" — `{f['image']}`" if f.get("image") else ""))
            for p in f.get("panels") or []:
                lines.append(f"  - {p['id']}: {p.get('caption') or ''}" + (f" — `{p['image']}`" if p.get("image") else ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# reading an extraction back; seeding the spec
# ---------------------------------------------------------------------------


def load_manifest(paper_dir: Path) -> dict[str, Any] | None:
    p = source_dir(paper_dir) / "manifest.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def load_json(paper_dir: Path, name: str) -> list[dict[str, Any]]:
    p = source_dir(paper_dir) / name
    return json.loads(p.read_text()) if p.is_file() else []


def stale_items(paper_id: str, paper_dir: Path, spec: dict[str, Any]) -> list[dict[str, str]]:
    """Warn about items seeded from an extraction other than the current one."""
    man = load_manifest(paper_dir)
    out = []
    for it in spec.get("items") or []:
        ext = it.get("extracted") if isinstance(it, dict) else None
        if not isinstance(ext, dict) or not ext.get("extraction"):
            continue
        if man is None:
            if it.get("status") == "candidate":
                out.append({"level": "warn", "paper": paper_id, "message": f"item {it.get('id')}: seeded from extraction {ext['extraction']}, but {rts.SOURCE_DIR}/manifest.json is gone — rerun `pawtrace extract run`"})
        elif ext["extraction"] != man.get("extraction"):
            out.append({"level": "warn", "paper": paper_id, "message": f"item {it.get('id')}: seeded from extraction {ext['extraction']}, current is {man.get('extraction')} — recheck it"})
    return out


def seed(paper_dir: Path, paper_id: str, *, kinds: list[str] | None = None, only: list[str] | None = None,
         dry_run: bool = False) -> dict[str, Any]:
    """Append candidate items to spec.yaml (comments kept) as ``status: candidate``; skip ids already there."""
    spec_path = paper_dir / "spec.yaml"
    spec = load_yaml(spec_path)
    if not spec:
        raise ExtractError(f"no spec.yaml for {paper_id}")
    cpath = source_dir(paper_dir) / "candidates.yaml"
    if not cpath.is_file():
        raise ExtractError(f"no extraction for {paper_id} — `pawtrace extract run {paper_id} <paper.pdf>` first")
    cands = (yaml.safe_load(cpath.read_text()) or {}).get("candidates") or []
    have = {str(i.get("id")) for i in spec.get("items") or [] if isinstance(i, dict)}
    picked, skipped = [], []
    for c in cands:
        if kinds and c.get("kind") not in kinds:
            continue
        if only and c.get("id") not in only:
            continue
        (skipped if c["id"] in have else picked).append(c)
    if only:
        unknown = sorted(set(only) - {c["id"] for c in cands})
        if unknown:
            raise ExtractError(f"no candidates {', '.join(unknown)} (see {cpath})")
    notes = []
    if rts.rules_of(spec).get("quotes_in_spec") is False:
        for c in picked:
            c["text"] = ""
        notes.append("rights.rules.quotes_in_spec is false: seeded items have empty text — paraphrase each one")
    if picked and not dry_run:
        man = load_manifest(paper_dir) or {}
        _append_items(spec_path, picked, man.get("extraction", "?"))
    return {"paper": paper_id, "added": [c["id"] for c in picked], "skipped_existing": [c["id"] for c in skipped],
            "dry_run": dry_run, "notes": notes}


def _append_items(spec_path: Path, items: list[dict[str, Any]], ident: str) -> None:
    lines = spec_path.read_text().splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^items\s*:", ln)), None)
    if start is None:
        lines += ["", "items:"]
        start = len(lines) - 1
    elif re.match(r"^items\s*:\s*\[\s*\]\s*(#.*)?$", lines[start]):
        lines[start] = "items:"
    end = start + 1
    while end < len(lines) and not re.match(r"^[A-Za-z_]", lines[end]):
        end += 1
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].startswith("#")):
        end -= 1
    indent = next((re.match(r"^(\s*)- ", ln).group(1) for ln in lines[start + 1:end] if re.match(r"^\s*- ", ln)), "  ")
    block = ["", f"{indent}# candidates from extraction {ident} — read each against the paper, then set status "
                 "(stated | missing | ambiguous | contradicted)"]
    for it in items:
        dumped = yaml.safe_dump([it], sort_keys=False, allow_unicode=True, width=1000).rstrip().splitlines()
        block += [indent + ln for ln in dumped]
    new = lines[:end] + block + lines[end:]
    text = "\n".join(new) + "\n"
    parsed = yaml.safe_load(text) or {}
    before = len((yaml.safe_load(spec_path.read_text()) or {}).get("items") or [])
    if len(parsed.get("items") or []) != before + len(items):
        raise ExtractError(f"couldn't append to {spec_path} safely (unusual layout) — copy items from candidates.yaml by hand")
    spec_path.write_text(text)
