"""Tests for pawtrace.spec: paper spec + ledger lint, summary, and rendered markdown."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pawtrace.spec import Paper


def _paper(tmp_path: Path, spec: dict[str, Any] | None, ledger: dict[str, Any] | None = None) -> Paper:
    root = tmp_path / "demo"
    root.mkdir(parents=True, exist_ok=True)
    if spec is not None:
        (root / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    if ledger is not None:
        (root / "ledger.yaml").write_text(yaml.safe_dump(ledger, sort_keys=False))
    return Paper("demo", root)


def _policy(use: str = "reference-only", off_limits: list[str] | None = None, with_code: bool = True) -> dict[str, Any]:
    pol: dict[str, Any] = {
        "when_silent": ["authors-code", "convention", "author-question", "deviation"],
        "off_limits": off_limits or [],
    }
    if with_code:
        pol["authors_code"] = {"url": "https://example.org/code", "license": "MIT", "use": use}
    return pol


def _spec(items: list[dict[str, Any]], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "paper": {"id": "demo", "title": "A Demo Paper", "authors": ["A. Author"], "year": 2024, "doi": "10.0/x", "version": "published"},
        "policy": _policy() if policy is None else policy,
        "items": items,
    }


def _item(iid: str, status: str = "stated", kind: str = "parameter", section: str | None = "§2.1", text: str = "some text") -> dict[str, Any]:
    d: dict[str, Any] = {"id": iid, "kind": kind, "text": text, "status": status}
    if section is not None:
        d["section"] = section
    return d


def _entry(eid: str, item: str, **kw: Any) -> dict[str, Any]:
    e = {
        "id": eid,
        "item": item,
        "type": "gap-fill",
        "value": 0.5,
        "source": "convention",
        "reason": "the usual default",
        "status": "adopted",
    }
    e.update(kw)
    return e


def _errors(problems: list[dict[str, str]]) -> list[str]:
    return [p["message"] for p in problems if p["level"] == "error"]


def _warns(problems: list[dict[str, str]]) -> list[str]:
    return [p["message"] for p in problems if p["level"] == "warn"]


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


def test_no_spec_is_an_error(tmp_path):
    paper = _paper(tmp_path, spec=None)
    problems = paper.lint()
    assert _errors(problems) == [
        "no spec.yaml (run intake: every detail stated or missing, with its section)",
    ]
    assert all(p["paper"] == "demo" for p in problems)


def test_no_policy_is_an_error(tmp_path):
    spec = _spec([_item("eq.1")])
    del spec["policy"]
    problems = _paper(tmp_path, spec=spec).lint()
    assert any("no policy" in m for m in _errors(problems))


def test_missing_item_without_ledger_entry_is_an_open_gap_warning(tmp_path):
    spec = _spec([_item("param.beta", status="missing", section=None)])
    problems = _paper(tmp_path, spec=spec, ledger={"entries": []}).lint()
    assert _errors(problems) == []
    assert "item param.beta is missing with no ledger entry (open gap)" in _warns(problems)


def test_ambiguous_and_contradicted_items_without_entries_are_open_gaps(tmp_path):
    spec = _spec([_item("a", status="ambiguous", section=None), _item("b", status="contradicted", section=None)])
    msgs = _warns(_paper(tmp_path, spec=spec, ledger={"entries": []}).lint())
    assert "item a is ambiguous with no ledger entry (open gap)" in msgs
    assert "item b is contradicted with no ledger entry (open gap)" in msgs


def test_missing_item_with_live_ledger_entry_is_not_a_gap(tmp_path):
    spec = _spec([_item("param.beta", status="missing", section=None)])
    ledger = {"entries": [_entry("gap.beta", "param.beta", status="proposed")]}
    problems = _paper(tmp_path, spec=spec, ledger=ledger).lint()
    assert not any("open gap" in m for m in _warns(problems))
    assert _errors(problems) == []


def test_rejected_entry_does_not_count_as_fill(tmp_path):
    spec = _spec([_item("param.beta", status="missing", section=None)])
    ledger = {"entries": [_entry("gap.beta", "param.beta", status="rejected")]}
    msgs = _warns(_paper(tmp_path, spec=spec, ledger=ledger).lint())
    assert "item param.beta is missing with no ledger entry (open gap)" in msgs


def test_ledger_entry_pointing_at_unknown_item_is_an_error(tmp_path):
    spec = _spec([_item("eq.1")])
    ledger = {"entries": [_entry("gap.x", "no.such.item")]}
    errs = _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint())
    assert "ledger gap.x: item 'no.such.item' is not in the spec" in errs


def test_adopted_deviation_without_tested_on_is_an_error(tmp_path):
    spec = _spec([_item("eq.1")])
    ledger = {
        "entries": [
            _entry("dev.1", "eq.1", type="deviation", source="deviation", status="adopted", evidence=["run x"]),
        ]
    }
    errs = _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint())
    assert any(m.startswith("ledger dev.1: deviation adopted without tested_on") for m in errs)


def test_adopted_deviation_with_tested_on_passes(tmp_path):
    spec = _spec([_item("eq.1")])
    ledger = {
        "entries": [
            _entry(
                "dev.1",
                "eq.1",
                type="deviation",
                source="deviation",
                status="adopted",
                evidence=["panel p1 gate g passes on seeds 100-109"],
                tested_on={"seeds": [100, 101, 102]},
            ),
        ]
    }
    problems = _paper(tmp_path, spec=spec, ledger=ledger).lint()
    assert _errors(problems) == []


def test_proposed_deviation_without_tested_on_is_fine(tmp_path):
    spec = _spec([_item("eq.1")])
    ledger = {"entries": [_entry("dev.1", "eq.1", type="deviation", source="deviation", status="proposed")]}
    assert _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint()) == []


def test_adopted_authors_code_with_reference_only_policy_is_an_error(tmp_path):
    spec = _spec([_item("eq.1")], policy=_policy(use="reference-only"))
    ledger = {"entries": [_entry("gap.1", "eq.1", source="authors-code", status="adopted")]}
    errs = _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint())
    assert "ledger gap.1: adopts authors' code but policy.authors_code.use is reference-only" in errs


def test_adopted_authors_code_with_off_limits_policy_is_an_error(tmp_path):
    spec = _spec([_item("eq.1")], policy=_policy(use="off-limits"))
    ledger = {"entries": [_entry("gap.1", "eq.1", source="authors-code", status="adopted")]}
    errs = _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint())
    assert any("policy.authors_code.use is off-limits" in m for m in errs)


def test_adopted_authors_code_with_allowed_policy_passes(tmp_path):
    spec = _spec([_item("eq.1")], policy=_policy(use="allowed"))
    ledger = {"entries": [_entry("gap.1", "eq.1", source="authors-code", status="adopted")]}
    assert _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint()) == []


def test_proposed_authors_code_with_reference_only_policy_is_not_an_error(tmp_path):
    spec = _spec([_item("eq.1")], policy=_policy(use="reference-only"))
    ledger = {"entries": [_entry("gap.1", "eq.1", source="authors-code", status="proposed")]}
    assert _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint()) == []


def test_ledger_ref_containing_off_limits_string_is_an_error(tmp_path):
    spec = _spec([_item("eq.1")], policy=_policy(use="allowed", off_limits=["GPL-Secret-Repo"]))
    ledger = {"entries": [_entry("gap.1", "eq.1", refs=["https://github.com/someone/gpl-secret-repo/blob/main/x.py"])]}
    errs = _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint())
    assert any(m.startswith("ledger gap.1: cites off-limits source") for m in errs)


def test_ledger_ref_not_matching_off_limits_is_fine(tmp_path):
    spec = _spec([_item("eq.1")], policy=_policy(use="allowed", off_limits=["GPL-Secret-Repo"]))
    ledger = {"entries": [_entry("gap.1", "eq.1", refs=["https://example.org/paper-appendix"])]}
    assert _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint()) == []


def test_clean_spec_and_ledger_has_no_errors(tmp_path):
    spec = _spec(
        [
            _item("eq.1", kind="equation", section="§2.1", text="y = a x"),
            _item("param.a", status="stated", section="§3"),
            _item("param.beta", status="missing", section=None, text="discount rate not stated"),
        ],
        policy=_policy(use="reference-only", off_limits=["GPL-Secret-Repo"]),
    )
    ledger = {
        "entries": [
            _entry("gap.beta", "param.beta", value=0.99, source="convention", reason="standard default in the field"),
        ],
        "questions": [],
    }
    problems = _paper(tmp_path, spec=spec, ledger=ledger).lint()
    assert _errors(problems) == []
    assert _warns(problems) == []


def test_lint_flags_bad_status_and_kind(tmp_path):
    spec = _spec([{"id": "x.1", "kind": "nonsense", "status": "vibes", "section": "§1", "text": "t"}])
    problems = _paper(tmp_path, spec=spec, ledger={"entries": []}).lint()
    assert "item x.1: kind 'nonsense' not in equation, parameter, protocol, architecture, data, panel, claim, foundation, other" in _warns(problems)
    assert any(m.startswith("item x.1: status must be one of") for m in _errors(problems))


def test_lint_flags_duplicate_item_and_ledger_ids(tmp_path):
    spec = _spec([_item("eq.1"), _item("eq.1")])
    ledger = {"entries": [_entry("gap.1", "eq.1"), _entry("gap.1", "eq.1")]}
    errs = _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint())
    assert "duplicate item id eq.1" in errs
    assert "duplicate ledger id gap.1" in errs


def test_lint_flags_entry_without_reason(tmp_path):
    spec = _spec([_item("eq.1")])
    ledger = {"entries": [_entry("gap.1", "eq.1", reason="")]}
    assert "ledger gap.1: no reason" in _errors(_paper(tmp_path, spec=spec, ledger=ledger).lint())


def test_stated_item_without_section_warns(tmp_path):
    spec = _spec([_item("eq.1", section=None)])
    msgs = _warns(_paper(tmp_path, spec=spec, ledger={"entries": []}).lint())
    assert "item eq.1: stated but no section — say where the paper states it" in msgs


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_summary_counts(tmp_path):
    spec = _spec(
        [
            _item("eq.1", status="stated"),
            _item("eq.2", status="stated"),
            _item("param.a", status="missing", section=None),
            _item("param.b", status="ambiguous", section=None),
            _item("param.c", status="contradicted", section=None),
            _item("param.d", status="missing", section=None),
        ]
    )
    ledger = {
        "entries": [
            _entry("dev.1", "eq.2", type="deviation", source="deviation", status="adopted", tested_on={"seeds": [9]}),
            _entry("gap.a", "param.a", status="testing"),
            _entry("gap.b", "param.b", status="rejected"),
        ],
        "questions": [
            {"id": "q.1", "items": ["param.b"], "question": "what was b?", "status": "open"},
            {"id": "q.2", "items": ["param.d"], "question": "what was d?", "status": "answered", "answer": "0.1"},
            {"id": "q.3", "items": ["param.c"], "question": "is c right?", "status": "sent"},
        ],
    }
    s = _paper(tmp_path, spec=spec, ledger=ledger).summary()
    assert s["paper"] == "demo"
    assert s["title"] == "A Demo Paper"
    assert s["items"] == 6
    assert s["items_by_status"] == {"stated": 2, "missing": 2, "ambiguous": 1, "contradicted": 1}
    assert s["ledger_entries"] == 3
    assert s["deviations_adopted"] == ["dev.1"]
    # param.a is covered by a testing entry, param.b only by a rejected one, param.c/param.d uncovered
    assert s["open_items"] == ["param.b", "param.c", "param.d"]
    assert s["open_questions"] == ["q.1", "q.3"]


def test_summary_of_empty_paper(tmp_path):
    s = _paper(tmp_path, spec=None).summary()
    assert s["items"] == 0
    assert s["items_by_status"] == {"stated": 0, "missing": 0, "ambiguous": 0, "contradicted": 0}
    assert s["open_items"] == []
    assert s["open_questions"] == []
    assert s["title"] is None


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def test_render_markdown_starts_with_generated_comment(tmp_path):
    spec = _spec([_item("eq.1"), _item("param.beta", status="missing", section=None)])
    md = _paper(tmp_path, spec=spec, ledger={"entries": []}).render_markdown()
    assert md.startswith("<!-- generated by `pawtrace spec render demo`")
    assert "# A Demo Paper" in md


def test_render_markdown_contains_item_ids_and_ledger(tmp_path):
    spec = _spec([_item("eq.1", kind="equation", text="y = a x"), _item("param.beta", status="missing", section=None)])
    ledger = {
        "entries": [_entry("gap.beta", "param.beta", value=0.99, reason="standard default")],
        "questions": [{"id": "q.1", "items": ["param.beta"], "question": "what is beta?", "status": "open"}],
    }
    md = _paper(tmp_path, spec=spec, ledger=ledger).render_markdown()
    assert "| eq.1 | equation | stated | §2.1 | y = a x |" in md
    assert "| param.beta | parameter | missing |" in md
    assert "| gap.beta | param.beta | gap-fill | convention | adopted | 0.99 | standard default |" in md
    assert "- **q.1** (open): what is beta?" in md
    # param.beta has an adopted entry, so it is not listed as an open gap
    assert "## open gaps" not in md


def test_render_markdown_lists_open_gaps(tmp_path):
    spec = _spec([_item("eq.1"), _item("param.beta", status="missing", section=None)])
    md = _paper(tmp_path, spec=spec, ledger={"entries": []}).render_markdown()
    assert md.endswith("## open gaps\n\n- param.beta\n")


def test_render_markdown_escapes_pipes_in_cells(tmp_path):
    spec = _spec([_item("eq.1", text="a | b")])
    md = _paper(tmp_path, spec=spec, ledger={"entries": []}).render_markdown()
    assert "a \\| b" in md


def test_render_markdown_policy_section(tmp_path):
    spec = _spec([_item("eq.1")], policy=_policy(use="reference-only", off_limits=["GPL-Secret-Repo"]))
    md = _paper(tmp_path, spec=spec, ledger={"entries": []}).render_markdown()
    assert "## code-use policy" in md
    assert "- authors' code: reference-only (MIT) — https://example.org/code" in md
    assert "- off limits: GPL-Secret-Repo" in md


def test_render_markdown_without_spec_still_renders_header(tmp_path):
    md = _paper(tmp_path, spec=None).render_markdown()
    assert md.startswith("<!-- generated by `pawtrace spec render demo`")
    assert "# demo" in md
    assert "(empty)" in md
