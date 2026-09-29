"""The console's file browser (a READER) and drag-and-drop (a WRITER).

Iteration 3, 4.4. New routes are new readers and writers, and DR3 says there
is one set of checks for all of them: the gate's own `within_scope` against
the TASK's artifact_scope, and `is_protected` against the task's state paths
(the database and its sidecars, the anchor, memory, graders, the token...).
Applied to the path as given AND to where it really leads, so a link inside
the workspace cannot show what it points at. tests/test_file_browser.py was
written first and shown red against a naive route.

A dropped file's content is UNTRUSTED: someone put it there, not the agent
and not the task's author. It is recorded on the task's chain as such, and
the agent reads it through `read_file`, whose observations are untrusted.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from pathlib import Path

from ..security.capabilities import is_protected, task_state_paths, within_scope

MAX_PREVIEW_BYTES = 512 * 1024
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class Refused(Exception):
    """403: outside the task's scope, or protected."""


class Missing(Exception):
    """404: allowed, but not there."""


class Exists(Exception):
    """409: a drop never overwrites."""


class Unwritable(Exception):
    """400: allowed, but the filesystem cannot hold this path (too long, a
    reserved name)."""


def _allowed(ctx, task, raw: str, state=None) -> Path:
    root = ctx.config.root
    if not raw or "\x00" in raw:
        raise Refused("no path")
    if state is None:
        state = task_state_paths(ctx, task)
    scope = task.contract.artifact_scope
    given = Path(os.path.abspath(os.path.join(root, raw)))
    # Both checks judge the path as given AND where it really leads (D61),
    # so one call each covers a link; checking the resolved spelling again
    # doubled the cost of every listing (audit I2).
    ok, _why = within_scope({"path": str(given)}, scope, root)
    if not ok or is_protected(str(given), root, state):
        raise Refused("outside this task's scope, or protected")
    return given


def _visible(ctx, task, child: Path, state) -> bool:
    try:
        _allowed(ctx, task, str(child), state)
        return True
    except Refused:
        return False


def browse(ctx, task, raw: str) -> dict:
    state = task_state_paths(ctx, task)            # once per listing (audit I2)
    p = _allowed(ctx, task, raw, state)
    rel = os.path.relpath(p, ctx.config.root).replace(os.sep, "/")
    if p.is_dir():
        entries = []
        for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if _visible(ctx, task, c, state):
                entries.append({"name": c.name, "dir": c.is_dir(),
                                "bytes": None if c.is_dir() else c.stat().st_size})
        return {"path": rel, "kind": "dir", "entries": entries}
    if not p.is_file():
        raise Missing(rel)
    n = p.stat().st_size
    if n > MAX_PREVIEW_BYTES:
        return {"path": rel, "kind": "file", "bytes": n, "too_large": True}
    data = p.read_bytes()
    try:
        return {"path": rel, "kind": "file", "bytes": n, "text": data.decode("utf-8")}
    except UnicodeDecodeError:
        return {"path": rel, "kind": "file", "bytes": n, "binary": True}


def drop(ctx, task, raw: str, content_b64: str) -> dict:
    try:
        data = base64.b64decode(content_b64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise Refused(f"not base64: {e}") from None
    if len(data) > MAX_UPLOAD_BYTES:
        raise Refused(f"larger than {MAX_UPLOAD_BYTES // 2**20} MB")
    p = _allowed(ctx, task, raw)
    if p.exists():
        raise Exists("a drop never overwrites; rename it")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "xb") as fh:      # exclusive: a race cannot overwrite either
            fh.write(data)
    except FileExistsError:
        raise Exists("a drop never overwrites; rename it") from None
    except OSError as e:               # audit B3: this was an unhandled 500
        raise Unwritable(f"cannot create {raw!r}: {e.strerror or e}") from None
    rel = os.path.relpath(p, ctx.config.root).replace(os.sep, "/")
    ctx.log.append(task.id, "human.uploaded", {
        "path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "taint": "untrusted"})
    return {"path": rel, "bytes": len(data)}
