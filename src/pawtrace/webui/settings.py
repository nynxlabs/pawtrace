"""The Settings page's API over the user config (``ui.yaml``, shown as ``pawtrace/settings.yaml``).

- ``GET /api/settings`` → ``{values, version, exists, writable, project, overrides, pending, comments}``
- ``POST /api/settings`` ``{values, version}`` → the same, after a checked write

``values`` holds the machine-wide keys plus this project's notes folders
(``projects.<name>.notes``). Saving merges them into the file: keys the page doesn't know (and
other projects) are kept, an empty value removes its key. The write is a compare-and-swap on
``version`` (the file's sha256, or ``"new"``), like the file API's. ``edit_cmd``, ``by`` and
``hide`` (name globs the file tree skips) apply at once; the rest need ``pawtrace serve``
restarted, and ``pending`` names the ones whose saved value isn't the running one yet.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..localweb import BadRequest
from .files import FilesError, sha256_bytes
from .server import ORIGIN_RE

if TYPE_CHECKING:
    from .server import UiState

STR_KEYS = ("edit_cmd", "theme", "by")
BOOL_KEYS = ("read_only", "write_checkout", "branches")
LIST_KEYS = ("frame_ancestors", "hide")
LIVE = ("edit_cmd", "by", "hide")  # applied without a restart
HEADER = "# pawtrace's settings on this machine (ui.yaml), written by the Settings page. Edit freely.\n"


def _read(state: "UiState") -> tuple[dict[str, Any], str, bool, bool]:
    """(the parsed file, its version, it exists, it has comments). A broken file is a 400."""
    sp = state.settings
    if sp is None:
        raise BadRequest("this server has no settings file", 404)
    p = sp.root / sp.label
    if not p.is_file():
        return {}, "new", False, False
    raw = p.read_bytes()
    try:
        data = yaml.safe_load(raw.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise BadRequest(f"settings.yaml doesn't parse ({exc}); fix it as YAML", 400) from None
    if not isinstance(data, dict):
        raise BadRequest("settings.yaml isn't a mapping; fix it as YAML", 400)
    comments = any(line.lstrip().startswith("#") for line in raw.decode("utf-8").splitlines()[1:])
    return data, sha256_bytes(raw), True, comments


def _project(data: dict[str, Any], name: str) -> dict[str, Any]:
    projects = data.get("projects")
    per = projects.get(name) if isinstance(projects, dict) else None
    return per if isinstance(per, dict) else {}


def _values(data: dict[str, Any], name: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in STR_KEYS:
        v = data.get(k)
        out[k] = v if isinstance(v, str) else ""
    for k in BOOL_KEYS:
        out[k] = bool(data.get(k))
    for k in LIST_KEYS:
        v = data.get(k)
        out[k] = [str(x) for x in v] if isinstance(v, list) else []
    notes = _project(data, name).get("notes")
    out["notes"] = [str(x) for x in notes] if isinstance(notes, list) else []
    return out


def _running(state: "UiState") -> dict[str, Any]:
    c = state.cfg
    return {"edit_cmd": c.edit_cmd or "", "theme": c.theme or "", "by": c.by or "",
            "read_only": c.read_only, "write_checkout": c.write_checkout, "branches": c.branches,
            "frame_ancestors": list(c.frame_ancestors), "hide": list(c.hide), "notes": list(c.notes)}


def view(state: "UiState") -> dict[str, Any]:
    data, version, exists, comments = _read(state)
    name = state.project_name
    values = _values(data, name)
    running = _running(state)
    per = _project(data, name)
    saved = getattr(state, "settings_saved", set())
    pending = []
    for k, v in values.items():
        if k in LIVE or v == running[k]:
            continue
        # an unset key leaves serve on its flag or default; it counts once the page saved it
        if (per if k == "notes" else data).get(k) is not None or k in saved:
            pending.append(k)
    overrides = sorted(k for k in (*STR_KEYS, *BOOL_KEYS, *LIST_KEYS) if k in _project(data, name))
    return {"values": values, "version": version, "exists": exists, "comments": comments,
            "writable": bool(state.settings and state.settings.writable), "project": name,
            "file": "pawtrace/settings.yaml", "overrides": overrides, "pending": pending, "running": running}


def _clean(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise BadRequest("values must be an object")
    out: dict[str, Any] = {}
    for k in STR_KEYS:
        v = values.get(k, "")
        if not isinstance(v, str):
            raise BadRequest(f"{k} must be text")
        out[k] = v.strip()
    if out["edit_cmd"] and "{path}" not in out["edit_cmd"]:
        raise BadRequest("the editor command needs {path} where the file's path goes")
    for k in BOOL_KEYS:
        v = values.get(k, False)
        if not isinstance(v, bool):
            raise BadRequest(f"{k} must be true or false")
        out[k] = v
    for k in (*LIST_KEYS, "notes"):
        v = values.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise BadRequest(f"{k} must be a list of text")
        out[k] = [x.strip() for x in v if x.strip()]
    bad = [o for o in out["frame_ancestors"] if not ORIGIN_RE.match(o)]
    if bad:
        raise BadRequest(f"frame ancestors are origins like http://127.0.0.1:8795, not {bad[0]!r}")
    return out


def save(state: "UiState", body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise BadRequest("send {values, version}")
    values = _clean(body.get("values"))
    version = body.get("version")
    data, current, exists, _ = _read(state)
    if version != current:
        raise FilesError("the settings changed since they were read", 409)
    sp = state.settings
    assert sp is not None
    for k in STR_KEYS + LIST_KEYS:
        if values[k]:
            data[k] = values[k]
        else:
            data.pop(k, None)
    for k in BOOL_KEYS:
        if values[k]:
            data[k] = True
        else:
            data.pop(k, None)
    name = state.project_name
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    per = dict(_project(data, name))
    if values["notes"]:
        per["notes"] = values["notes"]
    else:
        per.pop("notes", None)
    if per:
        projects[name] = per
    else:
        projects.pop(name, None)
    if projects:
        data["projects"] = projects
    else:
        data.pop("projects", None)
    text = HEADER + (yaml.safe_dump(data, sort_keys=False, allow_unicode=True) if data else "")
    if sp.writable:
        Path(sp.root).mkdir(parents=True, exist_ok=True)
    before = _values(_read(state)[0], name)
    sp.write(sp.label, text, current if exists else None)
    state.settings_saved = getattr(state, "settings_saved", set()) | {k for k in values if values[k] != before[k]}
    for k in ("edit_cmd", "by"):  # a per-project value in the file still wins over the machine-wide one
        setattr(state.cfg, k, per.get(k) if isinstance(per.get(k), str) and per.get(k) else values[k] or None)
    hide = [str(x) for x in per["hide"]] if isinstance(per.get("hide"), list) else values["hide"]
    if hide != state.cfg.hide:
        state.cfg.hide = hide
        hub = getattr(state, "hub", None)
        if hub is not None:
            hub.publish("change", {"paths": [], "tree": True})  # open apps refetch the tree
    return view(state)
