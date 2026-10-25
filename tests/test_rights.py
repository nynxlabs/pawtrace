"""Tests for the paper rights block: lint through ``Paper.lint()``, the ``pawtrace.rights``
helpers, and the restricted-content check against a real git repo."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from pawtrace import rights as rts
from pawtrace.spec import Paper


def _paper(tmp_path: Path, spec: dict[str, Any] | None) -> Paper:
    root = tmp_path / "demo"
    root.mkdir(parents=True, exist_ok=True)
    if spec is not None:
        (root / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return Paper("demo", root)


def _rights(**kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "copy_url": "https://arxiv.org/abs/0000.00000",
        "open_version": "https://arxiv.org/abs/0000.00000",
        "access": "open-access",
        "access_terms": "",
        "rules": {"redistribute": False, "quotes_in_spec": True, "ai_tools": "allowed"},
        "permissions": [],
    }
    r.update(kw)
    return r


def _spec(items: list[dict[str, Any]] | None = None, rights: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "paper": {"id": "demo", "title": "A Demo Paper", "authors": ["A. Author"], "year": 2024, "doi": "10.0/x", "version": "published"},
        "rights": _rights() if rights is None else rights,
        "policy": {
            "when_silent": ["authors-code", "convention", "author-question", "deviation"],
            "authors_code": {"url": "https://example.org/code", "license": "MIT", "use": "reference-only"},
            "off_limits": [],
        },
        "items": items or [],
    }


def _item(iid: str, status: str = "stated", kind: str = "parameter", section: str | None = "§2.1", text: str = "some text") -> dict[str, Any]:
    d: dict[str, Any] = {"id": iid, "kind": kind, "text": text, "status": status}
    if section is not None:
        d["section"] = section
    return d


def _errors(problems: list[dict[str, str]]) -> list[str]:
    return [p["message"] for p in problems if p["level"] == "error"]


def _warns(problems: list[dict[str, str]]) -> list[str]:
    return [p["message"] for p in problems if p["level"] == "warn"]


def _redistributing(license_: str, permissions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return _rights(license=license_, permissions=permissions or [],
                   rules={"redistribute": True, "quotes_in_spec": True, "ai_tools": "allowed"})


# ---------------------------------------------------------------------------
# lint of the rights: block
# ---------------------------------------------------------------------------


def test_missing_rights_block_is_an_error(tmp_path):
    spec = _spec()
    del spec["rights"]
    errs = _errors(_paper(tmp_path, spec).lint())
    assert any(m.startswith("no rights:") for m in errs)


def test_unknown_license_warns(tmp_path):
    warns = _warns(_paper(tmp_path, _spec(rights=_rights(license="unknown"))).lint())
    assert any("rights.license is unknown" in m for m in warns)


def test_access_must_be_a_known_value(tmp_path):
    errs = _errors(_paper(tmp_path, _spec(rights=_rights(access="nope"))).lint())
    assert any(m.startswith("rights.access must be one of") for m in errs)


def test_access_unknown_warns(tmp_path):
    warns = _warns(_paper(tmp_path, _spec(rights=_rights(access="unknown"))).lint())
    assert any("rights.access is unknown" in m for m in warns)


@pytest.mark.parametrize(
    "access, open_version, expect_warn",
    [
        ("subscription", "", True),
        ("open-access", "", False),
        ("subscription", "https://arxiv.org/abs/0000.00000", False),
    ],
)
def test_open_version_empty_warns_unless_open_access(tmp_path, access, open_version, expect_warn):
    probs = _paper(tmp_path, _spec(rights=_rights(access=access, open_version=open_version))).lint()
    hit = any("rights.open_version is empty" in m for m in _warns(probs))
    assert hit is expect_warn


def test_rules_block_missing_is_an_error(tmp_path):
    rights = _rights()
    del rights["rules"]
    errs = _errors(_paper(tmp_path, _spec(rights=rights)).lint())
    assert any("rights.rules is missing" in m for m in errs)


@pytest.mark.parametrize(
    "rules, expected",
    [
        ({"redistribute": "yes", "quotes_in_spec": True, "ai_tools": "allowed"}, "rights.rules.redistribute must be true or false"),
        ({"redistribute": False, "quotes_in_spec": True, "ai_tools": "maybe"}, "rights.rules.ai_tools must be one of"),
    ],
)
def test_bad_rule_values_are_errors(tmp_path, rules, expected):
    errs = _errors(_paper(tmp_path, _spec(rights=_rights(rules=rules))).lint())
    assert any(m.startswith(expected) for m in errs)


@pytest.mark.parametrize("ai_tools, flagged", [("allowed", True), ("never", False)])
def test_under_review_never_goes_to_ai_tools(tmp_path, ai_tools, flagged):
    rights = _rights(access="under-review", rules={"redistribute": False, "quotes_in_spec": True, "ai_tools": ai_tools})
    errs = _errors(_paper(tmp_path, _spec(rights=rights)).lint())
    assert any("under review" in m for m in errs) is flagged


def test_redistribution_needs_an_open_license_or_a_permission(tmp_path):
    errs = _errors(_paper(tmp_path / "a", _spec(rights=_redistributing("publisher-copyright"))).lint())
    assert any(m.startswith("rights.rules.redistribute is true but license") for m in errs)

    perm = [{"from": "Publisher", "date": "2026-10-01", "grants": ["redistribute"], "ref": "email"}]
    probs = _paper(tmp_path / "b", _spec(rights=_redistributing("publisher-copyright", perm))).lint()
    assert _errors(probs) == []

    probs = _paper(tmp_path / "c", _spec(rights=_redistributing("CC-BY-4.0"))).lint()
    assert _errors(probs) == []
    assert not any("NC/ND" in w for w in _warns(probs))


def test_nc_or_nd_license_warns_when_redistributing(tmp_path):
    warns = _warns(_paper(tmp_path, _spec(rights=_redistributing("CC-BY-NC-4.0"))).lint())
    assert any("NC/ND" in w for w in warns)


def test_permission_entry_needs_from_date_and_grants(tmp_path):
    perm = [{"from": "Publisher", "grants": ["redistribute"], "ref": "email"}]
    warns = _warns(_paper(tmp_path, _spec(rights=_rights(permissions=perm))).lint())
    assert any(m.startswith("rights.permissions[0] needs from, date and grants") for m in warns)


# ---------------------------------------------------------------------------
# pawtrace.rights helpers
# ---------------------------------------------------------------------------


def test_ai_tools_fails_closed():
    assert rts.ai_tools({}) == "never"
    assert rts.ai_tools({"rights": {"license": "CC-BY-4.0"}}) == "never"
    assert rts.ai_tools({"rights": {"rules": {"ai_tools": "bogus"}}}) == "never"
    assert rts.ai_tools({"rights": {"rules": {"ai_tools": "local-only"}}}) == "local-only"


def test_may_redistribute_needs_the_rule_and_a_license_or_permission():
    no_rules = _rights()
    del no_rules["rules"]
    assert rts.may_redistribute(_spec(rights=no_rules)) is False
    assert rts.may_redistribute(_spec(rights=_redistributing("CC-BY-4.0"))) is True
    assert rts.may_redistribute(_spec(rights=_redistributing("publisher-copyright"))) is False
    perm = [{"from": "Publisher", "date": "2026-10-01", "grants": ["redistribute"], "ref": "email"}]
    assert rts.may_redistribute(_spec(rights=_redistributing("publisher-copyright", perm))) is True
    stringy = _rights(license="CC-BY-4.0", rules={"redistribute": "yes", "quotes_in_spec": True, "ai_tools": "allowed"})
    assert rts.may_redistribute(_spec(rights=stringy)) is False


def _foundation(rights: dict[str, Any]) -> dict[str, Any]:
    return {"id": "found.1", "kind": "foundation", "text": "a foundation item", "status": "stated", "section": "§1", "rights": rights}


def test_foundation_item_with_unknown_license_warns(tmp_path):
    probs = _paper(tmp_path, _spec([_foundation({"license": "unknown"})])).lint()
    assert "item found.1: rights.license is unknown" in _warns(probs)


def test_foundation_item_with_bad_access_is_an_error(tmp_path):
    probs = _paper(tmp_path, _spec([_foundation({"license": "CC-BY-4.0", "access": "bogus"})])).lint()
    assert any(m.startswith("item found.1: rights.access must be one of") for m in _errors(probs))


def test_render_markdown_has_the_rights_section(tmp_path):
    md = _paper(tmp_path, _spec()).render_markdown()
    assert "## paper rights" in md
    assert "- rules: redistribute no; quotes in spec yes; AI tools allowed" in md


def test_candidate_item_is_a_warning_not_an_error(tmp_path):
    paper = _paper(tmp_path, _spec([_item("eq.1", status="candidate", kind="equation")]))
    probs = paper.lint()
    assert any("unreviewed candidate" in w for w in _warns(probs))
    assert not any("status must be one of" in e for e in _errors(probs))
    assert paper.summary()["candidates"] == 1


# ---------------------------------------------------------------------------
# restricted content vs git (real repo)
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path, rights: dict[str, Any] | None = None) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Test Runner")
    _git(root, "config", "user.email", "test@example.invalid")
    pdir = root / "papers" / "demo"
    pdir.mkdir(parents=True)
    spec = _spec(rights=rights)
    (pdir / "spec.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return root, pdir, spec


def test_untracked_pdf_that_git_would_commit_is_an_error(tmp_path):
    _, pdir, spec = _repo(tmp_path)
    (pdir / "paper.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    probs = rts.tracked_problems("demo", pdir, spec)
    assert len(probs) == 1
    assert probs[0]["level"] == "error"
    assert "would be committed" in probs[0]["message"]


def test_committed_source_file_is_an_error(tmp_path):
    root, pdir, spec = _repo(tmp_path)
    (pdir / "source").mkdir()
    (pdir / "source" / "text.md").write_text("extracted text")
    _git(root, "add", "-f", "papers/demo/source/text.md")
    probs = rts.tracked_problems("demo", pdir, spec)
    committed = [p for p in probs if "restricted paper content is committed" in p["message"]]
    assert len(committed) == 1
    assert "papers/demo/source/text.md" in committed[0]["message"]


def test_redistributable_paper_is_not_checked(tmp_path):
    rights = _rights(license="CC-BY-4.0", rules={"redistribute": True, "quotes_in_spec": True, "ai_tools": "allowed"})
    _, pdir, spec = _repo(tmp_path, rights=rights)
    (pdir / "paper.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    assert rts.tracked_problems("demo", pdir, spec) == []


def test_outside_a_git_repo_nothing_is_checked(tmp_path):
    pdir = tmp_path / "papers" / "demo"
    pdir.mkdir(parents=True)
    (pdir / "paper.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    if rts.git_toplevel(pdir) is not None:
        pytest.skip("tmp_path sits inside a git repo on this machine")
    assert rts.tracked_problems("demo", pdir, _spec()) == []
