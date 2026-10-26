"""Shared pieces: the brand header, the compact title bar, stage glyphs."""
from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from . import brand

STATE_GLYPH = {"done": brand.PASS, "active": brand.PARTIAL, "todo": brand.FAIL, "off": brand.NONE}


def glyph(state: str) -> str:
    return STATE_GLYPH.get(state, "?")


def verdict_text(verdict: bool | None, score: str = "") -> Text:
    """PASS reads plain, FAIL reverse video: shape and weight, never hue."""
    if verdict is None:
        return Text("—", style="dim")
    if verdict:
        return Text(f"{brand.PASS} pass {score}".rstrip())
    return Text(f" FAIL {score} ", style="reverse")


class BrandHeader(Static):
    """The pixel mark beside ``nynxlabs/<product>``; shrinks to fit the terminal.

    Wide: mark + ``nynxlabs/<product>`` in the pixel font. Narrower: mark +
    ``<product>``. Short or narrow terminals: one text line.
    """

    DEFAULT_CSS = """
    BrandHeader { height: auto; padding: 1 2 0 2; color: $foreground; }
    BrandHeader.-compact { padding: 0 1; height: 1; }
    """

    def __init__(self, product: str, subtitle: str = "", **kw) -> None:
        super().__init__(**kw)
        self.product = product
        self.subtitle = subtitle
        self._variants = [
            brand.lockup(word=brand.wordmark(f"nynxlabs/{product}")),
            brand.lockup(word=brand.wordmark(product)),
        ]

    def on_mount(self) -> None:
        self._fit()

    def on_resize(self) -> None:
        self._fit()

    def _fit(self) -> None:
        w, h = self.app.size
        lines = None
        if h >= 30:
            for v in self._variants:
                if max(len(x) for x in v) + 4 <= w:
                    lines = v
                    break
        if lines is None:
            self.add_class("-compact")
            t = Text()
            t.append("◐ ", style="bold")
            t.append(f"nynxlabs/{self.product}", style="bold")
            if self.subtitle:
                t.append(f"  {self.subtitle}", style="dim")
            self.update(t)
            return
        self.remove_class("-compact")
        t = Text("\n".join(lines))
        if self.subtitle:
            t.append(f"\n{self.subtitle}", style="dim")
        self.update(t)


class TitleBar(Static):
    """One line: where you are. ``nynxlabs/pawtrace › smith › 4a``."""

    DEFAULT_CSS = """
    TitleBar { height: 1; padding: 0 1; background: $panel; color: $foreground; }
    """

    def __init__(self, product: str, *crumbs: str, **kw) -> None:
        t = Text()
        t.append("◐ ", style="bold")
        t.append(f"nynxlabs/{product}", style="bold")
        for c in crumbs:
            t.append("  ›  ", style="dim")
            t.append(c)
        super().__init__(t, **kw)
