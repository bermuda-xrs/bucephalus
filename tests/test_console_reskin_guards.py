from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "agent"
CONTROL = PKG / "control"
UI = (CONTROL / "ui.js").read_text(encoding="utf-8")
HTML = (CONTROL / "ui.html").read_text(encoding="utf-8")
SERVER = (CONTROL / "server.py").read_text(encoding="utf-8")

# what the console offered on 2026-11-07, before the Hermes reskin; a feature may move, never vanish
PINNED_CALLS = {
    ("GET", "/alerts"), ("GET", "/context"), ("GET", "/conversations"), ("GET", "/conversations/{}"),
    ("GET", "/conversations/{}/export"), ("GET", "/gate/{}"), ("GET", "/health"), ("GET", "/memory"),
    ("GET", "/models"), ("GET", "/panels/channels"), ("GET", "/panels/integrity"), ("GET", "/panels/logs"),
    ("GET", "/panels/measurement"), ("GET", "/panels/schedules"), ("GET", "/panels/settings"),
    ("GET", "/policy"), ("GET", "/skills"), ("GET", "/skills/{}"), ("GET", "/suites"), ("GET", "/suites/{}"),
    ("GET", "/tasks"), ("GET", "/tasks/{}"), ("GET", "/tasks/{}/call"), ("GET", "/tasks/{}/calls"),
    ("GET", "/tasks/{}/checkpoints"), ("GET", "/tasks/{}/diff"), ("GET", "/tasks/{}/files"),
    ("GET", "/tasks/{}/plan"), ("GET", "/tasks/{}/stream"), ("GET", "/tasks/{}/trace"), ("GET", "/theme"),
    ("PATCH", "/conversations/{}"), ("POST", "/approvals/{}"), ("POST", "/conversations"),
    ("POST", "/conversations/{}/messages"), ("POST", "/memory/accept"), ("POST", "/memory/forget"),
    ("POST", "/models/run"), ("POST", "/models/select"), ("POST", "/schedules"), ("POST", "/settings/long_files"),
    ("POST", "/skills"), ("POST", "/suites"), ("POST", "/suites/{}/tasks"), ("POST", "/tasks/{}/files"),
    ("POST", "/tasks/{}/message"), ("POST", "/tasks/{}/pause"), ("POST", "/tasks/{}/plan"),
    ("POST", "/tasks/{}/plan/approve"), ("POST", "/tasks/{}/plan/reject"), ("POST", "/tasks/{}/rewind"),
    ("POST", "/tasks/{}/run"), ("PUT", "/policy"), ("DELETE", "/schedules/{}"), ("DELETE", "/suites/{}"),
    ("DELETE", "/suites/{}/tasks/{}"),
}
PINNED_ACTIONS = {
    "acceptMemory", "addSchedule", "addSkill", "archiveConversation", "askAgain", "checkGate", "closeControl",
    "closeSuite", "closeTrace", "closeTraceDetail", "copyAnswer", "copyCode", "createSuite", "decide",
    "delSuite", "delTask", "doRewind", "draft", "editAsk", "enableNotifications", "exportConversation",
    "forgetMemory", "newConversation", "openControl", "openFile", "openLooseList", "openPalette", "openPane",
    "openSuite", "openTaskAlone", "openTaskOf", "pinConversation", "removeSchedule", "renameConversation",
    "resumeTask", "runModel", "savePolicy", "saveTask", "selectConversation", "sendBack", "showChanges",
    "showRewind", "showSendBack", "sign", "stopTask", "toggleArchived", "toggleFiles", "toggleSide",
    "toggleTheme", "toggleTool", "traceNode", "traceTask", "viewSkill",
}
PINNED_PANES = {"channels", "integrity", "logs", "measurement", "memory", "policy", "schedules", "settings",
                "skills", "suites"}
PINNED_VIEWS = {"normal", "thinking", "verbose", "graph"}
PINNED_SLASH = {"/export", "/help", "/json", "/new", "/notify", "/pin", "/say", "/theme"}

_CALL = re.compile(r"""\b(api|post|fetch|download)\(\s*(`[^`]*`|"[^"]*")(\s*\+)?([^;]{0,160})""")


def _calls(js: str) -> set[tuple[str, str]]:
    out = set()
    for fn, lit, plus, rest in _CALL.findall(js):
        path = re.sub(r"\$\{[^}]*\}", "{}", lit[1:-1]).split("?")[0]
        if plus and path.endswith("/"):
            path += "{}"
        method = re.match(r"""[^)]*?method:\s*"(\w+)\"""", rest)
        out.add(("POST" if fn == "post" else method.group(1) if method else "GET", path))
    return out


def _routes() -> set[tuple[str, str]]:
    return {(m.upper(), re.sub(r"\{[^}]*\}", "{}", p))
            for m, p in re.findall(r'@app\.(get|post|put|patch|delete)\("([^"]+)"', SERVER)}


def _same(a: tuple[str, str], b: tuple[str, str]) -> bool:
    sa, sb = a[1].split("/"), b[1].split("/")
    return a[0] == b[0] and len(sa) == len(sb) and all(x == y or "{}" in (x, y) for x, y in zip(sa, sb))


def test_the_probe_reads_every_kind_of_call():
    js = ('api("/a"); api(`/b/${x}/c?d=${y}`); post("/e/" + id); api("/f/" + id, {method: "DELETE"}); '
          'fetch(`/g/${t}/stream`, {}); download(`/h/${c}/export?format=${f}`, n);')
    assert _calls(js) == {("GET", "/a"), ("GET", "/b/{}/c"), ("POST", "/e/{}"), ("DELETE", "/f/{}"),
                          ("GET", "/g/{}/stream"), ("GET", "/h/{}/export")}


def test_no_route_the_console_used_is_lost():
    used = _calls(UI)
    lost = sorted(p for p in PINNED_CALLS if not any(_same(p, u) for u in used))
    assert not lost, f"the console no longer calls: {lost}"


def test_every_route_the_console_calls_exists():
    routes = _routes()
    fake = sorted(u for u in _calls(UI) if not any(_same(u, r) for r in routes))
    assert not fake, f"the console calls routes the server does not serve: {fake}"


def test_no_control_the_console_offered_is_lost():
    page = UI + HTML
    actions = set(re.findall(r'data-action="(\w+)"', page))
    panes = set(re.findall(r'data-pane="(\w+)"', page))
    views = set(re.findall(r'data-view="(\w+)"', page))
    slash = UI[UI.index("const SLASH = {"):]
    slash = set(re.findall(r'"(/\w+)": \[', slash[:slash.index("};")]))
    assert not PINNED_ACTIONS - actions, PINNED_ACTIONS - actions
    assert not PINNED_PANES - panes, PINNED_PANES - panes
    assert not PINNED_VIEWS - views, PINNED_VIEWS - views
    assert not PINNED_SLASH - slash, PINNED_SLASH - slash


def _hex(v: str) -> tuple[float, float, float]:
    h = v.strip().lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h[:6]
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _ratio(a: str, b: str) -> float:
    def lum(v):
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in _hex(v)]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _palettes() -> dict[str, dict[str, str]]:
    css = (CONTROL / "ui.css").read_text(encoding="utf-8")
    theme = tomllib.loads((PKG / "themes" / "default.toml").read_text(encoding="utf-8"))["colors"]

    def block(sel):
        i = css.index(sel + " {")
        return dict(re.findall(r"--([\w-]+):\s*([^;]+);", css[i:css.index("}", i)]))

    def resolve(tokens):
        # /theme sends the theme file's colours and ui.js sets them as --theme-*, so they win over the fallback
        out = {}
        for k, v in tokens.items():
            m = re.fullmatch(r"var\(--theme-(\w+),\s*(#[0-9A-Fa-f]+)\)", v.strip())
            out[k] = theme.get(m.group(1), m.group(2)) if m else v.strip()
        return out

    dark = block(":root")
    return {"dark": resolve(dark), "light": resolve({**dark, **block(':root[data-theme="light"]')})}


@pytest.mark.parametrize("scheme", ["dark", "light"])
def test_every_text_colour_reads_on_every_surface(scheme):
    t = _palettes()[scheme]
    pairs = [(fg, bg, 4.5) for fg in ("text", "dim", "accent", "needs", "done", "danger", "primary")
             for bg in ("ground", "panel", "raised")]
    pairs += [("on-accent", "accent", 4.5)] + [("focus", bg, 3.0) for bg in ("ground", "panel", "raised")]
    low = [(fg, bg, round(_ratio(t[fg], t[bg]), 2)) for fg, bg, need in pairs if _ratio(t[fg], t[bg]) < need]
    assert not low, f"{scheme}: below WCAG AA: {low}"


def test_the_pinned_request_hides_what_scrolls_under_it():
    css = (CONTROL / "ui.css").read_text(encoding="utf-8")
    rules = " ".join(m.group(1) for m in re.finditer(r"\.ask-bubble\s*\{([^}]*)\}", css))
    assert re.search(r"position:\s*sticky", rules), "the request no longer stays in view over its reply"
    bg = re.search(r"background:\s*var\(--(\w[\w-]*)\)", rules)
    assert bg and bg.group(1) in ("ground", "panel", "raised"), "a pinned request needs an opaque surface"
