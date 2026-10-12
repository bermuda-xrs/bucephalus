from __future__ import annotations

import json
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from agent.config import HooksConfig, McpServerConfig
from agent.execute.loop import run_task
from agent.kernel.tasks import load_task

HERE = Path(__file__).parent
FINAL = '<final>{"answer": "ok"}</final>'


def act(tool, **a):
    return f'<action tool="{tool}">' + json.dumps(a) + "</action>"


def events(ctx, task, kind):
    return [json.loads(r[0]) for r in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq", (task.id, kind))]


def deny_hook(tmp_path):
    p = tmp_path / "deny.py"
    p.write_text("import sys; print('HOOK SAYS NO'); sys.exit(1)", encoding="utf-8")
    return [sys.executable, str(p)]


def test_h1_a_batched_call_goes_through_the_hooks_too(ctx, task, workspace, tmp_path):
    (workspace / "secret.txt").write_text("TOPSECRET-4242", encoding="utf-8")
    ctx.config.hooks = HooksConfig.model_validate({"pre_tool": [{"argv": deny_hook(tmp_path), "tools": ["read_file"]}]})
    ctx.backends["cloud"].queue(act("parallel", calls=[{"tool": "read_file", "args": {"path": "work/secret.txt"}}]), FINAL)
    run_task(ctx, task.id)
    tail = ctx.backends["cloud"].calls[1]["tail"]
    assert "TOPSECRET-4242" not in tail and "HOOK SAYS NO" in tail
    assert [e["tool"] for e in events(ctx, task, "hook.ran")] == ["read_file"]


def test_h3_a_hook_that_leaves_a_grandchild_holding_its_output_still_times_out(tmp_path):
    from agent.execute.hooks import _run_one

    gc = tmp_path / "mid.py"
    gc.write_text("import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], "
                  "stdout=sys.stdout.fileno())\n", encoding="utf-8")
    hk = tmp_path / "hook.py"
    hk.write_text(f"import subprocess, sys, time\nsubprocess.run([sys.executable, {str(gc)!r}], stdout=sys.stdout.fileno())\n"
                  "time.sleep(60)\n", encoding="utf-8")
    t0 = time.time()
    code, text = _run_one(types.SimpleNamespace(argv=[sys.executable, str(hk)], timeout_s=2), {}, str(tmp_path))
    assert code == 124 and time.time() - t0 < 10, (code, time.time() - t0)


def test_h3_hook_output_is_capped_while_it_is_read(tmp_path):
    from agent.execute.hooks import MAX_HOOK_TEXT, _run_one

    hk = tmp_path / "loud.py"
    hk.write_text("import sys\nsys.stdout.write('x' * 5_000_000)\n", encoding="utf-8")
    code, text = _run_one(types.SimpleNamespace(argv=[sys.executable, str(hk)], timeout_s=20), {}, str(tmp_path))
    assert code == 0 and len(text) <= MAX_HOOK_TEXT


def test_m1_a_server_chosen_name_never_reaches_the_prompt(ctx, task, workspace):
    ctx.config.mcp_servers = {"evil": McpServerConfig(argv=[sys.executable, str(HERE / "evil_mcp_server.py")])}
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
    call = ctx.backends["cloud"].calls[0]
    blob = call["prefix"] + json.dumps(call["tools"])
    assert "SYSTEM" not in blob and "More injected text" not in blob and "pre-approved" not in blob
    assert "mcp__evil__save" in call["prefix"]
    assert any("lookup" in e.get("why", "") for e in events(ctx, task, "mcp.skipped"))


def test_m3_a_malformed_reply_fails_the_server_not_the_task(ctx, task, workspace):
    from agent.proc import process_table

    before = {p for p, _, _ in process_table()}
    ctx.config.mcp_servers = {"bad": McpServerConfig(argv=[sys.executable, str(HERE / "bad_mcp_server.py")])}
    ctx.backends["cloud"].queue(FINAL)
    assert run_task(ctx, task.id).status.startswith("done")
    assert load_task(ctx.conn, task.id).status.value == "done"
    assert events(ctx, task, "mcp.failed")
    time.sleep(1)
    new = [(p, n) for p, _, n in process_table() if p not in before and "python" in n.lower()]
    assert not [x for x in new if x[0] in {s.proc.pid for s in ctx.mcp_servers}]
    assert ctx.mcp_servers == []


def test_m4_endless_pagination_is_capped(ctx, task, workspace):
    ctx.config.mcp_servers = {"endless": McpServerConfig(argv=[sys.executable, str(HERE / "endless_mcp_server.py")])}
    ctx.backends["cloud"].queue(FINAL)
    t0 = time.time()
    run_task(ctx, task.id)
    assert time.time() - t0 < 30
    assert any("pages" in e.get("why", "") for e in events(ctx, task, "mcp.failed"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory junctions")
def test_r1_a_junction_swapped_into_the_scope_is_neither_captured_nor_restored_through(tmp_path):
    import shutil

    from agent.execute import shadow
    from agent.execute.shadow import ShadowRepo, ShadowUnavailable

    if not shadow.git_available():
        pytest.skip("git is not installed")
    root = tmp_path / "proj"
    (root / "work" / "d").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious", encoding="utf-8")
    (root / "work" / "d" / "f.txt").write_text("v1", encoding="utf-8")
    repo = ShadowRepo(tmp_path / "shadow", root, ["work"])
    sha1 = repo.checkpoint("step0")
    shutil.rmtree(root / "work" / "d")
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "work" / "d"), str(outside)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("cannot make a junction here")
    sha2 = repo.checkpoint("step1")
    assert "work/d/keep.txt" not in repo.changed(sha1, sha2), "a checkpoint copied files from outside the scope"
    with pytest.raises(ShadowUnavailable, match="link"):
        repo.restore(sha1)
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "precious"


def test_l3_text_mode_exec_strips_terminal_control_characters(ctx, workspace, capsys):
    from rich.console import Console

    from agent.cli import build_parser

    ctx.backends["cloud"].queue('<final>{"answer": "ok \u001b]0;pwned\u0007 and \u001b[31mred\u001b[0m"}</final>')
    args = build_parser().parse_args(["exec", "do it", "--scope", "work"])
    args.fn(ctx, args, Console(file=open(__import__("os").devnull, "w")))
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "ok" in out and "red" in out


def test_h4_the_files_a_hook_or_mcp_server_runs_are_protected(ctx):
    from agent.security.capabilities import is_protected, state_paths

    root = Path(ctx.config.root)
    (root / "tools").mkdir(exist_ok=True)
    for name in ("precheck.py", "server.py"):
        (root / "tools" / name).write_text("", encoding="utf-8")
    ctx.config.hooks = HooksConfig.model_validate({"pre_tool": [{"argv": ["python", "tools/precheck.py"]}]})
    ctx.config.mcp_servers = {"s": McpServerConfig(argv=["python", str(root / "tools" / "server.py")])}
    state = state_paths(ctx)
    assert is_protected("tools/precheck.py", str(root), state)
    assert is_protected("tools/server.py", str(root), state)
    assert not is_protected("work/other.py", str(root), state)


def test_h4_the_loaded_config_is_protected_whatever_its_name(tmp_path):
    from agent.config import load_config
    from agent.security.capabilities import is_protected, state_paths

    import yaml

    cfg_file = tmp_path / "agent.yml"
    cfg_file.write_text(yaml.safe_dump({"backends": {"cloud": {"kind": "mock", "model": "m"}, "review": {"kind": "mock", "model": "r"}}}), encoding="utf-8")
    cfg = load_config(cfg_file)
    state = state_paths(types.SimpleNamespace(config=cfg))
    assert is_protected(str(cfg_file), cfg.root, state)


def test_h2_a_released_approval_still_goes_through_the_pre_hook(ctx, task, workspace, tmp_path):
    ctx.config.autonomy.approve_scoped_writes = False
    ctx.config.autonomy.auto_review = False
    ctx.config.hooks = HooksConfig.model_validate({"pre_tool": [{"argv": deny_hook(tmp_path), "tools": ["write_file"]}]})
    ctx.backends["cloud"].queue(act("write_file", path="work/a.txt", content="written"))
    res = run_task(ctx, task.id)
    assert res.status == "paused" and res.approval_id
    ctx.resolve_approval(res.approval_id, True)
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, task.id)
    assert not (workspace / "a.txt").exists()
    assert [e["outcome"] for e in events(ctx, task, "approval.consumed")] == ["void"]
    assert "HOOK SAYS NO" in ctx.backends["cloud"].calls[-1]["tail"]


def test_h2_a_call_from_inside_a_program_gets_the_post_hook(ctx, task, workspace, tmp_path):
    ctx.config.autonomy.approve_scoped_writes = True
    p = tmp_path / "lint.py"
    p.write_text("print('POST HOOK WAS HERE')", encoding="utf-8")
    ctx.config.hooks = HooksConfig.model_validate({"post_tool": [{"argv": [sys.executable, str(p)], "tools": ["write_file"]}]})
    code = '<code>\nprint(call_tool("write_file", path="work/b.txt", content="x"))\n</code>'
    ctx.backends["cloud"].queue(code, FINAL)
    run_task(ctx, task.id)
    assert (workspace / "b.txt").exists()
    assert [e["tool"] for e in events(ctx, task, "hook.ran")] == ["write_file"]
    assert "POST HOOK WAS HERE" in ctx.backends["cloud"].calls[1]["tail"]


@pytest.mark.parametrize("pad", [" ", "\t", "\x01", " \n"])
def test_m2_a_file_uri_behind_leading_whitespace_is_still_a_path(pad):
    from agent.security.capabilities import _path_strings

    assert list(_path_strings("note", pad + "file:///C:/Windows/win.ini", by_value=True)) != []


@pytest.mark.parametrize("v", ["fi\tle:///C:/Windows/win.ini", "file\n:///C:/Windows/win.ini", "FILE:///C:/Windows/win.ini \x00"])
def test_review_a_file_uri_split_by_tabs_or_newlines_is_read_as_a_browser_would(v):
    from agent.security.capabilities import _path_strings

    got = list(_path_strings("note", v, by_value=True))
    assert got and all("win.ini" in str(g) and "file" not in str(g).lower() for g in got), got


def test_review_what_a_hook_script_imports_beside_it_is_protected_too(ctx):
    from agent.security.capabilities import is_protected, state_paths

    root = Path(ctx.config.root)
    (root / "tools").mkdir(exist_ok=True)
    (root / "tools" / "precheck.py").write_text("", encoding="utf-8")
    (root / "lintpkg").mkdir(exist_ok=True)
    (root / "lintpkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "top.py").write_text("", encoding="utf-8")
    ctx.config.hooks = HooksConfig.model_validate({"pre_tool": [{"argv": ["python", "tools/precheck.py"]},
                                                                {"argv": ["python", "-m", "lintpkg.run"]},
                                                                {"argv": ["python", "top.py"]}]})
    state = state_paths(ctx)
    assert is_protected("tools/helper.py", str(root), state)
    assert is_protected("lintpkg/run.py", str(root), state)
    assert is_protected("top.py", str(root), state)
    assert not is_protected("work/a.txt", str(root), state)


def test_review_a_scope_that_is_itself_a_junction_is_neither_captured_nor_restored_through(tmp_path):
    import shutil

    from agent.execute import shadow
    from agent.execute.shadow import ShadowRepo, ShadowUnavailable

    if not shadow.git_available():
        pytest.skip("git is not installed")
    root = tmp_path / "proj"
    (root / "work").mkdir(parents=True)
    (root / "work" / "f.txt").write_text("v1", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("precious", encoding="utf-8")
    repo = ShadowRepo(tmp_path / "shadow", root, ["work"])
    sha1 = repo.checkpoint("step0")
    shutil.rmtree(root / "work")
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "work"), str(outside)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("cannot make a junction here")
    sha2 = repo.checkpoint("step1")
    assert "work/keep.txt" not in repo.changed(sha1, sha2), "a checkpoint copied files from outside the scope"
    with pytest.raises(ShadowUnavailable, match="link"):
        repo.restore(sha1)
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "precious"


@pytest.mark.parametrize("argv", [["python", "-m", "lintpkg.run"], ["python", "-mlintpkg.run"], ["python", "-Im", "lintpkg"],
                                  ["python", "-Imlintpkg"], ["python", "-X", "utf8", "-m", "lintpkg"]])
def test_review_a_module_a_hook_runs_is_protected_before_it_exists(ctx, argv):
    from agent.security.capabilities import is_protected, state_paths

    root = Path(ctx.config.root)
    ctx.config.hooks = HooksConfig.model_validate({"pre_tool": [{"argv": argv}]})
    state = state_paths(ctx)
    assert is_protected("lintpkg.py", str(root), state)
    assert is_protected("lintpkg/__init__.py", str(root), state)
    import importlib.machinery

    for suffix in importlib.machinery.all_suffixes():
        assert is_protected("lintpkg" + suffix, str(root), state), suffix
    assert not is_protected("work/a.py", str(root), state)
