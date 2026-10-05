from __future__ import annotations

import os
from pathlib import Path

APP = "alpha-test-1"

_CHECKOUT_MARKERS = ("pyproject.toml", "src/agent", "bench")

STATE_DIRS = ("runs", "work", "grading", "anchor", "skills_store")


def checkout_root(start: str | Path | None = None) -> Path | None:
    base = Path(start or Path.cwd()).resolve()
    for cand in (base, *base.parents):
        if all((cand / m).exists() for m in _CHECKOUT_MARKERS[:2]):
            return cand
        if cand == cand.parent:
            break
    return None


def user_config_dir() -> Path:
    import platformdirs

    return Path(os.environ.get("ALPHA_CONFIG_DIR")
                or platformdirs.user_config_dir(APP))


def user_data_dir() -> Path:
    import platformdirs

    return Path(os.environ.get("ALPHA_DATA_DIR")
                or platformdirs.user_data_dir(APP))


def state_root(config_path: str | Path | None = None) -> Path:
    if os.environ.get("ALPHA_DATA_DIR"):
        return Path(os.environ["ALPHA_DATA_DIR"])
    if config_path is not None:
        root = checkout_root(Path(config_path).resolve().parent)
        if root is not None:
            return root
    root = checkout_root()
    return root if root is not None else user_data_dir()


_SYNC_ENV = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
_SYNC_NAMES = ("dropbox", "iclouddrive", "icloud drive", "google drive", "my drive",
               "mobile documents", "cloudstorage", "box", "box sync", "pcloud drive",
               "nextcloud", "owncloud", "mega", "megasync", "proton drive", "sync",
               "synologydrive", "tresorit", "seadrive", "yandex.disk")
_SYNC_MARKERS = (".dropbox", ".nextcloudsync.log", ".owncloudsync.log",
                 ".SynologyWorkingDirectory", ".debris", ".sync", ".tresorit")


def sync_root(path: str | Path) -> Path | None:
    p = Path(path).resolve()
    for var in _SYNC_ENV:
        v = os.environ.get(var)
        if v:
            r = Path(v).resolve()
            if r == p or r in p.parents:
                return r
    for cand in (p, *p.parents):
        n = cand.name.lower()
        if n.startswith("onedrive") or n in _SYNC_NAMES:
            return cand
        try:
            if (any((cand / m).exists() for m in _SYNC_MARKERS)
                    or any(cand.glob(".sync_*.db"))):
                return cand
        except OSError:
            continue
    return None


def checkout_state_dir(root: str | Path) -> Path:
    import hashlib

    r = Path(root).resolve()
    tag = hashlib.sha256(str(r).encode("utf-8")).hexdigest()[:8]
    return user_data_dir() / "checkouts" / f"{r.name}-{tag}"


def ensure_state_dirs(root: str | Path) -> list[Path]:
    made = []
    for d in STATE_DIRS:
        p = Path(root) / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            made.append(p)
    return made


def default_config_path() -> Path | None:
    p = user_config_dir() / "config.yaml"
    return p if p.exists() else None
