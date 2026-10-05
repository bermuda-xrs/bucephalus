from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import types

import pytest

import agent.platform as platform_pkg
from agent.platform import darwin, posix

POSIX_ONLY = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX only - verified on a real machine by docs/PLATFORMS.md checklist step 5")
DARWIN_ONLY = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS only - verified on a real Mac by docs/PLATFORMS.md checklist step 5")


@pytest.fixture
def fake_os(monkeypatch):
    sent = {"killpg": [], "kill": []}
    monkeypatch.setattr(posix, "signal", types.SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(posix.os, "killpg", lambda g, s: sent["killpg"].append(g), raising=False)
    monkeypatch.setattr(posix.os, "kill", lambda p, s: sent["kill"].append(p))
    return sent


def test_kill_tree_never_signals_the_callers_group(monkeypatch, fake_os):
    monkeypatch.setattr(posix.os, "getpgid", lambda pid: 500, raising=False)
    from agent import proc

    table = [(10, 1, "python"), (11, 10, "python"), (12, 11, "sleep")]
    monkeypatch.setattr(proc, "process_table", lambda: table)
    monkeypatch.setattr(proc, "process_start_time", lambda pid: pid)
    posix.kill_tree_signal(10, 5)
    assert fake_os["killpg"] == [], "signalled the caller's own process group"
    assert fake_os["kill"] == [12, 11, 10], "not the whole tree, leaves first"


def test_kill_tree_uses_the_childs_own_group_when_it_has_one(monkeypatch, fake_os):
    monkeypatch.setattr(posix.os, "getpgid", lambda pid: 500 if pid == 0 else 10, raising=False)
    posix.kill_tree_signal(10, 5)
    assert fake_os["killpg"] == [10]


class _FakePopen:
    last: dict = {}

    def __init__(self, argv, **kw):
        _FakePopen.last = {"argv": argv, "kw": kw}
        self.pid, self.returncode = 4242, 0

    def poll(self):
        return 0


@pytest.mark.parametrize("mod,capped", [(posix, True), (darwin, False)])
def test_limits_are_set_by_a_trampoline_in_a_new_session(monkeypatch, mod, capped):
    monkeypatch.setattr(posix.subprocess, "Popen", _FakePopen)
    mod.spawn_limited(["child", "arg"], timeout_s=7, memory_bytes=512 * 2**20,
                      max_processes=32, fsize_bytes=64 * 2**20, cwd=".")
    kw, argv = _FakePopen.last["kw"], _FakePopen.last["argv"]
    assert kw.get("start_new_session") is True, "the child shares the caller's group"
    assert "preexec_fn" not in kw, "limits are still set in a preexec_fn"
    i = argv.index("-c")
    assert argv[i + 1] == posix.LIMITER and argv[-2:] == ["child", "arg"]
    limits = json.loads(argv[i + 2])
    assert limits["cpu"] == 8 and limits["fsize"] == 64 * 2**20
    assert ("as" in limits) is capped, "darwin must not set RLIMIT_AS; linux must"
    assert "nproc" not in limits, "RLIMIT_NPROC counts every process the USER owns"


def _run_limiter(monkeypatch, fail_on=None):
    calls = []

    class FakeResource:
        RLIMIT_AS, RLIMIT_CPU, RLIMIT_FSIZE = "AS", "CPU", "FSIZE"

        @staticmethod
        def setrlimit(which, pair):
            if which == fail_on:
                raise ValueError("invalid argument")
            calls.append((which, pair))

    class Exec(Exception):
        pass

    def fake_execv(path, args):
        raise Exec((path, args))

    def fake_exit(code):
        raise SystemExit(code)

    monkeypatch.setitem(sys.modules, "resource", FakeResource)
    monkeypatch.setattr(os, "execv", fake_execv, raising=False)
    monkeypatch.setattr(os, "_exit", fake_exit)
    monkeypatch.setattr(sys, "argv", ["-c", json.dumps({"as": 1, "cpu": 2, "fsize": 3}),
                                      "/bin/child", "x"])
    try:
        exec(compile(posix.LIMITER, "<limiter>", "exec"), {"__name__": "__main__"})  # noqa: S102
    except Exec as e:
        return calls, e.args[0]
    except SystemExit as e:
        return calls, e.code


def test_the_trampoline_sets_every_limit_then_becomes_the_child(monkeypatch):
    calls, execd = _run_limiter(monkeypatch)
    assert [c[0] for c in calls] == ["AS", "CPU", "FSIZE"]
    assert execd == ("/bin/child", ["/bin/child", "x"])


def test_a_refused_limit_means_the_child_never_runs(monkeypatch):
    calls, code = _run_limiter(monkeypatch, fail_on="AS")
    assert code == posix.REFUSED_EXIT


def test_the_watchdog_decides_on_the_process_group(monkeypatch):
    assert posix.over_limits(32, 32) is None
    assert "process limit" in posix.over_limits(33, 32)
    assert "memory limit" in posix.over_limits(3, 32, 600 * 2**20, 512 * 2**20)
    assert "reactive" in posix.over_limits(3, 32, 600 * 2**20, 512 * 2**20)


def test_the_watchdog_kills_the_group_and_records_why(monkeypatch):
    killed = []
    monkeypatch.setattr(posix.os, "killpg", lambda g, s: killed.append(g), raising=False)
    monkeypatch.setattr(posix, "signal", types.SimpleNamespace(SIGKILL=9))
    layer = types.SimpleNamespace(group_members=lambda pgid: [1, 2, 3],
                                  process_memory_bytes=lambda pid: 300 * 2**20)
    monkeypatch.setattr(platform_pkg, "current", layer)
    proc = types.SimpleNamespace(pid=77, returncode=-9, poll=lambda: None)
    posix._watch(proc, max_processes=32, memory_bytes=512 * 2**20)
    assert killed == [77]
    assert "memory limit" in posix.exit_note(proc, 30)


def test_macos_labels_its_memory_cap_reactive():
    note = darwin.limits_note(30, 512 * 2**20, 32)
    assert "REACTIVE" in note and "NOT kernel-enforced" in note and "RLIMIT_AS" in note
    assert "REACTIVE" in posix.limits_note(30, 512 * 2**20, 32)


PS = """    1     0 /sbin/launchd
  512     1 /Applications/Some App.app/Contents/MacOS/Some App
  900   512 /usr/bin/python3
"""


def test_darwin_reads_the_process_table_without_proc():
    rows = darwin.parse_ps_table(PS)
    assert rows == [(1, 0, "launchd"), (512, 1, "Some App"), (900, 512, "python3")]
    assert darwin.parse_ps_groups("  900   900\n  901   900\n  902   1\n", 900) == [900, 901]


def test_darwin_start_time_and_memory_parsers():
    assert darwin.parse_lstart("Thu Sep 25 12:00:01 2026") == int(time.mktime(
        time.strptime("Thu Sep 25 12:00:01 2026", "%a %b %d %H:%M:%S %Y")))
    assert darwin.parse_lstart("  ") is None
    vm = darwin.parse_vm_stat("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                              "Pages free:                               1000.\n"
                              "Pages inactive:                           500.\n"
                              "Pages speculative:                        20.\n")
    assert darwin.available_bytes(vm) == 16384 * 1520
    buf = bytearray(darwin._RUSAGE_BYTES)
    buf[darwin.FOOTPRINT_OFFSET:darwin.FOOTPRINT_OFFSET + 8] = (123 * 2**20).to_bytes(8, "little")
    assert darwin.footprint_from_rusage(bytes(buf)) == 123 * 2**20


def test_the_run_record_names_how_the_limits_held(ctx, task, workspace, scripted):
    from agent.execute.loop import run_task
    from agent.platform import current

    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    scripted("cloud", "<code>\nprint(6 * 7)\n</code>", '<final>{"answer": "42"}</final>')
    run_task(ctx, task.id)
    receipts = [json.loads(p) for (p,) in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type='tool.receipt'", (task.id,))]
    code = [r for r in receipts if r.get("tool") == "run_code"]
    assert code, "no run_code receipt"
    expected = current.limits_note(30, 512 * 2**20, current.spawn_denied_processes() or 32)
    assert expected and code[0].get("limits") == [expected]


@POSIX_ONLY
def test_real_memory_past_the_cap_does_not_allocate():
    from agent.execute.sandbox import run_sandboxed

    r = run_sandboxed("b = bytearray(640 * 2**20)\nimport time; time.sleep(1)\nprint('allocated')",
                      timeout_s=60)
    assert "allocated" not in r["stdout"], r


@POSIX_ONLY
def test_real_cpu_past_the_limit_is_killed():
    from agent.execute.sandbox import run_sandboxed

    t0 = time.time()
    r = run_sandboxed("while True: pass", timeout_s=3)
    assert r["returncode"] != 0 and time.time() - t0 < 30, r


@POSIX_ONLY
def test_real_processes_past_the_limit_are_killed_by_the_watchdog():
    from agent.execute.sandbox import run_sandboxed

    code = ("import subprocess, sys, time\n"
            "ps = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']) "
            "for _ in range(40)]\ntime.sleep(10)\nprint('all ran')")
    r = run_sandboxed(code, timeout_s=60)
    assert "all ran" not in r["stdout"] and "process limit" in r["stderr"], r


@POSIX_ONLY
def test_real_a_file_past_the_size_limit_is_refused():
    from agent.execute.sandbox import run_sandboxed

    code = ("import os\nwith open('big.bin', 'wb') as f:\n"
            "    f.write(b'0' * (70 * 2**20))\nprint('wrote')")
    r = run_sandboxed(code, timeout_s=60)
    assert "wrote" not in r["stdout"], r


@POSIX_ONLY
def test_real_the_timeout_kills_grandchildren():
    from agent.execute.sandbox import run_sandboxed
    from agent.proc import _alive

    code = ("import subprocess, sys, time\n"
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "print(g.pid, flush=True)\ntime.sleep(120)")
    r = run_sandboxed(code, timeout_s=3)
    assert r["stderr"] == "TIMEOUT"
    time.sleep(1)
    for line in (r["stdout"] or "").split():
        if line.isdigit():
            assert not _alive(int(line)), "a grandchild outlived the timeout"


@POSIX_ONLY
def test_real_kill_tree_does_not_kill_its_caller():
    probe = ("import subprocess, sys; sys.path.insert(0, 'src')\n"
             "from agent.proc import kill_tree\n"
             "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
             "kill_tree(c.pid)\nprint('survived', flush=True)\n")
    p = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                       timeout=60, start_new_session=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert "survived" in p.stdout, (p.returncode, p.stdout, p.stderr)


@POSIX_ONLY
def test_real_limits_hold_when_the_caller_is_threaded():
    from agent.execute.sandbox import run_sandboxed

    out = []
    ts = [threading.Thread(target=lambda: out.append(run_sandboxed("print(1)", timeout_s=60)))
          for _ in range(8)]
    [t.start() for t in ts]
    [t.join(120) for t in ts]
    assert len(out) == 8 and all(r["returncode"] == 0 for r in out), out


@DARWIN_ONLY
def test_real_darwin_sees_processes_and_their_memory():
    from agent.proc import process_start_time, process_table

    assert any(pid == os.getpid() for pid, _, _ in process_table())
    assert process_start_time(os.getpid()) is not None
    assert (darwin.process_memory_bytes(os.getpid()) or 0) > 0
