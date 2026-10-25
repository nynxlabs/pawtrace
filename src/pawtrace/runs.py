"""Run records: one JSON per run under ``runs/<paper>/<panel>/`` (gitignored).

A record holds what came back from the panel contract plus everything needed to
say what produced it::

    {"id", "panel", "source": "run" | "import", "seed", "config_name", "config",
     "config_hash", "git": {"commit", "dirty", "diff_sha256"}, "created_at",
     "imported_from"?, "extraction"?, "data": PanelData JSON, "checks": [{"at", "gates_hash", "report"}]}

Run ids are ``<commit12>-<confighash8>-s<seed>`` (``+d<diff8>`` when the tree was
dirty), so the same code + config + seed maps to the same record. Imported
artifacts are keyed by the artifact's content hash instead (``imp-<sha12>``).
Records are evidence, not backups: re-checking appends to ``checks`` rather than
copying the file.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .data import PanelData
from .gates import GateReport


def config_hash(config: Any) -> str:
    blob = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


def git_state(cwd: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout

    try:
        commit = git("rev-parse", "HEAD").strip()
        diff = git("diff", "HEAD")
        dirty = bool(diff.strip()) or bool(git("status", "--porcelain", "--untracked-files=no").strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"commit": None, "dirty": None}
    out: dict[str, Any] = {"commit": commit, "dirty": dirty}
    if dirty:
        out["diff_sha256"] = hashlib.sha256(diff.encode()).hexdigest()
    return out


def run_id(git: dict[str, Any], cfg_hash: str, seed: int) -> str:
    commit = (git.get("commit") or "nogit")[:12]
    rid = f"{commit}-{cfg_hash}-s{seed}"
    if git.get("dirty"):
        rid += f"+d{(git.get('diff_sha256') or '')[:8]}"
    return rid


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class RunStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def dir_for(self, panel_id: str) -> Path:
        return self.root / panel_id

    def path(self, panel_id: str, rid: str) -> Path:
        return self.dir_for(panel_id) / f"{rid}.json"

    def save(self, rec: dict[str, Any]) -> Path:
        p = self.path(rec["panel"], rec["id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(rec, indent=1) + "\n")
        tmp.replace(p)
        return p

    def load(self, panel_id: str, rid: str) -> dict[str, Any]:
        p = self.path(panel_id, rid)
        if not p.exists():
            hits = [q for q in self.dir_for(panel_id).glob(f"{rid}*.json")]
            if len(hits) != 1:
                raise FileNotFoundError(f"no single run {rid!r} for {panel_id} ({len(hits)} matches)")
            p = hits[0]
        return json.loads(p.read_text())

    def list(self, panel_id: str) -> list[dict[str, Any]]:
        d = self.dir_for(panel_id)
        if not d.is_dir():
            return []
        recs = [json.loads(p.read_text()) for p in d.glob("*.json")]
        return sorted(recs, key=lambda r: (r.get("created_at") or "", r["id"]))

    def latest(self, panel_id: str) -> dict[str, Any] | None:
        recs = self.list(panel_id)
        return recs[-1] if recs else None

    # -- record builders ---------------------------------------------------
    def record_run(
        self,
        panel_id: str,
        data: PanelData,
        *,
        seed: int,
        config_name: str | None,
        config: dict[str, Any],
        git: dict[str, Any],
        elapsed_s: float | None = None,
        extraction: str | None = None,
    ) -> dict[str, Any]:
        ch = config_hash(config)
        rec = {
            "id": run_id(git, ch, seed),
            "panel": panel_id,
            "source": "run",
            "seed": seed,
            "config_name": config_name,
            "config": config,
            "config_hash": ch,
            "git": git,
            "created_at": now(),
            "elapsed_s": elapsed_s,
            "data": data.to_json(),
            "checks": [],
        }
        if extraction:   # the paper extraction (source/manifest.json) in place when this ran
            rec["extraction"] = extraction
        prev = self.path(panel_id, rec["id"])
        if prev.exists():  # same commit + config + seed: keep the check history, count the rerun
            old = json.loads(prev.read_text())
            rec["checks"] = old.get("checks") or []
            rec["reruns"] = int(old.get("reruns", 0)) + 1
            rec["first_created_at"] = old.get("first_created_at") or old.get("created_at")
        self.save(rec)
        return rec

    def record_import(self, panel_id: str, data: PanelData, *, source: Path, info: dict[str, Any]) -> dict[str, Any]:
        sha = hashlib.sha256(Path(source).read_bytes()).hexdigest()
        rid = f"imp-{sha[:12]}"
        existing = self.path(panel_id, rid)
        if existing.exists():
            return json.loads(existing.read_text())
        config = info.get("config")
        rec = {
            "id": rid,
            "panel": panel_id,
            "source": "import",
            "imported_from": str(source),
            "imported_sha256": sha,
            "seed": info.get("seed"),
            "config_name": None,
            "config": config,
            "config_hash": config_hash(config) if config is not None else None,
            "git": {"commit": info.get("commit"), "dirty": None},
            "created_at": now(),
            "data": data.to_json(),
            "checks": [],
        }
        self.save(rec)
        return rec

    def add_check(self, rec: dict[str, Any], report: GateReport) -> dict[str, Any]:
        rec.setdefault("checks", []).append({"at": now(), "gates_hash": report.gates_hash, "report": report.to_json()})
        self.save(rec)
        return rec


def last_report(rec: dict[str, Any]) -> dict[str, Any] | None:
    checks = rec.get("checks") or []
    return checks[-1]["report"] if checks else None
