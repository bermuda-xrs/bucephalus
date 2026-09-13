"""Every artifact holding state the scope checks protect, unreachable by FILE
path from ANY task scope - not merely by query.

Phase 2 isolated `search_history` and `expand_artifact`; Phase 5 scoped the
tools' arguments. Both guard the QUERY path. The file path went round them
four ways, each measured before it was fixed:

  1. `read_file` on a protected file from a root-wide or empty scope - the
     database of ANOTHER config, the graders, the run records, the token
  2. `grep` or `glob` with the path OMITTED: the gate never saw the defaulted
     ".", so a narrow ["work"] scope searched the whole project
  3. a file-glob containing "..": pathlib follows it out of the directory
  4. `run_code`: the sandbox's audit hook restricted writes, never reads, and
     a subprocess is a process the hook is not in

A control that guards a query and not a read is not a control. Every case
here was run against the code before the fix and goes red there.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent.execute.codeact import run_code_action
from agent.execute.tools import tool_glob, tool_grep, tool_list_dir
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, Taint, TaskContract
from agent.memory.store import MemoryStore, record_fingerprint
from agent.security.capabilities import gate_tool_call

MARK = "zq-protected-marker"

# Every location, relative to the project root. The memory files are added
# per test, because tier 1 lives outside the root.
LOCATIONS = [
    "agent.db", "agent.db-wal",               # this config's live database
    "agent.local.db", "agent.local.db-wal",   # ANOTHER config's database
    "config.local.yaml", "policy.md", ".env", ".alpha_token",
    "anchor/chain.anchor",
    "skills_store/s_x/tests/holdout/1.txt",   # a skill's held-out checks
    "grading/E01/verify.py",                  # the suite's graders
    "bench/tasks/E01/verify.py",              # the harness, expected answers
    "runs/ledger.partial.jsonl",              # other attempts' results
    "docs/evidence/result.json",              # published results
    "graders/E07/verify.py",                  # THIS task's own grader_dir
]
SCOPES = {"root": ["."], "none": [], "narrow": ["work"]}


@pytest.fixture
def project(ctx, tmp_path):
    root = Path(ctx.config.root)
    for rel in LOCATIONS:
        p = root / rel
        if rel.startswith("agent.db") or rel.startswith("agent.local.db"):
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"{MARK} {rel}\n", encoding="utf-8")
    # A second config beside the active one, naming its own database - a
    # real one, with a row in it, so reading it proves something.
    (root / "config.local.yaml").write_text(
        'db_path: "./agent.local.db"\nanchor_path: "./anchor/chain.local.anchor"\n'
        f'skills_path: "./skills_store"\n# {MARK}\n', encoding="utf-8")
    other = sqlite3.connect(str(root / "agent.local.db"), isolation_level=None)
    other.execute("PRAGMA journal_mode=WAL")
    other.execute("CREATE TABLE answers(v TEXT)")
    other.execute(f"INSERT INTO answers VALUES ('{MARK} another arm answer')")
    ctx._other_db = other                       # held open: rows stay in -wal
    ctx.memory = MemoryStore(tmp_path / "memory")
    ctx.memory.write("memory", f"{MARK} a tier-one fact", Taint.USER)
    record_fingerprint(ctx.memory)
    (root / "work" / "notes.txt").write_text("ordinary work\n", encoding="utf-8")
    yield ctx
    other.close()


def _task(ctx, scope, grader="graders/E07"):
    return create_task(ctx.conn, ctx.log,
                       TaskContract(goal="probe", artifact_scope=scope,
                                    grader_dir=grader),
                       Budget(usd=1.0, seconds=300))


def _everything(ctx):
    return LOCATIONS + [str(ctx.memory.root / "MEMORY.md"),
                        str(ctx.memory.root / ".fingerprint")]


# -- route 1: a file read ------------------------------------------------------

@pytest.mark.parametrize("scope", list(SCOPES))
def test_no_protected_file_is_readable_by_path(project, scope):
    t = _task(project, SCOPES[scope])
    spec = project.tools.get("read_file")
    # Anything short of DENY is reachable: a verdict of needs_approval is one
    # human click from executing, and since f11be4d that click executes.
    reachable = [loc for loc in _everything(project)
                 if gate_tool_call(project, t, spec, {"path": loc}).verdict != "deny"]
    assert reachable == [], f"readable by path from a {scope} scope: {reachable}"


@pytest.mark.parametrize("scope", list(SCOPES))
def test_no_protected_file_is_writable_by_path(project, scope):
    t = _task(project, SCOPES[scope])
    spec = project.tools.get("write_file")
    reachable = [loc for loc in _everything(project)
                 if gate_tool_call(project, t, spec,
                                   {"path": loc, "content": "x"}).verdict != "deny"]
    assert reachable == [], f"writable by path from a {scope} scope: {reachable}"


# -- routes 2 and 3: traversal, omitted paths, and ".." ------------------------

TRAVERSALS = {
    "grep-no-path": ("grep", {"pattern": MARK}),
    "grep-root": ("grep", {"pattern": MARK, "path": "."}),
    "grep-dotdot-glob": ("grep", {"pattern": MARK, "path": "work",
                                  "glob": "../**/*"}),
    "glob-dotdot": ("glob", {"pattern": "../**/*", "path": "work"}),
    "glob-no-path": ("glob", {"pattern": "**/*"}),
    "list-no-path": ("list_dir", {}),
}
FNS = {"grep": tool_grep, "glob": tool_glob, "list_dir": tool_list_dir}


@pytest.mark.parametrize("scope", list(SCOPES))
@pytest.mark.parametrize("route", list(TRAVERSALS))
def test_no_traversal_reaches_protected_state(project, scope, route):
    tool, args = TRAVERSALS[route]
    t = _task(project, SCOPES[scope])
    if not gate_tool_call(project, t, project.tools.get(tool), args).allowed:
        return
    out = json.dumps(FNS[tool](project, t, **args))
    leaked = [s for s in (MARK, "verify.py", "agent.local.db", ".alpha_token")
              if s in out]
    assert not leaked, f"{route} from a {scope} scope reached {leaked}"


def test_ordinary_work_in_scope_is_unaffected(project):
    t = _task(project, ["work"])
    for tool, args in [("read_file", {"path": "work/notes.txt"}),
                       ("grep", {"pattern": "ordinary", "path": "work"}),
                       ("glob", {"pattern": "*.txt", "path": "work"}),
                       ("list_dir", {"path": "work"})]:
        assert gate_tool_call(project, t, project.tools.get(tool), args).allowed, \
            (tool, args)


# -- route 4: code ------------------------------------------------------------

def test_run_code_will_not_link_protected_state_into_its_sandbox(project):
    """The sandbox links every artifact_scope entry into its working
    directory. ["."] would carry the database, the graders and the token in."""
    t = _task(project, ["."])
    d = gate_tool_call(project, t, project.tools.get("run_code"), {"code": "print(1)"})
    assert d.allowed is False and "sandbox" in d.reason


def _target(project, which):
    root = Path(project.config.root)
    return {"grader": str(root / "grading" / "E01" / "verify.py"),
            "other-db": str(root / "agent.local.db"),
            "memory": str(project.memory.root / "MEMORY.md")}[which]


CODE = {
    "open-grader": ("print(open({p!r}).read())", "grader", MARK),
    "open-memory": ("print(open({p!r}).read())", "memory", MARK),
    "sqlite3-other-db": ("import sqlite3\n"
                         "print(sqlite3.connect({p!r}).execute("
                         "'select v from answers').fetchall())", "other-db", MARK),
    "listdir-graders": ("import os\nprint(os.listdir(os.path.dirname({p!r})))",
                        "grader", "verify.py"),
    "subprocess-grader": ("import subprocess\n"
                          "print(subprocess.run(['cmd', '/c', 'type', {p!r}],"
                          " capture_output=True, text=True).stdout)",
                          "grader", MARK),
}


@pytest.mark.parametrize("route", list(CODE))
def test_code_cannot_read_protected_state_by_absolute_path(project, route):
    """From a NARROW scope. The gate cannot see inside code, so this is the
    sandbox's own check: reads, sqlite connections, listings and process
    starts against protected paths are refused inside the child."""
    snippet, which, leak = CODE[route]
    t = _task(project, ["work"])
    res = run_code_action(project, t, snippet.format(p=_target(project, which)),
                          timeout_s=120)
    # STDOUT, not the whole result: a refused call's traceback echoes its own
    # source line, target path included, which reads as a leak and is not one.
    assert leak not in res.get("stdout", ""), (
        f"{route}: the sandbox let code reach it: {res.get('stdout', '')[:300]}")
    assert "sandbox:" in res.get("stderr", ""), (
        f"{route}: not refused by the sandbox: {res.get('stderr', '')[-300:]}")


def test_code_can_still_read_its_own_scope(project):
    t = _task(project, ["work"])
    out = json.dumps(run_code_action(
        project, t, "print(open('work/notes.txt').read())", timeout_s=120))
    assert "ordinary work" in out
