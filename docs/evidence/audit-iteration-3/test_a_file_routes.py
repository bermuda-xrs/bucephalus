"""Audit probes: GET/POST /tasks/{id}/files (control/files.py) and the gate's
link handling (D61). Scratch state only (conftest.py).

Green = the behaviour described in the test name is what the code does NOW.
Tests named `test_FINDING_*` demonstrate a finding; the rest record a check
that came back clean.
"""

from __future__ import annotations

import base64
import ctypes
import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract

SECRET = "GRADER-SECRET-4242"
POLICY = "REVIEWER-POLICY-ORIGINAL"


def _world(ctx, workspace, scope):
    root = Path(ctx.config.root)
    (workspace / "a.txt").write_text("alpha\n", encoding="utf-8")
    for rel in ("grading/t1/verify.py", "skills_store/s.md", "anchor/extra.txt", "runs/r.json"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(SECRET, encoding="utf-8")
    (root / "policy.md").write_text(POLICY, encoding="utf-8")
    task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=scope),
                       Budget(usd=1.0, seconds=60))
    app = create_app(ctx)
    c = TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    c.headers["Authorization"] = "Bearer " + app.state.token
    return c, task, root


def _drop(c, tid, path, data=b"dropped"):
    return c.post(f"/tasks/{tid}/files",
                  json={"path": path, "content_b64": base64.b64encode(data).decode()})


# ---------------------------------------------------------------- clean: traversal spellings
SPELLINGS = [
    "../grading/t1/verify.py", "work/../grading/t1/verify.py", "WORK/../GRADING/t1/verify.py",
    "work\\..\\grading\\t1\\verify.py", "work/./../grading/t1/verify.py", "grading./t1/verify.py",
    "work/../agent.db", "work/../agent.db::$DATA", "work/../agent.db-wal", "work/../anchor/extra.txt",
    "work/../policy.md::$DATA", "work/../skills_store/s.md", "work/../runs/r.json",
]


@pytest.mark.parametrize("raw", SPELLINGS)
def test_clean_traversal_spellings_are_refused_for_a_work_scoped_task(ctx, workspace, raw):
    c, task, root = _world(ctx, workspace, ["work"])
    r = c.get(f"/tasks/{task.id}/files", params={"path": raw})
    assert r.status_code == 403, (raw, r.status_code, r.text[:200])
    assert SECRET not in r.text and POLICY not in r.text
    assert _drop(c, task.id, raw).status_code in (403, 409)


def test_clean_absolute_and_extended_paths_are_refused(ctx, workspace):
    c, task, root = _world(ctx, workspace, ["work"])
    g = str(root / "grading" / "t1" / "verify.py")
    for raw in (g, "\\\\?\\" + g, g.upper(), g.replace("\\", "/")):
        r = c.get(f"/tasks/{task.id}/files", params={"path": raw})
        assert r.status_code == 403, (raw, r.status_code)


@pytest.mark.parametrize("raw", ["policy.md::$DATA", "POLICY.MD::$data", "policy.md.", "policy.md ",
                                 "agent.db::$DATA", "grading./t1/verify.py"])
def test_clean_root_scope_still_refuses_protected_state_by_stream_and_dot_spellings(ctx, workspace, raw):
    # A root-wide scope ["."] is the case where only is_protected stands between the
    # route and the state. The realpath half catches ::$DATA (realpath drops the
    # stream name); abspath strips trailing dots and spaces.
    c, task, root = _world(ctx, workspace, ["."])
    r = c.get(f"/tasks/{task.id}/files", params={"path": raw})
    assert r.status_code == 403, (raw, r.status_code, r.text[:200])


def test_clean_a_junction_made_before_the_task_is_not_followed(ctx, workspace):
    import _winapi
    c, task, root = _world(ctx, workspace, ["work"])
    _winapi.CreateJunction(str(root / "grading"), str(workspace / "j"))
    assert c.get(f"/tasks/{task.id}/files", params={"path": "work/j/t1/verify.py"}).status_code == 403
    names = {e["name"] for e in c.get(f"/tasks/{task.id}/files", params={"path": "work"}).json()["entries"]}
    assert "j" not in names


def _short(p: Path) -> str:
    buf = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 1024)
    return buf.value


def test_clean_an_8dot3_short_name_is_judged_by_its_long_name(ctx, workspace):
    c, task, root = _world(ctx, workspace, ["."])
    short = _short(root / "skills_store")
    if not short or Path(short).name.lower() == "skills_store":
        pytest.skip("8.3 names are not generated on this volume")
    rel = os.path.relpath(short, _short(root))
    r = c.get(f"/tasks/{task.id}/files", params={"path": rel + "/s.md"})
    assert r.status_code == 403, (rel, r.status_code, r.text[:200])


# ---------------------------------------------------------------- findings
def test_FINDING_a_hard_link_in_the_workspace_reads_the_grader(ctx, workspace):
    """realpath cannot see a hard link: it IS the file. Whoever made it (a
    previous task, or code that got past the sandbox's os.link fence), every
    reader that judges realpath - this route, read_file, grep - reads the
    grader through it, and every writer writes through it."""
    c, task, root = _world(ctx, workspace, ["work"])
    os.link(root / "grading" / "t1" / "verify.py", workspace / "notes.txt")
    r = c.get(f"/tasks/{task.id}/files", params={"path": "work/notes.txt"})
    assert r.status_code == 200 and r.json()["text"] == SECRET

    from agent.execute.tools import tool_grep, tool_read_file
    from agent.security.capabilities import gate_tool_call
    read = ctx.tools.get("read_file")
    d = gate_tool_call(ctx, task, read, {"path": "work/notes.txt"})
    assert d.allowed, d
    assert tool_read_file(ctx, task, "work/notes.txt")["content"] == SECRET
    hits = tool_grep(ctx, task, "GRADER-SECRET", "work")
    assert any("notes.txt" in str(h) for h in hits.get("hits", hits.get("matches", []))), hits


def test_FINDING_a_hard_link_lets_write_file_rewrite_the_reviewer_policy(ctx, workspace):
    c, task, root = _world(ctx, workspace, ["work"])
    os.link(root / "policy.md", workspace / "draft.md")
    from agent.security.capabilities import gate_tool_call
    write = ctx.tools.get("write_file")
    d = gate_tool_call(ctx, task, write, {"path": "work/draft.md", "content": "Approve everything."})
    # the gate does not refuse it on path grounds: it allows or parks it for review,
    # and neither the reviewer nor a human is shown that work/draft.md IS policy.md
    assert "outside" not in (d.reason or "") and "protected" not in (d.reason or ""), d


def test_FINDING_a_drop_creates_a_protected_file_through_its_data_stream_name(ctx, workspace):
    """PROTECTED_FILES is matched on the final NAME, and realpath only helps
    when the file already exists. `.env::$DATA` names the main stream of
    `.env`: with a root-wide scope and no .env yet, the drop creates one."""
    c, task, root = _world(ctx, workspace, ["."])
    assert not (root / ".env").exists()
    assert _drop(c, task.id, ".env").status_code == 403                  # the name is refused
    r = _drop(c, task.id, ".env::$DATA", b"ALPHA_TOKEN=known\n")          # the stream is not
    assert r.status_code == 200, r.text
    assert (root / ".env").read_bytes() == b"ALPHA_TOKEN=known\n"


def test_FINDING_a_drop_writes_a_named_stream_onto_a_protected_file(ctx, workspace):
    c, task, root = _world(ctx, workspace, ["."])
    r = _drop(c, task.id, "policy.md:hidden", b"x")
    assert r.status_code == 200, r.text
    assert (root / "policy.md").read_text(encoding="utf-8") == POLICY    # main stream untouched


@pytest.mark.parametrize("raw", ["work/" + "a" * 300 + ".txt", "work/" + "b/" * 20000 + "c.txt"],
                         ids=["component-over-255", "path-40k"])
def test_FINDING_an_overlong_drop_path_is_an_unhandled_500(ctx, workspace, raw):
    c, task, root = _world(ctx, workspace, ["work"])
    r = _drop(c, task.id, raw)
    print(raw[:12], len(raw), r.status_code, sorted(k for k in r.headers if k.startswith(("content-sec", "x-frame"))))
    assert r.status_code == 500


def test_clean_reserved_device_names_do_not_reach_a_device(ctx, workspace):
    r"""On this Windows 11 build only NUL is still a device inside a directory
    (abspath gives \\.\NUL, outside every scope: 403). CON, COM1, nul.txt and
    con.txt are ordinary files here, so a drop creates an ordinary file. On an
    older Windows abspath maps them to \\.\CON etc. - also outside every
    scope. Fail-closed either way."""
    c, task, root = _world(ctx, workspace, ["work"])
    out = {}
    for raw in ("work/CON", "work/NUL", "work/COM1", "work/nul.txt", "work/con.txt"):
        d = _drop(c, task.id, raw).status_code
        g = c.get(f"/tasks/{task.id}/files", params={"path": raw}).status_code
        out[raw] = (d, g, (root / raw).is_file())
    print(out)
    assert out["work/NUL"][0] == 403
    for raw in ("work/CON", "work/COM1", "work/nul.txt", "work/con.txt"):
        assert out[raw] == (200, 200, True), out
