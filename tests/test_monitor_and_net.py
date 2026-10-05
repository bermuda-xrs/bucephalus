from __future__ import annotations

import pytest

from agent.assure.monitor import flags, parse_findings, run_monitor
from agent.kernel.types import TaskContract


def test_parse_findings_shapes():
    out = parse_findings("warn | wandered outside scope\ncritical | probed after a refusal")
    assert [f.severity for f in out] == ["warn", "critical"]
    assert parse_findings("clean") == []
    assert parse_findings("") == []


def test_monitor_writes_flags_and_touches_nothing_else(ctx, task, scripted):
    from agent.execute.loop import run_task

    scripted("cloud", '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)

    events_before = ctx.conn.execute(
        "SELECT COUNT(*) FROM events WHERE task_id=?", (task.id,)
    ).fetchone()[0]

    ctx.backends["review"].queue(
        "warn | read a file outside the stated objective\n"
        "info | finished faster than the plan predicted"
    )
    n = run_monitor(ctx, task.id, backend_name="review")
    assert n == 2

    rows = flags(ctx.conn, task.id)
    assert {r["severity"] for r in rows} == {"warn", "info"}

    events_after = ctx.conn.execute(
        "SELECT COUNT(*) FROM events WHERE task_id=?", (task.id,)
    ).fetchone()[0]
    assert events_after == events_before, (
        "the monitor wrote to the events table, which feeds agent context"
    )


def test_monitor_clean_writes_nothing(ctx, task, scripted):
    from agent.execute.loop import run_task

    scripted("cloud", '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)
    ctx.backends["review"].queue("clean")
    assert run_monitor(ctx, task.id, backend_name="review") == 0
    assert flags(ctx.conn, task.id) == []


def test_fetcher_refuses_non_http_schemes():
    from agent.net.fetch import FetchError, fetch_with_provenance

    for bad in ("file:///etc/passwd", "ftp://example.com/x", "data:text/html,hi"):
        with pytest.raises(FetchError, match="scheme"):
            fetch_with_provenance(bad)


def test_fetcher_refuses_private_addresses():
    from agent.net.fetch import FetchError, fetch_with_provenance

    for bad in ("http://127.0.0.1/admin", "http://localhost:8080/",
                "http://192.168.1.1/", "http://10.0.0.5/"):
        with pytest.raises(FetchError, match="private|loopback|resolve"):
            fetch_with_provenance(bad)


def test_html_is_reduced_to_text():
    from agent.net.fetch import _Text

    p = _Text()
    p.feed("<head><title>t</title></head><body><script>evil()</script>"
           "<h1>Title</h1><p>Body text here.</p></body>")
    out = p.text()
    assert "Body text here." in out and "Title" in out
    assert "evil()" not in out, "script contents leaked into the agent's context"


def test_web_fetch_output_is_untrusted(ctx, workspace, scripted):
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget

    ctx.web_fetcher = lambda url: "some page text about bearings"
    t = create_task(ctx.conn, ctx.log,
                    TaskContract(goal="read a page", artifact_scope=["work"]),
                    Budget(usd=1.0, seconds=600))
    scripted("cloud", '<action tool="web_fetch">{"url": "https://example.test/x"}</action>',
             '<final>{"answer": "read"}</final>')
    run_task(ctx, t.id)

    taints = {r[0] for r in ctx.conn.execute(
        "SELECT taint FROM artifacts WHERE task_id=? AND kind='tool_output'", (t.id,)
    ).fetchall()}
    assert "untrusted" in taints


def _contract(expected=None, contains=None, exact=False) -> TaskContract:
    schema: dict = {"type": "object", "required": ["total"],
                    "properties": {"total": {"type": "integer"}}}
    if expected is not None:
        schema["x-expected"] = expected
    if contains is not None:
        schema["x-contains"] = contains
    if exact:
        schema["x-exact"] = True
    return TaskContract(goal="g", output_schema=schema, success_criterion="expected_value")


def test_expected_value_catches_a_right_shaped_wrong_answer(ctx):
    from agent.assure.verifiers import ExpectedValueVerifier, JsonSchemaVerifier

    c = _contract(expected={"total": 4825})
    wrong = '{"total": 0}'

    assert JsonSchemaVerifier().check(wrong, c).passed, "shape check should pass"
    r = ExpectedValueVerifier().check(wrong, c)
    assert not r.passed and "4825" in r.observation


def test_expected_value_accepts_the_right_answer(ctx):
    from agent.assure.verifiers import ExpectedValueVerifier

    r = ExpectedValueVerifier().check('{"total": 4825}', _contract(expected={"total": 4825}))
    assert r.passed and r.score == 1.0


def test_expected_value_needs_an_expectation(ctx):
    from agent.assure.verifiers import ExpectedValueVerifier

    r = ExpectedValueVerifier().check('{"total": 1}', _contract())
    assert not r.passed and "x-expected" in r.observation


def test_string_comparison_is_forgiving_unless_exact(ctx):
    from agent.assure.verifiers import ExpectedValueVerifier

    def contract(exact: bool) -> TaskContract:
        schema = {"type": "object", "required": ["name"],
                  "properties": {"name": {"type": "string"}},
                  "x-expected": {"name": "Drive Belt"}}
        if exact:
            schema["x-exact"] = True
        return TaskContract(goal="g", output_schema=schema,
                            success_criterion="expected_value")

    assert ExpectedValueVerifier().check('{"name": "  drive   belt "}',
                                         contract(exact=False)).passed
    assert not ExpectedValueVerifier().check('{"name": "drive belt"}',
                                             contract(exact=True)).passed


def test_contains_all_reports_what_is_missing(ctx):
    from agent.assure.verifiers import ContainsAllVerifier

    schema = {"type": "object", "required": ["summary"],
              "properties": {"summary": {"type": "string"}},
              "x-contains": ["compressor", "filter"]}
    c = TaskContract(goal="g", output_schema=schema, success_criterion="contains_all")

    ok = ContainsAllVerifier().check('{"summary":"the compressor and a filter"}', c)
    assert ok.passed

    bad = ContainsAllVerifier().check('{"summary":"the compressor"}', c)
    assert not bad.passed and "filter" in bad.observation and bad.score == 0.5
