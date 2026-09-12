"""M5.3 — the sandbox.

Honesty first: a subprocess sandbox is a safety measure against MISTAKES, not
a security boundary against an adversary. If you ever run untrusted code, use
Docker with --network=none, a read-only root, a non-root user, and dropped
capabilities. What follows is the minimum for a local prototype.

The spec's version uses `resource` and `preexec_fn`, which are POSIX-only.
This build runs on Windows too, so limits are applied where the platform
supports them and the gaps are reported in the result rather than hidden.

Two things beyond the spec's snippet, both required by its own M5 acceptance
tests: an audit hook that confines writes to scope, and a live tool bridge so
`call_tool` inside sandboxed code round-trips through the capability gate.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

IS_POSIX = os.name == "posix"

MEM_LIMIT_BYTES = 512 * 1024 * 1024
FSIZE_LIMIT_BYTES = 64 * 1024 * 1024
OUTPUT_CAP = 16000
TOOLCALL_MARKER = "\x01TOOLCALL"

ToolHandler = Callable[[str, dict], dict]


def _posix_limits(timeout_s: int):  # pragma: no cover - not reachable on Windows
    import resource

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT_BYTES,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_s, timeout_s))
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        except (ValueError, OSError):
            pass
        resource.setrlimit(resource.RLIMIT_FSIZE, (FSIZE_LIMIT_BYTES,) * 2)

    return apply


# SQLite files that carry CONTENT beside a database. The newest rows of a WAL
# database are in `-wal`, so a file copied on its own arrives without them -
# C41's shape, in the one place this project copies user files. `-shm` is left
# out on purpose: it is an index SQLite rebuilds, and a copied one can carry
# another process's lock state.
CARRIED_SIDECARS = ("-wal", "-journal")


def _mirror_sidecars(sandbox_main: Path, original_main: Path,
                     changed: list[str]) -> None:
    """Make the original's sidecars match the sandbox's, exactly.

    Copy back what exists; REMOVE what the sandbox no longer has. A main file
    copied back beside the original's older `-wal` would let SQLite replay
    stale frames onto newer pages on the next open.
    """
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


def link_into(workdir: str, path: str, rel: str | None = None) -> dict | None:
    """Expose one allowed path inside the sandbox workdir.

    `rel` preserves the path the agent was told to use. Flattening
    `work/E01` to `E01` means code written against the documented path finds
    nothing, writes its answer into a directory that is never copied back, and
    the task fails for a reason that has nothing to do with the task.

    Symlink where the platform allows it; copy otherwise, recording the copy so
    changes can be written back. Only artifact_scope paths ever get here.
    """
    src = Path(path).resolve()
    if not src.exists():
        return None
    dest = Path(workdir) / (rel or src.name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(src, dest, target_is_directory=src.is_dir())
        return {"mode": "symlink", "src": str(src), "dest": str(dest)}
    except (OSError, NotImplementedError, AttributeError):
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
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


# Writes are confined to the workdir and the paths explicitly linked into it,
# enforced inside the child with an audit hook. This is a real guard against
# an agent mistakenly writing somewhere it should not — including anchor/ and
# skills_store/, which M5.3 requires. It is NOT a guard against an adversary
# with code execution: an audit hook lives in the same process it polices.
_GUARD_TEMPLATE = '''\
import sys as _sys, os as _os

_ROOTS = [_os.path.realpath(p) for p in {roots!r}]
_WORKDIR = _os.path.realpath({workdir!r})
_BUSY = [False]


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


_FS_EVENTS = (
    "os.remove", "os.rmdir", "os.mkdir", "os.rename", "os.link",
    "os.symlink", "os.truncate", "shutil.copyfile", "shutil.move",
    "shutil.rmtree",
)


def _audit(event, args):
    if _BUSY[0]:
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
        if not writing or path is None:
            return
        _BUSY[0] = True
        try:
            ok = _allowed(path)
        finally:
            _BUSY[0] = False
        if not ok:
            raise PermissionError("sandbox: write denied outside scope: %s" % (path,))
    elif event in _FS_EVENTS:
        for a in args:
            if not isinstance(a, (str, bytes, _os.PathLike)):
                continue
            _BUSY[0] = True
            try:
                ok = _allowed(a)
            finally:
                _BUSY[0] = False
            if not ok:
                raise PermissionError(
                    "sandbox: filesystem change denied outside scope: %s" % (a,)
                )


_sys.addaudithook(_audit)
'''


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
    }


def run_sandboxed(
    code: str,
    allowed_paths: list[str] | None = None,
    timeout_s: int = 30,
    preamble: str = "",
    tool_handler: ToolHandler | None = None,
) -> dict:
    """Run python in an isolated working directory.

    If `tool_handler` is given, `call_tool(...)` inside the code is serviced
    LIVE: each request is handed to the handler (which gates it) and the
    result is written back to the child. Without a handler the child's
    requests are collected and reported instead of silently no-oping.
    """
    allowed_paths = allowed_paths or []
    notes: list[str] = []
    if not IS_POSIX:
        notes.append(
            "no rlimits on this platform: memory and CPU caps not enforced, "
            "only the wall-clock timeout"
        )

    with tempfile.TemporaryDirectory(prefix="alpha_sbx_") as workdir:
        links = []
        for p in allowed_paths:  # artifact_scope only
            # Accept either a bare path or (absolute, relative-name) so the
            # caller can preserve the layout the agent was told to expect.
            src, rel = p if isinstance(p, (tuple, list)) else (p, None)
            entry = link_into(workdir, src, rel)
            if entry:
                links.append(entry)

        guard = _GUARD_TEMPLATE.format(
            roots=[str(Path(p[0] if isinstance(p, (tuple, list)) else p).resolve())
                   for p in allowed_paths],
            workdir=str(Path(workdir).resolve()),
        )
        script = Path(workdir) / "_run.py"
        script.write_text(guard + "\n" + preamble + "\n" + code, encoding="utf-8")

        env = _build_env(workdir)
        argv = [sys.executable, "-I", "-u", str(script)]

        if tool_handler is None:
            result = _run_batch(argv, workdir, env, timeout_s)
        else:
            result = _run_with_bridge(argv, workdir, env, timeout_s, tool_handler)

        result["changed_files"] = _copy_back(links)
        result["notes"] = notes
        return result


def _run_batch(argv: list[str], workdir: str, env: dict, timeout_s: int) -> dict:
    kwargs: dict = {}
    if IS_POSIX:
        kwargs["preexec_fn"] = _posix_limits(timeout_s)
    try:
        proc = subprocess.run(  # noqa: S603 - the point of this function
            argv, cwd=workdir, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s, **kwargs,
        )
        return {
            "stdout": (proc.stdout or "")[-OUTPUT_CAP:],
            "stderr": (proc.stderr or "")[-OUTPUT_CAP:],
            "returncode": proc.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "TIMEOUT", "returncode": -1}


def _run_with_bridge(
    argv: list[str], workdir: str, env: dict, timeout_s: int, handler: ToolHandler
) -> dict:
    """Pump the child's stdout, servicing tool requests as they arrive.

    stderr is drained on its own thread so a chatty child cannot deadlock by
    filling the stderr pipe while we are blocked reading stdout.
    """
    kwargs: dict = {}
    if IS_POSIX:
        kwargs["preexec_fn"] = _posix_limits(timeout_s)

    proc = subprocess.Popen(  # noqa: S603
        argv, cwd=workdir, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        bufsize=1, **kwargs,
    )

    stderr_parts: list[str] = []

    def _drain() -> None:
        try:
            stderr_parts.append(proc.stderr.read() or "")
        except Exception:  # noqa: BLE001
            pass

    drainer = threading.Thread(target=_drain, daemon=True)
    drainer.start()

    timed_out = {"v": False}

    def _kill() -> None:
        timed_out["v"] = True
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    timer = threading.Timer(timeout_s, _kill)
    timer.start()

    out_lines: list[str] = []
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
                except Exception as e:  # noqa: BLE001 - reported into the child
                    reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                try:
                    proc.stdin.write(json.dumps(reply, default=str) + "\n")
                    proc.stdin.flush()
                except (BrokenPipeError, ValueError):
                    break
            else:
                out_lines.append(line)
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

    if timed_out["v"]:
        return {"stdout": "", "stderr": "TIMEOUT", "returncode": -1,
                "tool_calls": n_tool_calls}
    return {
        "stdout": "".join(out_lines)[-OUTPUT_CAP:],
        "stderr": "".join(stderr_parts)[-OUTPUT_CAP:],
        "returncode": proc.returncode if proc.returncode is not None else -1,
        "tool_calls": n_tool_calls,
    }
