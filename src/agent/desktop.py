from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

HOST_ENV = "ALPHA_HOST"


def resolve_host() -> str:
    from .control.guard import ALLOW_REMOTE_ENV

    if os.environ.get(ALLOW_REMOTE_ENV, "") == "1":
        return os.environ.get(HOST_ENV, "127.0.0.1")
    return "127.0.0.1"


def resolve_state() -> tuple[Path, Path]:
    from .paths import user_config_dir, user_data_dir

    data = Path(os.environ.get("ALPHA_DATA_DIR") or user_data_dir())
    os.environ["ALPHA_DATA_DIR"] = str(data)
    cfg = user_config_dir() / "config.yaml"
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(_default_config(), encoding="utf-8")
    return cfg, data


def _default_config() -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    for name in ("config.default.yaml", "config.local.yaml"):
        if (base / name).exists():
            return (base / name).read_text(encoding="utf-8")
    raise FileNotFoundError("no default config bundled (config.default.yaml)")


def _streams_to_log(data: Path) -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    data.mkdir(parents=True, exist_ok=True)
    log = open(data / "desktop.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def start_server(ctx, host: str):
    import socket

    import uvicorn

    from .control.server import GRACEFUL_SHUTDOWN_S, create_app

    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), log_level="warning",
                                           timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]},
                              daemon=True, name="alpha-control-plane")
    thread.start()
    while not server.started and thread.is_alive():
        threading.Event().wait(0.05)
    return server, thread, port


def descendants(root: int | None = None) -> list[int]:
    from . import proc

    return proc.descendants(os.getpid() if root is None else root)


_STOPPING = threading.Lock()


def shutdown(server, thread, timeout_s: float = 10.0) -> list[int]:
    with _STOPPING:
        return _shutdown(server, thread, timeout_s)


def _shutdown(server, thread, timeout_s: float) -> list[int]:
    from . import proc

    server.should_exit = True
    thread.join(timeout_s)
    left = descendants()
    started = {pid: proc.process_start_time(pid) for pid in left}
    for pid in left:
        if not proc._alive(pid):
            continue
        proc.kill_tree(pid, expect_start=started[pid])
    return left


def main(argv: list[str] | None = None) -> int:
    from .frozen import child_entry

    child_entry()

    import argparse

    p = argparse.ArgumentParser(prog="alpha-desktop")
    p.add_argument("--config", help="a config file; default: the user config")
    args = p.parse_args(argv)

    cfg, data = resolve_state()
    _streams_to_log(data)
    from .context import AgentContext

    ctx = AgentContext.open(args.config or cfg)
    server, thread, port = start_server(ctx, resolve_host())
    try:
        import webview

        from .control.server import console_url

        window = webview.create_window("alpha", console_url(port, server.config.app.state.token),
                                       width=1280, height=860, min_size=(900, 600))
        def stop_now() -> None:
            shutdown(server, thread)

        window.events.closed += stop_now
        webview.start()
    finally:
        shutdown(server, thread)
        _drop_private_profile()
        ctx.close()
    return 0


def _drop_private_profile() -> None:
    import shutil

    webview = sys.modules.get("webview")
    winforms = sys.modules.get("webview.platforms.winforms")
    path = getattr(winforms, "cache_dir", None)
    if path and getattr(webview, "_state", {}).get("private_mode"):
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
