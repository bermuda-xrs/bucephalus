from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.paths import user_data_dir
from test_console_reskin_guards import CONTROL, HTML, UI, _hex, _palettes, _ratio

CSS = (CONTROL / "ui.css").read_text(encoding="utf-8")
BARE = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
NODE = shutil.which("node")
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x40\x00\x00\x00WEBPVP8 " + b"\x00" * 64


def _node(start: str, end: str, script: str):
    if NODE is None:
        pytest.skip("node is not installed")
    src = UI[UI.index(start):UI.index(end)]
    return json.loads(subprocess.run([NODE, "-e", src + script], capture_output=True, text=True, check=True,
                                     encoding="utf-8").stdout)


def test_plan_steps_are_numbered_in_roman_numerals():
    out = _node("const roman =", "const FX =",
                "process.stdout.write(JSON.stringify([1, 4, 9, 14, 40, 49, 90].map(roman)));")
    assert out == ["I", "IV", "IX", "XIV", "XL", "XLIX", "XC"]
    assert "${roman(i + 1)}" in UI


# ---------------------------------------------------------------- no character-drawn graphics

def test_nothing_is_drawn_in_characters():
    page = HTML + UI + CSS
    for gone in ("frieze", "roseFrame", "roseStart", "ROSE", 'class="rose"', ".rose", "function decode("):
        assert gone not in page, f"{gone} is still there"
    assert not re.search(r"[─-▟]", page), "box-drawing characters"
    welcome = UI[UI.index("function renderWelcome()"):UI.index("async function selectConversation(")]
    assert "<pre" not in welcome


# ---------------------------------------------------------------- glass

def _root(scheme: str) -> dict[str, str]:
    def block(css, sel):
        i = css.find(sel + " {")
        return {} if i < 0 else dict(re.findall(r"--([\w-]+):\s*([^;]+);", css[i:css.index("}", i)]))
    m = re.search(r"@supports \(\(backdrop-filter: blur\(1px\)\) or \(-webkit-backdrop-filter: blur\(1px\)\)\) \{", BARE)
    assert m, "the translucent values must apply only where the backdrop can be filtered"
    inside = BARE[m.end():]
    out = {**block(BARE, ":root"), **block(inside, ":root")}
    if scheme == "light":
        out |= {**block(BARE, ':root[data-theme="light"]'), **block(inside, ':root[data-theme="light"]')}
    return {k: v.strip() for k, v in out.items()}


def test_without_backdrop_filter_the_surfaces_are_solid():
    first = BARE[BARE.index(":root {"):BARE.index("}", BARE.index(":root {"))]
    assert re.search(r"--glass:\s*100%;", first)


def test_every_surface_is_glass_and_square():
    assert re.search(r'<canvas id="bg" aria-hidden="true"></canvas>\s*<div class="app', HTML)
    bg = re.search(r"#bg\s*\{([^}]*)\}", BARE).group(1)
    assert "pointer-events: none" in bg and "position: fixed" in bg and re.search(r"z-index:\s*-1", bg)
    glass = re.search(r"\n([^{}]*\.nav[^{}]*)\{([^}]*backdrop-filter:[^}]*)\}", BARE)
    assert glass, "no glass rule"
    panes, body = glass.groups()
    for pane in (".nav", ".side", ".thread", ".trace", ".page", ".files", ".pal", "#toast", ".more-menu"):
        assert re.search(re.escape(pane) + r"\s*[,{]", panes + "{"), f"{pane} is not glass"
    want = "blur(var(--blur)) contrast(var(--back-contrast)) brightness(var(--back-bright))"
    assert f"backdrop-filter: {want}" in body and f"-webkit-backdrop-filter: {want}" in body
    assert "color-mix(in srgb, var(--ground) var(--glass), transparent)" in body
    assert re.search(r"body\s*\{[^}]*background:\s*(none|transparent)", BARE), "the page hides the image"
    for name in ("panel", "raised"):
        assert f"--g-{name}: color-mix(in srgb, var(--{name}) var(--glass), transparent)" in BARE
    solid = [(s.strip(), d.strip()) for s, body in re.findall(r"([^{}]+)\{([^{}]*)\}", BARE)
             for d in re.findall(r"background(?:-color)?:\s*([^;]*var\(--(?:ground|panel|raised)\)[^;]*)", body)
             if not re.fullmatch(r"(html|\.ask-bubble|\.skip|\.integrity(-row td)?)", s.strip())
             and "color-mix" not in d]
    assert not solid, f"opaque surfaces: {solid}"


def test_no_corner_is_rounded():
    root = _root("dark")
    assert root["r"] == "0" and root["r-sm"] == "0"
    bad = [(s.strip(), v) for s, body in re.findall(r"([^{}]+)\{([^{}]*)\}", BARE)
           for v in re.findall(r"border-radius:\s*([^;]+)", body)
           if not re.fullmatch(r"(0|var\(--r(-sm)?\)|0 var\(--r-sm\) var\(--r-sm\) 0)", v.strip())
           and not (v.strip() == "50%" and s.strip().startswith(".lg"))]
    assert not bad, bad


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_text_reads_on_glass_over_any_image(scheme):
    t, g = _palettes()[scheme], _root(scheme)
    a, c, b = float(g["glass"].rstrip("%")) / 100, float(g["back-contrast"]), float(g["back-bright"])
    assert a < 0.7, "that is not glass"
    behind = ((.5 - .5 * c) * b, min(1.0, (.5 + .5 * c) * b))
    low = []
    for bg in ("ground", "panel", "raised"):
        for w in behind:
            comp = "#" + "".join(f"{round((a * x + (1 - a) * w) * 255):02x}" for x in _hex(t[bg]))
            low += [(fg, bg, round(w, 2), round(_ratio(t[fg], comp), 2))
                    for fg in ("text", "dim", "accent", "needs", "done", "primary") if _ratio(t[fg], comp) < 4.5]
    assert not low, f"{scheme}: below WCAG AA on glass: {low}"


def test_the_integrity_colour_keeps_a_solid_surface():
    for sel in (r"\.integrity", r"\.integrity-row td"):
        body = " ".join(re.findall(sel + r"\s*\{([^}]*)\}", BARE))
        assert re.search(r"background:\s*var\(--(ground|panel|raised)\)", body), sel


# ---------------------------------------------------------------- the effect under the cursor

def _fx(script: str):
    return _node("const FX =", "const BG =", script)


def test_each_click_makes_the_effect_stronger_up_to_a_cap():
    out = _fx("""
const s = fxNew(); const seen = [];
for (let i = 0; i < 12; i++) { fxClick(s, 10, 10); seen.push(s.lvl); }
process.stdout.write(JSON.stringify({seen, max: FX.max}));""")
    seen = out["seen"]
    assert seen[0] > 0 and all(b > a for a, b in zip(seen, seen[1:out["max"]]))
    assert max(seen) == out["max"] == seen[-1]


def test_the_effect_fades_and_the_loop_stops_when_nothing_happens():
    out = _fx("""
const s = fxNew(); fxMove(s, 5, 5); fxClick(s, 5, 5); fxClick(s, 5, 5);
const live = fxStep(s, 0.016), mid = s.lvl;
let n = 0; while (fxStep(s, 0.05) && n < 100000) n++;
process.stdout.write(JSON.stringify({live, mid, end: s.lvl, energy: s.energy, n}));""")
    assert out["live"] is True and 1.5 < out["mid"] <= 2
    assert out["n"] < 100000, "the loop never rests"
    assert out["end"] < 0.01 and out["energy"] < 0.01


def test_the_effect_is_under_the_page_and_stops_for_reduced_motion_and_hidden_tabs():
    bg = UI[UI.index("const BG ="):]
    bg = bg[:bg.index("\n})();") + 6]
    assert "prefers-reduced-motion: reduce" in bg and "visibilitychange" in bg and "document.hidden" in bg
    assert "cancelAnimationFrame" in bg
    assert "getContext(\"webgl\"" in bg
    assert "pointerdown" in bg and "pointermove" in bg
    assert not re.search(r"\bcv\.addEventListener", bg), "the canvas takes no input"


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
    rest = _outside_motion_blocks(BARE)
    assert "animation" not in rest and "@keyframes" not in rest
    assert not re.search(r"transition:(?!\s*(opacity|border-color|color|background(-color)?)\b)", rest)


def test_the_page_loads_nothing_from_outside():
    assert "@font-face" not in CSS and "url(" not in CSS and "@import" not in CSS
    assert not re.search(r"""["'`](https?:)?//""", UI[UI.index("const FX ="):])


# ---------------------------------------------------------------- the background image

@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def _put(api, data: bytes):
    return api.put("/background", json={"content_b64": base64.b64encode(data).decode()})


def test_there_is_no_background_until_one_is_chosen(api):
    assert api.get("/background").status_code == 404


@pytest.mark.parametrize("data,media", [(PNG, "image/png"), (JPEG, "image/jpeg"), (WEBP, "image/webp")])
def test_a_chosen_image_is_kept_in_the_user_data_dir_and_served_back(api, ctx, data, media):
    assert _put(api, data).status_code == 200
    r = api.get("/background")
    assert r.status_code == 200 and r.content == data and r.headers["content-type"] == media
    assert r.headers["x-content-type-options"] == "nosniff"
    kept = [p for p in user_data_dir().rglob("*") if p.is_file() and p.read_bytes() == data]
    assert len(kept) == 1
    again = TestClient(create_app(ctx), base_url="http://127.0.0.1")
    again.headers["Authorization"] = api.headers["Authorization"]
    assert again.get("/background").content == data, "it did not survive a restart"


def test_a_new_image_replaces_the_old_one(api):
    _put(api, PNG)
    _put(api, JPEG)
    assert api.get("/background").content == JPEG
    assert sum(1 for p in (user_data_dir() / "console").iterdir()) == 1


@pytest.mark.parametrize("data", [
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    b"<?xml version='1.0'?><svg/>", b"GIF89a" + b"\x00" * 32, b"<html><script>x</script></html>", b"", b"MZ\x90\x00",
    b"RIFF\x40\x00\x00\x00WAVEfmt ",
])
def test_only_png_jpeg_and_webp_are_accepted(api, data):
    assert _put(api, data).status_code == 415
    assert api.get("/background").status_code == 404


def test_an_image_over_the_cap_is_refused(api):
    from agent.control import background

    assert _put(api, PNG + b"\x00" * background.MAX_BYTES).status_code == 413
    assert api.put("/background", json={"content_b64": "not base64!"}).status_code == 400
    assert api.get("/background").status_code == 404


def test_the_image_can_be_removed(api):
    _put(api, PNG)
    assert api.delete("/background").status_code == 200
    assert api.get("/background").status_code == 404
    assert api.delete("/background").status_code == 200


def test_the_image_needs_the_token(api):
    _put(api, PNG)
    anon = TestClient(api.app, base_url="http://127.0.0.1")
    assert anon.get("/background").status_code == 401
    assert anon.put("/background", json={"content_b64": base64.b64encode(PNG).decode()}).status_code == 401
    assert anon.delete("/background").status_code == 401


def test_the_config_image_shows_until_one_is_chosen_and_must_be_an_image_too(api, ctx, tmp_path):
    f = tmp_path / "bg.png"
    f.write_bytes(PNG)
    ctx.config.ui.background_image = str(f)
    assert api.get("/background").content == PNG
    _put(api, JPEG)
    assert api.get("/background").content == JPEG
    api.delete("/background")
    assert api.get("/background").content == PNG
    f.write_bytes(b"<svg/>")
    assert api.get("/background").status_code == 404
    ctx.config.ui.background_image = str(tmp_path / "missing.png")
    assert api.get("/background").status_code == 404


def test_settings_offers_choosing_and_removing_the_image():
    pane = UI[UI.index("async settings()"):]
    pane = pane[:pane.index("\n  },")]
    assert 'data-change="setBackground"' in pane and 'accept="image/png,image/jpeg,image/webp"' in pane
    assert 'data-action="removeBackground"' in pane
