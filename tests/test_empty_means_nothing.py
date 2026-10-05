from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from agent.control.guard import AccessGuard
from agent.delegate.contracts import scope_widens
from agent.execute.codeact import run_code_action
from agent.execute.loop import run_task
from agent.execute.tools import ToolSpec
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, Taint, TaskContract
from agent.security.capabilities import _GLOB_ARGS, _PATH_KEYS, gate_tool_call
from agent.skills.gate import ContaminationError, check_contamination

FINAL = '<final>{"answer": "done"}</final>'


def _task(ctx, scope):
    return create_task(ctx.conn, ctx.log,
                       TaskContract(goal="probe", artifact_scope=scope),
                       Budget(usd=1.0, seconds=300))


@pytest.fixture
def notes(ctx):
    (Path(ctx.config.root) / "work" / "notes.txt").write_text(
        "ordinary work\n", encoding="utf-8")
    return ctx


UNDECLARED = {
    "read": ("read_file", {"path": "work/notes.txt"}),
    "write": ("write_file", {"path": "work/new.txt", "content": "x"}),
    "edit": ("edit_file", {"path": "work/notes.txt", "old": "a", "new": "b"}),
    "list": ("list_dir", {"path": "work"}),
    "grep": ("grep", {"pattern": "work", "path": "work"}),
    "glob": ("glob", {"pattern": "*.txt", "path": "work"}),
}


@pytest.mark.parametrize("route", list(UNDECLARED))
def test_a_task_that_declared_no_scope_may_touch_no_path(notes, route):
    tool, args = UNDECLARED[route]
    d = gate_tool_call(notes, _task(notes, []), notes.tools.get(tool), args)
    assert d.verdict == "deny" and "declared no artifact_scope" in d.reason, d


def test_a_declared_scope_is_unaffected(notes):
    t = _task(notes, ["work"])
    assert gate_tool_call(notes, t, notes.tools.get("read_file"),
                          {"path": "work/notes.txt"}).allowed


@pytest.fixture
def outside(tmp_path):
    p = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    p.write_text("zq-outside-marker", encoding="utf-8")
    yield p
    p.unlink(missing_ok=True)


@pytest.mark.parametrize("scope", [[], ["work"]], ids=["no-scope", "narrow"])
def test_code_cannot_read_what_the_task_did_not_declare(notes, outside, scope):
    res = run_code_action(notes, _task(notes, scope),
                          f"print(open({str(outside)!r}).read())", timeout_s=120)
    assert "zq-outside-marker" not in res.get("stdout", ""), res
    assert "sandbox: read outside scope" in res.get("stderr", ""), res


def test_code_still_reads_its_scope_and_imports(notes):
    res = run_code_action(
        notes, _task(notes, ["work"]),
        "import json, math, decimal, sympy\n"
        "print(open('work/notes.txt').read().strip(), json.dumps(round(math.pi, 3)),"
        " sympy.sqrt(4))", timeout_s=180)
    assert res["returncode"] == 0, res
    assert "ordinary work 3.142 2" in res["stdout"], res


def test_a_parent_that_declared_no_scope_can_give_a_child_none():
    assert scope_widens([], ["work"]) == ["work"]
    assert scope_widens(["work"], ["work/sub"]) == []


def test_the_trusted_tier_is_human_only_whatever_the_config(notes):
    notes.config.autonomy.auto_review = True
    notes.config.autonomy.human_only_tiers = []
    notes.backends["review"].queue(json.dumps({"verdict": "approve",
                                               "rationale": "looks fine"}))
    ran = _launch_tool(notes)
    t = _task(notes, ["work"])
    notes.backends["cloud"].queue('<action tool="launch">{"service": "prod"}</action>',
                                  FINAL)
    r = run_task(notes, t.id)
    types = [x for (x,) in notes.conn.execute(
        "SELECT type FROM events WHERE task_id=?", (t.id,))]
    assert "human.denied" not in types, "refused for some other reason: test is vacuous"
    assert r.status == "paused" and ran == [], "a trusted action ran unapproved"
    assert "review.requested" not in types, "the deputy was consulted on a trusted action"


def _launch_tool(ctx) -> list:
    ran: list = []
    ctx.tools.register(ToolSpec(
        name="launch", fn=lambda ctx, task, service: ran.append(service) or {"ok": True},
        schema={"type": "object", "properties": {"service": {"type": "string"}}},
        side_effecting=True, max_arg_taint=Taint.USER,
        output_taint=Taint.TRUSTED, required_capability="trusted",
        description="test-only trusted-tier tool"))
    return ran


def test_the_reviewer_itself_escalates_the_trusted_tier(notes):
    from agent.plan.review import ReviewRequest, auto_review

    notes.config.autonomy.human_only_tiers = []
    notes.backends["review"].queue(json.dumps({"verdict": "approve",
                                               "rationale": "looks fine"}))
    _launch_tool(notes)
    t = _task(notes, ["work"])
    d = auto_review(notes, ReviewRequest(
        task_id=t.id, step_id="0", tool="launch", args={"service": "prod"},
        arg_taint=Taint.USER, compact_transcript="", plan_step=None,
        artifact_scope=["work"]))
    assert d.verdict == "escalate", d


def _skill(ctx, body, provenance):
    return ctx.skills.store(f"probe-{abs(hash(body)) % 997}", body,
                            {"applies_when": "x"}, provenance)


def test_an_unknown_drafting_set_certifies_no_trial(ctx):
    sid = _skill(ctx, "Undeclared drafting.\n", {})
    with pytest.raises(ContaminationError, match="no drafting set"):
        check_contamination(ctx, sid, "E01")


def test_an_unknown_skill_certifies_no_trial(ctx):
    with pytest.raises(ContaminationError):
        check_contamination(ctx, "s_does_not_exist", "E01")


def test_a_declared_drafting_set_is_honoured(ctx):
    sid = _skill(ctx, "Drafted from E01.\n", {"drafted_from": ["E01"]})
    with pytest.raises(ContaminationError, match="used to draft"):
        check_contamination(ctx, sid, "E01")
    check_contamination(ctx, sid, "E02")
    none = _skill(ctx, "Drafted from none.\n", {"drafted_from": []})
    check_contamination(ctx, none, "E01")


def _status(app, headers) -> int:
    async def run():
        sent = []
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.0",
                 "method": "GET", "scheme": "http", "path": "/health",
                 "raw_path": b"/health", "query_string": b"", "root_path": "",
                 "headers": headers, "client": ("127.0.0.1", 50000),
                 "server": ("127.0.0.1", 8901)}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        return next(m["status"] for m in sent if m["type"] == "http.response.start")
    return asyncio.run(run())


def test_a_request_with_no_host_header_is_refused():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    inner = Starlette(routes=[Route("/health", lambda r: PlainTextResponse("ok"))])
    app = AccessGuard(inner, token="t", allow_remote=False)
    assert _status(app, [(b"host", b"127.0.0.1:8901")]) == 200
    assert _status(app, []) == 403, "an absent Host skipped the rebinding check"


NOT_PATHS = {
    "append_to_outbox": {"to", "body"}, "edit_file": {"old", "new", "count"},
    "expand_artifact": {"id"}, "git_push": {"remote", "branch"},
    "memory_consolidate": {"tier"}, "memory_read": {"tier"},
    "memory_write": {"text", "tier"}, "parallel": {"calls", "step"},
    "run_code": {"code", "timeout_s"},
    "search_history": {"query", "limit", "offset"}, "web_fetch": {"url"},
    "web_search": {"query", "limit"}, "write_file": {"content"},
    "grep": {"pattern"},
}


def test_every_tool_argument_is_classified(ctx):
    for name in ctx.tools.names():
        params = set(inspect.signature(ctx.tools.get(name).fn).parameters)
        params -= {"ctx", "task"}
        glob = {_GLOB_ARGS[name]} if name in _GLOB_ARGS else set()
        unclassified = params - _PATH_KEYS - glob - NOT_PATHS.get(name, set())
        assert not unclassified, (
            f"{name}: {sorted(unclassified)} - a path or not? Classify it before "
            "it ships, or the path checks never see it.")


def test_a_drafting_set_is_declared_once_and_never_rewritten(ctx):
    sid = _skill(ctx, "Declared later.\n", {"drafted_by": "human"})
    with pytest.raises(ContaminationError, match="--declare-drafted-from"):
        check_contamination(ctx, sid, "E01")
    prov = ctx.skills.declare_drafted_from(sid, ["E01"])
    assert prov["drafted_from"] == ["E01"] and prov["trials_before_declaration"] == 0
    check_contamination(ctx, sid, "E02")
    with pytest.raises(ValueError, match="never overwritten"):
        ctx.skills.declare_drafted_from(sid, [])
    assert ctx.skills.load(sid, verify=False)["provenance"]["drafted_from"] == ["E01"]
    with pytest.raises(ContaminationError, match="used to draft"):
        check_contamination(ctx, sid, "E01")


def test_a_declaration_after_trials_says_so(ctx):
    sid = _skill(ctx, "Trials first.\n", {"drafted_by": "human"})
    ctx.conn.execute(
        "INSERT INTO skill_trials(skill_id, task_key, arm, success, paid_tokens,"
        " free_tokens, wall_ms, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (sid, "E01", "control", 1, 0, 0, 0, 0.0))
    ctx.conn.commit()
    assert ctx.skills.declare_drafted_from(sid, [])["trials_before_declaration"] == 1


def test_none_declares_the_empty_set():
    from agent.cli import _parse_drafted_from

    assert _parse_drafted_from("none") == [] and _parse_drafted_from("NONE ") == []
    assert _parse_drafted_from("E01, M11") == ["E01", "M11"]
