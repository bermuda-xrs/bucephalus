from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from agent.execute.loop import run_task

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
FINAL = '<final>{"answer": "ok"}</final>'


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(ctx, workspace):
    root = workspace.parent
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (workspace / "base.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "work/base.txt")
    _git(root, "commit", "-q", "-m", "base")
    ctx.config.autonomy.approve_scoped_writes = True
    return root


def _write(path, content):
    return '<action tool="write_file">' + json.dumps({"path": path, "content": content}) + "</action>"


def test_git_mode_commits_each_task_on_its_own_branch_and_leaves_the_users_alone(ctx, task, repo):
    ctx.config.git_mode = True
    head, status = _git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain", "--untracked-files=no")
    ctx.backends["cloud"].queue(_write("work/a.txt", "from the task\n"), FINAL)
    run_task(ctx, task.id)
    branch = f"bucephalus/{task.id}"
    assert _git(repo, "show", f"{branch}:work/a.txt") == "from the task"
    assert _git(repo, "rev-parse", f"{branch}^") == head
    assert _git(repo, "rev-parse", "HEAD") == head and _git(repo, "branch", "--show-current") == "main"
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == status
    ev = [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='git.committed'", (task.id,))]
    assert ev and ev[0]["branch"] == branch


def test_without_git_mode_no_branch_is_made(ctx, task, repo):
    ctx.backends["cloud"].queue(_write("work/a.txt", "x\n"), FINAL)
    run_task(ctx, task.id)
    assert "bucephalus/" not in _git(repo, "branch", "--list")


def test_outside_a_repository_git_mode_does_nothing(ctx, task, workspace):
    ctx.config.git_mode = True
    ctx.backends["cloud"].queue(_write("work/a.txt", "x\n"), FINAL)
    assert run_task(ctx, task.id).status.startswith("done")


def test_a_delegated_child_works_in_its_own_worktree(ctx, task, repo):
    from agent.delegate.contracts import SubtaskPlan, run_delegated
    from agent.kernel.types import Budget

    ctx.config.git_mode = True
    sub = SubtaskPlan(goal="write the child file", estimated_irrelevant_tokens=50_000,
                      budget=Budget(usd=1.0, seconds=60), is_tightly_coupled=False, output_schema={"type": "object"},
                      artifact_scope=["work"], success_criterion="always_pass")

    def responder(prefix, tail):
        return FINAL if "child.txt" in tail else _write(_child_path(ctx), "child\n")

    ctx.backends["cloud"]._responder = responder
    run_delegated(ctx, task, sub)
    child = ctx.conn.execute("SELECT id FROM tasks WHERE parent_id=?", (task.id,)).fetchone()[0]
    wt = repo / ".bucephalus-worktrees" / child
    assert (wt / "work" / "child.txt").read_text(encoding="utf-8") == "child\n"
    assert not (repo / "work" / "child.txt").exists()
    assert _git(repo, "show", f"bucephalus/{child}:work/child.txt") == "child"


def _child_path(ctx):
    row = ctx.conn.execute("SELECT contract_json FROM tasks WHERE parent_id IS NOT NULL ORDER BY rowid DESC").fetchone()
    scope = json.loads(row[0])["artifact_scope"][0]
    return f"{scope}/child.txt"
