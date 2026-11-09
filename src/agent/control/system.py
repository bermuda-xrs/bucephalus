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
RESTORE = "bucephalus restore is not offered here: stop the console, then unzip the backup over the state folder"


class SyncedFolder(Exception):
    ...


def backups_dir() -> Path:
    return user_config_dir() / "backups"


def info(ctx) -> dict:
    from .. import HARNESS_VERSION, TOOLSET_VERSION

    state = Path(ctx.config.state_dir or ctx.config.root)
    size = lambda p: p.stat().st_size if p.is_file() else 0  # noqa: E731
    return {"os": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count() or 1,
            "disk_free": shutil.disk_usage(state).free, "state_dir": str(state),
            "harness_version": HARNESS_VERSION, "toolset_version": TOOLSET_VERSION,
            "uptime_s": int(time.time() - STARTED),
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
