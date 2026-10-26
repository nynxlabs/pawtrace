"""nynxlabs look for the TUI: the theme, the pixel mark and the wordmark.

Monochrome on black like the nynxlabs site: light grey text, dim grey for
secondary text. Status reads from glyphs and weight, never from hue, so a light
theme (or a terminal without color) shows the same thing. The mark is a two-tone
yin-yang drawn in the foreground color, so it inverts cleanly on a light theme.

The art is the hand-drawn terminal logo (nynxlabs LLC, same license as this repo).
"""
from __future__ import annotations

from textual.theme import Theme

FG = "#e6e6e6"
DIM = "#7a7a7a"
BG = "#000000"

DARK = Theme(
    name="nynxlabs",
    dark=True,
    primary=FG,
    secondary=DIM,
    accent=FG,
    foreground=FG,
    background=BG,
    surface=BG,
    panel="#141414",
    boost="#1e1e1e",
    success=FG,
    warning=FG,
    error=FG,
    variables={
        "text-muted": DIM,
        "border": "#3a3a3a",
        "border-blurred": "#262626",
        "block-cursor-background": FG,
        "block-cursor-foreground": BG,
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": "#2a2a2a",
        "block-cursor-blurred-foreground": FG,
        "footer-key-foreground": FG,
        "footer-description-foreground": DIM,
        "footer-background": BG,
        "input-cursor-background": FG,
        "input-cursor-foreground": BG,
        "scrollbar": "#2a2a2a",
        "scrollbar-hover": DIM,
        "scrollbar-active": FG,
        "scrollbar-background": BG,
    },
)

LIGHT = Theme(
    name="nynxlabs-light",
    dark=False,
    primary="#1a1a1a",
    secondary="#6b6b6b",
    accent="#1a1a1a",
    foreground="#1a1a1a",
    background="#ffffff",
    surface="#ffffff",
    panel="#ececec",
    boost="#e0e0e0",
    success="#1a1a1a",
    warning="#1a1a1a",
    error="#1a1a1a",
    variables={
        "text-muted": "#6b6b6b",
        "border": "#c4c4c4",
        "block-cursor-background": "#1a1a1a",
        "block-cursor-foreground": "#ffffff",
        "block-cursor-text-style": "none",
        "footer-background": "#ffffff",
        "footer-key-foreground": "#1a1a1a",
        "footer-description-foreground": "#6b6b6b",
    },
)

THEMES = (DARK, LIGHT)

# Pixel mark, 16 px wide / 8 rows (half blocks).
MARK16 = """\
   ▄▄█▀▀▀▀█▄▄
 ▄████▄█▄█ ▄██▄
▄██▄▄▄█▀▀█ ▀▀██▄
██▄▄▄▄█▀▀     ▀█
█▀█████▀▀ ▄▄▄▄ █
▀█ ▀▀█ ▀▀ ▄▄▄ █▀
 ▀█▄▄█ ▄ ▄  ▄█▀
   ▀▀██████▀▀
"""

# Pixel mark, 24 px wide / 12 rows.
MARK24 = """\
      ▄▄▄▀▀▀▀▀▀▄▄▄
    ▄███▄█▄ █▄   ▀▀▄
  ▄██▄▄▀▀█████ ▄▄▄▄▄▀▄
 ██▀▀▀▀▀▀██▄▄█ ▀███████
▄█████▀▀▀███▄▀      ▀▀█▄
███▄▄▄███▄            ▀█
█▀████████████▄   ▄▄▄  █
▀▄▀▀██████▀▄   ▀▀▀    ▄▀
 █▄     ▀█ ▄▄  ▀▀▀▀▀▀▄█
  ▀█▄▄▄▄▄█     ▀▀▄▄ ▄▀
    ▀█████▄ █▄ ▄ ▄▄▀
      ▀▀▀██████▀▀▀
"""

# Pixel font of the nynxlabs wordmark (lowercase, 1 px stems, 4 px letters, 1 px
# apart; rows 0-1 ascender, 2-6 x-height, 7-8 descender). n y x l a b s / are the
# logo's own glyphs; the rest extend it so product names can follow the slash.
GLYPHS = {
    "n": ["", "", "###.", "#..#", "#..#", "#..#", "#..#", "", ""],
    "y": ["", "", "#..#", "#..#", "#..#", "#..#", ".###", "...#", "###."],
    "x": ["", "", "#..#", "#..#", ".##.", "#..#", "#..#", "", ""],
    "l": ["#", "#", "#", "#", "#", "#", "#", "", ""],
    "a": ["", "", "###.", "...#", ".###", "#..#", ".###", "", ""],
    "b": ["#...", "#...", "###.", "#..#", "#..#", "#..#", "###.", "", ""],
    "s": ["", "", ".###", "#...", ".##.", "...#", "###.", "", ""],
    "/": ["..#", "..#", "..#", ".#.", ".#.", ".#.", "#..", "#..", "#.."],
    "p": ["", "", "###.", "#..#", "#..#", "#..#", "###.", "#...", "#..."],
    "w": ["", "", "#...#", "#...#", "#.#.#", "#.#.#", ".#.#.", "", ""],
    "t": ["", ".#..", "####", ".#..", ".#..", ".#..", "..##", "", ""],
    "r": ["", "", "#.##", "##..", "#...", "#...", "#...", "", ""],
    "c": ["", "", ".###", "#...", "#...", "#...", ".###", "", ""],
    "e": ["", "", ".##.", "#..#", "####", "#...", ".###", "", ""],
    "d": ["...#", "...#", ".###", "#..#", "#..#", "#..#", ".###", "", ""],
    "i": ["#", "", "#", "#", "#", "#", "#", "", ""],
    "v": ["", "", "#...#", "#...#", ".#.#.", ".#.#.", "..#..", "", ""],
    "-": ["", "", "", "", "###", "", "", "", ""],
}


def wordmark(text: str = "nynxlabs/", gap: int = 1) -> str:
    """``text`` in the wordmark's pixel font, as half blocks (5 rows)."""
    rows: list[list[bool]] = [[] for _ in range(10)]  # 9 px + 1 blank so half blocks pair up
    for i, ch in enumerate(text):
        g = GLYPHS[ch]
        w = max(len(r) for r in g)
        for y in range(10):
            r = g[y] if y < len(g) else ""
            rows[y] += [c == "#" for c in r.ljust(w, ".")] + ([False] * gap if i < len(text) - 1 else [])
    out = []
    for y in range(0, 10, 2):
        out.append(
            "".join("█" if t and b else "▀" if t else "▄" if b else " " for t, b in zip(rows[y], rows[y + 1])).rstrip()
        )
    return "\n".join(out)


WORDMARK = wordmark("nynxlabs/")


# 80 px braille mark for the about screen (40 columns, 20 rows).
SPLASH = """\
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣤⣴⣶⣶⣿⣿⣿⣿⣿⣿⣶⣶⣦⣤⣀
⠀⠀⠀⠀⠀⠀⠀⠀⢀⣤⣶⣿⣿⣿⣿⣿⠟⠋⠁⢨⠏⠀⠀⠀⠉⠉⠛⠿⢿⣶⣤⡀
⠀⠀⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣦⣠⣿⣤⣤⣶⠂⠀⠀⠀⠀⠀⠉⠻⢿⣷⣄
⠀⠀⠀⠀⣠⣾⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠙⢿⣷⣄
⠀⠀⠀⣴⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣇⠀⠀⣠⣶⣾⣿⣿⣿⣶⣤⣀⠹⣿⣦
⠀⠀⣼⣿⣿⣿⣿⣿⣿⠿⠿⠶⢶⣭⣛⠿⣿⣿⡟⠿⣿⣿⣿⠀⠀⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣾⣿⣧
⠀⣼⣿⣿⣿⣿⣿⣿⣿⠿⠿⠿⠿⠶⠮⠭⣒⣿⣿⣦⣽⣿⡏⠀⠀⠀⠉⠉⠉⠉⠉⠉⠛⠿⣿⣿⣿⣿⣧
⢰⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡶⣿⣿⣿⣿⠛⡇⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⠙⣿⣿⣿⡆
⣼⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠁⠀⠀⠈⠹⠛⠋⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢿⣿⣧
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣤⣄⣀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⣿⣿
⣿⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣶⣶⣦⣤⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿
⢻⣿⠈⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⠟⠛⠹⢿⣿⣿⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⡟
⠸⣿⡇⠀⠻⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡇⠛⠀⠀⠀⠀⣁⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⣿⠇
⠀⢻⣿⡄⠀⠀⠉⠛⠿⠿⠿⠿⠿⠿⣿⣿⣿⠇⠀⠠⣄⠀⠀⣒⠭⢍⣉⠉⠉⠉⠉⠀⠀⠀⠀⠀⢠⣿⡟
⠀⠀⢻⣷⣄⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣿⣿⠀⠀⠀⠉⠃⠀⠀⠉⠒⠤⣈⣉⠉⠉⠀⠀⠀⠀⢠⣾⡟
⠀⠀⠀⠻⣿⣿⣶⣤⣀⠀⠀⠀⢀⣀⣴⣿⣿⡆⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣰⣿⠟
⠀⠀⠀⠀⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣠⣾⡿⠋
⠀⠀⠀⠀⠀⠀⠙⢿⣿⣿⣿⣿⣿⣿⣿⣿⣟⣀⣤⣤⠀⣴⣄⠀⠀⠀⠀⠀⠀⣀⣴⣾⡿⠋
⠀⠀⠀⠀⠀⠀⠀⠀⠈⠛⠿⣿⣿⣿⣿⣿⣿⣿⣿⠏⢼⡿⠟⠋⣀⣀⣤⣶⣾⠿⠛⠁
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠻⠿⠿⣿⣿⣿⣿⣿⣿⠿⠿⠟⠛⠉
"""


def lockup(mark: str = MARK16, word: str = WORDMARK, *, gap: int = 3, offset: int = 2) -> list[str]:
    """Mark beside the wordmark; ``offset`` rows down so the x-height sits on the mark's middle."""
    m = mark.split("\n")
    w = word.split("\n")
    mw = max(len(line) for line in m)
    rows = max(len(m), offset + len(w))
    out = []
    for i in range(rows):
        left = (m[i] if i < len(m) else "").ljust(mw)
        right = w[i - offset] if 0 <= i - offset < len(w) else ""
        out.append((left + " " * gap + right).rstrip())
    return out


# Status glyphs (shape carries the meaning; no color needed).
PASS = "●"
FAIL = "○"
PARTIAL = "◐"
NONE = "·"
