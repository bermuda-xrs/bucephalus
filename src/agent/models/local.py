from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .. import proc
from ..platform import current

USER_PORT = 11434
KEEP_ALIVE = "30m"
CONTEXT_LENGTH = "16384"
_owned: dict[str, subprocess.Popen] = {}


class Refused(Exception):
    pass


def server_of(cfg) -> str | None:
    if cfg.model_server != "ollama":
        return None
    url = (cfg.endpoint or cfg.base_url or f"http://localhost:{USER_PORT}").rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def endpoints(backend_cfgs) -> list[str]:
    return sorted({s for s in map(server_of, backend_cfgs) if s})


def guard(endpoint: str, backend_cfgs) -> str:
    endpoint = endpoint.rstrip("/")
    if (urlparse(endpoint).port or USER_PORT) == USER_PORT:
        raise Refused(f"the Ollama on {USER_PORT} is the user's own; it is never started or loaded from here")
    if endpoint not in endpoints(backend_cfgs):
        raise Refused(f"{endpoint} is not a model server in this config")
    return endpoint


def is_up(endpoint: str) -> bool:
    try:
        return httpx.get(f"{endpoint}/api/tags", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def status(endpoint: str) -> dict:
    out = {"endpoint": endpoint, "up": False, "installed": [], "loaded": [],
           "owned": endpoint in _owned, "managed": (urlparse(endpoint).port or USER_PORT) != USER_PORT}
    if not out["managed"]:
        return out
    try:
        tags = httpx.get(f"{endpoint}/api/tags", timeout=2.0).json().get("models", [])
        ps = httpx.get(f"{endpoint}/api/ps", timeout=2.0).json().get("models", [])
    except (httpx.HTTPError, ValueError):
        return out
    out.update(up=True,
               installed=[{"name": m["name"], "bytes": m.get("size", 0)} for m in tags],
               loaded=[{"name": m["name"], "bytes": m.get("size", 0), "expires_at": m.get("expires_at"),
                        "context_length": m.get("context_length")} for m in ps])
    return out


def window_warning(loaded: list[dict], model: str, configured: int) -> dict | None:
    served = next((m.get("context_length") for m in loaded if m["name"] == model), None)
    if not served or served >= configured:
        return None
    return {"window": {"server": served, "configured": configured},
            "warning": (f"The server holds {served} tokens of context for {model}; the config budgets {configured}. "
                        f"Past {served} it drops the start of the prompt without an error. "
                        "Start the server with OLLAMA_CONTEXT_LENGTH at least that large (ollama-serve.ps1 sets it).")}


def installed(endpoint: str) -> list[str]:
    return [m["name"] for m in status(endpoint)["installed"]]


def start_server(endpoint: str, root: Path, wait_s: float = 30.0) -> None:
    if is_up(endpoint):
        return
    u = urlparse(endpoint)
    if u.hostname not in ("127.0.0.1", "localhost"):
        raise Refused(f"{endpoint} is not on this machine; start it there")
    exe = shutil.which("ollama")
    if not exe:
        raise Refused("ollama is not installed (https://ollama.com)")
    env = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{u.port}",
           "OLLAMA_KEEP_ALIVE": KEEP_ALIVE, "OLLAMA_CONTEXT_LENGTH": CONTEXT_LENGTH}
    models = os.environ.get("ALPHA_OLLAMA_MODELS") or Path(root).parent / "ollama-models"
    if Path(models).is_dir():
        env["OLLAMA_MODELS"] = str(models)
    _owned[endpoint] = subprocess.Popen(  # noqa: S603
        [exe, "serve"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, **current.background_popen_flags())
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_up(endpoint):
            return
        if _owned[endpoint].poll() is not None:
            break
        time.sleep(0.3)
    raise Refused(f"the model server on {endpoint} did not start")


def run(endpoint: str, model: str, root: Path) -> dict:
    start_server(endpoint, root)
    if model not in installed(endpoint):
        raise ValueError(f"{model} is not installed on {endpoint}; ollama pull it first")
    t0 = time.monotonic()
    httpx.post(f"{endpoint}/api/generate", json={"model": model, "prompt": "", "keep_alive": KEEP_ALIVE},
               timeout=600.0).raise_for_status()
    return {"model": model, "endpoint": endpoint, "loaded": True, "seconds": round(time.monotonic() - t0, 2),
            "context_length": next((m.get("context_length") for m in status(endpoint)["loaded"]
                                    if m["name"] == model), None)}


def unload(endpoint: str, model: str) -> dict:
    httpx.post(f"{endpoint}/api/generate", json={"model": model, "keep_alive": 0},
               timeout=60.0).raise_for_status()
    return {"model": model, "loaded": False}


def stop_owned() -> None:
    while _owned:
        _, p = _owned.popitem()
        if p.poll() is None:
            proc.kill_tree(p.pid)


atexit.register(stop_owned)
