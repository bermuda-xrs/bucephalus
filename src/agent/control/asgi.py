"""Module-level ASGI app, for running the control plane under any server.

`create_app(ctx)` takes a context, which is right for tests and for `alpha
serve` but means uvicorn's CLI cannot construct it — `--factory` calls the
factory with no arguments. This module builds the context from config and
exposes a plain `app`, so the standard incantations work:

    uvicorn agent.control.asgi:app --host 127.0.0.1 --port 8901
    gunicorn -k uvicorn.workers.UvicornWorker agent.control.asgi:app

Set ALPHA_CONFIG to choose a profile (defaults to ./config.yaml):

    set ALPHA_CONFIG=config.local.yaml

One process, one context, one SQLite connection. Do not run multiple workers
against the same database file: the event log's hash chain assumes a single
writer, and two workers appending concurrently would interleave sequence
numbers and break verification.
"""

from __future__ import annotations

import os

from ..context import AgentContext
from .server import create_app

CONFIG_PATH = os.environ.get("ALPHA_CONFIG", "config.yaml")

ctx = AgentContext.open(CONFIG_PATH)
app = create_app(ctx)
