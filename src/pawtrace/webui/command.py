"""``pawtrace serve``: the local JSON API for one project, until Ctrl+C or ``POST /api/quit``.

Machine-specific settings (notes folders outside the repo, how to open a note in an
editor, the theme file) come from the command line or the user config
``$XDG_CONFIG_HOME/pawtrace/ui.yaml``, never from the project. The front end shows that file
as ``pawtrace/settings.yaml`` (offering :data:`SETTINGS_EXAMPLE` until it exists)::

    edit_cmd: "myeditor {path}"      # POST /api/files/open; {path} is the file's absolute path
    theme: ~/.config/some/theme.json  # a theme file (docs/api.md § Theme file)
    by: "Your Name"                   # recorded on digitizations started through the API
    read_only: false                  # true: no file writes; write_checkout: true lets the API write in checkouts
    branches: false                   # true: with a tree: map in pawtrace.yaml, also show the other checkouts
    hide: ["__init__.py"]             # file/folder name globs the file tree and find skip (still open by path)
    frame_ancestors: ["http://127.0.0.1:8795"]   # apps allowed to frame the digitize page
    projects:
      my-project:                     # the project's name: in pawtrace.yaml
        notes: [~/notes/my-project]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml

from ..localweb import announce, stop_on_sigterm
from ..project import Project
from .server import UiConfig, UiServer, UiState

SETTINGS_EXAMPLE = """\
# pawtrace's settings on this machine (ui.yaml). Uncomment what you need; restart pawtrace serve.
# edit_cmd: "myeditor {path}"      # open a note in an editor; {path} is the file's absolute path
# theme: ~/.config/some/theme.json  # a theme file (docs/api.md § Theme file)
# by: "Your Name"                   # recorded on digitizations started from the app
# read_only: false                  # true: no file writes
# write_checkout: false             # true: let the app write inside checkouts
# branches: false                   # true: with a tree: map in pawtrace.yaml, also show the other checkouts
# hide: ["__init__.py"]             # file/folder name globs the file tree skips
# projects:
#   my-project:                     # the project's name: in pawtrace.yaml
#     notes: [~/notes/my-project]
"""


def user_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pawtrace" / "ui.yaml"


def load_user_config(path: Path | None = None) -> dict[str, Any]:
    p = path or user_config_path()
    if not p.is_file():
        return {}
    try:
        cfg = yaml.safe_load(p.read_text()) or {}
    except (OSError, yaml.YAMLError) as exc:
        print(f"pawtrace serve: ignoring {p}: {exc}", file=sys.stderr)
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _globs(v: Any) -> list[str]:
    """``hide:`` as a list of name globs; one string counts as one glob."""
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []


def build_config(args: Any) -> UiConfig:
    proj = Project.find(args.project) if args.project else Project.find()
    config_path = Path(args.config).expanduser() if getattr(args, "config", None) else user_config_path()
    user = load_user_config(config_path)
    per = ((user.get("projects") or {}).get(proj.name) or {}) if isinstance(user.get("projects"), dict) else {}
    notes = list(args.notes or []) or list(per.get("notes") or [])
    return UiConfig(
        project=proj.root,
        notes=notes,
        edit_cmd=args.edit_cmd or per.get("edit_cmd") or user.get("edit_cmd"),
        theme=args.theme or per.get("theme") or user.get("theme"),
        by=args.by or per.get("by") or user.get("by"),
        frame_ancestors=list(getattr(args, "frame_ancestor", None) or per.get("frame_ancestors") or user.get("frame_ancestors") or []),
        read_only=bool(args.read_only or per.get("read_only") or user.get("read_only")),
        write_checkout=bool(args.write_checkout or per.get("write_checkout") or user.get("write_checkout")),
        frontend=getattr(args, "frontend", None) or per.get("frontend") or user.get("frontend"),
        settings=config_path,
        docs=True,
        branches=bool(per.get("branches") or user.get("branches")),
        hide=_globs(per.get("hide") if per.get("hide") is not None else user.get("hide")),
    )


def run(args: Any, emit) -> int:
    from .theme import resolve

    cfg = build_config(args)
    cfg.theme = resolve(cfg.theme)
    state = UiState(cfg)
    server = UiServer(state, port=args.port or 0)
    url = server.url
    announce("pawtrace serve", url, url_file=args.url_file)
    print(f"  project {state.root} · {len(state.checkouts)} checkout(s) · {len(state.roots)} notes folder(s)"
          "  ·  Ctrl+C or POST /api/quit to stop", file=sys.stderr, flush=True)
    stop_on_sigterm()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    emit({"stopped": True}, "pawtrace serve stopped")
    return 0
