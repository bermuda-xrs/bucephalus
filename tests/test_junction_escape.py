from __future__ import annotations

import os
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


def test_S1_sweep_sandboxed_code_cannot_plant_a_protected_file_through_a_stream(tmp_path):
    from agent.execute.sandbox import run_sandboxed

    target = tmp_path / ".env"
    code = f"open({str(target) + '::$DATA'!r}, 'w').write('BUCEPHALUS_TOKEN=x')\n"
    r = run_sandboxed(code, allowed_paths=[str(tmp_path)], timeout_s=15,
                      protected_paths=(str(target),))
    assert not target.exists(), "sandboxed code planted a protected file"
    assert r["returncode"] != 0


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
    clock[0] = 5.0
    assert cap.is_protected("work/alias/s.md", root)


def _grader_and_link(ctx, workspace, name="innocent.txt"):
    root = Path(ctx.config.root)
    (root / "grading" / "t1").mkdir(parents=True)
    grader = root / "grading" / "t1" / "verify.py"
    grader.write_text("EXPECTED = 4825\n", encoding="utf-8")
    link = workspace / name
    os.link(grader, link)
    return grader, link


def test_D70_the_gate_refuses_a_hard_link_to_protected_state(ctx, task, workspace):
    _grader_and_link(ctx, workspace)
    root = Path(ctx.config.root)
    (root / "policy.md").write_text("# policy\n", encoding="utf-8")
    os.link(root / "policy.md", workspace / "notes.md")
    for path in ("work/innocent.txt", "work/notes.md"):
        d = gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": path})
        assert d.verdict == "deny", (path, d.verdict, d.reason)


def test_D70_a_hard_link_between_workspace_files_is_fine(ctx, task, workspace):
    (workspace / "a.txt").write_text("mine", encoding="utf-8")
    os.link(workspace / "a.txt", workspace / "b.txt")
    d = gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": "work/b.txt"})
    assert d.verdict != "deny", d.reason


def test_D70_the_file_browser_hides_it(ctx, task, workspace):
    from agent.control import files

    _grader_and_link(ctx, workspace)
    listed = {e["name"] for e in files.browse(ctx, task, "work")["entries"]}
    assert "innocent.txt" not in listed, listed
    with pytest.raises(files.Refused):
        files.browse(ctx, task, "work/innocent.txt")


@pytest.mark.parametrize("copy_mode", [False, True])
def test_D70_sandboxed_code_cannot_read_it(ctx, workspace, monkeypatch, copy_mode):
    import agent.execute.sandbox as sb

    grader, _ = _grader_and_link(ctx, workspace)
    if copy_mode:
        monkeypatch.setattr(sb.os, "symlink", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    code = ("try:\n    print(open('work/innocent.txt').read())\n"
            "except OSError as e:\n    print('REFUSED', type(e).__name__)\n")
    r = run_sandboxed(code, allowed_paths=[(str(workspace), "work")], timeout_s=30,
                      protected_paths=[str(grader.parent.parent)])
    assert "4825" not in r["stdout"] and "REFUSED" in r["stdout"], r
