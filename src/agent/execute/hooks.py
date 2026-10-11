from __future__ import annotations

import json
import os
import re
import subprocess
import time

from ..proc import kill_tree

MAX_HOOK_TEXT = 1500
SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.I)


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if not SECRETISH.search(k)}


def _run_one(h, payload: dict, cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.Popen(  # noqa: S603
            h.argv, cwd=cwd, env=_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    except OSError as e:
        return 127, f"could not start: {e}"
    try:
        out, _ = p.communicate(json.dumps(payload, default=str), timeout=h.timeout_s)
    except subprocess.TimeoutExpired:
        kill_tree(p.pid)
        p.communicate()
        return 124, f"timed out after {h.timeout_s}s"
    except OSError as e:
        kill_tree(p.pid)
        return 126, str(e)
    return p.returncode, (out or "").strip()[:MAX_HOOK_TEXT]


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
