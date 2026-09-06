"""Retrieval isolation: what one task attempt is allowed to read back.

THE DEFECT THIS EXISTS TO PREVENT, stated as it would appear in a result:

    control(E03) runs, writes an answer, the trigger indexes it
    skill(E03)   runs, calls search_history, finds control's answer
    -> a discordant pair produced by RETRIEVAL, not by the skill

That is the grader-leak class one level up. `workspace.py` already documents
the disk version of it in its own docstring — two arms sharing a workspace,
so the control arm gets graded against the answer the skill arm just wrote —
and fixes it by rebuilding the directory before **every arm of every run**.
This is the same fix for the database, at the same granularity, and the
granularity is the part that matters: per ATTEMPT, not per pair.

Per-attempt rather than per-arm is not a detail. A per-arm namespace would
still let run 2 of the control arm read run 1 of the control arm, and the
noise-floor classification (3/3, 0/3, flaky) assumes those runs are
independent draws. Scoping to the attempt fixes the paired trial and the
noise floor with one rule.

WHY SCOPING RATHER THAN DISABLING THE TOOLS. The tool manual is rendered into
the cache-stable prefix. Removing a tool for benchmark runs would change the
prefix, and this project's own central finding is that prefix structure moves
pass rates substantially — the harness tax was 8 points on R0 to R5. Deleting
a tool from the manual perturbs the very thing under study, so the measured
system would differ from the shipped one in the way most likely to matter
here. Under scoping the prompt is byte-identical; only what a lookup can
reach changes, and "a search that finds nothing" is a state the shipped
system reaches on its first session anyway.

FAIL-CLOSED, AND NOT FROM A CONFIG FILE. `bench/noise.py` sets this itself
rather than reading it from yaml, because a contaminated measurement caused
by editing the wrong config is exactly the failure this is meant to remove.
"""

from __future__ import annotations

import sqlite3


class ScopeViolation(RuntimeError):
    """A read was refused because it left the attempt's scope."""


def task_tree(conn: sqlite3.Connection, task_id: str) -> set[str]:
    """Every task id in the same tree — root, siblings, descendants.

    An attempt is one root task plus whatever it delegated, so the tree is
    the attempt. Walk up to the root first, then down, because a delegated
    child asking the question must get the same answer as its parent.
    """
    root = conn.execute(
        """WITH RECURSIVE up(id, parent_id) AS (
               SELECT id, parent_id FROM tasks WHERE id = ?
               UNION ALL
               SELECT t.id, t.parent_id FROM tasks t JOIN up ON t.id = up.parent_id
           )
           SELECT id FROM up WHERE parent_id IS NULL LIMIT 1""",
        (task_id,),
    ).fetchone()
    root_id = root[0] if root else task_id
    rows = conn.execute(
        """WITH RECURSIVE down(id) AS (
               SELECT id FROM tasks WHERE id = ?
               UNION ALL
               SELECT t.id FROM tasks t JOIN down ON t.parent_id = down.id
           )
           SELECT id FROM down""",
        (root_id,),
    ).fetchall()
    return {r[0] for r in rows} | {task_id}


def visible_tasks(ctx, task) -> set[str] | None:
    """Task ids this attempt may read from. None means unrestricted.

    None is the shipped default: searching earlier sessions is the entire
    point of the feature. A benchmark turns it into a set.
    """
    if not getattr(ctx.config, "isolate_retrieval", False):
        return None
    if task is None:
        # Isolation is on and there is no task to scope to. Refuse rather
        # than fall back to unrestricted: an isolation rule that opens up
        # when it cannot identify the caller is not an isolation rule.
        return set()
    return task_tree(ctx.conn, task.id)


def isolation_state(ctx) -> dict:
    """What the run record reports. A reader must never have to infer this."""
    on = bool(getattr(ctx.config, "isolate_retrieval", False))
    return {
        "isolate_retrieval": on,
        "search_history": "scoped to the task tree" if on else "all sessions",
        "expand_artifact": "scoped to the task tree" if on else "all tasks",
        "memory_tier1": "unavailable" if on else "user data dir",
        "note": (
            "a benchmark attempt can read only what it produced itself; "
            "cross-arm and cross-run retrieval are impossible by construction"
            if on else
            "unrestricted retrieval — correct for normal use, INVALID for a "
            "paired trial"
        ),
    }
