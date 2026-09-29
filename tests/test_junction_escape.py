"""A directory junction is a link out of the workspace (iteration 3, found
building the file browser, 4.4).

Two halves, each shown red first:

  * The sandbox fenced `os.symlink` - both ends must be in scope - but not the
    audit event `_winapi.CreateJunction`, which needs no privilege on
    Windows. Model-written code could make work/gj point at grading/.
  * The gate judged tool paths by `abspath`, never following links, so
    `read_file("work/gj/t1/verify.py")` was inside scope and named nothing
    protected - and read a grader. However the link came to exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent.execute.sandbox import run_sandboxed
from agent.security.capabilities import gate_tool_call

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions")


def test_sandboxed_code_cannot_make_a_junction_out_of_scope(workspace, tmp_path):
    target = tmp_path / "grading"
    target.mkdir()
    link = workspace / "gj"
    code = ("import _winapi\n"
            f"_winapi.CreateJunction({str(target)!r}, {str(link)!r})\n")
    r = run_sandboxed(code, allowed_paths=[str(workspace)], timeout_s=15)
    assert not link.exists(), "sandboxed code linked the workspace to grading/"
    assert r["returncode"] != 0 and "outside scope" in r["stderr"]


def test_the_gate_does_not_read_through_a_junction(ctx, task, workspace):
    import _winapi

    root = Path(ctx.config.root)
    (root / "grading" / "t1").mkdir(parents=True)
    (root / "grading" / "t1" / "verify.py").write_text("EXPECTED = 42\n", encoding="utf-8")
    _winapi.CreateJunction(str(root / "grading"), str(workspace / "gj"))
    for tool, args in (("read_file", {"path": "work/gj/t1/verify.py"}),
                       ("list_dir", {"path": "work/gj"})):
        d = gate_tool_call(ctx, task, ctx.tools.get(tool), args)
        assert d.verdict == "deny", (tool, d.verdict, d.reason)


def test_a_junction_that_stays_inside_the_workspace_is_fine(ctx, task, workspace):
    import _winapi

    (workspace / "real").mkdir()
    (workspace / "real" / "a.txt").write_text("x", encoding="utf-8")
    _winapi.CreateJunction(str(workspace / "real"), str(workspace / "alias"))
    d = gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": "work/alias/a.txt"})
    assert d.verdict != "deny", d.reason


def test_a_walk_does_not_descend_through_a_junction(ctx, task, workspace):
    """DR5 sweep: grep and glob are allowed on `work` and then walk it
    themselves - through a junction, on Python 3.11, where a junction is not
    a symlink. Each file they touch is judged like any other path."""
    import _winapi

    root = Path(ctx.config.root)
    (root / "grading" / "t1").mkdir(parents=True)
    (root / "grading" / "t1" / "verify.py").write_text("EXPECTED = 42\n", encoding="utf-8")
    (workspace / "mine.txt").write_text("EXPECTED here too\n", encoding="utf-8")
    _winapi.CreateJunction(str(root / "grading"), str(workspace / "gj"))
    g = ctx.tools.get("grep").fn(ctx, task, pattern="EXPECTED", path="work")
    files = {m["file"].replace("\\", "/") for m in g["matches"]}
    assert files == {"mine.txt"}, files
    hits = ctx.tools.get("glob").fn(ctx, task, pattern="**/*.py", path="work")["matches"]
    assert not any("verify.py" in h for h in hits), hits


# AUDIT-ITERATION-3 S1, swept into the sandbox: an NTFS stream name.
def test_S1_sweep_sandboxed_code_cannot_plant_a_protected_file_through_a_stream(tmp_path):
    """DR5: the sandbox's own write fence judged realpath against the protected
    paths too, and `.env::$DATA` is not `.env` to realpath while it does not
    exist."""
    from agent.execute.sandbox import run_sandboxed

    target = tmp_path / ".env"
    code = f"open({str(target) + '::$DATA'!r}, 'w').write('ALPHA_TOKEN=x')\n"
    r = run_sandboxed(code, allowed_paths=[str(tmp_path)], timeout_s=15,
                      protected_paths=(str(target),))
    assert not target.exists(), "sandboxed code planted a protected file"
    assert r["returncode"] != 0


# Mutation testing (2.3) found each of these checks unpinned: the tests above
# all point a junction at the graders, which are PROTECTED - so the scope
# check's real-path half and the protected check's real-path half each hid
# behind the other.
def test_a_junction_to_an_unprotected_place_outside_scope_is_refused(ctx, task, workspace):
    import _winapi

    root = Path(ctx.config.root)
    (root / "notes").mkdir()
    (root / "notes" / "x.txt").write_text("not yours", encoding="utf-8")
    _winapi.CreateJunction(str(root / "notes"), str(workspace / "out"))
    d = gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": "work/out/x.txt"})
    assert d.verdict == "deny" and "outside artifact_scope" in d.reason, d.reason


def test_a_junction_to_protected_state_inside_scope_is_refused(ctx, task, workspace):
    import _winapi

    (workspace / "skills_store").mkdir()
    (workspace / "skills_store" / "s.md").write_text("a skill", encoding="utf-8")
    _winapi.CreateJunction(str(workspace / "skills_store"), str(workspace / "alias"))
    d = gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": "work/alias/s.md"})
    assert d.verdict == "deny", d.reason


def test_a_swapped_link_is_seen_once_the_two_second_bucket_turns(workspace, monkeypatch):
    """The resolution cache promises a swapped link is seen within 2 s. A
    bucket that never turns - or turns every 3 s - breaks that promise."""
    import _winapi

    from agent.security import capabilities as cap

    clock = [3.0]
    monkeypatch.setattr(cap.time, "monotonic", lambda: clock[0])
    (workspace / "skills_store").mkdir()
    (workspace / "skills_store" / "s.md").write_text("a skill", encoding="utf-8")
    (workspace / "alias").mkdir()
    (workspace / "alias" / "s.md").write_text("plain", encoding="utf-8")
    root = str(workspace.parent)
    assert not cap.is_protected("work/alias/s.md", root)
    (workspace / "alias" / "s.md").unlink()
    (workspace / "alias").rmdir()
    _winapi.CreateJunction(str(workspace / "skills_store"), str(workspace / "alias"))
    clock[0] = 5.0                                   # 2 s later: a new bucket
    assert cap.is_protected("work/alias/s.md", root)
