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
    ...


class Missing(Exception):
    ...


class Exists(Exception):
    ...


class Unwritable(Exception):
    ...


def _allowed(ctx, task, raw: str, state=None) -> Path:
    root = ctx.config.root
    if not raw or "\x00" in raw:
        raise Refused("no path")
    if state is None:
        state = task_state_paths(ctx, task)
    scope = task.contract.artifact_scope
    given = Path(os.path.abspath(os.path.join(root, raw)))
    try:
        ok, _why = within_scope({"path": str(given)}, scope, root)
        if not ok or is_protected(str(given), root, state):
            raise Refused("outside this task's scope, or protected")
    except ValueError:
        raise Unwritable("that path is too long or not usable here") from None
    return given


def _visible(ctx, task, child: Path, state) -> bool:
    try:
        _allowed(ctx, task, str(child), state)
        return True
    except Refused:
        return False


def browse(ctx, task, raw: str) -> dict:
    state = task_state_paths(ctx, task)
    p = _allowed(ctx, task, raw, state)
    rel = os.path.relpath(p, ctx.config.root).replace(os.sep, "/")
    try:
        is_dir, is_file = p.is_dir(), p.is_file()
    except (OSError, ValueError):
        raise Missing(rel) from None
    if is_dir:
        entries = []
        for c in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
            if _visible(ctx, task, c, state):
                entries.append({"name": c.name, "dir": c.is_dir(),
                                "bytes": None if c.is_dir() else c.stat().st_size})
        return {"path": rel, "kind": "dir", "entries": entries}
    if not is_file:
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
    try:
        if p.exists():
            raise Exists("a drop never overwrites; rename it")
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        raise Exists("a drop never overwrites; rename it") from None
    except (OSError, ValueError) as e:
        raise Unwritable(f"cannot create {raw[:80]!r}: {getattr(e, 'strerror', None) or e}") from None
    rel = os.path.relpath(p, ctx.config.root).replace(os.sep, "/")
    ctx.log.append(task.id, "human.uploaded", {
        "path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "taint": "untrusted"})
    return {"path": rel, "bytes": len(data)}
