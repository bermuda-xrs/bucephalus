"""The console's file browser is a new READER, and drag-and-drop a new WRITER
(iteration 3, 4.4). Both go through the same scope and protected-path checks
as every other reader and writer (DR3).

Written first and shown red against a naive route that read whatever path it
was given under the project root.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.control.server import create_app
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract

SECRET = "S3CRET-DO-NOT-SHOW"


@pytest.fixture
def world(ctx, workspace):
    root = Path(ctx.config.root)
    (workspace / "a.txt").write_bytes(b"alpha\n")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "b.txt").write_text("beta\n", encoding="utf-8")
    (workspace / ".env").write_text(f"KEY={SECRET}\n", encoding="utf-8")
    for rel in ("grading/t1/verify.py", "skills_store/s.md", "anchor/extra.txt",
                "outside/notes.txt", "runs/r.json"):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(SECRET, encoding="utf-8")
    ctx.memory.root.mkdir(parents=True, exist_ok=True)
    (ctx.memory.root / "MEMORY.md").write_text(f"- [user] {SECRET}\n", encoding="utf-8")
    task = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=60))
    app = create_app(ctx)
    client = TestClient(app, base_url="http://127.0.0.1")
    client.headers["Authorization"] = "Bearer " + app.state.token
    token_file = next(Path(os.environ["ALPHA_CONFIG_DIR"]).glob("*token*"))
    return client, task.id, root, token_file, ctx


def _get(client, tid, path):
    return client.get(f"/tasks/{tid}/files", params={"path": path})


def test_the_workspace_is_browsable(world):
    client, tid, *_ = world
    r = _get(client, tid, "work")
    assert r.status_code == 200, r.text
    names = {e["name"] for e in r.json()["entries"]}
    assert {"a.txt", "sub"} <= names
    r = _get(client, tid, "work/a.txt")
    assert r.status_code == 200 and r.json()["text"] == "alpha\n"


def test_a_protected_name_inside_scope_is_neither_listed_nor_shown(world):
    client, tid, *_ = world
    assert ".env" not in {e["name"] for e in _get(client, tid, "work").json()["entries"]}
    r = _get(client, tid, "work/.env")
    assert r.status_code == 403 and SECRET not in r.text


@pytest.mark.parametrize("target", [
    "agent.db", "work/../agent.db", "work/../agent.db-wal", "anchor/chain.anchor",
    "anchor/extra.txt", "grading/t1/verify.py", "skills_store/s.md", "runs/r.json",
    "outside/notes.txt", ".", "..", "work/..", "WORK/../AGENT.DB", "work\\..\\agent.db",
])
def test_nothing_outside_the_scope_or_inside_protected_state_is_shown(world, target):
    client, tid, *_ = world
    r = _get(client, tid, target)
    assert r.status_code in (403, 404), (target, r.status_code, r.text[:200])
    assert SECRET not in r.text


def test_absolute_paths_to_state_are_refused(world):
    client, tid, root, token_file, ctx = world
    for p in (root / "agent.db", ctx.memory.root / "MEMORY.md", token_file,
              root / "grading" / "t1" / "verify.py"):
        r = _get(client, tid, str(p))
        assert r.status_code in (403, 404), p
        assert SECRET not in r.text and "alpha-token" not in r.text


def test_a_link_out_of_the_workspace_is_not_followed(world):
    client, tid, root, *_ = world
    link = root / "work" / "db_link"
    try:
        os.symlink(root / "agent.db", link)
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks without privilege")
    assert _get(client, tid, "work/db_link").status_code == 403


def _put(client, tid, path, data=b"dropped in\n"):
    return client.post(f"/tasks/{tid}/files",
                       json={"path": path, "content_b64": base64.b64encode(data).decode()})


def test_a_dropped_file_lands_in_the_workspace_as_untrusted(world):
    client, tid, root, _t, ctx = world
    r = _put(client, tid, "work/dropped.txt")
    assert r.status_code == 200, r.text
    assert (root / "work" / "dropped.txt").read_bytes() == b"dropped in\n"
    ev = [e for e in ctx.log.events(tid) if e["type"] == "human.uploaded"]
    assert ev and ev[-1]["payload"]["taint"] == "untrusted"
    assert ev[-1]["payload"]["path"] == "work/dropped.txt"


@pytest.mark.parametrize("target", ["agent.db", "work/../outside.txt", "work/.env",
                                    "grading/t1/verify.py", "skills_store/x.md",
                                    "work/a.txt", "config.yaml", "/tmp/x.txt"])
def test_a_drop_never_writes_outside_the_workspace_or_over_anything(world, target):
    client, tid, root, *_ = world
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    r = _put(client, tid, target, SECRET.encode())
    assert r.status_code in (403, 409), (target, r.status_code)
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()
             and not p.name.startswith("agent.db")}
    assert {p: b for p, b in before.items() if not p.name.startswith("agent.db")} == after


def test_a_junction_out_of_the_workspace_is_not_followed(world):
    """A directory junction needs no privilege on Windows - the link a
    sandboxed program could actually make here."""
    client, tid, root, *_ = world
    try:
        import _winapi
        _winapi.CreateJunction(str(root / "grading"), str(root / "work" / "gj"))
    except (ImportError, OSError, AttributeError):
        pytest.skip("no junctions on this platform")
    listing = {e["name"] for e in _get(client, tid, "work").json()["entries"]}
    assert "gj" not in listing
    r = _get(client, tid, "work/gj/t1/verify.py")
    assert r.status_code == 403 and SECRET not in r.text
    assert _put(client, tid, "work/gj/t1/new.py").status_code == 403
