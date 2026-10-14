from __future__ import annotations

import json
import os
import re
import subprocess
import time

from ..proc import kill_tree

MAX_HOOK_TEXT = 1500
ENV_ALLOW = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "USERNAME", "USER", "LOGNAME", "SHELL",
    "TERM", "LANG", "LANGUAGE", "TZ", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS",
    "PYTHONUTF8", "PYTHONIOENCODING",
})
_PYTHON = re.compile(r"python[0-9.]*w?(\.exe)?", re.I)


def _env(extra: dict | None = None) -> dict:
    base = {k: v for k, v in os.environ.items() if k.upper() in ENV_ALLOW or k.upper().startswith("LC_")}
    return {**base, **(extra or {})}


def _isolated(argv: list[str]) -> list[str]:
    if argv and _PYTHON.fullmatch(os.path.basename(argv[0])) and argv[1:2] != ["-I"]:
        return [argv[0], "-I", *argv[1:]]
    return list(argv)


def _run_one(h, payload: dict, cwd: str) -> tuple[int, str]:
    import tempfile

    with tempfile.TemporaryFile() as out:
        try:
            p = subprocess.Popen(  # noqa: S603
                _isolated(h.argv), cwd=cwd, env=_env(getattr(h, "env", None)), stdin=subprocess.PIPE, stdout=out,
                stderr=subprocess.STDOUT)
        except OSError as e:
            return 127, f"could not start: {e}"
        try:
            p.communicate(json.dumps(payload, default=str).encode("utf-8"), timeout=h.timeout_s)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid)
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                pass
            return 124, f"timed out after {h.timeout_s}s"
        except OSError as e:
            kill_tree(p.pid)
            return 126, str(e)
        out.seek(0)
        text = out.read(MAX_HOOK_TEXT * 4).decode("utf-8", errors="replace")
    return p.returncode, text.strip()[:MAX_HOOK_TEXT]


def run_hooks(ctx, task, event: str, tool: str | None = None, **extra) -> tuple[bool, str]:
    ok, texts = True, []
    for h in getattr(ctx.config.hooks, event):
        if h.tools and tool not in h.tools:
            continue
        t0 = time.time()
        code, text = _run_one(h, {"event": event, "task_id": task.id, "tool": tool, **extra},
                              str(ctx.config.root))
        ctx.log.append(task.id, "hook.ran", {"event": event, "tool": tool, "argv0": h.argv[0],
                                             "exit_code": code, "ms": int((time.time() - t0) * 1000)})
        if text:
            texts.append(text)
        if code != 0 and event != "post_tool":
            ok = False
            break
    return ok, "\n".join(texts)
