from __future__ import annotations

import os

from ..context import AgentContext
from .server import create_app

CONFIG_PATH = os.environ.get("ALPHA_CONFIG", "config.yaml")

ctx = AgentContext.open(CONFIG_PATH)
app = create_app(ctx)
