"""The desktop app: the existing control-plane console in a native window.

pywebview, not Tauri. The console already exists - FastAPI plus `ui.html`,
guarded by token, Origin and Host checks - and a window only has to show it.
Tauri would add a Rust toolchain and run this Python as a separate frozen
SIDECAR, which is the very arrangement D13 was about; pywebview shows the same
page from inside the one process that owns the sandbox.

Four rules, each tested in tests/test_desktop.py:

  * LOOPBACK ONLY. The server binds 127.0.0.1 on a port the OS picks. Another
    interface needs ALPHA_ALLOW_REMOTE=1 and ALPHA_HOST, and the guard's Host,
    Origin and token checks still apply.
  * STATE IN PLATFORMDIRS. Always - even when launched from inside a
    checkout, where `state_root()` would otherwise pick the project
    directory. ALPHA_DATA_DIR still overrides.
  * CLOSING THE WINDOW STOPS EVERYTHING. The server stops, then every process
    this one started - sandbox children, their grandchildren - goes through
    `kill_tree`, never a bare terminate (HANDOVER: the venv trampoline).
  * NO TASK CONTENT. A first run writes the local profile's config and
    nothing else; suites are made in the console.

Module-level imports are stdlib only: in a frozen build `main()` must reach
`child_entry()` before any application code (D13).
"""

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
    """(config file, data dir). Creates the config on first run."""
    from .paths import user_config_dir, user_data_dir

    data = Path(os.environ.get("ALPHA_DATA_DIR") or user_data_dir())
    os.environ["ALPHA_DATA_DIR"] = str(data)       # state_root() reads this
    cfg = user_config_dir() / "config.yaml"
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(_default_config(), encoding="utf-8")
    return cfg, data


def _default_config() -> str:
    """The all-local profile. Frozen, bundled beside the app; in a checkout,
    the file itself."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    for name in ("config.default.yaml", "config.local.yaml"):
        if (base / name).exists():
            return (base / name).read_text(encoding="utf-8")
    raise FileNotFoundError("no default config bundled (config.default.yaml)")


def _streams_to_log(data: Path) -> None:
    """A windowed build has no console, so sys.stdout and sys.stderr are None
    - and uvicorn's logging writes to them, which killed the first frozen
    build with "Unhandled exception in script" before the window opened.
    They go to a log in the data directory instead, which is also where a
    person looking for what went wrong would look."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    data.mkdir(parents=True, exist_ok=True)
    log = open(data / "desktop.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def start_server(ctx, host: str):
    """Serve the console on a free port. Returns (server, thread, port)."""
    import socket

    import uvicorn

    from .control.server import create_app

    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(ctx), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]},
                              daemon=True, name="alpha-control-plane")
    thread.start()
    while not server.started and thread.is_alive():
        threading.Event().wait(0.05)
    return server, thread, port


def descendants(root: int | None = None) -> list[int]:
    """This process's tree, start-time validated (proc.descendants, D42)."""
    from . import proc

    return proc.descendants(os.getpid() if root is None else root)


def shutdown(server, thread, timeout_s: float = 10.0) -> list[int]:
    """Stop the server, then kill every process this one started. Returns the
    pids that were still running and had to be killed."""
    from . import proc

    server.should_exit = True
    thread.join(timeout_s)
    left = descendants()
    started = {pid: proc.process_start_time(pid) for pid in left}
    for pid in left:
        # Already gone with a parent's tree (leaves-first kills take the whole
        # validated subtree); asking the kernel is microseconds.
        if not proc._alive(pid):
            continue
        # Pinned to the process in the snapshot: by the time this runs the
        # pid may be someone else's (D42).
        proc.kill_tree(pid, expect_start=started[pid])
    return left


def main(argv: list[str] | None = None) -> int:
    from .frozen import child_entry

    child_entry()                         # D13: first, before any app code

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

        webview.create_window("alpha", console_url(port, server.config.app.state.token),
                              width=1280, height=860, min_size=(900, 600))
        webview.start()
    finally:
        shutdown(server, thread)
        ctx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
