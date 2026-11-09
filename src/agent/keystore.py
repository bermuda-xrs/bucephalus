from __future__ import annotations

import json
import os
from pathlib import Path

from .paths import sync_root, user_config_dir

MAX_LEN = 512


class SyncedFolder(Exception):
    ...


def path() -> Path:
    return user_config_dir() / "keys.json"


def _read() -> dict[str, str]:
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)} if isinstance(d, dict) else {}


def get(name: str) -> str | None:
    return os.environ.get(name) or _read().get(name) or None


def source(name: str) -> str | None:
    return "environment" if os.environ.get(name) else "console" if _read().get(name) else None


def declared(cfg) -> dict[str, str]:
    out = {cfg.channels.telegram.token_env: "the Telegram bot"}
    out.update({b.api_key_env: f"backend {n}" for n, b in cfg.backends.items() if b.api_key_env})
    return out


def _write(d: dict[str, str]) -> None:
    p = path()
    if sync_root(p.parent):
        raise SyncedFolder(f"{p.parent} is inside a synced folder; keys must not leave this machine. "
                           "Set BUCEPHALUS_CONFIG_DIR to a local folder.")
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(p.parent, 0o700)
    tmp = p.with_name(p.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    os.replace(tmp, p)


def put(name: str, value: str) -> None:
    if not value.strip() or len(value) > MAX_LEN or "\n" in value or "\r" in value:
        raise ValueError(f"a key is one line of 1 to {MAX_LEN} characters")
    _write({**_read(), name: value.strip()})


def remove(name: str) -> None:
    d = _read()
    d.pop(name, None)
    _write(d)
