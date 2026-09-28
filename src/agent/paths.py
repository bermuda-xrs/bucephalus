"""Where state lives: beside the source in a checkout, in user dirs once installed.

A wheel that writes `agent.db` into the current working directory is a wheel
that behaves differently depending on where you stand when you run it — the
same class of bug as `verify_program` resolving `artifact_scope` against the
process CWD, which produced a silent 0% on every task from the wrong
directory.

So there are exactly two modes, chosen by evidence rather than a flag:

  CHECKOUT   a `config.yaml` (or the config the caller named) sits next to
             `pyproject.toml`/`src/`. State stays project-relative, because
             that is what a developer running `bench/` expects and what every
             existing run record was written against.

  INSTALLED  no such marker. State goes to platformdirs user data/config,
             so two projects cannot collide and nothing is written next to
             site-packages.

`state_root()` is the one place that decides. Nothing else should call
platformdirs directly.
"""

from __future__ import annotations

import os
from pathlib import Path

APP = "alpha-test-1"

# Files that mark a source checkout rather than an installed package.
_CHECKOUT_MARKERS = ("pyproject.toml", "src/agent", "bench")

# State that must never ship inside a wheel and must never be written next to
# site-packages. Ordered as they appear in config.
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
    """The directory every relative state path resolves against.

    `ALPHA_DATA_DIR` overrides both modes — needed by the post-install test,
    which must run from a directory that is deliberately not the checkout.
    """
    if os.environ.get("ALPHA_DATA_DIR"):
        return Path(os.environ["ALPHA_DATA_DIR"])
    if config_path is not None:
        root = checkout_root(Path(config_path).resolve().parent)
        if root is not None:
            return root
    root = checkout_root()
    return root if root is not None else user_data_dir()


# Folders a sync client uploads. Matched by directory NAME, because the client
# is not always running and its settings are not always readable; the
# environment variables are Windows' own record of where OneDrive lives.
_SYNC_ENV = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")
_SYNC_NAMES = ("dropbox", "iclouddrive", "icloud drive", "google drive", "my drive",
               "mobile documents", "cloudstorage", "box sync", "pcloud drive")


def sync_root(path: str | Path) -> Path | None:
    """The synced folder `path` is inside, or None."""
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
    return None


def checkout_state_dir(root: str | Path) -> Path:
    """Where a checkout inside a synced folder keeps its runtime state: the
    database, the anchor, the run records. One directory per checkout, so two
    clones never share a database."""
    import hashlib

    r = Path(root).resolve()
    tag = hashlib.sha256(str(r).encode("utf-8")).hexdigest()[:8]
    return user_data_dir() / "checkouts" / f"{r.name}-{tag}"


def ensure_state_dirs(root: str | Path) -> list[Path]:
    """Create the state directories. Returns the ones created."""
    made = []
    for d in STATE_DIRS:
        p = Path(root) / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            made.append(p)
    return made


def default_config_path() -> Path | None:
    """An installed package looks for its config in the user config dir."""
    p = user_config_dir() / "config.yaml"
    return p if p.exists() else None
