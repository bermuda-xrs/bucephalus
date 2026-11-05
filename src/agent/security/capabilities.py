from __future__ import annotations

import functools
import importlib.machinery
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
    "checkouts", "shadow", "tasklocks", "AGENTS.md", ".git",
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


@functools.lru_cache(maxsize=32)
def _repo_dirs(root: str) -> tuple[Path, ...]:
    from ..gitmode import GitModeError, git_dirs, toplevel

    try:
        top = toplevel(root)
        return tuple(git_dirs(top)) if top is not None else ()
    except (GitModeError, OSError):
        return ()


def state_paths(ctx) -> tuple[Path, ...]:
    cfg = ctx.config
    root = Path(os.path.abspath(cfg.root))
    db = str(cfg.resolve_state(cfg.db_path))
    out = [Path(db + s) for s in DB_FILES]
    out += [cfg.resolve_state(cfg.anchor_path), cfg.resolve(cfg.skills_path)]
    if cfg.state_dir:
        out.append(Path(cfg.state_dir))
    out += [root / rel for rel in PROJECT_STATE]
    out += list((root / ".bucephalus-worktrees").glob("*/.git"))
    if getattr(cfg, "git_mode", False):
        out += _repo_dirs(str(root))
    if cfg.source_path:
        out.append(Path(cfg.source_path))
    out += _argv_files(cfg, root)
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


def _argv_files(cfg, root: Path) -> list[Path]:
    hooks = cfg.hooks.pre_tool + cfg.hooks.post_tool + cfg.hooks.stop
    out = []
    for argv in [h.argv for h in hooks] + [s.argv for s in cfg.mcp_servers.values()]:
        for mod in _modules(argv):
            top = root / mod.split(".")[0]
            out += [top] + [top.parent / (top.name + s) for s in importlib.machinery.all_suffixes()]
        for a in argv:
            p = Path(os.path.abspath(a if Path(a).is_absolute() else root / a))
            if p.is_file():
                out.append(p)
                if (p.parent != root and p.parent not in root.parents
                        and not _holds_the_stdlib(p.parent)):
                    out.append(p.parent)
    return out


def _holds_the_stdlib(d: Path) -> bool:
    # A hook's argv[0] is often the interpreter itself. On Windows python.exe
    # sits directly above Lib\, so protecting "the directory beside it" fenced
    # off the standard library: sandboxed code could not import json.
    import sysconfig

    lib = Path(os.path.abspath(sysconfig.get_paths()["stdlib"]))
    return d == lib or d in lib.parents


def _modules(argv: list[str]) -> list[str]:
    out, skip = [], False
    for i, a in enumerate(argv[1:], 1):
        if skip:
            skip = False
            continue
        if not a.startswith("-") or a.startswith("--"):
            continue
        for j, ch in enumerate(a[1:], 2):
            if ch in "cWXm":
                rest = a[j:]
                if ch == "m" and (rest or i + 1 < len(argv)):
                    out.append(rest or argv[i + 1])
                skip = not rest
                break
    return out


def task_state_paths(ctx, task) -> tuple[Path, ...]:
    out = state_paths(ctx)
    grader = getattr(getattr(task, "contract", None), "grader_dir", None)
    if grader:
        out += (Path(os.path.abspath(ctx.config.resolve(grader))),)
    return out


def _names_a_stream(p: Path) -> bool:
    from ..platform import current

    return current.has_data_streams() and any(":" in part for part in p.parts[1:])


def within_scope(args: dict, artifact_scope: list[str], root: str = ".",
                 extra_keys: frozenset = frozenset(), by_value: bool = False) -> tuple[bool, str]:
    if not artifact_scope:
        for key, value in args.items():
            for v in _path_strings(key, value, extra_keys, by_value, root):
                if v is REFUSED_URI:
                    return False, f"{key}: a file: URI with a query, fragment, params or encoded separator is refused"
                return False, (f"{key}={v!r}: this task declared no "
                               "artifact_scope, so no path is permitted - "
                               "declare one (bucephalus new --scope work)")
        return True, ""
    roots = [Path(os.path.abspath(os.path.join(root, s))) for s in artifact_scope]
    real_roots = [_resolved(r) for r in roots]
    for key, value in args.items():
        for v in _path_strings(key, value, extra_keys, by_value, root):
            if v is REFUSED_URI:
                return False, f"{key}: a file: URI with a query, fragment, params or encoded separator is refused"
            p = Path(os.path.abspath(os.path.join(root, v)))
            if _names_a_stream(p):
                return False, f"{key}={v!r} names an alternate data stream"
            real = _resolved(p)
            if (not any(_is_within(p, r) for r in roots)
                    or not any(_is_within(real, r) for r in real_roots)):
                return False, f"{key}={v!r} resolves outside artifact_scope"
    return True, ""


_PATH_KEYS = frozenset({"path", "file", "filename", "dir", "directory", "dest",
                        "src", "target"})


_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _pathish_value(v: str) -> bool:
    return (os.path.isabs(v) or v.startswith(("/", chr(92), "~")) or bool(_DRIVE.match(v))
            or ".." in re.split(r"[\\/]", v))


REFUSED_URI = object()


def _file_uri_path(v: str):
    v = re.sub("[\t\r\n]", "", v).strip("".join(map(chr, range(33))))
    if not v.lower().startswith("file:"):
        return None
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    u = urlparse(v)
    if u.query or u.fragment or u.params or re.search(r"%(2[ef5]|5c)", v, re.I):
        return REFUSED_URI
    return url2pathname((("//" + u.netloc) if u.netloc else "") + u.path)


_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")


def _file_like(v: str) -> bool:
    return bool(v) and len(v) <= 4096 and not any(c.isspace() for c in v) and not _URL.match(v)


def _names_something(v: str, root) -> bool:
    if root is None or not v or chr(0) in v or "\n" in v or "\r" in v or len(v) > 4096:
        return False
    try:
        q = Path(os.path.normpath(os.path.join(root, v)))
        return q.exists() or (any(sep in v for sep in "/" + chr(92)) and q.parent.is_dir() and q.parent != Path(root))
    except (OSError, ValueError):
        return False


def _path_strings(key: str, value, extra: frozenset = frozenset(), by_value=False, root=None):
    keyed = key in _PATH_KEYS or key in extra or by_value == "all"

    def walk(v):
        if isinstance(v, str):
            uri = _file_uri_path(v) if by_value else None
            if uri is not None:
                yield uri
            elif keyed or (by_value and (_pathish_value(v) or _names_something(v, root)
                                         or (by_value == "write" and _file_like(v)))):
                yield v
        elif isinstance(v, (list, tuple)):
            for x in v:
                yield from walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                yield from walk(x)

    yield from walk(value)


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
    low = type(p)(str(p).casefold())
    for s in state:
        fs = type(s)(str(s).casefold())
        if _is_within(p, s) or _is_within(s, p) or _is_within(low, fs) or _is_within(fs, low):
            return True
    return False


_ANCHOR = re.compile(r"[0-9]{1,6}:[0-9a-f]{2}")


def _under_root(v, root):
    # a '..' or '.' collapsed lexically can differ from the real path (a link), so only a canonical path is mapped
    if not (isinstance(v, str) and os.path.isabs(v)) or {".", ".."} & set(re.split(r"[\\/]", v)):
        return v
    try:
        return Path(os.path.abspath(v)).relative_to(os.path.abspath(root)).as_posix()
    except ValueError:
        return v


def gate_tool_call(ctx, task, tool, args: dict, phase: str = "execute") -> GateDecision:
    tracker = ctx.taint_tracker(task.id)
    # a line anchor (12:a3) only names a line the tool then checks against the file; it is copied from the read output
    named = {k: v for k, v in args.items() if not (tool.name == "edit_lines" and k in ("start", "end") and _ANCHOR.fullmatch(str(v)))}
    # read_file echoes the absolute path; an absolute path inside the root is the same file as its relative form
    named = {k: _under_root(v, ctx.config.root) if k in _PATH_KEYS else v for k, v in named.items()}
    arg_taint = tracker.args_taint(named)

    if phase == "plan" and tool.side_effecting:
        return GateDecision.deny(
            f"{tool.name} is a write tool and the plan phase is read-only"
        )

    if arg_taint.level > tool.max_arg_taint.level:
        d = GateDecision.deny(
            f"{tool.name} requires <={tool.max_arg_taint.value} arguments, "
            f"args are {arg_taint.value}"
        )
        d.detail = {"untrusted_args": sorted(k for k, v in named.items()
                                             if tracker.args_taint({k: v}).level > tool.max_arg_taint.level)}
        return d

    eff = _effective_args(tool, args)
    extra = frozenset(getattr(tool, "path_args", ()))
    by_value = getattr(tool, "path_values", False)
    state = None
    for key, value in eff.items():
        for v in _path_strings(key, value, extra, by_value, ctx.config.root):
            if v is REFUSED_URI:
                return GateDecision.deny(f"{key}: a file: URI with a query, fragment, params or encoded separator is refused")
            if state is None:
                state = task_state_paths(ctx, task)
            if is_protected(v, ctx.config.root, state):
                return GateDecision.deny(
                    f"{key}={v!r} is a protected path: it is, contains, or lies "
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

    ok, why = within_scope(eff, task.contract.artifact_scope, ctx.config.root, extra, by_value)
    if not ok:
        return GateDecision.deny(why)

    if tool.required_capability == "trusted":
        return GateDecision.needs_approval(
            f"{tool.name} is a trusted-tier action and always parks for a human",
            preview=render_preview(tool, args),
        )

    if getattr(tool, "always_ask", ""):
        return GateDecision.needs_approval(tool.always_ask, preview=render_preview(tool, args))

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
