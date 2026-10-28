from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agent.execute.codeact import run_code_action
from agent.execute.loop import run_task
from agent.execute.sandbox import run_sandboxed
from agent.kernel.events import sha256_hex
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract

FINAL = '<final>{"answer": "done"}</final>'


def _run_code(ctx, code: str):
    root = Path(ctx.config.root)
    (root / "work" / "notes.txt").write_text("ordinary work\n", encoding="utf-8")
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    t = create_task(ctx.conn, ctx.log,
                    TaskContract(goal="probe", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=300))
    ctx.backends["cloud"].queue(
        '<action tool="run_code">' + json.dumps({"code": code, "timeout_s": 120})
        + "</action>", FINAL)
    run_task(ctx, t.id)
    ev = [(typ, json.loads(p)) for typ, p in ctx.conn.execute(
        "SELECT type, payload_json FROM events WHERE task_id=? ORDER BY seq", (t.id,))]
    intent = next(p for typ, p in ev if typ == "tool.intent" and p["tool"] == "run_code")
    receipt = next(p for typ, p in ev if typ == "tool.receipt" and p["tool"] == "run_code")
    return t, intent, receipt


LONG = "# " + "x" * 400 + "\nprint(open('work/notes.txt').read().strip())\n"


def test_the_full_source_is_recoverable_from_the_record(ctx):
    _, intent, receipt = _run_code(ctx, LONG)
    assert receipt["ok"], receipt
    assert intent["code_sha256"] == sha256_hex(LONG)
    (content,) = ctx.conn.execute("SELECT content FROM artifacts WHERE id=?",
                                  (intent["code_artifact"],)).fetchone()
    assert content == LONG and intent["code_chars"] == len(LONG)
    assert LONG not in intent["preview"], "the preview alone never could answer it"


def _ops(receipt) -> set:
    return {(e["op"], e["path"], bool(e.get("denied"))) for e in receipt["code_touched"]}


def test_what_the_code_touched_is_on_the_receipt(ctx):
    code = "open('work/out.txt', 'w').write(open('work/notes.txt').read())\n"
    _, _, receipt = _run_code(ctx, code)
    ops = _ops(receipt)
    assert ("read", "work/notes.txt", False) in ops, receipt
    assert ("write", "work/out.txt", False) in ops, receipt
    assert receipt["code_denied"] == 0


def test_a_refused_read_is_recorded_as_refused(ctx, tmp_path):
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    outside.write_text("zq", encoding="utf-8")
    try:
        code = (f"try:\n    open({str(outside)!r}).read()\n"
                "except PermissionError:\n    print('refused')\n")
        _, _, receipt = _run_code(ctx, code)
    finally:
        outside.unlink(missing_ok=True)
    want = os.path.normcase(os.path.realpath(outside))
    hits = [e for e in receipt["code_touched"]
            if e.get("denied") and e["op"] == "read"
            and os.path.normcase(e["path"]) == want]
    assert hits and receipt["code_denied"] >= 1, receipt


def test_imports_do_not_flood_the_record(ctx):
    _, _, receipt = _run_code(ctx, "import json, decimal, sympy\nprint(sympy.sqrt(4))\n")
    assert receipt["ok"], receipt
    interp = {os.path.normcase(os.path.realpath(p))
              for p in (sys.prefix, sys.base_prefix, sys.exec_prefix)}
    noisy = [e for e in receipt["code_touched"]
             if any(os.path.normcase(e["path"]).startswith(r) for r in interp)]
    assert not noisy, noisy[:5]


def test_the_observation_the_model_sees_is_unchanged(ctx):
    t, _, _ = _run_code(ctx, "print('hello')\n")
    (content,) = ctx.conn.execute(
        """SELECT a.content FROM events e JOIN artifacts a
             ON a.id = json_extract(e.payload_json, '$.id')
           WHERE e.task_id=? AND e.type='artifact.written'
             AND json_extract(e.payload_json, '$.kind')='tool_output'""",
        (t.id,)).fetchone()
    assert "hello" in content
    assert '"touched"' not in content and "code_touched" not in content


def test_touched_is_null_when_nothing_captured_it():
    r = run_sandboxed("print(1)", allowed_paths=[], timeout_s=120)
    assert r["returncode"] == 0 and r["touched"] is None


def test_in_memory_sqlite_still_runs_in_agent_code(ctx, task):
    res = run_code_action(
        ctx, task, "import sqlite3\n"
        "print(sqlite3.connect(':memory:').execute('select 41+1').fetchone()[0])\n",
        timeout_s=120)
    assert res["returncode"] == 0 and "42" in res["stdout"], res


def test_the_work_directory_is_writable_by_any_name_it_was_given(tmp_path, monkeypatch):
    # The Windows runner's TEMP is an 8.3 short name (C:\Users\RUNNER~1\...);
    # the guard fences by the resolved name, so a write into the work directory
    # under the short one - tempfile's, pytest's - was refused. A symlinked
    # temp directory gives the same two names anywhere symlinks can be made.
    real, link = tmp_path / "real", tmp_path / "link"
    real.mkdir()
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("cannot make a directory symlink here")
    monkeypatch.setattr("tempfile.tempdir", str(link))
    code = ("import os, tempfile\n"
            "open(os.path.join(os.environ['TEMP'], 'x.txt'), 'w').write('1')\n"
            "with tempfile.TemporaryFile() as f:\n"
            "    print('wrote', tempfile.gettempdir() != '')\n")
    r = run_sandboxed(code, protected_paths=[str(tmp_path / "state")], timeout_s=120)
    assert r["returncode"] == 0 and "wrote True" in r["stdout"], r["stderr"][-1500:]
