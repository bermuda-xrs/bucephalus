from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from agent.execute.codeact import run_code_action


def _try(ctx, task, *lines):
    body = "".join(f"    {ln}\n" for ln in lines)
    code = ("import sqlite3\ntry:\n" + body + "    print('OPENED')\n"
            "except Exception as e:\n    print('REFUSED', type(e).__name__, e)\n")
    return run_code_action(ctx, task, code, timeout_s=120)


def _fwd(ctx) -> str:
    return str(Path(ctx.config.db_path).resolve()).replace("\\", "/")


CONNECT_ROUTES = {
    "uri": lambda f: f"'file:' + {f!r} + '?mode=ro'",
    "uri-triple-slash": lambda f: f"'file:///' + {f!r} + '?mode=ro'",
    "uri-localhost": lambda f: f"'file://localhost/' + {f!r} + '?mode=ro'",
    "uri-percent": lambda f: repr("file:" + f[: -len("agent.db")] + "%61gent.db?mode=ro"),
}


@pytest.mark.parametrize("route", list(CONNECT_ROUTES))
def test_no_uri_spelling_reaches_the_project_database(ctx, task, route):
    res = _try(ctx, task, f"c = sqlite3.connect({CONNECT_ROUTES[route](_fwd(ctx))}, uri=True)",
               "c.execute('select count(*) from sqlite_master').fetchone()")
    assert "OPENED" not in res["stdout"], res
    assert "sandbox: protected path" in res["stdout"], res


def test_attach_cannot_reach_the_project_database(ctx, task):
    db = str(Path(ctx.config.db_path).resolve())
    res = _try(ctx, task, "c = sqlite3.connect(':memory:')",
               f"c.execute('ATTACH DATABASE ? AS p', ({db!r},))",
               "c.execute('select count(*) from p.sqlite_master').fetchone()")
    assert "OPENED" not in res["stdout"] and "not authorized" in res["stdout"], res
    assert any(e["op"] == "attach" and e.get("denied")
               for e in res["touched"]["entries"]), res["touched"]


def test_an_attach_whose_target_is_not_known_yet_is_refused(ctx, task):
    db = str(Path(ctx.config.db_path).resolve())
    head, tail = db[: -len("agent.db")], "agent.db"
    res = _try(ctx, task, "c = sqlite3.connect(':memory:')",
               f"c.execute(\"ATTACH DATABASE {head!r} || {tail!r} AS p\")",
               "c.execute('select count(*) from p.sqlite_master').fetchone()")
    assert "OPENED" not in res["stdout"] and "not authorized" in res["stdout"], res


def test_attach_by_uri_cannot_either(ctx, task):
    res = _try(ctx, task, "c = sqlite3.connect('file::memory:', uri=True)",
               f"c.execute(\"ATTACH DATABASE 'file:{_fwd(ctx)}?mode=ro' AS p\")",
               "c.execute('select count(*) from p.sqlite_master').fetchone()")
    assert "OPENED" not in res["stdout"] and "not authorized" in res["stdout"], res


def test_vacuum_into_cannot_write_outside_scope(ctx, task, tmp_path):
    target = tmp_path.parent / f"vacuum-{tmp_path.name}.db"
    try:
        res = _try(ctx, task, "c = sqlite3.connect(':memory:')",
                   "c.execute('create table t(x)')",
                   f"c.execute('VACUUM INTO ?', ({str(target)!r},))")
        assert "OPENED" not in res["stdout"] and not target.exists(), res
    finally:
        target.unlink(missing_ok=True)


def test_sqlite_cannot_create_a_file_outside_scope(ctx, task):
    target = Path(sys.prefix) / "zz-alpha-sqlite-probe.db"
    try:
        res = _try(ctx, task, f"c = sqlite3.connect({str(target)!r})",
                   "c.execute('create table t(x)')", "c.commit()")
        assert "OPENED" not in res["stdout"] and not target.exists(), res
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)


def test_loading_a_native_extension_is_refused(ctx, task):
    if not hasattr(sqlite3.Connection, "enable_load_extension"):
        pytest.skip("this Python's sqlite3 cannot load extensions at all")
    res = _try(ctx, task, "c = sqlite3.connect(':memory:')",
               "c.enable_load_extension(True)")
    assert "OPENED" not in res["stdout"] and "sandbox:" in res["stdout"], res


def test_ordinary_databases_still_work(ctx, task):
    res = _try(ctx, task,
               "sqlite3.connect(':memory:').execute('select 1').fetchone()",
               "sqlite3.connect('file::memory:?cache=shared', uri=True).execute('select 1')",
               "c = sqlite3.connect('work/scratch.db')",
               "c.execute('create table if not exists t(x)')",
               "c.execute(\"ATTACH DATABASE 'work/other.db' AS o\")",
               "c.execute('create table if not exists o.t(x)')",
               "c.commit()")
    assert "OPENED" in res["stdout"], res
