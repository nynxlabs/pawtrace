"""panel-claims module: claim / renew / refuse / expire / release, where the lock files live,
and concurrent claimers (exactly one wins). Uses the project from ``test_runs_cli``."""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest
import yaml

from pawtrace import claims as cl
from pawtrace.project import Project
from test_runs_cli import _git, call, jcall, make_project


@pytest.fixture
def root(tmp_path, capsys):
    r = make_project(tmp_path)
    assert jcall(capsys, r, "modules", "enable", "panel-claims")[0] == 0
    return r


def test_parse_ttl():
    assert cl.parse_ttl("90s") == timedelta(seconds=90)
    assert cl.parse_ttl("30m") == timedelta(minutes=30)
    assert cl.parse_ttl("1.5h") == timedelta(minutes=90)
    assert cl.parse_ttl("2d") == timedelta(days=2)
    assert cl.parse_ttl(None) == timedelta(hours=4)
    assert cl.parse_ttl("never") is None and cl.parse_ttl("0") is None
    with pytest.raises(ValueError):
        cl.parse_ttl("soon")


def test_needs_module_on(tmp_path, capsys):
    r = make_project(tmp_path)
    code, _, err = call(capsys, "claims", "--project", str(r))
    assert code == 2 and "panel-claims module is off" in err


def test_claim_file_lives_in_the_git_common_dir(root, capsys):
    code, data = jcall(capsys, root, "claim", "p1", "--by", "agent-a", "--task", "tune gate g")
    assert code == 0 and data["result"] == "claimed"
    f = root / ".git" / "pawtrace-claims" / "_root" / "demo" / "p1.yaml"
    rec = yaml.safe_load(f.read_text())
    assert rec["panel"] == "demo/p1" and rec["by"] == "agent-a" and rec["task"] == "tune gate g"
    assert rec["expires_at"] > rec["claimed_at"]
    assert not list(f.parent.glob(".*tmp"))      # the temp file is gone
    assert "pawtrace-claims" not in _git(root, "status", "--porcelain")


def test_second_claim_is_refused_and_names_the_holder(root, capsys):
    jcall(capsys, root, "claim", "demo/p1", "--by", "agent-a", "--task", "tune gate g")
    code, data = jcall(capsys, root, "claim", "p1", "--by", "agent-b")
    assert code == 1 and data["result"] == "refused"
    assert "claimed by agent-a" in data["error"] and "tune gate g" in data["error"]
    code, _, err = call(capsys, "claim", "p1", "--by", "agent-b", "--project", str(root))
    assert code == 1 and "claimed by agent-a" in err


def test_same_holder_renews(root, capsys):
    _, first = jcall(capsys, root, "claim", "p1", "--by", "agent-a", "--ttl", "1h", "--task", "one")
    code, data = jcall(capsys, root, "claim", "p1", "--by", "agent-a", "--ttl", "2d", "--task", "two")
    assert code == 0 and data["result"] == "renewed"
    assert data["claim"]["token"] == first["claim"]["token"]
    assert data["claim"]["task"] == "two" and data["claim"]["expires_at"] > first["claim"]["expires_at"]


def test_expired_claim_is_taken_over(root):
    proj = Project.find(root)
    past = cl.utcnow() - timedelta(hours=5)
    cl.claim(proj, "p1", by="agent-a", ttl="4h", at=past)
    (row,) = cl.list_claims(proj)
    assert row["state"] == "expired"
    what, rec = cl.claim(proj, "p1", by="agent-b")
    assert what == "took-over" and rec["by"] == "agent-b" and rec["took_over"]["by"] == "agent-a"
    assert [r["state"] for r in cl.list_claims(proj)] == ["live"]


def test_never_expiring_claim(root):
    proj = Project.find(root)
    cl.claim(proj, "p1", by="agent-a", ttl="never", at=cl.utcnow() - timedelta(days=365))
    with pytest.raises(cl.ClaimError):
        cl.claim(proj, "p1", by="agent-b")


def test_release_rules(root, capsys):
    jcall(capsys, root, "claim", "p1", "--by", "agent-a")
    code, data = jcall(capsys, root, "release", "p1", "--by", "agent-b")
    assert code == 1 and "--force" in data["error"]
    code, data = jcall(capsys, root, "release", "p1", "--by", "agent-a")
    assert code == 0 and data["claim"]["by"] == "agent-a"
    code, data = jcall(capsys, root, "release", "p1", "--by", "agent-a")
    assert code == 1 and "isn't claimed" in data["error"]
    jcall(capsys, root, "claim", "p1", "--by", "agent-a")
    code, _ = jcall(capsys, root, "release", "p1", "--by", "agent-b", "--force")
    assert code == 0
    assert jcall(capsys, root, "claims")[1]["claims"] == []


def test_anyone_may_release_an_expired_claim(root):
    proj = Project.find(root)
    cl.claim(proj, "p1", by="agent-a", ttl="1m", at=cl.utcnow() - timedelta(hours=1))
    assert cl.release(proj, "p1", by="agent-b")["by"] == "agent-a"


def test_default_holder_from_env(root, capsys, monkeypatch):
    monkeypatch.setenv("PAWTRACE_CLAIMANT", "thread-123")
    _, data = jcall(capsys, root, "claim", "p1")
    assert data["claim"]["by"] == "thread-123"
    assert jcall(capsys, root, "release", "p1")[0] == 0


def test_list_text_and_json(root, capsys):
    code, out, _ = call(capsys, "claims", "--project", str(root))
    assert code == 0 and "no claims" in out
    jcall(capsys, root, "claim", "p1", "--by", "agent-a", "--task", "overlay")
    code, out, _ = call(capsys, "claims", "--project", str(root))
    assert "demo/p1" in out and "live" in out and "agent-a" in out and "overlay" in out
    _, data = jcall(capsys, root, "claims")
    assert data["claims"][0]["state"] == "live" and data["dir"].endswith("_root")


def test_unknown_panel_and_bad_ttl_are_bad_input(root, capsys):
    code, _ = jcall(capsys, root, "claim", "nope", "--by", "a")
    assert code == 2
    code, data = jcall(capsys, root, "claim", "p1", "--by", "a", "--ttl", "soon")
    assert code == 2 and "bad ttl" in data["error"]


def test_worktrees_share_claims(root, tmp_path, capsys):
    _git(root, "commit", "-qam", "turn on panel-claims")
    wt = tmp_path / "wt"
    _git(root, "worktree", "add", "-q", "-b", "other", str(wt))
    assert cl.claims_dir(Project.find(wt)) == cl.claims_dir(Project.find(root))
    jcall(capsys, root, "claim", "p1", "--by", "agent-a")
    code, data = jcall(capsys, wt, "claim", "p1", "--by", "agent-b")
    assert code == 1 and "agent-a" in data["error"]


def test_paths_claims_override_and_no_git(tmp_path, capsys):
    r = make_project(tmp_path)
    cfg = r / "pawtrace.yaml"
    cfg.write_text(cfg.read_text().replace("modules: {}", "modules: {panel-claims: true}") + "paths:\n  claims: shared/claims\n")
    jcall(capsys, r, "claim", "p1", "--by", "a")
    assert (r / "shared/claims/demo/p1.yaml").is_file()

    plain = tmp_path / "plain"
    (plain / "papers/demo/panels").mkdir(parents=True)
    (plain / "pawtrace.yaml").write_text("pawtrace: 1\nname: plain\nprofile: minimal\nmodules: {panel-claims: true}\n")
    (plain / "papers/demo/panels/p1.yaml").write_text("panel: p1\n")
    assert cl.claims_dir(Project.find(plain)) == plain / ".pawtrace-claims"


def test_concurrent_claimers_exactly_one_wins(root):
    proj = Project.find(root)
    n = 12
    barrier = threading.Barrier(n)
    wins, refusals, errors = [], [], []

    def go(i):
        barrier.wait()
        try:
            what, rec = cl.claim(proj, "p1", by=f"agent-{i}")
            wins.append((what, rec["by"]))
        except cl.ClaimError:
            refusals.append(i)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(wins) == 1 and wins[0][0] == "claimed" and len(refusals) == n - 1
    (row,) = cl.list_claims(proj)
    assert row["by"] == wins[0][1]


def test_concurrent_takeover_of_an_expired_claim(root):
    proj = Project.find(root)
    cl.claim(proj, "p1", by="old", ttl="1m", at=cl.utcnow() - timedelta(hours=1))
    n = 8
    barrier = threading.Barrier(n)
    wins, errors = [], []

    def go(i):
        barrier.wait()
        try:
            wins.append(cl.claim(proj, "p1", by=f"agent-{i}")[1]["by"])
        except cl.ClaimError:
            pass
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and len(wins) == 1
    (row,) = cl.list_claims(proj)
    assert row["by"] == wins[0] and row["state"] == "live"
