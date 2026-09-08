"""Task graph. State is projected from the event log, never written directly."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass

from .events import EventLog, canonical
from .types import Budget, TaskContract, TaskStatus


@dataclass
class Task:
    id: str
    parent_id: str | None
    contract: TaskContract
    status: TaskStatus
    budget: Budget
    # A4. 'local', or the name of the channel this arrived over. Read from the
    # row rather than held in process memory, because the restrictions derived
    # from it must outlive the process — see security/origin.py.
    origin: str = "local"

    @property
    def goal(self) -> str:
        return self.contract.goal


def new_task_id() -> str:
    """Non-deterministic by design — recorded as an event, read back on replay."""
    return "t_" + uuid.uuid4().hex[:16]


def create_task(
    conn: sqlite3.Connection,
    log: EventLog,
    contract: TaskContract,
    budget: Budget,
    parent_id: str | None = None,
    task_id: str | None = None,
    origin: str = "local",
) -> Task:
    task_id = task_id or new_task_id()
    now = time.time()
    # A child inherits its parent's origin. A channel task that delegates must
    # not launder its restrictions off by opening a subtask, and the union in
    # `tool_policy` only holds if the child is a channel task too.
    if parent_id:
        row = conn.execute(
            "SELECT origin FROM tasks WHERE id=?", (parent_id,)
        ).fetchone()
        if row and row[0] and row[0] != "local":
            origin = row[0]
    conn.execute(
        """INSERT INTO tasks(id, parent_id, contract_json, status, budget_json,
                             origin, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            task_id,
            parent_id,
            contract.model_dump_json(),
            TaskStatus.PENDING.value,
            budget.model_dump_json(),
            origin,
            now,
            now,
        ),
    )
    conn.commit()
    log.append(
        task_id,
        "task.created",
        {"contract": json.loads(contract.model_dump_json()),
         "parent_id": parent_id, "origin": origin},
    )
    return Task(task_id, parent_id, contract, TaskStatus.PENDING, budget, origin)


def load_task(conn: sqlite3.Connection, task_id: str) -> Task:
    row = conn.execute(
        """SELECT id, parent_id, contract_json, status, budget_json, origin
           FROM tasks WHERE id=?""",
        (task_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"no such task: {task_id}")
    return Task(
        id=row[0],
        parent_id=row[1],
        contract=TaskContract.model_validate_json(row[2]),
        status=TaskStatus(row[3]),
        budget=Budget.model_validate_json(row[4]),
        origin=row[5] or "local",
    )


def set_status(
    conn: sqlite3.Connection,
    log: EventLog,
    task: Task,
    status: TaskStatus,
    reason: str = "",
) -> None:
    task.status = status
    conn.execute(
        "UPDATE tasks SET status=?, updated_at=? WHERE id=?",
        (status.value, time.time(), task.id),
    )
    conn.commit()
    log.append(
        task.id, "task.status_changed", {"status": status.value, "reason": reason}
    )


def save_budget(conn: sqlite3.Connection, task: Task) -> None:
    conn.execute(
        "UPDATE tasks SET budget_json=?, updated_at=? WHERE id=?",
        (task.budget.model_dump_json(), time.time(), task.id),
    )
    conn.commit()


def spend(
    conn: sqlite3.Connection,
    log: EventLog,
    task: Task,
    usd: float = 0.0,
    seconds: float = 0.0,
) -> None:
    task.budget.spent_usd += usd
    task.budget.spent_seconds += seconds
    save_budget(conn, task)
    log.append(
        task.id,
        "task.budget_spent",
        {
            "usd": round(usd, 8),
            "seconds": round(seconds, 3),
            "total_usd": round(task.budget.spent_usd, 8),
        },
    )


def children(conn: sqlite3.Connection, task_id: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT id FROM tasks WHERE parent_id=? ORDER BY created_at", (task_id,)
        ).fetchall()
    ]


def contract_hash(contract: TaskContract) -> str:
    from .events import sha256_hex

    return sha256_hex(canonical(json.loads(contract.model_dump_json())))
