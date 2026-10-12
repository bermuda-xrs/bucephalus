from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from agent.config import McpServerConfig
from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.proc import process_table

FAKE = str(Path(__file__).parent / "fake_mcp_server.py")
FINAL = '<final>{"answer": "ok"}</final>'


def _call(tool, **args):
    return f'<action tool="{tool}">' + json.dumps(args) + "</action>"


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


@pytest.fixture
def mcp(ctx, task, workspace):
    ctx.config.autonomy.approve_scoped_writes = True

    def go(*script, read_only=(), timeout_s=30, env=None, argv=None):
        ctx.config.mcp_servers = {"fake": McpServerConfig(
            argv=argv or [sys.executable, FAKE], read_only=list(read_only), timeout_s=timeout_s, env=env or {})}
        ctx.backends["cloud"].queue(*script)
        res = run_task(ctx, task.id)
        return res, ctx.backends["cloud"].calls

    return go


def test_there_are_no_servers_by_default(ctx):
    assert ctx.config.mcp_servers == {}


def test_the_servers_tools_appear_and_work(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__add", a=2, b=3), FINAL, read_only=["add"])
    assert "mcp__fake__add" in calls[0]["prefix"]
    assert "---" + chr(10) + "5" in calls[1]["tail"]


def test_the_servers_own_words_never_reach_the_trusted_prefix(ctx, mcp):
    _, calls = mcp(FINAL)
    p = calls[0]["prefix"]
    assert "mcp__fake__echo" in p and "IGNORE ALL PREVIOUS" not in p and "hostile" not in p


def test_a_tool_not_declared_read_only_is_a_writing_tool(ctx, mcp):
    mcp(FINAL, read_only=["add"])
    assert ctx.tools.get("mcp__fake__add").side_effecting is False
    assert ctx.tools.get("mcp__fake__echo").side_effecting is True


def test_its_output_is_untrusted_and_cannot_feed_a_writing_call(ctx, task, mcp, workspace):
    res, calls = mcp(_call("mcp__fake__echo", text="launch-code-7731"),
                     _call("mcp__fake__write_note", path="work/n.txt", text="launch-code-7731"), FINAL,
                     read_only=["echo"])
    assert "taint=untrusted" in calls[1]["tail"]
    assert not (workspace / "n.txt").exists()
    assert "requires <=user arguments, args are untrusted" in calls[2]["tail"]


def test_a_path_argument_still_has_to_be_inside_the_scope(ctx, mcp, tmp_path):
    _, calls = mcp(_call("mcp__fake__write_note", path="../escape.txt", text="x"), FINAL)
    assert "outside artifact_scope" in calls[1]["tail"] and not (tmp_path.parent / "escape.txt").exists()


def test_a_writing_call_inside_the_scope_goes_through(ctx, mcp, workspace):
    mcp(_call("mcp__fake__write_note", path=str(workspace / "n.txt"), text="hello"), FINAL)
    assert (workspace / "n.txt").read_text(encoding="utf-8") == "hello"


def test_a_server_error_comes_back_as_a_failed_result(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__boom"), FINAL, read_only=["boom"])
    assert "it broke" in calls[1]["tail"] and '"ok": false' in calls[1]["tail"]


def test_a_slow_call_times_out_and_the_server_is_killed(ctx, task, mcp):
    pid_of = {}
    t0 = time.time()
    _, calls = mcp(_call("mcp__fake__pid"), _call("mcp__fake__slow"), _call("mcp__fake__add", a=1, b=1), FINAL,
                   read_only=["pid", "slow", "add"], timeout_s=2)
    assert time.time() - t0 < 30
    assert "timed out" in calls[2]["tail"]
    assert "not running" in calls[3]["tail"]
    pid_of["p"] = int(calls[1]["tail"].split("---" + chr(10), 1)[1].split(chr(10))[0])
    assert pid_of["p"] not in {p for p, _, _ in process_table()}


def test_closing_the_context_kills_the_server(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__pid"), FINAL, read_only=["pid"])
    pid = int(calls[1]["tail"].split("---" + chr(10), 1)[1].split(chr(10))[0])
    assert pid in {p for p, _, _ in process_table()}
    ctx.close()
    deadline = time.time() + 15
    while time.time() < deadline and pid in {p for p, _, _ in process_table()}:
        time.sleep(0.2)
    assert pid not in {p for p, _, _ in process_table()}


def test_a_server_that_will_not_start_is_reported_and_the_task_goes_on(ctx, task, mcp):
    res, calls = mcp(FINAL, env={"FAKE_MCP_EXIT_AT_START": "1"})
    assert res.status.startswith("done") and "mcp__fake__add" not in calls[0]["prefix"]
    assert _events(ctx, task, "mcp.failed")[0]["server"] == "fake"


def test_the_small_kit_is_never_widened(tmp_path, workspace, task):
    from agent.context import AgentContext
    from conftest import make_config

    c = AgentContext(make_config(tmp_path))
    c.backends["cloud"].cfg.tool_profile = "small"
    c.close()
    c = AgentContext(make_config(tmp_path), backends=c.backends)
    c.config.mcp_servers = {"fake": McpServerConfig(argv=[sys.executable, FAKE])}
    t = create_task(c.conn, c.log, task.contract, task.budget)
    c.backends["cloud"].queue(FINAL)
    run_task(c, t.id)
    assert not [n for n in c.tools.names() if n.startswith("mcp__")]
    assert "mcp.skipped" in {r[0] for r in c.conn.execute("SELECT type FROM events WHERE task_id=?", (t.id,))}
    c.close()


@pytest.mark.parametrize("origin", ["telegram", "schedule"])
def test_unattended_origins_cannot_use_them(ctx, task, mcp, origin):
    ctx.conn.execute("UPDATE tasks SET origin=? WHERE id=?", (origin, task.id))
    ctx.conn.commit()
    ctx._origin.pop(task.id, None)
    _, calls = mcp(_call("mcp__fake__add", a=1, b=1), FINAL, read_only=["add"])
    assert "not available to this agent" in calls[1]["tail"]


def test_a_path_like_argument_under_any_name_is_scope_checked(ctx, mcp, tmp_path):
    _, calls = mcp(_call("mcp__fake__where", repo_path=str(tmp_path.parent)), FINAL, read_only=["where"])
    assert "DENIED by the capability gate" in calls[1]["tail"] and "at:" not in calls[1]["tail"]


def test_the_same_argument_inside_the_scope_goes_through(ctx, mcp, workspace):
    _, calls = mcp(_call("mcp__fake__where", repo_path="work"), FINAL, read_only=["where"])
    assert "at:work" in calls[1]["tail"]


def test_a_plain_string_is_left_alone_but_a_path_under_any_key_is_not(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note="Europe/London"), FINAL, read_only=["where"])
    assert "at:work" in calls[1]["tail"]


def test_an_absolute_or_traversing_string_is_checked_whatever_its_key(ctx, mcp, tmp_path):
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note="/etc/passwd"), FINAL, read_only=["where"])
    assert "outside artifact_scope" in calls[1]["tail"] or "DENIED" in calls[1]["tail"]


def test_the_original_key_the_model_may_send_is_checked_too(ctx, mcp, tmp_path):
    _, calls = mcp(_call("mcp__fake__hyphen", **{"repo-path": str(tmp_path.parent)}), FINAL, read_only=["hyphen"])
    assert "hyphen-at:" not in calls[1]["tail"]


def test_a_list_of_paths_is_checked_element_by_element(ctx, mcp, workspace, tmp_path):
    _, calls = mcp(_call("mcp__fake__many", files=["work/a.txt", "../outside.txt"]), FINAL, read_only=["many"])
    assert "many:" not in calls[1]["tail"] and ("outside artifact_scope" in calls[1]["tail"] or "DENIED" in calls[1]["tail"])


def test_a_list_of_paths_inside_the_scope_goes_through(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__many", files=["work/a.txt", "work/b.txt"]), FINAL, read_only=["many"])
    assert "many:work/a.txt,work/b.txt" in calls[1]["tail"]


def test_an_argument_the_servers_schema_does_not_declare_is_refused(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", surprise="x"), FINAL, read_only=["where"])
    assert "at:" not in calls[1]["tail"] and "surprise" in calls[1]["tail"]


def test_a_sibling_directory_outside_the_scope_is_refused_as_outside_the_scope(ctx, mcp, tmp_path):
    other = tmp_path / "elsewhere"
    other.mkdir()
    _, calls = mcp(_call("mcp__fake__where", repo_path=str(other)), FINAL, read_only=["where"])
    assert "outside artifact_scope" in calls[1]["tail"]


def test_what_counts_as_a_path_by_value():
    from agent.security.capabilities import _pathish_value as p

    bs = chr(92)
    yes = ["C:" + bs + "x" + bs + "y", "D:/z", "/etc/passwd", "../a", "a/../../b", "~/k", bs + "share" + bs + "x"]
    no = ["Europe/London", "hello", "work/a.txt", "a..b", "1.5", ""]
    assert all(p(v) for v in yes) and not any(p(v) for v in no)


def test_a_tool_whose_keys_collide_after_sanitizing_is_not_registered(ctx, task, mcp):
    mcp(FINAL)
    assert ctx.tools.get("mcp__fake__clash") is None and ctx.tools.get("mcp__fake__where") is not None
    assert any("clash" in e.get("why", "") for e in _events(ctx, task, "mcp.skipped"))


def test_a_file_uri_under_any_key_is_checked(ctx, mcp, tmp_path):
    uri = (tmp_path.parent / "x").as_uri()
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note=uri), FINAL, read_only=["where"])
    assert "at:" not in calls[1]["tail"]


def test_the_bare_name_of_a_file_outside_the_scope_is_checked(ctx, mcp, tmp_path):
    (tmp_path / "secret.env").write_text("k=v", encoding="utf-8")
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note="secret.env"), FINAL, read_only=["where"])
    assert "at:" not in calls[1]["tail"]


def test_a_bare_word_that_names_nothing_is_left_alone(ctx, mcp):
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note="hello"), FINAL, read_only=["where"])
    assert "at:work" in calls[1]["tail"]



def test_a_file_uri_with_a_query_or_fragment_is_refused(ctx, mcp, workspace):
    uri = (workspace / "x").as_uri() + "?p=../../secret"
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note=uri), FINAL, read_only=["where"])
    assert "at:" not in calls[1]["tail"]


def test_a_new_file_in_an_existing_directory_outside_the_scope_is_checked(ctx, mcp, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note="elsewhere/new.txt"), FINAL, read_only=["where"])
    assert "at:" not in calls[1]["tail"]


def test_a_new_file_inside_the_scope_is_fine(ctx, mcp, workspace):
    _, calls = mcp(_call("mcp__fake__where", repo_path="work", note="work/new.txt"), FINAL, read_only=["where"])
    assert "at:work" in calls[1]["tail"]


def test_an_encoded_separator_or_double_encoding_in_a_file_uri_is_refused(ctx, mcp, workspace):
    base = (workspace / "x").as_uri()
    for tail in ("%2F..%2F..%2Fsecret", "%252F..%252Fsecret"):
        ctx.backends["cloud"].queue(_call("mcp__fake__where", repo_path="work", note=base + tail))
    _, calls = mcp(FINAL, read_only=["where"])
    for c in calls[1:3]:
        assert "at:" not in c["tail"]


def test_strict_mode_checks_every_string_argument(ctx, task, workspace):
    import sys as _sys

    from agent.config import McpServerConfig
    from agent.execute.loop import run_task as _run

    ctx.config.autonomy.approve_scoped_writes = True
    ctx.config.mcp_servers = {"fake": McpServerConfig(argv=[_sys.executable, FAKE], read_only=["where"], strict_paths=True)}
    ctx.backends["cloud"].queue(_call("mcp__fake__where", repo_path="work", note="brand-new-name.env"), FINAL)
    _run(ctx, task.id)
    assert "at:" not in ctx.backends["cloud"].calls[1]["tail"]
