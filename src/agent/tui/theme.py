from __future__ import annotations

import sys
import tomllib
from pathlib import Path

DEFAULT_COLORS = {
    "accent": "#7DB4C8",
    "success": "#88C070",
    "warning": "#D9A441",
    "danger": "#FF4B4B",
    "muted": "#7A7A7A",
    "background": "#0E1116",
}

BRAILLE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
ASCII_SPINNER = ["-", "\\", "|", "/"]

PLAIN_BANNER = "alpha test 1"


def supports_glyphs(stream=None) -> bool:
    enc = getattr(stream or sys.stdout, "encoding", None) or ""
    if not enc:
        return False
    try:
        "⠋▄α".encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def load(theme_path: str | Path, root: str | Path = ".") -> dict:
    colors = dict(DEFAULT_COLORS)
    banner_rel = "./assets/banner.ans"
    p = Path(theme_path)
    if not p.is_absolute():
        p = Path(root) / p
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        colors.update(data.get("colors", {}))
        banner_rel = data.get("banner", banner_rel)
    except Exception:  # noqa: BLE001
        pass
    return {"colors": colors, "banner_path": banner_rel,
            "spinner": BRAILLE if supports_glyphs() else ASCII_SPINNER}


def banner_text(theme: dict, root: str | Path = ".", glyphs: bool | None = None) -> str:
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
