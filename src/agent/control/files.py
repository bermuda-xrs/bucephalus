from __future__ import annotations

import base64
import binascii
import hashlib
import os
import stat
import tempfile
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


class Busy(Exception):
    ...


class Stale(Exception):
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
        return {"path": rel, "abs": str(p), "kind": "dir", "entries": entries}
    if not is_file:
        raise Missing(rel)
    n = p.stat().st_size
    out = {"path": rel, "abs": str(p), "kind": "file", "bytes": n}
    if n > MAX_PREVIEW_BYTES:
        return {**out, "too_large": True}
    data = p.read_bytes()
    try:
        return {**out, "text": data.decode("utf-8"), "sha256": hashlib.sha256(data).hexdigest()}
    except UnicodeDecodeError:
        return {**out, "binary": True}


def edit(ctx, task, raw: str, text: str, base: str) -> dict:
    from ..execute.say import OPEN, say
    from ..kernel.tasks import load_task
    from ..kernel.types import Taint
    from ..ledger.artifacts import write_artifact
    from ..runlock import RunLockBusy, task_lock

    data = text.encode("utf-8")
    if len(data) > MAX_UPLOAD_BYTES:
        raise Refused(f"larger than {MAX_UPLOAD_BYTES // 2**20} MB")
    try:
        lock = task_lock(ctx, task.id).acquire()
    except RunLockBusy:
        raise Busy("the task is running a step; stop it first") from None
    try:
        p = _allowed(ctx, task, raw)
        rel = os.path.relpath(p, ctx.config.root).replace(os.sep, "/")
        if p.is_symlink():
            raise Refused("that is a link; edit the file it points to")
        task = load_task(ctx.conn, task.id)
        if task.status.value == "running":
            raise Busy("the task is running; stop it first")
        if not p.is_file():
            raise Missing(rel)
        old = p.read_bytes()
        if hashlib.sha256(old).hexdigest() != base:
            raise Stale("the file changed since you opened it; open it again")
        try:
            old_text = old.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused("only a UTF-8 text file can be edited here") from None
        aid = write_artifact(ctx.conn, task.id, "human_edit_before", old_text, Taint.UNTRUSTED, {"path": rel})
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".edit", dir=p.parent)  # O_EXCL: no planted link
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp, stat.S_IMODE(p.stat().st_mode))
            os.replace(tmp, p)  # replaces the name itself: never writes through a link swapped in meanwhile
        except OSError as e:
            if tmp:
                Path(tmp).unlink(missing_ok=True)
            raise Unwritable(f"cannot write {rel}: {e.strerror or e}") from None
        after = hashlib.sha256(data).hexdigest()
        ctx.log.append(task.id, "human.edited", {"path": rel, "before_sha256": base, "after_sha256": after,
                                                 "bytes": len(data), "before_artifact": aid})
        if task.status in OPEN:  # no path: a file name may come from untrusted text, and this message is trusted
            say(ctx, task.id, "I edited a file in the workspace by hand while you were stopped. Read a file again "
                              "before relying on what you remember of it.")
    finally:
        lock.release()
    return {"path": rel, "bytes": len(data), "sha256": after}


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


def make_folder(ctx, task, raw: str) -> dict:
    p = _allowed(ctx, task, raw)
    if p.exists():
        raise Exists("already there")
    try:
        p.mkdir(parents=True)
    except (OSError, ValueError) as e:
        raise Unwritable(f"cannot create {raw[:80]!r}: {getattr(e, 'strerror', None) or e}") from None
    rel = os.path.relpath(p, ctx.config.root).replace(os.sep, "/")
    ctx.log.append(task.id, "human.made_folder", {"path": rel})
    return {"path": rel}


def remove(ctx, task, raw: str) -> dict:
    if task.status.value == "running":
        raise Busy("the task is running; stop it first")
    p = _allowed(ctx, task, raw)
    rel = os.path.relpath(p, ctx.config.root).replace(os.sep, "/")
    if not p.is_file():
        raise Missing(rel)
    added = [e["payload"] for e in ctx.log.events(task.id)
             if e["type"] == "human.uploaded" and e["payload"].get("path") == rel]
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    if not added or added[-1].get("sha256") != digest:
        raise Refused("only a file you added, unchanged since, can be removed here; rewind undoes the agent's files")
    p.unlink()
    ctx.log.append(task.id, "human.removed", {"path": rel, "sha256": digest})
    return {"removed": rel}
