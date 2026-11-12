from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app

CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"
ROOT = Path(__file__).resolve().parents[1]
VENDORED = CONTROL / "force-graph.min.js"
SHA256 = "f59db59ba92ff5a76ed93d7a4d77a703b176260e5d9cd29f4f8eb4f6c369a629"
UI = (CONTROL / "ui.js").read_text(encoding="utf-8")
HTML = (CONTROL / "ui.html").read_text(encoding="utf-8")
CSS = (CONTROL / "ui.css").read_text(encoding="utf-8")


def test_the_vendored_library_is_the_pinned_release_byte_for_byte():
    data = VENDORED.read_bytes()
    assert hashlib.sha256(data).hexdigest() == SHA256, "force-graph changed; re-verify against the npm integrity hash"
    assert data.startswith(b"// Version 1.52.0 force-graph")


def test_the_library_has_no_network_eval_or_worker_paths():
    src = VENDORED.read_text(encoding="utf-8")
    for bad in ("fetch(", "XMLHttpRequest", "new Function", "eval(", "Worker(", "importScripts", "localStorage",
                "document.cookie", "sendBeacon", "WebSocket"):
        assert bad not in src, bad


@pytest.fixture
def client(ctx, workspace):
    return TestClient(create_app(ctx), base_url="http://127.0.0.1")


def test_it_is_served_from_our_origin_like_the_console_script(client):
    r = client.get("/force-graph.min.js")
    assert r.status_code == 200 and r.content == VENDORED.read_bytes()
    assert r.headers["content-type"].startswith("text/javascript")
    assert "script-src 'self'" in r.headers["content-security-policy"]


def test_the_library_loads_only_when_the_graph_opens_and_only_from_here():
    assert "force-graph" not in HTML, "not on every page load"
    loader = UI[UI.index("function loadForceGraph("):]
    loader = loader[:loader.index("\n}\n") + 3]
    assert 'el.src = "/force-graph.min.js"' in loader and "cdn" not in loader.lower()
    assert "S.fgLib" in loader, "one load, shared by every caller"
    view = UI[UI.index("function forceView("):UI.index("function dropForceView(")]
    assert "loadForceGraph()" in view


def test_the_graph_never_hands_agent_text_to_the_librarys_html_tooltip():
    block = UI[UI.index("function forceView("):]
    block = block[:block.index("\nfunction ", 10)]
    assert '.nodeLabel(() => "")' in block and '.linkLabel(() => "")' in block
    assert ".float-tooltip-kap { display: none" in CSS


def test_the_css_the_library_would_inject_is_in_our_stylesheet():
    assert ".force-graph-container canvas" in CSS


def test_the_graph_has_a_node_cap():
    assert "const MAX_GRAPH_NODES = 5000;" in UI


def test_the_notice_names_the_library_and_its_license():
    notes = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "force-graph 1.52.0" in notes and "Vasco Asturiano" in notes and SHA256 in notes


NODE = __import__("shutil").which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_layout_puts_steps_on_a_spine_calls_left_and_files_right():
    import json
    import subprocess

    src = UI[UI.index("const ROW = "):UI.index("function fitGraph(")]
    script = src + """
const nodes = [{id: "a", kind: "step"}, {id: "t1", kind: "tool"}, {id: "c1", kind: "check"},
               {id: "b", kind: "step"}, {id: "t2", kind: "edit"}, {id: "e", kind: "end"},
               {id: "f:x", kind: "file"}];
layOut(nodes, [{source: "a", target: "f:x"}, {source: "b", target: "f:x"}, {source: "a", target: "t1"}]);
process.stdout.write(JSON.stringify(nodes));"""
    out = {n["id"]: n for n in json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                                                         check=True).stdout)}
    spine = [out[k] for k in ("a", "b", "e")]
    assert all(n["fx"] == 0 for n in spine) and [n["fy"] for n in spine] == sorted(n["fy"] for n in spine)
    assert spine[0]["fy"] < spine[1]["fy"] < spine[2]["fy"]
    assert out["t1"]["tx"] < 0 and out["t1"]["ty"] == out["a"]["fy"] and out["t2"]["ty"] == out["b"]["fy"]
    assert out["f:x"]["tx"] > 0 and out["f:x"]["ty"] == (out["a"]["fy"] + out["b"]["fy"]) / 2


def test_a_question_icon_explains_the_layout_and_every_dot():
    block = UI[UI.index("function forceView("):UI.index("function dropForceView(")]
    legend = UI[UI.index("const GRAPH_HELP"):UI.index("function forceView(")]
    assert '<details class="fg-help">${GRAPH_HELP}</details>' in block
    assert legend.index("<summary") < legend.index("<svg") and 'aria-label="How this graph is laid out"' in legend
    assert "<svg" in legend and "</svg>" in legend, "a drawn question mark, not an emoji"
    assert not any(ord(c) > 0x2000 and c != "…" for c in legend), "no emoji or pictographs"
    for kind in ("step", "tool", "edit", "check", "hold", "human", "end", "file"):
        assert f'class="lg lg-{kind}"' in legend, kind
    assert 'class="lg lg-bad"' in legend
    for rule in ("spine", "left", "right", "Drag"):
        assert rule in legend, rule
    for kind in ("step", "tool", "edit", "check", "hold", "human", "end", "file", "bad"):
        assert f".lg-{kind}" in CSS, kind
