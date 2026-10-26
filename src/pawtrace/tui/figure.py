"""Figure view: a PNG drawn inline with the best graphics the terminal has.

textual-image picks kitty graphics, sixel, or colored half blocks on its own.
``PAWTRACE_TUI_IMAGES`` forces one: ``kitty``, ``sixel``, ``halfcell``,
``unicode`` or ``off`` (path only). Import this module before the app starts:
textual-image asks the terminal what it supports, which it can't do once
Textual owns the screen.
"""
from __future__ import annotations

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static

MODE = os.environ.get("PAWTRACE_TUI_IMAGES", "auto").strip().lower()

_Image: type[Widget] | None = None
RENDERER = "off"
if MODE != "off":
    try:
        from textual_image import widget as _tiw

        _Image = {
            "kitty": _tiw.TGPImage,
            "sixel": _tiw.SixelImage,
            "halfcell": _tiw.HalfcellImage,
            "unicode": _tiw.UnicodeImage,
        }.get(MODE, _tiw.Image)
        RENDERER = {
            "TGPImage": "kitty graphics",
            "SixelImage": "sixel",
            "HalfcellImage": "half blocks",
            "UnicodeImage": "unicode blocks",
        }.get(getattr(_Image, "__name__", ""), "auto")
        if _Image is _tiw.Image:  # AutoImage: name the renderable it picked
            from textual_image.renderable import Image as _auto

            RENDERER = {
                "TGPImage": "kitty graphics",
                "SixelImage": "sixel",
                "HalfcellImage": "half blocks",
                "UnicodeImage": "unicode blocks",
            }.get(_auto.__name__, _auto.__name__)
    except ImportError:  # the `tui` extra brings textual-image; without it, paths only
        _Image = None
        RENDERER = "off (install the tui extra)"


class Figure(Vertical):
    """A titled figure slot. ``show(path, title, note)`` swaps what it draws."""

    DEFAULT_CSS = """
    Figure { height: 1fr; min-height: 6; }
    Figure > .fig-title { height: 1; color: $foreground; text-style: bold; }
    Figure > .fig-note { height: auto; color: $text-muted; }
    Figure > .fig-body { height: 1fr; }
    Figure > .fig-empty { height: 1fr; color: $text-muted; content-align: center middle; border: dashed $border; }
    """

    def __init__(self, title: str = "", path: Path | None = None, note: str = "", **kw) -> None:
        super().__init__(**kw)
        self._title, self._path, self._note = title, path, note

    def _parts(self) -> list[Widget]:
        has = bool(self._path and self._path.is_file())
        return [Static(self._title, classes="fig-title"), *self._body(), Static(self._note if has else "", classes="fig-note")]

    def compose(self) -> ComposeResult:
        yield from self._parts()

    def _body(self) -> list[Widget]:
        p = self._path
        if p and p.is_file() and _Image is not None:
            return [_Image(self._flatten(p), classes="fig-body")]
        if p and p.is_file():
            return [Static(f"{p}\n\n(figures inline need the tui extra: textual-image)", classes="fig-empty")]
        return [Static(self._note or "no figure", classes="fig-empty")]

    def _flatten(self, p: Path):
        """Transparent PNGs (the ``_dark`` renders) composited onto the theme background:
        half-block and sixel output have no alpha, so transparency would show as white."""
        try:
            from PIL import Image as PILImage

            im = PILImage.open(p)
            if im.mode not in ("RGBA", "LA", "P"):
                return str(p)
            im = im.convert("RGBA")
            try:
                bg = self.app.current_theme.background or "#000000"
            except Exception:
                bg = "#000000"
            base = PILImage.new("RGBA", im.size, bg)
            base.alpha_composite(im)
            return base.convert("RGB")
        except Exception:
            return str(p)

    def show(self, path: Path | None, title: str = "", note: str = "") -> None:
        self._path, self._title, self._note = path, title, note
        if self.is_mounted:
            self.remove_children()
            self.mount_all(self._parts())
