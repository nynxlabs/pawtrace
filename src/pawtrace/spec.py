"""Paper spec + ledger: the source of truth for what the paper says and how gaps were filled.

``spec.yaml`` — written at intake, before any build::

    paper:  {id, title, authors, year, doi, version}
    rights:                       # the paper's license, access and the rules that follow (rights.py)
      license, license_url, copy_url, open_version, access, access_terms,
      rules: {redistribute, quotes_in_spec, ai_tools: allowed|local-only|never (new specs start at never)}, permissions: [...]
    policy:                       # code-use policy, set per paper up front
      when_silent: [authors-code, convention, author-question, deviation]   # preference order
      authors_code: {url, license, use: allowed | reference-only | off-limits}
      off_limits: ["<source we must not open>", ...]
    items:                        # every equation, parameter, protocol detail, panel, claim
      - {id, kind, text, section, status: stated|missing|ambiguous|contradicted, value?, ledger?}
      # items seeded by `pawtrace extract seed` start as status: candidate (+ extracted: {...})
      # until someone reads them against the paper and sets a real status

``ledger.yaml`` — every gap fill, interpretation and deviation, with its source and reason::

    entries:
      - {id, item, type: gap-fill|interpretation|deviation, value, source, reason,
         status: proposed|testing|adopted|rejected|superseded,
         evidence: [...], tested_on: {seeds: [...], runs: [...]}, decided, history: [...]}
    questions:                    # author questions
      - {id, items: [...], question, status: open|sent|answered, answer?}

Lint enforces the lessons from rl-adaptive-dbs: a policy and the paper's rights exist
before any build;
every missing/ambiguous item has a ledger entry; ledger sources respect the
policy; deviations are adopted only after testing on fresh seeds.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import rights as rts

ITEM_KINDS = ("equation", "parameter", "protocol", "architecture", "data", "panel", "claim", "foundation", "other")
ITEM_STATUS = ("stated", "missing", "ambiguous", "contradicted")
CANDIDATE = "candidate"   # seeded by extraction, not reviewed yet — never counts as stated
ENTRY_TYPES = ("gap-fill", "interpretation", "deviation")
ENTRY_SOURCES = ("paper", "authors-code", "convention", "cited-work", "author", "deviation")
ENTRY_STATUS = ("proposed", "testing", "adopted", "rejected", "superseded")
CODE_USE = ("allowed", "reference-only", "off-limits")


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    obj = yaml.safe_load(path.read_text()) or {}
    if not isinstance(obj, dict):
        raise ValueError(f"{path}: expected a mapping")
    return obj


class Paper:
    def __init__(self, paper_id: str, root: Path):
        self.id = paper_id
        self.root = root
        self.spec = load_yaml(root / "spec.yaml")
        self.ledger = load_yaml(root / "ledger.yaml")

    @property
    def items(self) -> list[dict[str, Any]]:
        return list(self.spec.get("items") or [])

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self.ledger.get("entries") or [])

    @property
    def questions(self) -> list[dict[str, Any]]:
        return list(self.ledger.get("questions") or [])

    def summary(self) -> dict[str, Any]:
        items = self.items
        by_status = {s: sum(1 for i in items if i.get("status") == s) for s in ITEM_STATUS}
        entries = self.entries
        covered = {e.get("item") for e in entries if e.get("status") in ("adopted", "testing", "proposed")}
        open_items = [i["id"] for i in items if i.get("status") in ("missing", "ambiguous", "contradicted") and i["id"] not in covered]
        return {
            "paper": self.id,
            "title": (self.spec.get("paper") or {}).get("title"),
            "items": len(items),
            "items_by_status": by_status,
            "candidates": sum(1 for i in items if i.get("status") == CANDIDATE),
            "ledger_entries": len(entries),
            "deviations_adopted": [e["id"] for e in entries if e.get("type") == "deviation" and e.get("status") == "adopted"],
            "open_items": open_items,
            "open_questions": [q["id"] for q in self.questions if q.get("status", "open") != "answered"],
        }

    def lint(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []

        def add(level: str, msg: str) -> None:
            out.append({"level": level, "paper": self.id, "message": msg})

        if not self.spec:
            add("error", "no spec.yaml (run intake: every detail stated or missing, with its section)")
            return out
        pmeta = self.spec.get("paper") or {}
        for key in ("title", "authors", "year"):
            if not pmeta.get(key):
                add("warn", f"paper.{key} is empty")
        if not pmeta.get("version"):
            add("warn", "paper.version is empty (which version was read: preprint, published, erratum)")
        rts.lint_rights(self.spec, add)
        policy = self.spec.get("policy")
        if not policy:
            add("error", "no policy: set the code-use policy at intake, before any build")
            policy = {}
        ac = (policy.get("authors_code") or {}) if isinstance(policy, dict) else {}
        use = ac.get("use")
        if ac and use not in CODE_USE:
            add("error", f"policy.authors_code.use must be one of {', '.join(CODE_USE)}")
        off_limits = [str(s).lower() for s in (policy.get("off_limits") or [])] if isinstance(policy, dict) else []

        ids: set[str] = set()
        for i in self.items:
            iid = i.get("id")
            if not iid:
                add("error", f"item without id: {i}")
                continue
            if iid in ids:
                add("error", f"duplicate item id {iid}")
            ids.add(iid)
            if str(iid).startswith("example."):
                add("warn", f"item {iid} is the template placeholder — replace it with the paper's items")
            if i.get("kind") not in ITEM_KINDS:
                add("warn", f"item {iid}: kind {i.get('kind')!r} not in {', '.join(ITEM_KINDS)}")
            if i.get("kind") == "foundation":
                rts.lint_foundation_rights(i, add)
            st = i.get("status")
            if st == CANDIDATE:
                add("warn", f"item {iid} is an unreviewed candidate from extraction — check it against the paper, then set status ({', '.join(ITEM_STATUS)})")
            elif st not in ITEM_STATUS:
                add("error", f"item {iid}: status must be one of {', '.join(ITEM_STATUS)}")
            if st == "stated" and not i.get("section"):
                add("warn", f"item {iid}: stated but no section — say where the paper states it")

        covered: dict[str, list[dict[str, Any]]] = {}
        eids: set[str] = set()
        for e in self.entries:
            eid = e.get("id", "?")
            if eid in eids:
                add("error", f"duplicate ledger id {eid}")
            eids.add(eid)
            if e.get("item") not in ids:
                add("error", f"ledger {eid}: item {e.get('item')!r} is not in the spec")
            covered.setdefault(e.get("item"), []).append(e)
            if e.get("type") not in ENTRY_TYPES:
                add("error", f"ledger {eid}: type must be one of {', '.join(ENTRY_TYPES)}")
            src = e.get("source")
            if src not in ENTRY_SOURCES:
                add("error", f"ledger {eid}: source must be one of {', '.join(ENTRY_SOURCES)}")
            if e.get("status") not in ENTRY_STATUS:
                add("error", f"ledger {eid}: status must be one of {', '.join(ENTRY_STATUS)}")
            if not e.get("reason"):
                add("error", f"ledger {eid}: no reason")
            if src == "authors-code" and use in ("reference-only", "off-limits") and e.get("status") == "adopted":
                add("error", f"ledger {eid}: adopts authors' code but policy.authors_code.use is {use}")
            if src == "authors-code" and not ac:
                add("warn", f"ledger {eid}: uses authors' code but policy.authors_code isn't set")
            for ref in e.get("refs") or []:
                if any(o and o in str(ref).lower() for o in off_limits):
                    add("error", f"ledger {eid}: cites off-limits source {ref!r}")
            if e.get("type") == "deviation" and e.get("status") == "adopted":
                tested = e.get("tested_on") or {}
                if not (tested.get("seeds") or tested.get("runs")):
                    add("error", f"ledger {eid}: deviation adopted without tested_on (fresh seeds/runs) — propose, test, then adopt")
                if not e.get("evidence"):
                    add("warn", f"ledger {eid}: adopted deviation has no evidence")
        for i in self.items:
            if i.get("status") in ("missing", "ambiguous", "contradicted"):
                live = [e for e in covered.get(i.get("id"), []) if e.get("status") not in ("rejected", "superseded")]
                if not live:
                    add("warn", f"item {i.get('id')} is {i.get('status')} with no ledger entry (open gap)")
        return out

    def render_markdown(self) -> str:
        """The generated markdown view of spec + ledger (never edited by hand)."""
        p = self.spec.get("paper") or {}
        lines = [
            f"<!-- generated by `pawtrace spec render {self.id}` from spec.yaml + ledger.yaml; do not edit -->",
            f"# {p.get('title') or self.id}",
            "",
        ]
        meta = [f"{k}: {p[k]}" for k in ("authors", "year", "doi", "version") if p.get(k)]
        if meta:
            lines += [" · ".join(str(m) for m in meta), ""]
        lines += rts.render_lines(self.spec)
        pol = self.spec.get("policy") or {}
        if pol:
            lines += ["## code-use policy", ""]
            if pol.get("when_silent"):
                lines.append(f"- when the paper is silent: {' → '.join(pol['when_silent'])}")
            ac = pol.get("authors_code") or {}
            if ac:
                lines.append(f"- authors' code: {ac.get('use', '?')} ({ac.get('license', 'license unknown')}){' — ' + ac['url'] if ac.get('url') else ''}")
            for o in pol.get("off_limits") or []:
                lines.append(f"- off limits: {o}")
            lines.append("")
        s = self.summary()
        counts = ", ".join(f"{v} {k}" for k, v in s["items_by_status"].items())
        if s["candidates"]:
            counts += f", {s['candidates']} unreviewed candidates"
        lines += ["## spec", "", f"{s['items']} items — {counts}", ""]
        lines += ["| id | kind | status | section | text | ledger |", "| --- | --- | --- | --- | --- | --- |"]
        by_item: dict[str, list[str]] = {}
        for e in self.entries:
            by_item.setdefault(e.get("item"), []).append(f"{e.get('id')} ({e.get('status')})")
        for i in self.items:
            lines.append(
                f"| {i.get('id')} | {i.get('kind', '')} | {i.get('status', '')} | {i.get('section', '')} | "
                f"{_cell(i.get('text', ''))} | {', '.join(by_item.get(i.get('id'), []))} |"
            )
        lines += ["", "## ledger", ""]
        if not self.entries:
            lines.append("(empty)")
        else:
            lines += ["| id | item | type | source | status | value | reason |", "| --- | --- | --- | --- | --- | --- | --- |"]
            for e in self.entries:
                lines.append(
                    f"| {e.get('id')} | {e.get('item')} | {e.get('type')} | {e.get('source')} | {e.get('status')} | "
                    f"{_cell(e.get('value', ''))} | {_cell(e.get('reason', ''))} |"
                )
        if self.questions:
            lines += ["", "## author questions", ""]
            for q in self.questions:
                lines.append(f"- **{q.get('id')}** ({q.get('status', 'open')}): {q.get('question')}")
        if s["open_items"]:
            lines += ["", "## open gaps", ""] + [f"- {i}" for i in s["open_items"]]
        return "\n".join(lines) + "\n"


def _cell(v: Any) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")
