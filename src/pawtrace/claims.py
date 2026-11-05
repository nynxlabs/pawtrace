"""panel-claims module: a lock file per panel, so two people or agents don't work on one panel.

``pawtrace claim <panel> --by NAME --task "…"`` creates ``<claims dir>/<paper>/<panel>.yaml``::

    {panel, by, task, claimed_at, expires_at | null, token, host}

* a second live claim on the same panel is refused (exit 1) and names the holder
* the same holder claiming again renews it (new expiry, new task text)
* claims expire (default ``4h``; ``--ttl 30m|2h|1d|never``); an expired claim is taken over
  by the next ``claim`` and shown as ``expired`` by ``claims``
* ``release`` removes it (the holder, anyone once it has expired, or ``--force``)

Where the files live: ``paths.claims`` in ``pawtrace.yaml`` when set; otherwise inside the git
repo's common dir (``.git/pawtrace-claims/<project path>/``), which every worktree of the
repo shares — so agents in separate worktrees see each other's claims without committing
anything; outside git, ``.pawtrace-claims/`` in the project. Claims are coordination state,
not results, so they stay out of commits.

Safe for concurrent writers: a claim is written to a temp file and published with
``os.link`` (atomic, fails if the claim exists), so a reader never sees half a file and two
claimers can't both win. Taking over an expired claim goes through a short-lived
``.break`` file created with ``O_EXCL``. No processes are spawned or watched; liveness is
only the expiry time.
"""
from __future__ import annotations

import getpass
import os
import re
import secrets
import socket
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from .project import Project

DEFAULT_TTL = "4h"
BREAK_STALE_S = 60   # a leftover .break file older than this is from a crashed claimer


class ClaimError(RuntimeError):
    """Refused (exit 1): the panel is held by someone else, or the claim isn't yours."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_ttl(s: str | None) -> timedelta | None:
    """``"90s" | "30m" | "4h" | "2d"`` → timedelta; ``"never"`` / ``"0"`` → ``None`` (no expiry)."""
    s = str(s if s is not None else DEFAULT_TTL).strip().lower()
    if s in ("never", "none", "0"):
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])", s)
    if not m:
        raise ValueError(f"bad ttl {s!r} (use e.g. 30m, 4h, 2d or never)")
    n, unit = float(m.group(1)), m.group(2)
    return timedelta(seconds=n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])


def default_holder() -> str:
    env = os.environ.get("PAWTRACE_CLAIMANT")
    if env:
        return env
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name in some sandboxes
        user = "unknown"
    return f"{user}@{socket.gethostname()}"


def claims_dir(project: Project) -> Path:
    paths = project.cfg.get("paths") or {}
    if paths.get("claims"):
        return project.path(paths["claims"])

    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=project.root, capture_output=True, text=True, check=True).stdout.strip()

    try:
        common = Path(git("rev-parse", "--git-common-dir"))
        prefix = git("rev-parse", "--show-prefix").strip("/")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return project.root / ".pawtrace-claims"
    if not common.is_absolute():
        common = (project.root / common).resolve()
    return common / "pawtrace-claims" / (prefix or "_root")


def claim_path(project: Project, panel_id: str) -> Path:
    paper, panel = panel_id.split("/", 1)
    return claims_dir(project) / paper / f"{panel}.yaml"


def _read(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    try:
        obj = yaml.safe_load(text)
    except yaml.YAMLError:
        obj = None
    if not isinstance(obj, dict):
        return {"corrupt": True, "by": "?", "token": None, "expires_at": None}
    return obj


def is_expired(rec: dict[str, Any], at: datetime) -> bool:
    exp = rec.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(str(exp)) <= at
    except ValueError:
        return False


def _describe(rec: dict[str, Any]) -> str:
    if rec.get("corrupt"):
        return "an unreadable claim file (remove it with `release --force`)"
    s = f"{rec.get('by')} since {rec.get('claimed_at')}"
    if rec.get("task"):
        s += f" ({rec['task']})"
    s += f", expires {rec['expires_at']}" if rec.get("expires_at") else ", no expiry"
    return s


def _publish(target: Path, rec: dict[str, Any]) -> bool:
    """Atomically create ``target`` with ``rec``; False when it already exists."""
    tmp = target.with_name(f".{target.name}.{rec['token']}.tmp")
    tmp.write_text(yaml.safe_dump(rec, sort_keys=False, allow_unicode=True))
    try:
        os.link(tmp, target)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def _break_expired(target: Path, seen_token: Any, at: datetime) -> None:
    """Remove an expired claim, once, under an O_EXCL ``.break`` guard."""
    guard = target.with_name(target.name + ".break")
    try:
        fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        try:
            age = at.timestamp() - guard.stat().st_mtime
        except FileNotFoundError:
            age = 0
        if age > BREAK_STALE_S:
            guard.unlink(missing_ok=True)
        raise ClaimError(f"someone else is taking over the expired claim on {target.stem} — try again") from None
    try:
        os.close(fd)
        cur = _read(target)
        if cur is not None and cur.get("token") == seen_token and is_expired(cur, at):
            target.unlink(missing_ok=True)
    finally:
        guard.unlink(missing_ok=True)


def claim(
    project: Project,
    panel_ref: str,
    *,
    by: str | None = None,
    task: str | None = None,
    ttl: str | None = None,
    at: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Claim a panel. Returns ``("claimed" | "renewed" | "took-over", record)``; raises ClaimError."""
    pid = project.resolve_panel_id(panel_ref)
    at = at or utcnow()
    life = parse_ttl(ttl)
    holder = by or default_holder()
    target = claim_path(project, pid)
    target.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "panel": pid,
        "by": holder,
        "task": task or "",
        "claimed_at": _iso(at),
        "expires_at": _iso(at + life) if life else None,
        "token": secrets.token_hex(8),
        "host": socket.gethostname(),
    }
    if _publish(target, rec):
        return "claimed", rec
    cur = _read(target)
    if cur is None:  # released between our attempt and the read
        if _publish(target, rec):
            return "claimed", rec
        cur = _read(target) or {}
    if not cur.get("corrupt") and not is_expired(cur, at):
        if cur.get("by") == holder:
            renewed = {**cur, "task": task if task is not None else cur.get("task", ""),
                       "expires_at": rec["expires_at"], "renewed_at": _iso(at)}
            tmp = target.with_name(f".{target.name}.{rec['token']}.tmp")
            tmp.write_text(yaml.safe_dump(renewed, sort_keys=False, allow_unicode=True))
            tmp.replace(target)
            return "renewed", renewed
        raise ClaimError(f"{pid} is claimed by {_describe(cur)}")
    if cur.get("corrupt"):
        raise ClaimError(f"{pid} has {_describe(cur)}")
    _break_expired(target, cur.get("token"), at)
    if _publish(target, {**rec, "took_over": {"by": cur.get("by"), "expired_at": cur.get("expires_at")}}):
        return "took-over", _read(target) or rec
    now_held = _read(target) or {}
    raise ClaimError(f"{pid} is claimed by {_describe(now_held)}")


def release(
    project: Project,
    panel_ref: str,
    *,
    by: str | None = None,
    force: bool = False,
    at: datetime | None = None,
) -> dict[str, Any]:
    pid = project.resolve_panel_id(panel_ref)
    at = at or utcnow()
    target = claim_path(project, pid)
    cur = _read(target)
    if cur is None:
        raise ClaimError(f"{pid} isn't claimed")
    holder = by or default_holder()
    if not force and cur.get("by") != holder and not is_expired(cur, at):
        raise ClaimError(f"{pid} is claimed by {_describe(cur)}, not {holder} — `--force` releases someone else's claim")
    target.unlink(missing_ok=True)
    return cur


def list_claims(project: Project, at: datetime | None = None) -> list[dict[str, Any]]:
    at = at or utcnow()
    root = claims_dir(project)
    out = []
    if not root.is_dir():
        return out
    for f in sorted(root.glob("*/*.yaml")):
        rec = _read(f)
        if rec is None:
            continue
        pid = rec.get("panel") or f"{f.parent.name}/{f.stem}"
        state = "corrupt" if rec.get("corrupt") else ("expired" if is_expired(rec, at) else "live")
        out.append({**{k: v for k, v in rec.items() if k != "corrupt"}, "panel": pid, "state": state, "file": str(f)})
    return out


def format_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "no claims"
    lines = []
    for r in rows:
        exp = f"expires {r['expires_at']}" if r.get("expires_at") else "no expiry"
        task = f" — {r['task']}" if r.get("task") else ""
        lines.append(f"  {r['panel']:<20} {r['state']:<7} {r.get('by')}  since {r.get('claimed_at')}, {exp}{task}")
    return "\n".join(lines)
