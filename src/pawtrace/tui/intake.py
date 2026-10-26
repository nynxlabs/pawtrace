"""The add-paper walk-through: questions for the ``rights:`` block, and writing it.

Each question explains what the field is for and suggests an answer; the
answers are checked with the same ``rights`` lint the CLI runs, and written
into the new paper's ``spec.yaml`` in place of the template's rights block
(comments elsewhere in the file are kept).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import rights as rts

LICENSES = (
    "CC-BY-4.0",
    "CC-BY-SA-4.0",
    "CC-BY-NC-4.0",
    "CC-BY-NC-ND-4.0",
    "CC0-1.0",
    "public-domain",
    "arXiv-nonexclusive",
    "publisher-copyright",
    "unknown",
)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass(frozen=True)
class Question:
    key: str            # dotted path in the answers, e.g. "rules.ai_tools"
    label: str
    help: str
    choices: tuple[str, ...] = ()   # a pick list; empty = free text
    optional: bool = False


QUESTIONS: tuple[Question, ...] = (
    Question("paper.id", "paper id", "Short folder name for the paper: lowercase, digits, dots, dashes. Usually the first author, e.g. `smith`."),
    Question("paper.title", "title", "The paper's title, as printed."),
    Question("paper.year", "year", "Publication year (or the preprint's year).", optional=True),
    Question("paper.doi", "DOI", "DOI or arXiv id, if there is one.", optional=True),
    Question(
        "license",
        "license",
        "The license the paper is published under. Check the journal page or the arXiv listing. "
        "Open licenses (CC BY, CC0, public domain) are the only ones that let the PDF, text or figure crops "
        "be committed or published. Pick `unknown` until you've checked: nothing leaves this machine then.",
        LICENSES,
    ),
    Question("license_url", "license URL", "Link to the license text or the journal's license statement.", optional=True),
    Question("copy_url", "copy you work from", "URL of the copy you're reading. Prefer an open-access or arXiv version when one exists.", optional=True),
    Question(
        "open_version",
        "open version",
        'An open-access or arXiv URL for the paper, or "none found" after you looked. Blank means nobody checked yet.',
        optional=True,
    ),
    Question(
        "access",
        "access",
        "How you got the copy. `under-review` means a manuscript under review: it never goes to AI tools.",
        rts.ACCESS,
    ),
    Question(
        "access_terms",
        "access terms",
        "The text/data-mining and AI clauses of the access license (and the AI tool's data terms), with a link. "
        "This is what decides whether the paper may go to AI tools.",
        optional=True,
    ),
    Question(
        "rules.redistribute",
        "redistribute",
        "May the PDF, text, equations or figure crops be committed or published? Only `yes` when the license "
        "allows it (CC BY, CC0, public domain) or an author/publisher permission does.",
        ("no", "yes"),
    ),
    Question(
        "rules.quotes_in_spec",
        "quotes in spec",
        "Short quotes with section citations in spec.yaml. Usually fine for any paper read for research.",
        ("yes", "no"),
    ),
    Question(
        "rules.ai_tools",
        "AI tools",
        "May the paper (and anything extracted from it) go to AI tools? New papers start at `never` (fail closed). "
        "Published papers read for research are usually `allowed`, subject to the access terms above; `local-only` "
        "means only tools running on this machine. Manuscripts under review: always `never`.",
        rts.AI_TOOLS,
    ),
)


def blank_answers() -> dict[str, Any]:
    return {
        "paper.id": "",
        "paper.title": "",
        "paper.year": "",
        "paper.doi": "",
        "license": "unknown",
        "license_url": "",
        "copy_url": "",
        "open_version": "",
        "access": "unknown",
        "access_terms": "",
        "rules.redistribute": "no",
        "rules.quotes_in_spec": "yes",
        "rules.ai_tools": "never",
    }


def rights_block(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "license": a.get("license") or "unknown",
        "license_url": a.get("license_url") or "",
        "copy_url": a.get("copy_url") or "",
        "open_version": a.get("open_version") or "",
        "access": a.get("access") or "unknown",
        "access_terms": a.get("access_terms") or "",
        "rules": {
            "redistribute": a.get("rules.redistribute") == "yes",
            "quotes_in_spec": a.get("rules.quotes_in_spec") != "no",
            "ai_tools": a.get("rules.ai_tools") or "never",
        },
        "permissions": [],
    }


def _scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list) and not v:
        return "[]"
    return json.dumps(str(v), ensure_ascii=False)  # a JSON string is a valid YAML scalar


def render_rights(a: dict[str, Any]) -> str:
    r = rights_block(a)
    lines = ["rights:"]
    for k in ("license", "license_url", "copy_url", "open_version", "access", "access_terms"):
        lines.append(f"  {k}: {_scalar(r[k])}")
    lines.append("  rules:")
    for k, v in r["rules"].items():
        lines.append(f"    {k}: {_scalar(v)}")
    lines.append("  permissions: []   # add {from, date, grants, ref} later")
    return "\n".join(lines)


def problems(a: dict[str, Any]) -> list[tuple[str, str]]:
    """Lint the answers with the CLI's rights check, plus the id."""
    out: list[tuple[str, str]] = []
    pid = str(a.get("paper.id") or "")
    if not ID_RE.match(pid):
        out.append(("error", "paper id: lowercase letters, digits, dots and dashes"))
    if not str(a.get("paper.title") or "").strip():
        out.append(("warn", "title is empty"))
    if a.get("paper.year") and not str(a["paper.year"]).isdigit():
        out.append(("error", "year must be a number"))
    rts.lint_rights({"rights": rights_block(a)}, lambda lvl, msg: out.append((lvl, msg)))
    return out


def suggest(a: dict[str, Any], key: str) -> str | None:
    """A nudge for the current question from what's already answered."""
    lic = str(a.get("license") or "")
    if key == "rules.redistribute":
        if rts.license_allows_redistribution(lic):
            return f"{lic} allows sharing, so `yes` is possible (check NC/ND conditions)."
        return f"{lic or 'unknown'} doesn't allow sharing: keep `no` unless you record a permission in spec.yaml."
    if key == "rules.ai_tools":
        if a.get("access") == "under-review":
            return "under review: must stay `never`."
        if lic.lower() == "unknown" or a.get("access") == "unknown":
            return "license or access still unknown: keep `never` until they're filled in."
        return "published and read for research: `allowed` is usual, if the access terms permit it."
    if key == "open_version" and a.get("access") == "open-access":
        return "open access: the copy URL can double as the open version."
    return None


def _splice_rights(text: str, block: str) -> str:
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^rights\s*:", ln)), None)
    if start is None:
        return text.rstrip("\n") + "\n\n" + block + "\n"
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "\n".join(lines[:start] + block.splitlines() + lines[end:]) + "\n"


def _set_paper_field(text: str, key: str, value: str) -> str:
    return re.sub(rf"(?m)^(  {key}:)[^\n#]*?(\s*#.*)?$", lambda m: f"{m.group(1)} {value}{m.group(2) or ''}", text, count=1)


def write_paper(project_papers_dir: Path, a: dict[str, Any]) -> list[str]:
    """Create ``papers/<id>/`` from the templates (like ``pawtrace add-paper``) and fill in the answers."""
    from ..cli import _paper_skeleton

    pid = str(a["paper.id"])
    d = project_papers_dir / pid
    created = _paper_skeleton(d, pid)
    spec = d / "spec.yaml"
    text = spec.read_text()
    if a.get("paper.title"):
        text = _set_paper_field(text, "title", _scalar(a["paper.title"]))
    if a.get("paper.year"):
        text = _set_paper_field(text, "year", str(int(a["paper.year"])))
    if a.get("paper.doi"):
        text = _set_paper_field(text, "doi", _scalar(a["paper.doi"]))
    spec.write_text(_splice_rights(text, render_rights(a)))
    return created
