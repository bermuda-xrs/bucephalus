"""B5 — live output, in process, for any surface that wants it.

The adapters have streamed since the stall-detector rewrite: chunks arrive,
get judged for rate, and are joined into a string that the caller sees only
when the call finishes. So the SYSTEM streams and every SURFACE waits. On a
CPU tier where a turn is 20-40 seconds, that is the difference between
watching something work and staring at a spinner.

WHY A BUS RATHER THAN A CALLBACK ON THE CONTEXT. Two surfaces want the same
tokens — the TUI in-process and the control plane's SSE — and a single
callback slot means the second one to attach silently replaces the first.
That is the shadowing-default shape (DR2) waiting to happen, so subscription
is many-to-one from the start.

WHAT THIS IS NOT. It is not durable and it is not the record. The hash chain
is the record; this is a view of work in flight, dropped the moment nobody is
watching. Nothing here may be read back to make a claim about what happened —
`ctx.log` is for that, and it is what every test asserts against.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, defaultdict
from typing import Callable

# One token chunk, or one lifecycle event, for one task.
Listener = Callable[[str, dict], None]

_lock = threading.RLock()
_listeners: dict[str, list[Listener]] = defaultdict(list)

# A short tail per task, so a surface attaching mid-turn is not staring at
# nothing until the next chunk. Bounded hard: this is a view, and an unbounded
# buffer of model output is a memory leak with a nice name.
BACKLOG = 200

# And a bound on how many TASKS are retained, not only how much per task. A
# long-lived control-plane process runs many tasks, and a dict keyed by task
# id with no ceiling is a leak that only shows up after a long day. Insertion
# ordered, so dropping the oldest is one line.
BACKLOG_TASKS = 32
_backlog: "OrderedDict[str, list[tuple[str, dict]]]" = OrderedDict()


def subscribe(task_id: str, fn: Listener) -> Callable[[], None]:
    """Attach a listener. Returns the function that detaches it."""
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
    """Fan out to every listener. A listener that raises is DROPPED, not
    propagated: a broken display must never be able to fail a task."""
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
        except Exception:  # noqa: BLE001 - see the docstring
            pass


def backlog(task_id: str) -> list[tuple[str, dict]]:
    with _lock:
        return list(_backlog.get(task_id, ()))


def reset() -> None:
    """Test hook. The bus is process-global by design — one running task per
    process is this project's whole operating assumption."""
    with _lock:
        _listeners.clear()
        _backlog.clear()


def token_sink(task_id: str) -> Callable[[str], None]:
    """The callback an adapter is handed. One argument, so an adapter never
    has to know what a task is."""
    def emit(chunk: str) -> None:
        if chunk:
            publish(task_id, "token", {"text": chunk})
    return emit
