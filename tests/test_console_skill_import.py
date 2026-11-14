from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.skills.retrieve import retrieve
from test_console_reskin_guards import UI, _calls

FM = """---
name: unit-conversion
description: when converting units before computing a result
---
# unit conversion

Convert every quantity to SI before computing.
"""


@pytest.fixture
def api(ctx, workspace):
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1")
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c


def imp(api, text=FM, name="unit.md", **kw):
    return api.post("/skills/import", json={"file_name": name, "text": text, **kw})


def test_a_skill_file_arrives_as_an_untrusted_candidate(ctx, api):
    r = imp(api)
    assert r.status_code == 200, r.text
    s = ctx.skills.load(r.json()["id"], verify=True)
    assert s["status"] == "candidate" and s["name"] == "unit-conversion"
    assert s["contract"]["applies_when"] == "when converting units before computing a result"
    assert s["body"].startswith("# unit conversion") and "---" not in s["body"]
    p = s["provenance"]
    assert p["drafted_by"] == "external" and p["trust"] == "untrusted"
    assert p["source_file"] == "unit.md" and p["sha256"] == hashlib.sha256(FM.encode()).hexdigest()
    assert "drafted_from" not in p


def test_without_front_matter_the_name_is_the_file_name_and_the_text_is_kept_whole(ctx, api):
    body = "# plain\n\nDo the thing.\n"
    s = ctx.skills.load(imp(api, body, "my-skill.md").json()["id"], verify=True)
    assert s["name"] == "my-skill" and s["body"] == body
    assert s["contract"]["applies_when"] == "unknown — externally sourced"


def test_only_the_file_name_is_kept_never_a_path(ctx, api):
    s = ctx.skills.load(imp(api, name="../../etc/evil.md").json()["id"], verify=True)
    assert s["provenance"]["source_file"] == "evil.md"


def test_declaring_it_was_written_without_the_eval_tasks_is_recorded(ctx, api):
    s = ctx.skills.load(imp(api, drafted_from_none=True).json()["id"], verify=True)
    assert s["provenance"]["drafted_from"] == []


def test_an_imported_skill_never_reaches_a_prompt_until_promoted(ctx, api):
    imp(api)
    assert retrieve(ctx, "convert units before computing the result") == []


@pytest.mark.parametrize("text", ["", "   ", "x" * (64 * 1024 + 1), "---\nname: [unclosed\n---\nbody\n",
                                  "---\n- a list, not a mapping\n---\nbody\n"],
                         ids=["empty", "blank", "too-large", "bad-yaml", "not-a-mapping"])
def test_a_bad_file_is_refused(api, text):
    assert imp(api, text).status_code == 400


def test_the_same_skill_twice_is_refused(api):
    assert imp(api).status_code == 200
    assert imp(api).status_code == 400


def test_the_page_imports_through_its_route_from_a_local_file():
    assert ("POST", "/skills/import") in _calls(UI)
    block = UI[UI.index("async skills()"):]
    block = block[:block.index("\n  async ")]
    assert 'type="file"' in block and 'accept=".md,.markdown,.txt"' in block and "kNoEval" in block
