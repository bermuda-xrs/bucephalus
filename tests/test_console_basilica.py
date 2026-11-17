from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from test_console_reskin_guards import CONTROL, HTML, UI

CSS = (CONTROL / "ui.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _node(script: str):
    if NODE is None:
        pytest.skip("node is not installed")
    src = UI[UI.index("const roman ="):UI.index("function roseStart(")]
    return json.loads(subprocess.run([NODE, "-e", src + script], capture_output=True, text=True, check=True,
                                     encoding="utf-8").stdout)


def test_plan_steps_are_numbered_in_roman_numerals():
    out = _node("process.stdout.write(JSON.stringify([1, 4, 9, 14, 40, 49, 90].map(roman)));")
    assert out == ["I", "IV", "IX", "XIV", "XL", "XLIX", "XC"]
    assert "${roman(i + 1)}" in UI


def test_the_rose_window_is_a_fixed_grid_with_still_stone_and_moving_glass():
    out = _node("""
const a = roseFrame(1, 1), b = roseFrame(2.5, 1), boot = roseFrame(0, 0);
process.stdout.write(JSON.stringify({w: ROSE.w, h: ROSE.h, a, b, boot}));""")
    w, h = out["w"], out["h"]
    for layer in out["a"] + out["b"] + out["boot"]:
        rows = layer.split("\n")
        assert len(rows) == h and all(len(r) == w for r in rows)
    (glass_a, stone_a), (glass_b, stone_b), (_, stone_boot) = out["a"], out["b"], out["boot"]
    assert stone_a == stone_b, "the tracery moved"
    assert glass_a != glass_b, "the glass is still"
    assert stone_a.strip() and glass_a.strip()
    assert not stone_boot.strip(), "the stone shows before the window has assembled"
    rows = stone_a.split("\n")
    assert rows[0][0] == rows[0][-1] == rows[-1][0] == rows[-1][-1] == " ", "the window is not round"
    assert rows[h // 2][w // 2] != " ", "the window has no hub"
    for g, s in zip(glass_a, stone_a):
        assert g == " " or s == " " or g == "\n", "glass drawn over stone"


def test_the_ascii_art_is_hidden_from_screen_readers():
    assert re.search(r'<div class="frieze" id="frieze" aria-hidden="true">', HTML)
    welcome = UI[UI.index("function renderWelcome()"):UI.index("async function selectConversation(")]
    assert welcome.count('aria-hidden="true"') >= 1 and "roseGlass" in welcome and "roseStone" in welcome


def test_the_rose_does_not_restart_while_it_is_showing_and_stops_for_reduced_motion():
    welcome = UI[UI.index("function renderWelcome()"):UI.index("async function selectConversation(")]
    assert '$("#items .welcome")' in welcome
    start = UI[UI.index("function roseStart("):]
    start = start[:start.index("\n}\n")]
    assert "prefers-reduced-motion: reduce" in start and "isConnected" in start and "document.hidden" in start


def _outside_motion_blocks(css: str) -> str:
    out, i = [], 0
    for m in re.finditer(r"@media \(prefers-reduced-motion: no-preference\) \{", css):
        if m.start() < i:
            continue
        out.append(css[i:m.start()])
        depth, j = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        i = j
    return "".join(out) + css[i:]


def test_nothing_moves_for_someone_who_asked_for_no_motion():
    rest = _outside_motion_blocks(re.sub(r"/\*.*?\*/", "", CSS, flags=re.S))
    assert "animation" not in rest and "@keyframes" not in rest
    assert not re.search(r"transition:(?!\s*(opacity|border-color|color|background(-color)?)\b)", rest)


def test_the_page_loads_nothing_from_outside():
    assert "@font-face" not in CSS and "url(" not in CSS and "@import" not in CSS


def test_the_look_is_the_basilica():
    root = CSS[CSS.index(":root {"):CSS.index("}", CSS.index(":root {"))]
    assert "--display:" in root and "--body:" in root and "--r: 0" in root
    assert ".frieze" in CSS and ".rose" in CSS and ".card::before" in CSS
