from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP = "bucephalus"
LEGACY_APP = "alpha-test-1"

_CHECKOUT_MARKERS = ("pyproject.toml", "src/agent", "bench")

STATE_DIRS = ("runs", "work", "grading", "anchor", "skills_store")

TOOL_DIRS = ("bench", "packaging", "scripts")
SCRATCH_ONLY = "BUCEPHALUS_SCRATCH_ONLY"


class RealDirRefused(RuntimeError):
    pass


def _started_from_tool_dir() -> bool:
    main = getattr(sys.modules.get("__main__"), "__file__", None) or (sys.argv[0] if sys.argv else "")
    if not main or getattr(sys, "frozen", False):
        return False
    root = Path(__file__).resolve().parents[2]
    try:
        p = Path(main).resolve()
    except OSError:
        return False
    return any(p.is_relative_to(root / d) for d in TOOL_DIRS)


def scratch_only() -> bool:
    if os.environ.get(SCRATCH_ONLY) or _started_from_tool_dir():
        os.environ[SCRATCH_ONLY] = "1"
        return True
    return False


def _refuse_real(var: str) -> None:
    if scratch_only():
        raise RealDirRefused(f"a process started from {', '.join(TOOL_DIRS)}/ never uses the real user directories; "
                             f"set {var} to a scratch directory")


def checkout_root(start: str | Path | None = None) -> Path | None:
    base = Path(start or Path.cwd()).resolve()
    for cand in (base, *base.parents):
        if all((cand / m).exists() for m in _CHECKOUT_MARKERS[:2]):
            return cand
        if cand == cand.parent:
            break
    return None


def _adopt_legacy(new: Path, old: Path) -> Path:
    if new.exists() or new == old or not old.is_dir():
        return new
    staging = new.with_name(f"{new.name}.migrating-{os.getpid()}")
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(old, staging)
        os.replace(staging, new)
    except OSError as e:
        shutil.rmtree(staging, ignore_errors=True)
        if new.is_dir():
            return new
        raise RuntimeError(f"could not copy {old} to {new}: {e}. nothing was changed; the old directory is intact") from e
    return new


def user_config_dir() -> Path:
    import platformdirs

    if os.environ.get("BUCEPHALUS_CONFIG_DIR"):
        return Path(os.environ["BUCEPHALUS_CONFIG_DIR"])
    _refuse_real("BUCEPHALUS_CONFIG_DIR")
    return _adopt_legacy(Path(platformdirs.user_config_dir(APP)), Path(platformdirs.user_config_dir(LEGACY_APP)))


def user_data_dir() -> Path:
    import platformdirs

    if os.environ.get("BUCEPHALUS_DATA_DIR"):
        return Path(os.environ["BUCEPHALUS_DATA_DIR"])
    _refuse_real("BUCEPHALUS_DATA_DIR")
    return _adopt_legacy(Path(platformdirs.user_data_dir(APP)), Path(platformdirs.user_data_dir(LEGACY_APP)))


def state_root(config_path: str | Path | None = None) -> Path:
    if os.environ.get("BUCEPHALUS_DATA_DIR"):
        return Path(os.environ["BUCEPHALUS_DATA_DIR"])
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


scratch_only()
