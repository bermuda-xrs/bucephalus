from __future__ import annotations

import subprocess

import pytest

from agent.execute import shadow
from agent.execute.shadow import ShadowRepo

pytestmark = pytest.mark.skipif(not shadow.git_available(), reason="git is not installed")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "project"
    (root / "work").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "other" / "keep.txt").write_text("not in scope\n", encoding="utf-8")
    return ShadowRepo(tmp_path / "state" / "shadow" / "t1", root, ["work"]), root


def test_a_checkpoint_commits_the_scope_in_a_repo_outside_the_workspace(repo):
    r, root = repo
    (root / "work" / "a.txt").write_text("v1\n", encoding="utf-8")
    sha = r.checkpoint("step 0")
    assert sha and len(sha) == 40 and (r.git_dir / "HEAD").exists()
    assert not (root / ".git").exists()
    files = subprocess.run(["git", "--git-dir", str(r.git_dir), "ls-tree", "-r", "--name-only", sha],
                           capture_output=True, text=True, check=True).stdout.split()
    assert files == ["work/a.txt"]


def test_every_checkpoint_is_its_own_commit(repo):
    r, root = repo
    (root / "work" / "a.txt").write_text("v1\n", encoding="utf-8")
    a = r.checkpoint("step 0")
    b = r.checkpoint("step 1")
    (root / "work" / "a.txt").write_text("v2\n", encoding="utf-8")
    c = r.checkpoint("step 2")
    assert len({a, b, c}) == 3 and [m for _, m in r.log()] == ["step 2", "step 1", "step 0"]


def test_restore_puts_back_edits_deletions_and_removes_new_files(repo):
    r, root = repo
    (root / "work" / "a.txt").write_text("v1\n", encoding="utf-8")
    (root / "work" / "gone.txt").write_text("keep me\n", encoding="utf-8")
    sha = r.checkpoint("step 0")
    (root / "work" / "a.txt").write_text("v2\n", encoding="utf-8")
    (root / "work" / "gone.txt").unlink()
    (root / "work" / "new.txt").write_text("made later\n", encoding="utf-8")
    (root / "other" / "keep.txt").write_text("changed outside\n", encoding="utf-8")
    r.checkpoint("step 1")
    r.restore(sha)
    assert (root / "work" / "a.txt").read_text(encoding="utf-8") == "v1\n"
    assert (root / "work" / "gone.txt").read_text(encoding="utf-8") == "keep me\n"
    assert not (root / "work" / "new.txt").exists()
    assert (root / "other" / "keep.txt").read_text(encoding="utf-8") == "changed outside\n"


def test_files_a_gitignore_hides_are_still_captured(repo):
    r, root = repo
    (root / "work" / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (root / "work" / "run.log").write_text("log\n", encoding="utf-8")
    sha = r.checkpoint("step 0")
    (root / "work" / "run.log").write_text("changed\n", encoding="utf-8")
    r.restore(sha)
    assert (root / "work" / "run.log").read_text(encoding="utf-8") == "log\n"


def test_a_file_over_the_limit_is_left_out(repo, monkeypatch):
    r, root = repo
    monkeypatch.setattr(shadow, "BIG_FILE_BYTES", 10)
    (root / "work" / "big.bin").write_bytes(b"x" * 50)
    (root / "work" / "small.txt").write_text("s", encoding="utf-8")
    sha = r.checkpoint("step 0")
    files = subprocess.run(["git", "--git-dir", str(r.git_dir), "ls-tree", "-r", "--name-only", sha],
                           capture_output=True, text=True, check=True).stdout.split()
    assert files == ["work/small.txt"]


def test_the_workspaces_own_repository_is_untouched(repo):
    r, root = repo
    subprocess.run(["git", "init", "-q", str(root / "work")], check=True)
    (root / "work" / "a.txt").write_text("x\n", encoding="utf-8")
    before = subprocess.run(["git", "-C", str(root / "work"), "status", "--porcelain"],
                            capture_output=True, text=True, check=True).stdout
    r.checkpoint("step 0")
    after = subprocess.run(["git", "-C", str(root / "work"), "status", "--porcelain"],
                           capture_output=True, text=True, check=True).stdout
    assert before == after and "a.txt" in after


def test_hooks_never_run(repo):
    r, root = repo
    (root / "work" / "a.txt").write_text("x\n", encoding="utf-8")
    r.ensure()
    hook = r.git_dir / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    assert r.checkpoint("step 0")


def test_no_usable_scope_means_no_checkpoint(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    assert ShadowRepo(tmp_path / "g", root, []).checkpoint("x") is None


def test_missing_git_is_reported_not_raised_as_a_crash(repo, monkeypatch):
    r, _ = repo
    monkeypatch.setattr(shadow.shutil, "which", lambda name: None)
    with pytest.raises(shadow.ShadowUnavailable):
        r.checkpoint("step 0")


def test_restoring_to_an_empty_scope_keeps_the_scope_directory(repo):
    r, root = repo
    sha = r.checkpoint("step 0")
    (root / "work" / "a.txt").write_text("v1\n", encoding="utf-8")
    r.checkpoint("step 1")
    r.restore(sha)
    assert (root / "work").is_dir() and not (root / "work" / "a.txt").exists()


def test_a_path_that_does_not_exist_is_not_a_link(tmp_path):
    from agent.execute.shadow import _is_link

    assert not _is_link(str(tmp_path / "missing"))


def test_the_whole_root_and_protected_state_are_never_scopes(ctx):
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract

    t = create_task(ctx.conn, ctx.log, TaskContract(goal="g", artifact_scope=[".", "anchor", "work"]), Budget(usd=1.0, seconds=60))
    assert ShadowRepo.for_task(ctx, t).scopes == ["work"]


def test_a_git_failure_is_shadow_unavailable_and_a_new_repo_has_no_log(tmp_path):
    from agent.execute.shadow import ShadowUnavailable

    (tmp_path / "w" / "work").mkdir(parents=True)
    repo = ShadowRepo(tmp_path / "s", tmp_path / "w", ["work"])
    assert repo.log() == []
    repo.checkpoint("one")
    with pytest.raises(ShadowUnavailable, match="git diff failed"):
        repo.diff("0" * 40, "1" * 40)


def test_a_link_two_levels_down_is_found(tmp_path):
    (tmp_path / "w" / "work" / "sub").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    from conftest import make_dir_link

    if not make_dir_link(tmp_path / "w" / "work" / "sub" / "d", tmp_path / "out"):
        pytest.skip("cannot make a junction here")
    assert ShadowRepo(tmp_path / "s", tmp_path / "w", ["work"])._links() == ["work/sub/d"]
