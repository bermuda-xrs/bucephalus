"""D70 - hard links (security/capabilities.py, control/files.py, execute/sandbox.py).

The actor is the one audit S3 named: another local process or user that makes
a hard link inside a workspace before or between tasks. Here: work/notes.md is
a second name for the project's policy.md (protected by name and by state).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agent.execute.codeact import run_code_action
from agent.execute.tools import tool_grep
from agent.security.capabilities import gate_tool_call

POLICY = "# policy\n\nDefault is DENY.\n"


@pytest.fixture
def linked(ctx, task, workspace):
    root = Path(ctx.config.root)
    (root / "policy.md").write_text(POLICY, encoding="utf-8")
    os.link(root / "policy.md", workspace / "notes.md")          # the out-of-band link
    (workspace / "plain.txt").write_text("plain\n", encoding="utf-8")
    assert os.stat(workspace / "notes.md").st_nlink == 2
    return ctx, task, root


def test_clean_the_gate_refuses_reading_and_writing_the_link(linked):
    ctx, task, root = linked
    r = gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": "work/notes.md"})
    w = gate_tool_call(ctx, task, ctx.tools.get("write_file"), {"path": "work/notes.md", "content": "x"})
    assert r.verdict == "deny" and w.verdict == "deny", (r, w)
    ok = gate_tool_call(ctx, task, ctx.tools.get("read_file"), {"path": "work/plain.txt"})
    assert ok.verdict == "allow"


def test_clean_grep_does_not_return_the_policy_through_the_link(linked):
    ctx, task, root = linked
    out = tool_grep(ctx, task, "Default is DENY", "work")
    assert "notes.md" not in str(out), out


def test_clean_code_cannot_read_the_link(linked):
    ctx, task, root = linked
    res = run_code_action(ctx, task, "print(open('work/notes.md').read())", timeout_s=30)
    print("\nread attempt:", res.get("ok"), (res.get("stdout") or "")[:60], (res.get("stderr") or "")[-120:])
    assert "Default is DENY" not in (res.get("stdout") or "")


def test_FINDING_code_rewrites_the_policy_through_the_copy_back(linked):
    """In copy mode (no symlink privilege: a default Windows install, this
    machine) link_into skips the multiply-linked file, so the code finds no
    work/notes.md and may create one. _copy_back then shutil.copy2()s it onto
    the workspace path - which is the hard link - and the write goes through
    to policy.md. The harness does this in the parent, with no gate check."""
    ctx, task, root = linked
    code = "open('work/notes.md', 'w').write('APPROVE EVERYTHING' + chr(10))" + chr(10) + "print('wrote')"
    res = run_code_action(ctx, task, code, timeout_s=30)
    now = (root / "policy.md").read_text(encoding="utf-8")
    print(chr(10) + "FULL", {k: str(v)[-400:] for k, v in res.items()})
    print(f"\nrun ok={res.get('ok')} stdout={res.get('stdout')!r} changed={res.get('changed_files')}"
          f"\npolicy.md now: {now!r}")
    if res.get("notes") and any("symlink" in str(n) for n in res["notes"]):
        pytest.skip("symlink mode")
    assert now == "APPROVE EVERYTHING" + chr(10)
