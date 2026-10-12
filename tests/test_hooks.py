from __future__ import annotations

import json
import sys
import time

import pytest

from agent.config import HooksConfig
from agent.execute.loop import run_task
from agent.kernel.tasks import create_task

FINAL = '<final>{"answer": "ok"}</final>'


def _write(content="x", path="work/a.txt"):
    return '<action tool="write_file">' + json.dumps({"path": path, "content": content}) + "</action>"


LIST = '<action tool="list_dir">{"path": "work"}</action>'


@pytest.fixture
def hook(tmp_path):
    def make(name, body):
        p = tmp_path / f"{name}.py"
        p.write_text(body, encoding="utf-8")
        return [sys.executable, str(p)]

    return make


@pytest.fixture
def hooked(ctx, task, workspace):
    ctx.config.autonomy.approve_scoped_writes = True

    def go(spec, *script):
        ctx.config.hooks = HooksConfig.model_validate(spec)
        ctx.backends["cloud"].queue(*script)
        res = run_task(ctx, task.id)
        return res, ctx.backends["cloud"].calls

    return go


def _events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


def test_there_are_no_hooks_by_default(ctx):
    assert ctx.config.hooks == HooksConfig() and not ctx.config.hooks.pre_tool


def test_hooks_come_from_the_config_file(tmp_path):
    import yaml

    from agent.config import load_config

    (tmp_path / "c.yaml").write_text(yaml.safe_dump({
        "backends": {"cloud": {"kind": "mock", "model": "m"}, "review": {"kind": "mock", "model": "r"}},
        "hooks": {"pre_tool": [{"argv": ["python", "-V"], "tools": ["write_file"]}]}}), encoding="utf-8")
    cfg = load_config(tmp_path / "c.yaml")
    assert cfg.hooks.pre_tool[0].tools == ["write_file"] and cfg.hooks.pre_tool[0].timeout_s == 10


def test_a_failing_pre_hook_refuses_the_call(ctx, task, workspace, hook, hooked):
    argv = hook("no", "import sys; print('no writes today'); sys.exit(1)")
    _, calls = hooked({"pre_tool": [{"argv": argv}]}, _write(), FINAL)
    assert not (workspace / "a.txt").exists()
    assert "hook refused write_file" in calls[1]["tail"] and "no writes today" in calls[1]["tail"]
    assert _events(ctx, task, "hook.denied")[0]["tool"] == "write_file"


def test_a_passing_pre_hook_lets_it_through_and_is_told_the_call(ctx, task, workspace, hook, hooked, tmp_path):
    seen = tmp_path / "seen.json"
    argv = hook("ok", f"import sys; open({str(seen)!r}, 'w').write(sys.stdin.read())")
    hooked({"pre_tool": [{"argv": argv}]}, _write("hi"), FINAL)
    got = json.loads(seen.read_text(encoding="utf-8"))
    assert got["event"] == "pre_tool" and got["tool"] == "write_file" and got["args"]["content"] == "hi"
    assert got["task_id"] == task.id and (workspace / "a.txt").read_text(encoding="utf-8") == "hi"


def test_a_hook_only_runs_for_the_tools_it_names(hook, hooked, tmp_path):
    mark = tmp_path / "ran.txt"
    argv = hook("m", f"open({str(mark)!r}, 'a').write('x')")
    hooked({"pre_tool": [{"argv": argv, "tools": ["write_file"]}]}, LIST, _write(), FINAL)
    assert mark.read_text(encoding="utf-8") == "x"


def test_a_hook_cannot_permit_what_the_gate_denied(hook, hooked, tmp_path, workspace):
    mark = tmp_path / "ran.txt"
    argv = hook("m", f"open({str(mark)!r}, 'a').write('x')")
    _, calls = hooked({"pre_tool": [{"argv": argv}]}, _write(path="../outside.txt"), FINAL)
    assert "outside artifact_scope" in calls[1]["tail"] and not mark.exists()
    assert not (tmp_path.parent / "outside.txt").exists()


def test_a_hook_that_hangs_refuses_the_call_and_dies(hook, hooked, tmp_path):
    pidfile = tmp_path / "pid"
    argv = hook("slow", f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(60)")
    t0 = time.time()
    _, calls = hooked({"pre_tool": [{"argv": argv, "timeout_s": 1}]}, _write(), FINAL)
    assert time.time() - t0 < 20 and "timed out" in calls[1]["tail"]
    from agent.proc import process_table

    assert int(pidfile.read_text()) not in {pid for pid, _, _ in process_table()}


def test_a_hook_that_cannot_start_refuses_the_call(hooked, workspace):
    _, calls = hooked({"pre_tool": [{"argv": ["no-such-program-anywhere"]}]}, _write(), FINAL)
    assert "hook refused write_file" in calls[1]["tail"] and not (workspace / "a.txt").exists()


def test_a_hook_does_not_see_secrets(hook, hooked, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "t")
    out = tmp_path / "env.json"
    argv = hook("env", f"import os, json; json.dump(sorted(os.environ), open({str(out)!r}, 'w'))")
    hooked({"pre_tool": [{"argv": argv}]}, LIST, FINAL)
    names = json.loads(out.read_text(encoding="utf-8"))
    assert "ANTHROPIC_API_KEY" not in names and "MY_SERVICE_TOKEN" not in names and "PATH" in {n.upper() for n in names}


def test_post_hook_output_is_added_as_untrusted_and_cannot_change_the_result(ctx, task, workspace, hook, hooked):
    argv = hook("lint", "import sys; print('3 style warnings'); sys.exit(7)")
    _, calls = hooked({"post_tool": [{"argv": argv, "tools": ["write_file"]}]}, _write("hi"), FINAL)
    tail = calls[1]["tail"]
    assert "3 style warnings" in tail and "taint=untrusted" in tail.split("3 style warnings")[0].rsplit("<observation", 1)[1]
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hi"


def test_a_stop_hook_holds_the_answer_back_then_lets_it_go(ctx, task, hook, hooked, tmp_path):
    flag = tmp_path / "second"
    argv = hook("stop", f"import os, sys\nif os.path.exists({str(flag)!r}): sys.exit(0)\n"
                        f"open({str(flag)!r}, 'w').write('1'); print('tests have not been run'); sys.exit(1)")
    res, calls = hooked({"stop": [{"argv": argv}]}, FINAL, FINAL)
    assert res.status.startswith("done") and len(calls) == 2
    assert "tests have not been run" in calls[1]["tail"]
    assert len(_events(ctx, task, "hook.stop_blocked")) == 1


def test_a_stop_hook_cannot_hold_the_task_forever(ctx, task, hook, hooked):
    argv = hook("never", "import sys; print('no'); sys.exit(1)")
    ctx.config.limits.max_steps = 10
    res, calls = hooked({"stop": [{"argv": argv}]}, FINAL)
    assert res.status.startswith("done") and len(calls) == 4
    assert len(_events(ctx, task, "hook.stop_blocked")) == 3


def test_a_hook_also_guards_calls_made_from_inside_a_program(hook, hooked, workspace):
    argv = hook("no", "import sys; print('blocked by policy'); sys.exit(1)")
    code = '<code>\nprint(call_tool("write_file", path="work/b.txt", content="x"))\n</code>'
    _, calls = hooked({"pre_tool": [{"argv": argv, "tools": ["write_file"]}]}, code, FINAL)
    assert not (workspace / "b.txt").exists() and "blocked by policy" in calls[1]["tail"]


@pytest.mark.parametrize("profile", ["patch", "small"])
def test_every_profile_runs_them(tmp_path, workspace, task, hook, profile):
    from agent.context import AgentContext
    from conftest import make_config

    c = AgentContext(make_config(tmp_path))
    c.backends["cloud"].cfg.tool_profile = profile
    c.close()
    c = AgentContext(make_config(tmp_path), backends=c.backends)
    c.config.autonomy.approve_scoped_writes = True
    c.config.hooks = HooksConfig.model_validate({"pre_tool": [{"argv": hook("no", "import sys; sys.exit(1)")}]})
    t = create_task(c.conn, c.log, task.contract, task.budget)
    c.backends["cloud"].queue(_write(), FINAL)
    run_task(c, t.id)
    assert not (workspace / "a.txt").exists()
    c.close()


def test_a_hook_that_cannot_start_reports_127(tmp_path):
    from types import SimpleNamespace

    from agent.execute.hooks import _run_one

    code, text = _run_one(SimpleNamespace(argv=[str(tmp_path / "no-such-hook.exe")], timeout_s=5), {}, str(tmp_path))
    assert code == 127 and text.startswith("could not start")


def test_hook_output_keeps_its_full_cap_in_four_byte_characters(hook, tmp_path):
    from types import SimpleNamespace

    from agent.execute.hooks import MAX_HOOK_TEXT, _run_one

    argv = hook("wide", f"import sys; sys.stdout.buffer.write(('\U0001F600' * {MAX_HOOK_TEXT + 100}).encode('utf-8'))")
    code, text = _run_one(SimpleNamespace(argv=argv, timeout_s=20), {}, str(tmp_path))
    assert code == 0 and text == "\U0001F600" * MAX_HOOK_TEXT
