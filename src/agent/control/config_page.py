from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import yaml

PHRASE = "apply config"
TENFOLD = 10
LIMITS = {"max_steps": int, "max_refinement_iterations": int, "max_parallel_tools": int,
          "default_task_budget_seconds": int, "default_task_budget_usd": float,
          "delegation_min_isolated_tokens": int, "context_fill_ratio": float}
NO_TENFOLD = {"context_fill_ratio", "delegation_min_isolated_tokens"}
LONG_FILES = ["pages", "elide", "whole"]
HIDDEN = {"profile_provenance", "root", "state_dir", "source_path"}


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _path(ctx) -> Path:
    if not ctx.config.source_path:
        raise Refused(409, "this console was not started from a config file, so there is nothing to edit")
    return Path(ctx.config.source_path)


def _load(ctx) -> tuple[dict, str]:
    p = _path(ctx)
    data = p.read_bytes()
    return yaml.safe_load(data.decode("utf-8")) or {}, hashlib.sha256(data).hexdigest()


def _get(raw: dict, path: str, default=None):
    cur = raw
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _set(raw: dict, path: str, value) -> None:
    *head, last = path.split(".")
    cur = raw
    for k in head:
        cur = cur.setdefault(k, {})
    cur[last] = value


def _defaults(ctx) -> dict:
    return {**{f"limits.{k}": getattr(ctx.config.limits, k) for k in LIMITS}, "long_files": "pages"}


def editable(ctx, raw: dict) -> dict[str, dict]:
    d = _defaults(ctx)
    out = {f"limits.{k}": {"kind": "int" if t is int else "float"} for k, t in LIMITS.items()}
    out["long_files"] = {"kind": "choice", "choices": LONG_FILES}
    for name in (raw.get("backends") or {}):
        out[f"backends.{name}.model"] = {"kind": "text"}
    for p, spec in out.items():
        spec["value"] = _get(raw, p, d.get(p))
    return out


def _check(path: str, spec: dict, value):
    kind = spec["kind"]
    if kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
    elif kind == "float":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        if ok and path == "limits.context_fill_ratio":
            ok = value <= 1
    elif kind == "choice":
        ok = value in spec["choices"]
    else:
        ok = isinstance(value, str) and value.strip() == value and 0 < len(value) <= 200 and "\n" not in value
    if not ok:
        raise Refused(400, f"{path}: that value is not valid")
    return float(value) if kind == "float" else value


def _flat(prefix: str, v, out: list) -> None:
    if isinstance(v, dict):
        for k, x in v.items():
            if not prefix and k in HIDDEN:
                continue
            _flat(f"{prefix}.{k}" if prefix else str(k), x, out)
    else:
        out.append({"path": prefix, "value": ", ".join(map(str, v)) if isinstance(v, list) else v})


def view(ctx) -> dict:
    prov = ctx.config.profile_provenance or {}
    base = {"path": ctx.config.source_path, "editable": [], "readonly": [], "backups": [], "phrase": PHRASE,
            "profile": {"name": ctx.config.profile, "cloud_backends": prov.get("cloud_backends", []),
                        "credentials_present": prov.get("credentials_present", [])}}
    flat: list = []
    _flat("", json.loads(ctx.config.model_dump_json()), flat)
    if not ctx.config.source_path:
        return {**base, "readonly": flat}
    raw, _ = _load(ctx)
    ed = editable(ctx, raw)
    base["editable"] = [{"path": p, **s} for p, s in ed.items()]
    base["readonly"] = [r for r in flat if r["path"] not in ed]
    base["backups"] = sorted(({"name": b.name, "created": b.stat().st_mtime}
                              for b in _path(ctx).parent.glob("config.bak-*.yaml")), key=lambda b: -b["created"])
    return base


def _loosens(ctx, raw: dict, new: dict, changes: dict) -> list[dict]:
    out = []
    for p, v in changes.items():
        k = p.split(".", 1)[-1]
        old = _get(raw, p, _defaults(ctx).get(p))
        if p.startswith("limits.") and k in LIMITS and k not in NO_TENFOLD and old and v > old * TENFOLD:
            out.append({"path": p, "why": f"raised more than {TENFOLD}x ({old} to {v})"})
    rev = _get(new, "autonomy.auto_review_backend", "review")
    same = lambda r: _get(r, f"backends.{rev}.model") == _get(r, "backends.cloud.model")  # noqa: E731
    if same(new) and not same(raw):
        out += [{"path": p, "why": "the reviewer and the executor would be the same model"}
                for p in changes if p in (f"backends.{rev}.model", "backends.cloud.model")]
    return out


def propose(ctx, store: dict, changes: dict) -> dict:
    raw, sha = _load(ctx)
    ed = editable(ctx, raw)
    clean = {}
    for p, v in changes.items():
        if p not in ed:
            raise Refused(400, f"{p} is not editable here; edit the config file for it")
        clean[p] = _check(p, ed[p], v)
    new = copy.deepcopy(raw)
    diff = []
    for p, v in clean.items():
        old = ed[p]["value"]
        if old != v:
            diff.append({"path": p, "old": old, "new": v})
            _set(new, p, v)
    if not diff:
        raise Refused(400, "nothing would change")
    error = None
    try:
        from ..config import config_from_raw

        probe = copy.deepcopy(new)
        probe["root"], probe["source_path"] = ctx.config.root, ctx.config.source_path
        probe["state_dir"] = ctx.config.state_dir
        config_from_raw(probe)
    except Exception as e:  # noqa: BLE001
        error = str(e)[:600]
    loosens = _loosens(ctx, raw, new, {d["path"]: d["new"] for d in diff})
    h = hashlib.sha256(json.dumps({"file": sha, "changes": clean}, sort_keys=True).encode()).hexdigest()
    if error is None:
        store[h] = {"sha": sha, "new": new, "loosens": [f["path"] for f in loosens]}
    return {"hash": h if error is None else None, "diff": diff, "loosens": loosens, "error": error,
            "phrase": PHRASE}


def apply(ctx, store: dict, h: str, phrase: str, acks: list[str]) -> dict:
    prop = store.get(h)
    if prop is None:
        raise Refused(404, "no such proposal; review the change again")
    if phrase != PHRASE:
        raise Refused(400, f"type {PHRASE!r} to apply")
    missing = [p for p in prop["loosens"] if p not in acks]
    if missing:
        raise Refused(400, "tick each loosening to accept it: " + ", ".join(missing))
    p = _path(ctx)
    _, sha = _load(ctx)
    if sha != prop["sha"]:
        store.pop(h, None)
        raise Refused(409, "the config file changed since you reviewed this; review the change again")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = p.with_name(f"config.bak-{stamp}.yaml")
    n = 1
    while backup.exists():
        backup = p.with_name(f"config.bak-{stamp}-{n}.yaml")
        n += 1
    shutil.copy2(p, backup)
    fd, tmp = tempfile.mkstemp(prefix="config-", suffix=".part", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(prop["new"], fh, sort_keys=False, allow_unicode=True)
        os.replace(tmp, p)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    store.pop(h, None)
    return {"applied": True, "backup": backup.name, "takes_effect": "next start",
            "note": "Comments in the file are not kept; the backup has them."}
