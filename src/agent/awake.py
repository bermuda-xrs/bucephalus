from __future__ import annotations

import contextlib

from .platform import current


@contextlib.contextmanager
def keep_awake():
    current.hold_awake(True)
    try:
        yield
    finally:
        current.hold_awake(False)
