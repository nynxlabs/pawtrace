"""Colours and sizes for a front end, served at ``/api/theme``.

A front end may follow a theme file (``docs/api.md`` § Theme file) when one is around: ``--theme``
(or ``theme:`` in the user config), else ``$PAWTRACE_THEME``, else ``$XDG_CONFIG_HOME/pawtrace/theme.json``;
``$XDG_CONFIG_HOME/nynx-theme/active.json`` (same shape) is also read if present. The API
re-reads it when it changes (``/api/stamp`` reports ``updatedAt``). Without one it sends the
bundled palette below, a dark grey theme with the same token names. A host app may ignore
this and pass its own theme.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Bundled fallback: the default dark palette's 20 chrome tokens.
FALLBACK_COLORS = {
    "bg": "#1c1c1c", "sidebar": "#282828", "titleBar": "#333333", "surface": "#282828", "code": "#242424",
    "border": "#333333", "borderStrong": "#3f3f3f", "tabOutline": "#3f3f3f",
    "text": "#dadada", "textStrong": "#f0f0f0", "textMuted": "#999999", "textFaint": "#666666",
    "hover": "#2e2e2e", "selection": "#3a3a3a", "accent": "#dadada", "accentText": "#1c1c1c",
    "link": "#a68af9", "success": "#40c977", "danger": "#fa423e", "warning": "#e0ac00",
}
FALLBACK_TEXT = {
    "desktop": {"base": 13, "ui": 13, "uiLg": 14, "uiSm": 12, "uiXs": 11, "ui2xs": 10, "meta": 11, "code": 12, "tiny": 9},
    "tablet": {"base": 14, "ui": 14, "uiLg": 15, "uiSm": 13, "uiXs": 12, "ui2xs": 11, "meta": 12, "code": 13, "tiny": 9},
    "phone": {"base": 14, "ui": 14, "uiLg": 15, "uiSm": 13, "uiXs": 12, "ui2xs": 11, "meta": 12, "code": 13, "tiny": 9},
}
FALLBACK = {"id": "pawtrace-default", "label": "pawtrace default (dark)", "appearance": "dark",
            "colors": FALLBACK_COLORS, "text": FALLBACK_TEXT, "updatedAt": 0}


def default_path() -> Path | None:
    """The theme file when ``--theme`` gives none: ``$PAWTRACE_THEME``, then pawtrace's own file, then
    ``nynx-theme/active.json`` (the same shape) if present; ``None`` for the built-in theme."""
    env = os.environ.get("PAWTRACE_THEME")
    if env:
        return Path(os.path.expanduser(env))
    xdg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    for p in (xdg / "pawtrace" / "theme.json", xdg / "nynx-theme" / "active.json"):
        if p.is_file():
            return p
    return None


def resolve(explicit: str | None) -> str | None:
    """The theme file serve uses: ``--theme`` / ``theme:`` when given, else :func:`default_path`."""
    if explicit:
        return explicit
    dp = default_path()
    return str(dp) if dp else None


class Theme:
    def __init__(self, path: str | Path | None):
        self.path = Path(os.path.expanduser(str(path))) if path else None
        self._cache: tuple[float, dict[str, Any]] | None = None

    def load(self) -> dict[str, Any]:
        """The theme to send to the page; the fallback when the file is missing or broken."""
        if not self.path:
            return {**FALLBACK, "source": "bundled"}
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return {**FALLBACK, "source": "bundled"}
        if self._cache and self._cache[0] == mtime:
            return self._cache[1]
        try:
            raw = json.loads(self.path.read_text())
            colors = {**FALLBACK_COLORS, **{k: v for k, v in (raw.get("colors") or {}).items() if isinstance(v, str) or v is None}}
        except (OSError, ValueError, AttributeError):
            return {**FALLBACK, "source": "bundled"}
        out = {
            "id": raw.get("id"), "label": raw.get("label"), "appearance": raw.get("appearance") or "dark",
            "colors": colors,
            "text": raw.get("text") or FALLBACK_TEXT,
            "tabs": raw.get("tabs") or None,
            "layout": raw.get("layout") or None,
            "updatedAt": raw.get("updatedAt") or int(mtime * 1000),
            "source": "file",
        }
        self._cache = (mtime, out)
        return out
