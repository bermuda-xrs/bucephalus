from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control import docs
from agent.control.server import create_app
from test_console_reskin_guards import HTML, UI, _calls

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def test_the_list_holds_the_source_docs_marked_frozen_or_live(api):
    d = api.get("/documents").json()
    by = {x["name"]: x for x in d["docs"]}
    assert d["available"] and d["docs"][0]["name"] == "README.md"
    assert by["FINDINGS.md"]["kind"] == "frozen" and by["AUDIT-2026-09.md"]["kind"] == "frozen"
    assert by["INVARIANTS.md"]["kind"] == "frozen" and by["HANDOVER.md"]["kind"] == "live"
    assert by["CHANGELOG.md"]["kind"] == "live" and by["PROPOSALS.md"]["kind"] == ""
    assert by["HANDOVER.md"]["title"] and all(x["name"].endswith(".md") for x in d["docs"])


def test_a_doc_comes_back_as_raw_text(api):
    d = api.get("/documents/HANDOVER.md").json()
    assert d["text"] == (ROOT / "docs" / "HANDOVER.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["%2e%2e","..%2fpyproject.toml", "..%5cpyproject.toml", "pyproject.toml",
                                  "evidence", "benchmark-2026-08-19.html", "readme.MD"])
def test_nothing_outside_the_list_is_served(api, name):
    assert api.get(f"/documents/{name}").status_code == 404


def test_an_installed_copy_lists_nothing(tmp_path, monkeypatch, api):
    monkeypatch.setattr(docs, "source_root", lambda: None)
    assert api.get("/documents").json() == {"available": False, "docs": []}
    assert api.get("/documents/README.md").status_code == 404


def test_the_docs_page_reads_only_its_routes_and_renders_through_md():
    block = UI[UI.index("const PANES = {"):]
    page = block[block.index("async docs()"):]
    page = page[:page.index("\n  async ")]
    assert _calls(page) == {("GET", "/documents")}
    fn = UI[UI.index("async function readDoc("):]
    fn = fn[:fn.index("\n}")]
    assert _calls(fn) == {("GET", "/documents/{}")} and "renderMarkdown(" in fn
    assert 'data-pane="docs"' in HTML and "<iframe" not in UI + HTML
