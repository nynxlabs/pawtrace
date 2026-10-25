"""Paper rights: the paper's license, which copy we work from, and the rules that follow.

Set at intake, in ``spec.yaml``, before anyone (person or agent) works from the paper::

    rights:
      license: unknown          # CC-BY-4.0, CC-BY-NC-4.0, CC0-1.0, public-domain, arXiv-nonexclusive,
                                # publisher-copyright, unknown, or the license's own name
      license_url: ""
      copy_url: ""              # the copy we work from (paper.version says which version)
      open_version: ""          # an open-access or arXiv copy, or "none found"
      access: unknown           # open-access | subscription | personal-copy | author-provided | under-review | unknown
      access_terms: ""          # text/data-mining and AI clauses of the access license, in brief, with a link
      rules:
        redistribute: false     # PDF, full text, extracted text/equations, figure crops may be committed or published
        quotes_in_spec: true    # short quotes with section citations in spec.yaml
        ai_tools: allowed       # allowed | local-only | never
      permissions: []           # [{from, date, grants: [redistribute | ai-tools | figures | ...], ref}]

Lint enforces it: the block exists; ``redistribute`` is only true when the license or a
recorded permission allows it; a manuscript under review never goes to AI tools; and
restricted content (the PDF, anything under ``source/``) is never tracked by git or
left where ``git add`` would pick it up.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

ACCESS = ("open-access", "subscription", "personal-copy", "author-provided", "under-review", "unknown")
AI_TOOLS = ("allowed", "local-only", "never")
SOURCE_DIR = "source"   # per-paper folder for the PDF and everything extracted from it

# licenses that allow redistribution (prefix match, case-insensitive); NC/ND variants allow it with conditions
_OPEN = ("cc-by", "cc0", "public-domain")
_CONDITIONAL = ("-nc", "-nd")


def rights_of(spec: dict[str, Any]) -> dict[str, Any]:
    r = spec.get("rights")
    return r if isinstance(r, dict) else {}


def rules_of(spec: dict[str, Any]) -> dict[str, Any]:
    r = rights_of(spec).get("rules")
    return r if isinstance(r, dict) else {}


def license_allows_redistribution(license_name: Any) -> bool:
    lic = str(license_name or "").strip().lower().replace(" ", "-")
    return lic.startswith(_OPEN)


def permission_grants(rights: dict[str, Any], what: str) -> bool:
    for p in rights.get("permissions") or []:
        if isinstance(p, dict) and what in [str(g) for g in (p.get("grants") or [])]:
            return True
    return False


def may_redistribute(spec: dict[str, Any]) -> bool:
    """True only when the rules say so *and* the license or a recorded permission backs it."""
    rights = rights_of(spec)
    if rules_of(spec).get("redistribute") is not True:
        return False
    return license_allows_redistribution(rights.get("license")) or permission_grants(rights, "redistribute")


def ai_tools(spec: dict[str, Any]) -> str:
    """The ``ai_tools`` rule; ``never`` when unset or invalid (fail closed)."""
    v = rules_of(spec).get("ai_tools")
    return v if v in AI_TOOLS else "never"


def lint_rights(spec: dict[str, Any], add) -> None:
    """Check the ``rights:`` block; ``add(level, message)`` collects problems."""
    rights = spec.get("rights")
    if not rights:
        add("error", "no rights: record the paper's license, access and rules at intake, before anyone works from the paper")
        return
    if not isinstance(rights, dict):
        add("error", "rights must be a mapping (license, access, rules, ...)")
        return
    lic = str(rights.get("license") or "").strip()
    if not lic or lic.lower() == "unknown":
        add("warn", "rights.license is unknown — check the journal page or arXiv listing; until then nothing from the paper is committed or published")
    access = rights.get("access")
    if access not in ACCESS:
        add("error", f"rights.access must be one of {', '.join(ACCESS)}")
    elif access == "unknown":
        add("warn", "rights.access is unknown — say how the copy was obtained (open access, subscription, personal copy, ...)")
    if access != "open-access" and not str(rights.get("open_version") or "").strip():
        add("warn", 'rights.open_version is empty — look for an open-access or arXiv version (or write "none found")')

    rules = rights.get("rules")
    if not isinstance(rules, dict):
        add("error", "rights.rules is missing (redistribute, quotes_in_spec, ai_tools)")
        rules = {}
    else:
        for key in ("redistribute", "quotes_in_spec"):
            if key not in rules:
                add("error", f"rights.rules.{key} is missing (true or false)")
            elif not isinstance(rules[key], bool):
                add("error", f"rights.rules.{key} must be true or false")
        if rules.get("ai_tools") not in AI_TOOLS:
            add("error", f"rights.rules.ai_tools must be one of {', '.join(AI_TOOLS)}")
    if access == "under-review" and rules.get("ai_tools") != "never":
        add("error", "the paper is under review: rights.rules.ai_tools must be never (manuscripts under review never go to AI tools)")
    if rules.get("redistribute") is True:
        if not (license_allows_redistribution(lic) or permission_grants(rights, "redistribute")):
            add("error", f"rights.rules.redistribute is true but license {lic or 'unknown'!r} doesn't allow it and no permission grants it")
        elif lic.lower().replace(" ", "-").startswith(_OPEN) and any(c in lic.lower() for c in _CONDITIONAL):
            add("warn", f"rights.license {lic} has NC/ND conditions — check them before publishing figure crops or extracted text")

    perms = rights.get("permissions") or []
    if not isinstance(perms, list):
        add("error", "rights.permissions must be a list")
        perms = []
    for n, p in enumerate(perms):
        if not isinstance(p, dict) or not all(p.get(k) for k in ("from", "date", "grants")):
            add("warn", f"rights.permissions[{n}] needs from, date and grants (and a ref to the email or letter)")


def lint_foundation_rights(item: dict[str, Any], add) -> None:
    """Foundation items may carry the same ``rights:`` shape for the foundation's own paper."""
    r = item.get("rights")
    if r is None:
        return
    iid = item.get("id")
    if not isinstance(r, dict):
        add("error", f"item {iid}: rights must be a mapping")
        return
    if not str(r.get("license") or "").strip() or str(r.get("license")).lower() == "unknown":
        add("warn", f"item {iid}: rights.license is unknown")
    if r.get("access") is not None and r.get("access") not in ACCESS:
        add("error", f"item {iid}: rights.access must be one of {', '.join(ACCESS)}")
    ai = (r.get("rules") or {}).get("ai_tools") if isinstance(r.get("rules"), dict) else None
    if ai is not None and ai not in AI_TOOLS:
        add("error", f"item {iid}: rights.rules.ai_tools must be one of {', '.join(AI_TOOLS)}")


# ---------------------------------------------------------------------------
# restricted content vs git
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def git_toplevel(path: Path) -> Path | None:
    d = path if path.is_dir() else path.parent
    while not d.exists():
        d = d.parent
    r = _git(d, "rev-parse", "--show-toplevel")
    return Path(r.stdout.strip()) if r.returncode == 0 else None


def dir_ignored(directory: Path) -> bool | None:
    """Whether git ignores files written into ``directory``; None outside a git work tree."""
    top = git_toplevel(directory)
    if top is None:
        return None
    probe = (directory / ".pawtrace-probe").resolve()
    if not probe.is_relative_to(top.resolve()):
        return None
    return _git(top, "check-ignore", "-q", "--no-index", str(probe.relative_to(top.resolve()))).returncode == 0


def _restricted(rel_paper: str, rel: str) -> bool:
    sub = rel[len(rel_paper):].lstrip("/") if rel.startswith(rel_paper) else rel
    return sub.startswith(SOURCE_DIR + "/") or sub.lower().endswith(".pdf")


def tracked_problems(paper_id: str, paper_dir: Path, spec: dict[str, Any]) -> list[dict[str, str]]:
    """Restricted paper content that git tracks, or that ``git add`` would pick up.

    Restricted = the paper's ``source/`` folder and any PDF under the paper folder, unless
    the license (or a permission) allows redistribution and ``rules.redistribute`` is true.
    """
    if may_redistribute(spec) or not paper_dir.is_dir():
        return []
    top = git_toplevel(paper_dir)
    if top is None:
        return []
    rel_paper = str(paper_dir.resolve().relative_to(top.resolve())) if paper_dir.resolve().is_relative_to(top.resolve()) else None
    if rel_paper is None:
        return []
    out: list[dict[str, str]] = []
    why = "the paper's license doesn't allow redistribution (rights.rules.redistribute)"
    tracked = _git(top, "ls-files", "-z", "--", rel_paper).stdout.split("\0")
    for rel in sorted(t for t in tracked if t and _restricted(rel_paper, t)):
        out.append({"level": "error", "paper": paper_id,
                    "message": f"restricted paper content is committed: {rel} — {why}; `git rm --cached` it and keep it under {SOURCE_DIR}/"})
    loose = _git(top, "ls-files", "-z", "--others", "--exclude-standard", "--", rel_paper).stdout.split("\0")
    for rel in sorted(t for t in loose if t and _restricted(rel_paper, t)):
        out.append({"level": "error", "paper": paper_id,
                    "message": f"{rel} is not gitignored and would be committed — {why}; gitignore {rel_paper}/{SOURCE_DIR}/ and PDFs"})
    return out


def render_lines(spec: dict[str, Any]) -> list[str]:
    """The ``## paper rights`` section of the generated SPEC.md."""
    r = rights_of(spec)
    if not r:
        return []
    rules = rules_of(spec)
    lines = ["## paper rights", ""]
    lines.append(f"- license: {r.get('license') or 'unknown'}" + (f" — {r['license_url']}" if r.get("license_url") else ""))
    lines.append(f"- access: {r.get('access') or 'unknown'}" + (f" — {r['copy_url']}" if r.get("copy_url") else ""))
    if r.get("open_version"):
        lines.append(f"- open version: {r['open_version']}")
    if r.get("access_terms"):
        lines.append(f"- access terms: {r['access_terms']}")
    if rules:
        lines.append(
            f"- rules: redistribute {'yes' if rules.get('redistribute') is True else 'no'}; "
            f"quotes in spec {'yes' if rules.get('quotes_in_spec') is not False else 'no'}; "
            f"AI tools {rules.get('ai_tools', '?')}"
        )
    for p in r.get("permissions") or []:
        if isinstance(p, dict):
            lines.append(f"- permission from {p.get('from', '?')} ({p.get('date', '?')}): {', '.join(str(g) for g in p.get('grants') or [])}")
    lines.append("")
    return lines
