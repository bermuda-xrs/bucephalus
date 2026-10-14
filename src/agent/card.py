from __future__ import annotations

import os
import platform
import sys
from urllib.parse import urlparse


def edit_format(names) -> str:
    names = set(names)
    for tool, fmt in (("edit_lines", "hashline"), ("apply_patch", "apply_patch"), ("edit_file", "edit_file")):
        if tool in names:
            return fmt
    return "whole_file"


def _model_server(cfg) -> tuple[str | None, str | None]:
    from .models.local import USER_PORT, server_of

    endpoint = server_of(cfg)
    if not endpoint or (urlparse(endpoint).port or USER_PORT) == USER_PORT:
        return None, None
    import httpx

    try:
        show = httpx.post(f"{endpoint}/api/show", json={"model": cfg.model}, timeout=3.0).json()
        version = httpx.get(f"{endpoint}/api/version", timeout=3.0).json().get("version")
    except (httpx.HTTPError, ValueError):
        return None, None
    return (show.get("details") or {}).get("quantization_level"), version


def harness_card(ctx, slot: str = "cloud") -> dict:
    from . import HARNESS_VERSION, TOOLSET_VERSION, proc
    from .provenance import code_identity

    b = ctx.backends.get(slot)
    cfg = b.cfg if b else None
    quant, server = _model_server(cfg) if cfg else (None, None)
    c = ctx.config
    mem = proc.memory_pressure()
    free = proc.free_memory_gb()
    return {
        "model": cfg.model if cfg else None, "quant": quant, "ctx": cfg.budgeted_window if cfg else None,
        "profile": cfg.tool_profile if cfg else None, "backend": cfg.model_dump() if cfg else {},
        "mechanisms": c.mechanisms.model_dump(),
        "settings": {"long_files": c.long_files, "limits": c.limits.model_dump(), "profile": c.profile,
                     "compress_observations_over_tokens": c.compress_observations_over_tokens,
                     "isolate_retrieval": c.isolate_retrieval, "grader_held_out": c.grader_held_out,
                     "approve_scoped_writes": c.autonomy.approve_scoped_writes, "auto_review": c.autonomy.auto_review},
        "tools": sorted(ctx.tools.names()), "edit_format": edit_format(ctx.tools.names()),
        "versions": {"harness": HARNESS_VERSION, "toolset": TOOLSET_VERSION, "code": code_identity(),
                     "python": sys.version.split()[0], "model_server": server},
        "hardware": {"platform": platform.platform(), "machine": platform.machine(), "cpus": os.cpu_count(),
                     "physical_gb": mem.get("physical_gb")},
        "free_memory_gb": round(free, 2) if free is not None else None,
    }
