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


def test_review_a_filter_in_the_repository_config_never_runs(ctx, task, repo, tmp_path):
    marker = tmp_path / "ran.txt"
    _git(repo, "config", "filter.evil.clean", f"echo ran > '{marker.as_posix()}'; cat")
    (repo / ".gitattributes").write_text("*.txt filter=evil\n", encoding="utf-8")
    ctx.config.git_mode = True
    ctx.backends["cloud"].queue(_write("work/a.txt", "x\n"), FINAL)
    run_task(ctx, task.id)
    assert not marker.exists(), "git mode ran a filter command from the repository's config"
    assert _git(repo, "show", f"bucephalus/{task.id}:work/a.txt") == "x"


def test_review_a_scope_that_covers_the_git_directory_is_refused(ctx, repo):
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    ctx.config.git_mode = True
    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=["."]), Budget(usd=1.0, seconds=60))
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, t.id)
    assert "bucephalus/" not in _git(repo, "branch", "--list")
    why = [json.loads(r[0])["why"] for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='git.skipped'", (t.id,))]
    assert why and ".git" in why[0]


def test_review_the_git_directory_is_protected_state(ctx, repo):
    from agent.security.capabilities import is_protected, state_paths

    assert is_protected(".git/config", str(repo), state_paths(ctx))


@pytest.mark.parametrize("how", ["dotted", "included"])
def test_review_no_filter_runs_whatever_its_name_or_where_it_is_defined(ctx, task, repo, tmp_path, how):
    marker = tmp_path / "ran.txt"
    cmd = f"echo ran > '{marker.as_posix()}'; cat"
    if how == "dotted":
        _git(repo, "config", "filter.ev.il.clean", cmd)
        attr = "ev.il"
    else:
        inc = tmp_path / "extra.cfg"
        inc.write_text(f"[filter \"inc\"]\n\tclean = {cmd}\n", encoding="utf-8")
        _git(repo, "config", "include.path", inc.as_posix())
        attr = "inc"
    (repo / ".gitattributes").write_text(f"*.txt filter={attr}\n", encoding="utf-8")
    ctx.config.git_mode = True
    ctx.backends["cloud"].queue(_write("work/a.txt", "x\n"), FINAL)
    run_task(ctx, task.id)
    assert not marker.exists(), "git mode ran a filter command"
    assert _git(repo, "show", f"bucephalus/{task.id}:work/a.txt") == "x"


def test_review_an_ignored_file_stays_out_and_a_deleted_one_goes(ctx, task, repo):
    (repo / ".gitignore").write_text("work/*.log\n", encoding="utf-8")
    ctx.config.git_mode = True
    ctx.backends["cloud"].queue(_write("work/keep.txt", "k\n"), _write("work/noise.log", "n\n"), FINAL)
    (repo / "work" / "base.txt").unlink()
    run_task(ctx, task.id)
    files = _git(repo, "ls-tree", "-r", "--name-only", f"bucephalus/{task.id}").splitlines()
    assert "work/keep.txt" in files and "work/noise.log" not in files and "work/base.txt" not in files


def test_review_the_resolved_git_directory_is_protected_when_the_checkout_is_a_subdirectory(ctx, repo):
    from agent.security.capabilities import is_protected, state_paths

    ctx.config.git_mode = True
    sub = repo / "inner"
    sub.mkdir()
    ctx.config.root = str(sub)
    assert is_protected(str(repo / ".git" / "config"), str(sub), state_paths(ctx))


def test_review_a_child_worktree_is_checked_out_by_git_without_running_a_filter(ctx, task, repo, tmp_path):
    from agent.gitmode import add_worktree

    marker = tmp_path / "ran.txt"
    _git(repo, "config", "filter.evil.smudge", f"echo ran > '{marker.as_posix()}'; cat")
    (repo / ".gitattributes").write_text("*.txt filter=evil" + chr(10), encoding="utf-8")
    _git(repo, "add", ".gitattributes")
    _git(repo, "-c", "filter.evil.clean=cat", "commit", "-q", "-m", "attrs")
    path, _, ident = add_worktree(repo, "c1", "bucephalus/c1")
    assert not marker.exists() and (path / "work" / "base.txt").read_text(encoding="utf-8") == "base" + chr(10)
    assert set(ident) == {"gitdir", "common", "dotgit"}


def test_review_a_worktree_whose_git_file_changed_is_refused(ctx, task, repo):
    from agent.gitmode import GitModeError, add_worktree, commit_scope

    path, _, ident = add_worktree(repo, "c2", "bucephalus/c2")
    with open(path / ".git", "r+", encoding="utf-8") as fh:
        fh.seek(0, 2)
        fh.write(chr(10))
    with pytest.raises(GitModeError, match="no longer points"):
        commit_scope(path, ["work"], "bucephalus/c2", "m", expect=ident)


def test_review_git_older_than_the_attribute_source_is_refused(monkeypatch, repo):
    from agent import gitmode

    monkeypatch.setattr(gitmode, "git_version", lambda: (2, 39))
    with pytest.raises(gitmode.GitModeError, match="2.40"):
        gitmode.commit_scope(repo, ["work"], "bucephalus/x", "m")
