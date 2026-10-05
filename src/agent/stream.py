from __future__ import annotations

import threading
from collections import OrderedDict, defaultdict
from typing import Callable

Listener = Callable[[str, dict], None]

_lock = threading.RLock()
_listeners: dict[str, list[Listener]] = defaultdict(list)

BACKLOG = 200

BACKLOG_TASKS = 32
_backlog: "OrderedDict[str, list[tuple[str, dict]]]" = OrderedDict()


def subscribe(task_id: str, fn: Listener) -> Callable[[], None]:
    with _lock:
        _listeners[task_id].append(fn)

    def unsubscribe() -> None:
        with _lock:
            try:
                _listeners[task_id].remove(fn)
            except ValueError:
                pass
            if not _listeners[task_id]:
                _listeners.pop(task_id, None)

    return unsubscribe


def publish(task_id: str, kind: str, data: dict) -> None:
    with _lock:
        buf = _backlog.setdefault(task_id, [])
        _backlog.move_to_end(task_id)
        buf.append((kind, data))
        del buf[:-BACKLOG]
        while len(_backlog) > BACKLOG_TASKS:
            _backlog.popitem(last=False)
        targets = list(_listeners.get(task_id, ()))
    for fn in targets:
        try:
            fn(kind, data)
        except Exception:  # noqa: BLE001
            pass


def backlog(task_id: str) -> list[tuple[str, dict]]:
    with _lock:
        return list(_backlog.get(task_id, ()))


def reset() -> None:
    with _lock:
        _listeners.clear()
        _backlog.clear()


def token_sink(task_id: str) -> Callable[[str], None]:
    def emit(chunk: str) -> None:
        if chunk:
            publish(task_id, "token", {"text": chunk})
    return emit
