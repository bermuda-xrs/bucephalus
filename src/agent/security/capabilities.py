from __future__ import annotations

import functools
import inspect
import os
import re
import stat
import time
from pathlib import Path

from ..kernel.types import GateDecision, Taint
from .origin import is_unattended

PROTECTED_DIRS = ("anchor", "skills_store")
PROTECTED_FILES = ("config.yaml", "policy.md", ".env")

DB_FILES = ("", "-wal", "-shm", "-journal")

PROJECT_STATE = (
    "anchor", "skills_store",
    "grading", "bench",
    "runs", "docs/evidence",
    ".bucephalus_token", ".env", "policy.md",
    "cal.json", ".token_calibration.json", ".measurement.lock",
    "checkouts", "shadow",
)


@functools.lru_cache(maxsize=32)
def _sibling_state(root: str, stamps: tuple) -> tuple[str, ...]:
    import yaml

    out: list[str] = []
    for name, _ in stamps:
        path = Path(root) / name
        out.append(str(path))
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            continue
        for key in ("db_path", "anchor_path", "skills_path"):
            v = raw.get(key) if isinstance(raw, dict) else None
            if not v:
                continue
            q = Path(v) if Path(v).is_absolute() else Path(root) / v
            out += ([str(q) + s for s in DB_FILES] if key == "db_path"
                    else [str(q)])
    return tuple(out)


def state_paths(ctx) -> tuple[Path, ...]:
    cfg = ctx.config
    root = Path(os.path.abspath(cfg.root))
    db = str(cfg.resolve_state(cfg.db_path))
    out = [Path(db + s) for s in DB_FILES]
    out += [cfg.resolve_state(cfg.anchor_path), cfg.resolve(cfg.skills_path)]
    if cfg.state_dir:
        out.append(Path(cfg.state_dir))
    out += [root / rel for rel in PROJECT_STATE]
    try:
        stamps = tuple(sorted((c.name, c.stat().st_mtime)
                              for c in root.glob("config*.yaml")))
    except OSError:
        stamps = ()
    out += [Path(p) for p in _sibling_state(str(root), stamps)]
    mem = getattr(getattr(ctx, "memory", None), "root", None)
    if mem is not None:
        out.append(Path(mem))
    from ..control.guard import TOKEN_FILE
    from ..paths import user_config_dir

    out.append(user_config_dir() / TOKEN_FILE)
    return tuple(Path(os.path.abspath(p)) for p in out)


def task_state_paths(ctx, task) -> tuple[Path, ...]:
    out = state_paths(ctx)
    grader = getattr(getattr(task, "contract", None), "grader_dir", None)
    if grader:
        out += (Path(os.path.abspath(ctx.config.resolve(grader))),)
    return out


def _names_a_stream(p: Path) -> bool:
    from ..platform import current

    return current.has_data_streams() and any(":" in part for part in p.parts[1:])


def within_scope(args: dict, artifact_scope: list[str], root: str = ".") -> tuple[bool, str]:
    if not artifact_scope:
        for key, value in args.items():
            if _looks_like_path(key, value):
                return False, (f"{key}={value!r}: this task declared no "
                               "artifact_scope, so no path is permitted - "
                               "declare one (bucephalus new --scope work)")
        return True, ""
    roots = [Path(os.path.abspath(os.path.join(root, s))) for s in artifact_scope]
    real_roots = [_resolved(r) for r in roots]
    for key, value in args.items():
        if not _looks_like_path(key, value):
            continue
        p = Path(os.path.abspath(os.path.join(root, str(value))))
        if _names_a_stream(p):
            return False, f"{key}={value!r} names an alternate data stream"
        real = _resolved(p)
        if (not any(_is_within(p, r) for r in roots)
                or not any(_is_within(real, r) for r in real_roots)):
            return False, f"{key}={value!r} resolves outside artifact_scope"
    return True, ""


_PATH_KEYS = frozenset({"path", "file", "filename", "dir", "directory", "dest",
                        "src", "target"})


def _looks_like_path(key: str, value) -> bool:
    if not isinstance(value, str):
        return False
    return key in _PATH_KEYS


def _effective_args(tool, args: dict) -> dict:
    eff = dict(args)
    fn = getattr(tool, "fn", None)
    if fn is None:
        return eff
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return eff
    for name, prm in params.items():
        if name in _PATH_KEYS and name not in eff and isinstance(prm.default, str):
            eff[name] = prm.default
    return eff


_GLOB_ARGS = {"glob": "pattern", "grep": "glob"}


def _escapes(pattern: str) -> bool:
    return (".." in re.split(r"[\\/]", pattern) or os.path.isabs(pattern)
            or pattern.startswith(("/", "\\")))


def _is_within(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return p == root


def is_protected(path_value: str, root: str = ".",
                 state: tuple[Path, ...] = ()) -> bool:
    p = Path(os.path.abspath(os.path.join(root, path_value)))
    if _names_a_stream(p) or _protected_at(p, state):
        return True
    real = _resolved(p)
    if real != p and _protected_at(real, _real_state(tuple(state), _bucket())):
        return True
    return _protected_elsewhere(str(p), tuple(state), _bucket())


@functools.lru_cache(maxsize=8192)
def _protected_elsewhere(p: str, state: tuple, _b: int) -> bool:
    from ..platform import current

    try:
        st = os.stat(p)
    except (OSError, ValueError):
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_nlink < 2:
        return False
    names = current.link_names(p)
    if names is None:
        return True
    real_state = _real_state(state, _b)
    return any(_protected_at(Path(n), state) or _protected_at(Path(n), real_state)
               for n in names)


def _bucket() -> int:
    return int(time.monotonic() // 2)


def _resolved(p) -> Path:
    return _realpath(str(p), _bucket())


# ponytail: cached per 2 s bucket - a link created or swapped is seen within
@functools.lru_cache(maxsize=8192)
def _realpath(p: str, _b: int) -> Path:
    return Path(os.path.realpath(p))


@functools.lru_cache(maxsize=16)
def _real_state(state: tuple, _b: int) -> tuple:
    return tuple(_resolved(x) for x in state)


def _protected_at(p: Path, state: tuple[Path, ...]) -> bool:
    parts = {x.lower() for x in p.parts}
    if any(d in parts for d in PROTECTED_DIRS):
        return True
    if p.name.lower() in PROTECTED_FILES:
        return True
    return any(_is_within(p, s) or _is_within(s, p) for s in state)


def gate_tool_call(ctx, task, tool, args: dict, phase: str = "execute") -> GateDecision:
    tracker = ctx.taint_tracker(task.id)
    arg_taint = tracker.args_taint(args)

    if phase == "plan" and tool.side_effecting:
        return GateDecision.deny(
            f"{tool.name} is a write tool and the plan phase is read-only"
        )

    if arg_taint.level > tool.max_arg_taint.level:
        return GateDecision.deny(
            f"{tool.name} requires <={tool.max_arg_taint.value} arguments, "
            f"args are {arg_taint.value}"
        )

    eff = _effective_args(tool, args)
    state = None
    for key, value in eff.items():
        if not _looks_like_path(key, value):
            continue
        if state is None:
            state = task_state_paths(ctx, task)
        if is_protected(str(value), ctx.config.root, state):
            return GateDecision.deny(
                f"{key}={value!r} is a protected path: it is, contains, or lies "
                "inside the agent's own state (databases, anchor, skills, "
                "memory, graders, run records, config, secrets), which no task "
                "may read or write"
            )

    glob_key = _GLOB_ARGS.get(getattr(tool, "name", ""))
    pattern = eff.get(glob_key) if glob_key else None
    if isinstance(pattern, str) and _escapes(pattern):
        return GateDecision.deny(
            f"{glob_key}={pattern!r} leaves its directory: a file pattern may "
            "not contain '..' or be absolute, because pathlib follows it")

    if getattr(tool, "name", "") in ("run_code", "run_command"):
        state = state or task_state_paths(ctx, task)
        for s in task.contract.artifact_scope:
            if is_protected(s, ctx.config.root, state):
                return GateDecision.deny(
                    f"run_code with artifact_scope {s!r}: that scope contains "
                    "the agent's own state, and the sandbox would link it in")

    ok, why = within_scope(eff, task.contract.artifact_scope, ctx.config.root)
    if not ok:
        return GateDecision.deny(why)

    if tool.required_capability == "trusted":
        return GateDecision.needs_approval(
            f"{tool.name} is a trusted-tier action and always parks for a human",
            preview=render_preview(tool, args),
        )

    if tool.side_effecting and arg_taint != Taint.TRUSTED:
        if (getattr(ctx.config.autonomy, "approve_scoped_writes", False)
                and not is_unattended(getattr(task, "origin", None))
                and arg_taint.level <= Taint.USER.level
                and task.contract.artifact_scope):
            return GateDecision.allow_(
                "code gate sufficient: in scope, taint <= user, "
                "approve_scoped_writes is on"
            )
        return GateDecision.needs_approval(
            f"{tool.name} with {arg_taint.value} arguments",
            preview=render_preview(tool, args),
        )

    return GateDecision.allow_()


def render_preview(tool, args: dict, limit: int = 300) -> str:
    rendered = ", ".join(f"{k}={_short(v, 80)}" for k, v in sorted(args.items()))
    return f"{tool.name}({rendered})"[:limit]


def _short(v, n: int) -> str:
    s = repr(v)
    return s if len(s) <= n else s[: n - 3] + "..."
