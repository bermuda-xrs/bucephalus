"""A8 — the theme a surface reads, and the banner it may only display.

THE BANNER NEVER ENTERS A PROMPT. It is loaded here, by a module that no
prompt-building code imports, and `tests/test_tui.py` greps every prompt sent
during a real task to prove it. That is not paranoia about ASCII art: the
banner is the most obviously decorative thing in the system, so if it can
reach a prompt then anything can, and the stable prefix would be carrying
bytes nobody thinks of as content.

GLYPH DEGRADATION IS ABOUT THE TERMINAL, NOT THE THEME. A theme names colours
and a spinner style; whether the box-drawing characters in the banner or the
braille spinner frames actually render is a property of the font the user
happens to have. So the check is made against the ENCODING the terminal
reports, and the fallback is plain ASCII rather than a different theme.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

DEFAULT_COLORS = {
    "accent": "#7DB4C8",
    "success": "#88C070",
    "warning": "#D9A441",
    # Integrity alerts ONLY. Reserved, and used for nothing else in any
    # surface — a broken hash chain must never look like a permission request,
    # because you will eventually approve one by muscle memory.
    "danger": "#FF4B4B",
    "muted": "#7A7A7A",
    "background": "#0E1116",
}

BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
ASCII_SPINNER = ["-", "\\", "|", "/"]

PLAIN_BANNER = "alpha test 1"


def supports_glyphs(stream=None) -> bool:
    """Can this terminal render the banner and the braille spinner?

    Asked of the ENCODING rather than of a platform or a TERM string: a
    terminal that cannot encode the character cannot show it, whatever it
    calls itself, and one that can usually will. Errors mean no — a surface
    that guesses wrong here produces mojibake, and mojibake in a status line
    is worse than plain text.
    """
    enc = getattr(stream or sys.stdout, "encoding", None) or ""
    if not enc:
        return False
    try:
        "⠋▄α".encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def load(theme_path: str | Path, root: str | Path = ".") -> dict:
    """Colours, spinner frames, and the banner path. Never raises.

    A broken theme file degrades to the default. A surface that refused to
    start because someone mistyped a hex colour would be trading a cosmetic
    problem for an operational one.
    """
    colors = dict(DEFAULT_COLORS)
    banner_rel = "./assets/banner.ans"
    p = Path(theme_path)
    if not p.is_absolute():
        p = Path(root) / p
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        colors.update(data.get("colors", {}))
        banner_rel = data.get("banner", banner_rel)
    except Exception:  # noqa: BLE001 - see the docstring
        pass
    return {"colors": colors, "banner_path": banner_rel,
            "spinner": BRAILLE if supports_glyphs() else ASCII_SPINNER}


def banner_text(theme: dict, root: str | Path = ".", glyphs: bool | None = None) -> str:
    """The banner, or a plain-ASCII stand-in. DISPLAY ONLY."""
    if glyphs is None:
        glyphs = supports_glyphs()
    if not glyphs:
        return PLAIN_BANNER
    p = Path(theme.get("banner_path", ""))
    if not p.is_absolute():
        p = Path(root) / p
    try:
        return p.read_text(encoding="utf-8", errors="replace").rstrip("\n")
    except OSError:
        return PLAIN_BANNER
