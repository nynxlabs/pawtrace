"""A small key-value store for a front end's own state: open tabs, layout, preferences.

One JSON file per project under ``$XDG_STATE_HOME/pawtrace/ui/`` (mode 0600), written
atomically. It holds what the front end puts there and nothing pawtrace reads itself, so it
stays out of the project and out of git. Front ends should keep it to references (routes,
paths, sizes), not file contents.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
VALUE_MAX = 256 * 1024
TOTAL_MAX = 1024 * 1024


class StateError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def default_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "pawtrace" / "ui"


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def for_project(cls, name: str, directory: Path | None = None) -> "StateStore":
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "project"
        return cls((directory or default_dir()) / f"{safe}.json")

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str | None = None) -> Any:
        with self._lock:
            data = self._load()
        if key is None:
            return data
        self._check_key(key)
        return data.get(key)

    def set(self, key: str, value: Any) -> None:
        self._check_key(key)
        blob = json.dumps(value)
        if len(blob) > VALUE_MAX:
            raise StateError("value too large", 413)
        with self._lock:
            data = self._load()
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
            out = json.dumps(data, indent=1, sort_keys=True)
            if len(out) > TOTAL_MAX:
                raise StateError("state too large", 413)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state.", suffix=".tmp")
            with os.fdopen(fd, "w") as fh:
                fh.write(out)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)

    @staticmethod
    def _check_key(key: Any) -> None:
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise StateError("keys are 1-64 chars of a-z 0-9 . _ -", 400)
