from __future__ import annotations

import os
import platform
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from ..paths import sync_root, user_config_dir

STARTED = time.time()
RESTORE = "bucephalus restore <backup.zip>, with the console stopped; it checks the backup first and keeps what it replaces"


class SyncedFolder(Exception):
    ...


class Unsound(Exception):
    ...


def console_lock(ctx):
    from ..runlock import RunLock

    return RunLock(ctx.config.resolve_state("console.lock"), label="console")


def hold_console_lock(ctx):
    # ponytail: a second console on the same record is let through unlocked; if the first one stops, restore is
    # no longer blocked by the second. Count holders if two consoles at once becomes a normal setup.
    from ..runlock import RunLockBusy

    try:
        return console_lock(ctx).acquire()
    except RunLockBusy:
        return None


def code_changed() -> bool:
    src = Path(__file__).resolve().parents[1]
    return any(p.stat().st_mtime > STARTED for p in src.rglob("*.py"))


def backups_dir() -> Path:
    return user_config_dir() / "backups"


def info(ctx) -> dict:
    from .. import HARNESS_VERSION, TOOLSET_VERSION

    state = Path(ctx.config.state_dir or ctx.config.root)
    size = lambda p: p.stat().st_size if p.is_file() else 0  # noqa: E731
    return {"os": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count() or 1,
            "disk_free": shutil.disk_usage(state).free, "state_dir": str(state),
            "harness_version": HARNESS_VERSION, "toolset_version": TOOLSET_VERSION,
            "uptime_s": int(time.time() - STARTED), "code_changed": code_changed(),
            "db_bytes": size(ctx.config.resolve_state(ctx.config.db_path)),
            "anchor_bytes": size(ctx.config.resolve_state(ctx.config.anchor_path)),
            "backups_dir": str(backups_dir()), "restore": RESTORE}


def listing() -> list[dict]:
    d = backups_dir()
    return sorted(({"name": p.name, "bytes": p.stat().st_size, "created": p.stat().st_mtime}
                   for p in d.glob("bucephalus-*.zip") if p.is_file()), key=lambda b: -b["created"]) if d.is_dir() else []


def find(name: str) -> Path | None:
    return next((backups_dir() / b["name"] for b in listing() if b["name"] == name), None)


def backup(ctx) -> dict:
    from ..kernel.db import snapshot

    d = backups_dir()
    if sync_root(d):
        raise SyncedFolder(f"{d} is inside a synced folder; a backup holds your conversations, so keep it off sync. "
                           "Set BUCEPHALUS_CONFIG_DIR to a local folder.")
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    name = time.strftime("bucephalus-%Y%m%d-%H%M%S.zip")
    with tempfile.TemporaryDirectory() as tmp:
        db = snapshot(ctx.conn, Path(tmp) / "agent.db")
        fd = os.open(d / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as fh, zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db, "agent.db")
            anchor = ctx.config.resolve_state(ctx.config.anchor_path)
            if anchor.is_file():
                z.write(anchor, "chain.anchor")
            for f in sorted(Path(ctx.memory.root).glob("*")):
                if f.is_file():
                    z.write(f, f"memory/{f.name}")
    return {"name": name, "bytes": (d / name).stat().st_size}


def _unpack(zip_path: Path, dest: Path) -> list[str]:
    # ponytail: no size cap; a backup is the user's own file. Cap it if backups ever come from elsewhere.
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist()
                 if n in ("agent.db", "chain.anchor") or (n.startswith("memory/") and "/" not in n[7:] and n[7:] not in ("", ".", ".."))]
        for n in names:
            out = dest / n
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(n))
    return names


def _examine(d: Path) -> dict:
    from contextlib import closing

    from ..kernel.db import connect
    from ..kernel.events import EventLog
    from ..kernel.integrity import Anchor, critical, verify_all
    from ..memory.store import MemoryStore

    if not (d / "agent.db").is_file():
        return {"ok": False, "tasks": 0, "events": 0, "problems": ["the zip holds no agent.db"]}
    with closing(connect(d / "agent.db")) as conn:
        try:
            problems = [r[0] for r in conn.execute("PRAGMA integrity_check") if r[0] != "ok"]
            tasks = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            memory = MemoryStore(d / "memory") if (d / "memory").is_dir() else None
            problems += [f"{f['task_id']}: {f['detail']}"
                         for f in critical(verify_all(conn, EventLog(conn), Anchor(d / "chain.anchor"), memory))]
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "tasks": 0, "events": 0, "problems": [f"the database does not open as a record: {e}"]}
    return {"ok": not problems, "tasks": tasks, "events": events, "problems": problems}


def check(zip_path: Path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _unpack(zip_path, Path(tmp))
        except (zipfile.BadZipFile, OSError) as e:
            return {"ok": False, "tasks": 0, "events": 0, "problems": [f"not a readable zip: {e}"]}
        return _examine(Path(tmp))


def restore(ctx, zip_path: Path) -> dict:
    zip_path = Path(zip_path)
    found = check(zip_path)
    if not found["ok"]:
        raise Unsound("; ".join(found["problems"]))
    lock = console_lock(ctx).acquire()
    try:
        db = ctx.config.resolve_state(ctx.config.db_path)
        anchor = ctx.config.resolve_state(ctx.config.anchor_path)
        memory = Path(ctx.memory.root)
        ctx.close()
        replaced = db.parent / time.strftime("replaced-%Y%m%d-%H%M%S")
        replaced.mkdir()
        with tempfile.TemporaryDirectory(dir=db.parent) as tmp:
            _unpack(zip_path, Path(tmp))
            for p in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm"), anchor, memory):
                if p.exists():
                    shutil.move(str(p), str(replaced / p.name))
            for src, dest in ((Path(tmp) / "agent.db", db), (Path(tmp) / "chain.anchor", anchor), (Path(tmp) / "memory", memory)):
                if src.exists():
                    shutil.move(str(src), str(dest))
    finally:
        lock.release()
    return {"restored": zip_path.name, "replaced": str(replaced), "tasks": found["tasks"], "events": found["events"]}
