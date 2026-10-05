from __future__ import annotations

import shutil
import subprocess

import pytest

from agent.execute.tools import tool_git_push
from agent.security.capabilities import gate_tool_call

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, timeout=60)


@pytest.fixture
def repo(ctx, tmp_path):
    root = ctx.config.resolve(".")
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))

    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.test")
    _git(root, "config", "user.name", "test")
    (root / "work").mkdir(exist_ok=True)
    (root / "work" / "a.txt").write_text("hello", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    _git(root, "remote", "add", "origin", str(remote))
    return root, remote


def test_push_reaches_the_remote(ctx, task, repo):
    root, remote = repo
    out = tool_git_push(ctx, task, remote="origin")
    assert out["ok"] is True, out

    log = _git(remote, "log", "--oneline")
    assert "initial" in log.stdout, "the commit never arrived at the remote"


def test_unknown_remote_reports_what_exists(ctx, task, repo):
    out = tool_git_push(ctx, task, remote="nope")
    assert out["ok"] is False
    assert "nope" in out["error"] and "origin" in out["error"]


def test_push_outside_a_repository_is_refused(ctx, task, tmp_path):
    ctx.config.root = str(tmp_path / "not-a-repo")
    (tmp_path / "not-a-repo").mkdir()
    out = tool_git_push(ctx, task, remote="origin")
    assert out["ok"] is False and "not a git repository" in out["error"]


def test_push_always_parks_for_a_human(ctx, task, repo):
    tool = ctx.tools.get("git_push")

    parked = gate_tool_call(ctx, task, tool, {})
    assert parked.verdict == "needs_approval" and "human" in parked.reason

    supplied = gate_tool_call(ctx, task, tool, {"remote": "origin"})
    assert supplied.verdict == "deny"


def test_push_is_receipted_so_a_crash_cannot_double_push(ctx, task, repo):
    from agent.kernel.receipts import execute_once, idempotency_key

    calls = []
    key = idempotency_key(task.id, 0, "git_push", {"remote": "origin"})
    for _ in range(3):
        execute_once(ctx.conn, key, "git_push", {"remote": "origin"},
                     lambda: (calls.append(1), {"ok": True})[1])
    assert len(calls) == 1
