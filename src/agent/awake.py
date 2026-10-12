from __future__ import annotations

import contextlib
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def _set(flags: int) -> int:
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.SetThreadExecutionState.restype = ctypes.c_uint
    return k32.SetThreadExecutionState(flags)


@contextlib.contextmanager
def keep_awake():
    if sys.platform != "win32":
        yield
        return
    _set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        _set(ES_CONTINUOUS)
