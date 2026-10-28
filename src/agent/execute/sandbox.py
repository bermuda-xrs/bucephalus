from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import Callable

from ..frozen import CHILD_ENV, interpreter_argv
from ..platform import LimitsRefused
from ..platform import current as _platform

MEM_LIMIT_BYTES = 512 * 1024 * 1024
FSIZE_LIMIT_BYTES = 64 * 1024 * 1024
MAX_PROCESSES = 32
OUTPUT_CAP = 16000
TOOLCALL_MARKER = "\x01TOOLCALL"

ToolHandler = Callable[[str, dict], dict]


class _Tail:
    def __init__(self, cap: int):
        self.cap, self.n, self.parts = cap, 0, deque()

    def add(self, text: str) -> None:
        self.parts.append(text)
        self.n += len(text)
        while len(self.parts) > 1 and self.n - len(self.parts[0]) >= self.cap:
            self.n -= len(self.parts.popleft())

    def held(self) -> int:
        return self.n

    def text(self) -> str:
        return "".join(self.parts)[-self.cap:]


_LIMITS = dict(memory_bytes=MEM_LIMIT_BYTES, max_processes=MAX_PROCESSES,
               fsize_bytes=FSIZE_LIMIT_BYTES)


def _limits_refused(error: Exception) -> dict:
    return {"stdout": "", "returncode": -1,
            "stderr": ("sandbox: resource limits could not be applied, so the "
                       f"code was not run: {error}")}


CARRIED_SIDECARS = ("-wal", "-journal")


def _mirror_sidecars(sandbox_main: Path, original_main: Path,
                     changed: list[str]) -> None:
    for s in CARRIED_SIDECARS:
        a = sandbox_main.with_name(sandbox_main.name + s)
        b = original_main.with_name(original_main.name + s)
        if a.exists():
            if not b.exists() or a.read_bytes() != b.read_bytes():
                shutil.copy2(a, b)
                changed.append(str(b))
        elif b.exists():
            b.unlink()
            changed.append(str(b))


def link_into(workdir: str, path: str, rel: str | None = None,
              skip_multilinked: bool = False) -> dict | None:
    src = Path(path).resolve()
    if not src.exists():
        return None
    dest = Path(workdir) / (rel or src.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(src, dest, target_is_directory=src.is_dir())
        return {"mode": "symlink", "src": str(src), "dest": str(dest)}
    except (OSError, NotImplementedError, AttributeError):
        def several_names(d, names):
            return [n for n in names if skip_multilinked and os.path.isfile(os.path.join(d, n))
                    and os.stat(os.path.join(d, n)).st_nlink > 1]
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True, ignore=several_names)
        elif several_names(str(src.parent), [src.name]):
            return None
        else:
            shutil.copy2(src, dest)
            for s in CARRIED_SIDECARS:
                side = src.with_name(src.name + s)
                if side.exists():
                    shutil.copy2(side, dest.with_name(dest.name + s))
        return {"mode": "copy", "src": str(src), "dest": str(dest)}


def _copy_back(entries: list[dict]) -> list[str]:
    changed: list[str] = []
    for e in entries:
        if e["mode"] != "copy":
            continue
        src, dest = Path(e["src"]), Path(e["dest"])
        if not dest.exists():
            continue
        if dest.is_dir():
            for f in dest.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(dest)
                    out = src / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    if not out.exists() or out.read_bytes() != f.read_bytes():
                        shutil.copy2(f, out)
                        changed.append(str(out))
            for f in dest.rglob("*"):
                if f.is_file() and not f.name.endswith(CARRIED_SIDECARS):
                    _mirror_sidecars(f, src / f.relative_to(dest), changed)
        else:
            if not src.exists() or src.read_bytes() != dest.read_bytes():
                shutil.copy2(dest, src)
                changed.append(str(src))
            _mirror_sidecars(dest, src, changed)
    return changed


_GUARD_TEMPLATE = '''\
import sys as _sys, os as _os

# The child runs with -I, which ignores _build_env's PYTHONDONTWRITEBYTECODE.
# Without this, importing a stdlib module whose .pyc is missing (a fresh CI
# interpreter) tries to write into the interpreter's own __pycache__: refused,
# and recorded as a denied write the code never made.
_sys.dont_write_bytecode = True
_ROOTS =[_os.path.realpath(p) for p in {roots!r}]
_WORKDIR = _os.path.realpath({workdir!r})
_BUSY = [False]
_PROTECTED = [_os.path.realpath(p) for p in {protected!r}]
_DENY_SPAWN = {deny_spawn!r}
_STREAMS = {streams!r}
_READ_FENCE = {read_fence!r}
# D29 for code. Reads are fenced to what the task DECLARED plus what the
# interpreter needs: the work directory, the linked scope, and the
# interpreter's own paths, captured before any user code runs. Anything else -
# a home directory, another project - is not declared, so it is not permitted.
_INTERP_ROOTS = sorted({{_os.path.realpath(p) for p in (
    [x for x in _sys.path if x] + [_sys.prefix, _sys.base_prefix,
     _sys.exec_prefix, getattr(_sys, "base_exec_prefix", _sys.prefix),
     _os.environ.get("SYSTEMROOT", "")]
    # D13. Frozen, the interpreter's modules live in an archive INSIDE the
    # executable, which sits beside the bundle rather than under any prefix -
    # unfrozen, python.exe is under base_prefix and already readable. That one
    # FILE, not its directory: the equivalent allowance and no wider.
    + ([_sys.executable] if getattr(_sys, "frozen", False) else [])) if p}})
_READ_ROOTS = [_WORKDIR] + _ROOTS + _INTERP_ROOTS
# A2-4. What this program touched, for the record: every open, listing,
# database and filesystem change outside the interpreter's own paths, refusals
# included. Written a line at a time to a file OUTSIDE the work directory,
# opened before the hook exists, so a listing of the work directory is
# unchanged and os._exit loses at most a line. Telemetry, not a control: code
# that wants to can write to it too (D32).
_TOUCH = [None]
if {touch_path!r}:
    try:
        _TOUCH[0] = open({touch_path!r}, "a", encoding="utf-8", buffering=1)
    except Exception:
        _TOUCH[0] = None
# A new process is a process this hook is not in: starting one bypasses every
# rule here in a single call. Refused for agent code; harness runs keep it.
_SPAWN = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn",
          "os.spawn", "os.startfile", "os.fork", "os.forkpty")
# AUDIT-2026-09 F3. The sandbox promises NO NETWORK (execute/libraries.py) and
# nothing enforced it: code connected anywhere, loopback included - where the
# control plane listens, which is how F2 was exploitable. Every run, not only
# agent code: a grader has no business on the network either. Creating a
# socket is refused, so every route out - connect, bind, a UDP send, a DNS
# lookup that would itself carry data - fails at its first step. Advisory like
# the rest of this hook (D32): ctypes or a native extension goes around it.
_NET = ("socket.__new__", "socket.connect", "socket.bind", "socket.sendto",
        "socket.sendmsg", "socket.getaddrinfo", "socket.gethostbyname",
        "socket.gethostbyaddr", "socket.getnameinfo")


def _under(path, root):
    return path == root or path.startswith(root + _os.sep)


def _allowed(path):
    try:
        raw = _os.path.abspath(_os.fspath(path))
    except Exception:
        return False
    if _under(raw, _WORKDIR):
        return True
    try:
        real = _os.path.realpath(raw)
    except Exception:
        return False
    return any(_under(real, r) for r in _ROOTS)


def _is_protected(path):
    try:
        real = _os.path.realpath(_os.path.abspath(_os.fspath(path)))
    except Exception:
        return False
    if isinstance(real, bytes):
        real = _os.fsdecode(real)
    # An NTFS data stream (`.env::$DATA` writes `.env`) is invisible to
    # realpath while the file does not exist; no program here needs one
    # (AUDIT-ITERATION-3 S1, swept into the sandbox).
    if _STREAMS and ":" in real[2:]:
        return True
    if any(_under(real, p) for p in _PROTECTED):
        return True
    # D70: a hard link in the workspace is the protected file under another
    # name. Agent code has no need of a file with several names; refused.
    # Workspace files only: an installer may hard-link the interpreter's own.
    if any(_under(real, r) for r in _ROOTS) or _under(real, _WORKDIR):
        try:
            st = _os.stat(real)
        except OSError:
            return False
        # Regular files: a POSIX directory always has several links.
        return (st.st_mode & 0o170000) == 0o100000 and st.st_nlink > 1
    return False


def _check_protected(path):
    if not _PROTECTED or path is None or isinstance(path, int):
        return
    _BUSY[0] = True
    try:
        hit = _is_protected(path)
    finally:
        _BUSY[0] = False
    if hit:
        raise PermissionError("sandbox: protected path: %s" % (path,))


def _check_read(path):
    if not _READ_FENCE or path is None or isinstance(path, int):
        return
    if str(path).lower() in (_os.devnull.lower(), "nul", "/dev/null"):
        return
    _BUSY[0] = True
    try:
        try:
            real = _os.path.realpath(_os.path.abspath(_os.fspath(path)))
            if isinstance(real, bytes):
                real = _os.fsdecode(real)
        except Exception:
            real = None
        ok = real is not None and any(_under(real, r) for r in _READ_ROOTS)
    finally:
        _BUSY[0] = False
    if not ok:
        raise PermissionError("sandbox: read outside scope: %s" % (path,))


def _touch(op, path, denied=False, raw=False):
    f = _TOUCH[0]
    if f is None or path is None or isinstance(path, int):
        return
    _BUSY[0] = True
    try:
        if raw:
            real = _os.fsdecode(path) if isinstance(path, bytes) else str(path)
        else:
            try:
                real = _os.path.realpath(_os.path.abspath(_os.fspath(path)))
                if isinstance(real, bytes):
                    real = _os.fsdecode(real)
            except Exception:
                real = repr(path)
        if denied or not any(_under(real, r) for r in _INTERP_ROOTS):
            f.write(("D " if denied else "  ") + op + " " + real + "\\n")
    except Exception:
        pass
    finally:
        _BUSY[0] = False


def _unpct(s):
    out = bytearray()
    i = 0
    while i < len(s):
        h = s[i + 1:i + 3]
        if s[i] == "%" and len(h) == 2:
            try:
                out.append(int(h, 16))
                i += 3
                continue
            except ValueError:
                pass
        out.extend(s[i].encode("utf-8"))
        i += 1
    return out.decode("utf-8", "replace")


def _sqlite_targets(db):
    """Every file this database string can make SQLite open.

    SQLite opens files itself, in C, where no Python audit event sees them, so
    the string must be read the way SQLite reads it: as a URI when it starts
    with file:, and as a plain name as well. `file:C:/x/agent.db?mode=ro` was
    checked only as a plain name - a path under the work directory - and
    opened the project database.
    """
    s = _os.fsdecode(db) if isinstance(db, bytes) else str(db)
    if s in ("", ":memory:"):
        return []
    if s[:5].lower() != "file:":
        return [s]
    body, _, query = s[5:].partition("?")
    body = body.partition("#")[0]
    if "mode=memory" in query.lower() or body in ("", ":memory:"):
        return []
    if body.startswith("//"):
        body = "/" + body[2:].partition("/")[2]     # file://host/p -> /p
    body = _unpct(body)
    if len(body) >= 3 and body[0] == "/" and body[2] == ":":
        body = body[1:]                                # /C:/x -> C:/x
    return [body, s]


def _check_sqlite(db):
    """A database is fenced by what SQLite will OPEN, for read or write. Agent
    code has no business with one outside its scope, and a database SQLite
    creates is a write the write-confinement below never sees."""
    if not _DENY_SPAWN:
        return
    for t in _sqlite_targets(db):
        _check_protected(t)
        _BUSY[0] = True
        try:
            ok = _allowed(t)
        finally:
            _BUSY[0] = False
        if not ok:
            raise PermissionError("sandbox: database outside scope: %s" % (t,))


# SQLite's authorizer code for ATTACH - which VACUUM INTO also reports for its
# target file - and its answers. Numbers rather than sqlite3.SQLITE_*, so the
# guard does not import sqlite3 into every run.
_SQLITE_ATTACH, _SQLITE_DENY, _SQLITE_OK = 24, 1, 0


def _sqlite_auth(action, a1, a2, dbname, trigger):
    if action != _SQLITE_ATTACH:
        return _SQLITE_OK
    if a1 is None:
        # A bound parameter or an expression is not a filename yet when SQLite
        # asks about an ATTACH: it is resolved after this answer. An unknown
        # target is not a safe one (DR4). VACUUM INTO is asked at run time,
        # with the name.
        _touch("attach", "<target unknown when authorized>", True, raw=True)
        return _SQLITE_DENY
    try:
        _check_sqlite(a1)
    except PermissionError:
        _touch("attach", a1, True, raw=True)
        return _SQLITE_DENY
    _touch("attach", a1, raw=True)
    return _SQLITE_OK


_FS_EVENTS = (
    "os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.link",
    "os.symlink", "os.truncate", "shutil.copyfile", "shutil.move",
    "shutil.rmtree",
    # A directory junction is a link that needs no privilege on Windows; both
    # ends must be in scope, as for os.symlink (tests/test_junction_escape.py).
    "_winapi.CreateJunction",
)


def _audit(event, args):
    if _BUSY[0]:
        return
    if event in _NET:
        _touch("network", event, True, raw=True)
        raise PermissionError("sandbox: network access is denied (%s) - "
                              "loopback included, where the control plane "
                              "listens" % (event,))
    if _DENY_SPAWN and event in _SPAWN:
        _touch("spawn", event, True, raw=True)
        raise PermissionError("sandbox: starting a process is denied (%s): it "
                              "would run outside this hook" % (event,))
    if _DENY_SPAWN and event in ("sqlite3.enable_load_extension",
                                 "sqlite3.load_extension"):
        # Native code is a process-sized hole in this hook, same as a spawn.
        _touch("load_extension", event, True, raw=True)
        raise PermissionError("sandbox: loading a native extension is denied "
                              "(%s): it would run outside this hook" % (event,))
    if event == "sqlite3.connect":
        p = args[0] if args else ""
        try:
            _check_sqlite(p)
        except PermissionError:
            _touch("sqlite", p, True, raw=True)
            raise
        _touch("sqlite", p, raw=True)
        return
    if event in ("os.listdir", "os.scandir"):
        p = args[0] if args else "."
        try:
            _check_protected(p)
            _check_read(p)
        except PermissionError:
            _touch("list", p, True)
            raise
        _touch("list", p)
        return
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        writing = False
        if isinstance(mode, str) and any(c in mode for c in "wxa+"):
            writing = True
        if isinstance(flags, int) and flags & (
            getattr(_os, "O_WRONLY", 1) | getattr(_os, "O_RDWR", 2)
            | getattr(_os, "O_APPEND", 8) | getattr(_os, "O_CREAT", 64)
        ):
            writing = True
        op = "write" if writing else "read"
        try:
            # READS too. The hook guarded writes only, so code could open any
            # file on the machine by absolute path, whatever the task's scope.
            _check_protected(path)
            _check_read(path)
            if writing and path is not None:
                _BUSY[0] = True
                try:
                    ok = _allowed(path)
                finally:
                    _BUSY[0] = False
                if not ok:
                    raise PermissionError(
                        "sandbox: write denied outside scope: %s" % (path,))
        except PermissionError:
            _touch(op, path, True)      # a refused open is on the record too
            raise
        _touch(op, path)
    elif event in _FS_EVENTS:
        op = event.split(".", 1)[1]
        for a in args:
            if not isinstance(a, (str, bytes, _os.PathLike)):
                continue
            _BUSY[0] = True
            try:
                ok = _allowed(a)
            finally:
                _BUSY[0] = False
            if not ok:
                _touch(op, a, True)
                raise PermissionError(
                    "sandbox: filesystem change denied outside scope: %s" % (a,)
                )
            _touch(op, a)


# ATTACH and VACUUM INTO open files from inside SQL, with no audit event at
# all; SQLite's authorizer is the one place that sees them. The audit event for
# a new connection carries a raw sqlite3* handle, not the Connection, so the
# authorizer is installed by the function that makes connections - wrapped
# here, before any agent code runs. A connection it cannot be installed on is
# refused. Code that builds a Connection some other way, or replaces the
# authorizer, gets past it: advisory, like the rest of this hook (D32).
if _DENY_SPAWN:
    import sqlite3 as _sq
    _SQ_CONNECT = _sq.connect

    def _fenced_connect(*a, **k):
        c = _SQ_CONNECT(*a, **k)
        try:
            c.set_authorizer(_sqlite_auth)
        except Exception:
            c.close()
            raise PermissionError("sandbox: could not fence this database "
                                  "connection")
        return c

    _sq.connect = _sq.dbapi2.connect = _fenced_connect


_sys.addaudithook(_audit)
'''


MAX_TOUCHED = 200


def _read_touched(log_path: str, workdir: str, links: list[dict]) -> dict | None:
    try:
        lines = Path(log_path).read_text(encoding="utf-8",
                                         errors="replace").splitlines()
    except OSError:
        return None
    wd = os.path.realpath(workdir)
    copies = [(os.path.realpath(e["dest"]), os.path.realpath(e["src"]))
              for e in links if e.get("mode") == "copy"]
    seen: set = set()
    entries: list[dict] = []
    for line in lines:
        if len(line) < 4:
            continue
        denied = line.startswith("D ")
        op, _, path = line[2:].partition(" ")
        for dest, src in copies:
            if path == dest or path.startswith(dest + os.sep):
                path = src + path[len(dest):]
                break
        else:
            if path == wd or path.startswith(wd + os.sep):
                path = "<sandbox>" + path[len(wd):].replace(os.sep, "/")
        key = (op, path, denied)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"op": op, "path": path, **({"denied": True} if denied else {})})
    return {"entries": entries[:MAX_TOUCHED],
            "dropped": max(0, len(entries) - MAX_TOUCHED),
            "denied": sum(1 for e in entries if e.get("denied"))}


def _build_env(workdir: str) -> dict:
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": workdir,
        "TEMP": workdir,
        "TMP": workdir,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        CHILD_ENV: "1",
    }


def run_sandboxed(
    code: str,
    allowed_paths: list[str] | None = None,
    timeout_s: int = 30,
    preamble: str = "",
    tool_handler: ToolHandler | None = None,
    protected_paths=(),
    on_output=None,
) -> dict:
    allowed_paths = allowed_paths or []
    notes: list[str] = []
    limits = dict(_LIMITS)
    if protected_paths:
        limits["max_processes"] = _platform.spawn_denied_processes() or MAX_PROCESSES
    note = _platform.limits_note(timeout_s, MEM_LIMIT_BYTES, limits["max_processes"])
    if note:
        notes.append(note)

    with tempfile.TemporaryDirectory(prefix="bucephalus_sbx_") as workdir:
        links = []
        for p in allowed_paths:
            src, rel = p if isinstance(p, (tuple, list)) else (p, None)
            entry = link_into(workdir, src, rel, skip_multilinked=bool(protected_paths))
            if entry:
                links.append(entry)

        touch_path = None
        if protected_paths:
            fd, touch_path = tempfile.mkstemp(prefix="bucephalus_touch_", suffix=".log")
            os.close(fd)
        guard = _GUARD_TEMPLATE.format(
            roots=[str(Path(p[0] if isinstance(p, (tuple, list)) else p).resolve())
                   for p in allowed_paths],
            workdir=str(Path(workdir).resolve()),
            protected=[str(p) for p in protected_paths],
            deny_spawn=bool(protected_paths),
            streams=_platform.has_data_streams(),
            read_fence=bool(protected_paths),
            touch_path=touch_path,
        )
        script = Path(workdir) / "_run.py"
        script.write_text(guard + "\n" + preamble + "\n" + code, encoding="utf-8")

        env = _build_env(workdir)
        argv = [*interpreter_argv(), str(script)]

        if tool_handler is None:
            result = _run_streaming(argv, workdir, env, timeout_s, on_output, limits)
        else:
            result = _run_with_bridge(argv, workdir, env, timeout_s, tool_handler, limits)

        result["changed_files"] = _copy_back(links)
        result["notes"] = notes
        result["touched"] = (_read_touched(touch_path, workdir, links)
                             if touch_path else None)
        if touch_path:
            try:
                os.unlink(touch_path)
            except OSError:
                pass
        return result


LIVE_CAP = 20_000


def _run_streaming(argv: list[str], workdir: str, env: dict, timeout_s: int, on_output, limits: dict = _LIMITS) -> dict:
    try:
        proc, close = _platform.spawn_limited(
            argv, timeout_s=timeout_s, **limits, cwd=workdir, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
    except LimitsRefused as e:
        return _limits_refused(e)
    parts = {"out": _Tail(OUTPUT_CAP), "err": _Tail(OUTPUT_CAP)}
    sent = {"n": 0}
    lock = threading.Lock()

    def pump(pipe, name: str) -> None:
        try:
            with pipe:  # closed by its reader: from another thread a close waits on a blocked read
                for line in iter(lambda: pipe.readline(65536), ""):
                    parts[name].add(line)
                    if on_output is None:
                        continue
                    with lock:
                        if sent["n"] < LIVE_CAP:
                            sent["n"] += len(line)
                            try:
                                on_output(line)
                            except Exception:  # noqa: BLE001
                                pass
        except (OSError, ValueError):
            pass

    readers = [threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True)]
    for r in readers:
        r.start()
    try:
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            close()
            proc.kill()
            proc.wait()
            for r in readers:
                r.join(5)
            return {"stdout": "", "stderr": "TIMEOUT", "returncode": -1}
        for r in readers:
            r.join(30)
    finally:
        close()
    return {"stdout": parts["out"].text(),
            "stderr": (parts["err"].text() + _platform.exit_note(proc, timeout_s))[-OUTPUT_CAP:],
            "returncode": proc.returncode}


def _run_with_bridge(
    argv: list[str], workdir: str, env: dict, timeout_s: int, handler: ToolHandler,
    limits: dict = _LIMITS,
) -> dict:
    popen = dict(cwd=workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                 stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                 bufsize=1)
    try:
        proc, close = _platform.spawn_limited(argv, timeout_s=timeout_s, **limits, **popen)
    except LimitsRefused as e:
        return {**_limits_refused(e), "tool_calls": 0}

    err = _Tail(OUTPUT_CAP)

    def _drain() -> None:
        try:
            with proc.stderr:  # closed by its reader: from another thread a close waits on a blocked read
                for chunk in iter(lambda: proc.stderr.read(65536), ""):
                    err.add(chunk)
        except Exception:  # noqa: BLE001
            pass

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()

    timed_out = {"v": False}

    def _kill() -> None:
        timed_out["v"] = True
        close()
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    timer = threading.Timer(timeout_s, _kill)
    timer.start()

    out = _Tail(OUTPUT_CAP)
    n_tool_calls = 0
    try:
        assert proc.stdout is not None and proc.stdin is not None
        for line in proc.stdout:
            if line.startswith(TOOLCALL_MARKER):
                n_tool_calls += 1
                try:
                    req = json.loads(line[len(TOOLCALL_MARKER):])
                    result = handler(req["tool"], req.get("args", {}) or {})
                    reply = {"ok": True, "result": result}
                except Exception as e:  # noqa: BLE001
                    reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                try:
                    proc.stdin.write(json.dumps(reply, default=str) + "\n")
                    proc.stdin.flush()
                except (BrokenPipeError, ValueError):
                    break
            else:
                out.add(line)
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        _kill()
    finally:
        timer.cancel()
        for stream in (proc.stdin, proc.stdout):
            try:
                if stream:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        drainer.join(timeout=2)
        close()

    if timed_out["v"]:
        return {"stdout": "", "stderr": "TIMEOUT", "returncode": -1,
                "tool_calls": n_tool_calls}
    return {
        "stdout": out.text(),
        "stderr": (err.text() + _platform.exit_note(proc, timeout_s))[-OUTPUT_CAP:],
        "returncode": proc.returncode if proc.returncode is not None else -1,
        "tool_calls": n_tool_calls,
    }
