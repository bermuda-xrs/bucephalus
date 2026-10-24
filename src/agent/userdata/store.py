from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_OWNER = "local"

DEFAULT_POLICY = """\
# Auto-review policy

**Default is DENY.** An action is approved only if a rule below explicitly
permits it. A category with no rule is refused.

## Always deny
- Credentials, API keys, tokens, cookies, or session material leaving the machine.
- Writes outside the task's artifact_scope.
- Writes to anchor/, skills_store/, config files, or .env.
- Network egress to hosts not named in the approved plan.
- Any side-effecting action whose arguments carry untrusted taint.

## Approve when all of these hold
- The tool is write_file, run_code, or list_dir.
- Every path argument is inside artifact_scope.
- Argument taint is trusted or user.
- The action matches the approved plan step it belongs to.

## Escalate to a human
- Anything at trusted tier.
- Anything irreversible the plan did not anticipate.
"""

VALID_KINDS = ("policy",)


class ValidationError(ValueError):
    ...


@dataclass
class SuiteTask:
    task_key: str
    goal: str
    success_criterion: str = "expected_value"
    output_schema: dict = field(default_factory=dict)
    artifact_scope: list[str] = field(default_factory=lambda: ["work"])
    fixtures: dict[str, str] = field(default_factory=dict)
    budget_usd: float | None = None
    budget_seconds: int | None = None
    catastrophic: bool = False
    position: int = 0
    id: str = ""

    def to_spec(self) -> dict:
        d: dict[str, Any] = {
            "key": self.task_key,
            "goal": self.goal,
            "success_criterion": self.success_criterion,
            "output_schema": self.output_schema,
            "artifact_scope": self.artifact_scope,
            "grader_dir": f"grading/{self.task_key}",
            "fixtures": self.fixtures,
            "catastrophic": self.catastrophic,
        }
        if self.budget_usd is not None:
            d["budget_usd"] = self.budget_usd
        if self.budget_seconds is not None:
            d["budget_seconds"] = self.budget_seconds
        return d


@dataclass
class Suite:
    name: str
    description: str = ""
    id: str = ""
    owner: str = DEFAULT_OWNER
    tasks: list[SuiteTask] = field(default_factory=list)

    def to_specs(self) -> list[dict]:
        return [t.to_spec() for t in self.tasks]


def validate_task(t: SuiteTask, known_verifiers: set[str] | None = None) -> list[str]:
    problems: list[str] = []
    if not t.task_key.strip():
        problems.append("Key is required — it identifies the paired trial.")
    elif not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", t.task_key):
        problems.append("Key must be a plain name (letters, digits, '.', '_', '-'): it names the task's grader folder.")
    if not t.goal.strip():
        problems.append("Goal is required.")

    if t.success_criterion == "always_pass":
        problems.append(
            "always_pass measures nothing and will happily promote noise. "
            "Use expected_value or contains_all."
        )
    if known_verifiers and t.success_criterion not in known_verifiers:
        problems.append(
            f"No verifier named {t.success_criterion!r}. "
            f"Available: {', '.join(sorted(known_verifiers))}."
        )

    schema = t.output_schema or {}
    if t.success_criterion == "expected_value" and "x-expected" not in schema:
        problems.append(
            "expected_value needs an x-expected value in the output schema — "
            "otherwise it cannot tell a right answer from a well-shaped wrong one."
        )
    if t.success_criterion == "contains_all" and not schema.get("x-contains"):
        problems.append("contains_all needs a non-empty x-contains list.")

    if t.budget_seconds is not None and t.budget_seconds <= 0:
        problems.append("Time budget must be positive.")
    return problems


class UserStore:
    def __init__(self, conn, owner: str = DEFAULT_OWNER):
        self.conn = conn
        self.owner = owner

    def create_suite(self, name: str, description: str = "") -> Suite:
        name = name.strip()
        if not name:
            raise ValidationError("Suite name is required.")
        if self.get_suite_by_name(name):
            raise ValidationError(f"A suite named {name!r} already exists.")
        sid = "s_" + uuid.uuid4().hex[:12]
        now = time.time()
        self.conn.execute(
            "INSERT INTO suites(id,name,owner,description,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (sid, name, self.owner, description, now, now),
        )
        self.conn.commit()
        return Suite(id=sid, name=name, description=description, owner=self.owner)

    def list_suites(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.id, s.name, s.description, s.updated_at, "
            "  (SELECT COUNT(*) FROM suite_tasks t WHERE t.suite_id=s.id) "
            "FROM suites s WHERE s.owner=? ORDER BY s.name",
            (self.owner,),
        ).fetchall()
        return [
            {"id": r[0], "name": r[1], "description": r[2], "updated_at": r[3],
             "n_tasks": r[4], "ready": r[4] >= 30}
            for r in rows
        ]

    def get_suite_by_name(self, name: str) -> Suite | None:
        row = self.conn.execute(
            "SELECT id,name,description FROM suites WHERE owner=? AND name=?",
            (self.owner, name),
        ).fetchone()
        return self.get_suite(row[0]) if row else None

    def get_suite(self, suite_id: str) -> Suite | None:
        row = self.conn.execute(
            "SELECT id,name,description,owner FROM suites WHERE id=?", (suite_id,)
        ).fetchone()
        if not row:
            return None
        s = Suite(id=row[0], name=row[1], description=row[2], owner=row[3])
        s.tasks = self.list_tasks(suite_id)
        return s

    def delete_suite(self, suite_id: str) -> None:
        self.conn.execute("DELETE FROM suite_tasks WHERE suite_id=?", (suite_id,))
        self.conn.execute("DELETE FROM suites WHERE id=?", (suite_id,))
        self.conn.commit()

    def list_tasks(self, suite_id: str) -> list[SuiteTask]:
        rows = self.conn.execute(
            "SELECT id,task_key,goal,success_criterion,output_schema_json,"
            "artifact_scope_json,fixtures_json,budget_usd,budget_seconds,"
            "catastrophic,position FROM suite_tasks WHERE suite_id=? "
            "ORDER BY position, task_key",
            (suite_id,),
        ).fetchall()
        return [
            SuiteTask(
                id=r[0], task_key=r[1], goal=r[2], success_criterion=r[3],
                output_schema=json.loads(r[4]), artifact_scope=json.loads(r[5]),
                fixtures=json.loads(r[6]), budget_usd=r[7], budget_seconds=r[8],
                catastrophic=bool(r[9]), position=r[10],
            )
            for r in rows
        ]

    def upsert_task(self, suite_id: str, t: SuiteTask,
                    known_verifiers: set[str] | None = None) -> SuiteTask:
        problems = validate_task(t, known_verifiers)
        if problems:
            raise ValidationError(" ".join(problems))
        if not self.conn.execute(
            "SELECT 1 FROM suites WHERE id=?", (suite_id,)
        ).fetchone():
            raise ValidationError("No such suite.")

        now = time.time()
        existing = self.conn.execute(
            "SELECT id FROM suite_tasks WHERE suite_id=? AND task_key=?",
            (suite_id, t.task_key),
        ).fetchone()
        tid = t.id or (existing[0] if existing else "st_" + uuid.uuid4().hex[:12])

        if t.position == 0 and not existing:
            nxt = self.conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM suite_tasks WHERE suite_id=?",
                (suite_id,),
            ).fetchone()[0]
            t.position = nxt

        self.conn.execute(
            "INSERT INTO suite_tasks(id,suite_id,task_key,goal,success_criterion,"
            "output_schema_json,artifact_scope_json,fixtures_json,budget_usd,"
            "budget_seconds,catastrophic,position,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(suite_id,task_key) DO UPDATE SET "
            "goal=excluded.goal, success_criterion=excluded.success_criterion, "
            "output_schema_json=excluded.output_schema_json, "
            "artifact_scope_json=excluded.artifact_scope_json, "
            "fixtures_json=excluded.fixtures_json, budget_usd=excluded.budget_usd, "
            "budget_seconds=excluded.budget_seconds, "
            "catastrophic=excluded.catastrophic, updated_at=excluded.updated_at",
            (tid, suite_id, t.task_key, t.goal, t.success_criterion,
             json.dumps(t.output_schema), json.dumps(t.artifact_scope),
             json.dumps(t.fixtures), t.budget_usd, t.budget_seconds,
             int(t.catastrophic), t.position, now, now),
        )
        self.conn.execute("UPDATE suites SET updated_at=? WHERE id=?", (now, suite_id))
        self.conn.commit()
        t.id = tid
        return t

    def delete_task(self, suite_id: str, task_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM suite_tasks WHERE suite_id=? AND id=?", (suite_id, task_id)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def import_suite_from_dir(self, name: str, folder: str | Path) -> Suite:
        folder = Path(folder)
        if not folder.is_dir():
            raise ValidationError(f"{folder} is not a directory.")
        suite = self.get_suite_by_name(name) or self.create_suite(
            name, f"imported from {folder.name}"
        )
        for p in sorted(folder.glob("*.json")):
            spec = json.loads(p.read_text(encoding="utf-8"))
            self.upsert_task(suite.id, SuiteTask(
                task_key=spec.get("key", p.stem),
                goal=spec.get("goal", ""),
                success_criterion=spec.get("success_criterion", "expected_value"),
                output_schema=spec.get("output_schema", {}),
                artifact_scope=spec.get("artifact_scope", ["work"]),
                fixtures=spec.get("fixtures", {}),
                budget_usd=spec.get("budget_usd"),
                budget_seconds=spec.get("budget_seconds"),
                catastrophic=bool(spec.get("catastrophic", False)),
            ))
        return self.get_suite(suite.id)  # type: ignore[return-value]

    def get_active_policy(self) -> str | None:
        row = self.conn.execute(
            "SELECT body FROM documents WHERE owner=? AND kind='policy' AND active=1 "
            "ORDER BY updated_at DESC, rowid DESC LIMIT 1",
            (self.owner,),
        ).fetchone()
        return row[0] if row else None

    def set_policy(self, body: str, name: str = "default") -> None:
        if not body.strip():
            raise ValidationError(
                "Policy cannot be empty. An empty rule book would be read as "
                "'nothing is explicitly permitted', which denies everything."
            )
        self.put_document("policy", name, body)
        self.conn.execute(
            "UPDATE documents SET active=0 WHERE owner=? AND kind='policy' AND name!=?",
            (self.owner, name),
        )
        self.conn.commit()

    def put_document(self, kind: str, name: str, body: str,
                     actor: str = "local") -> dict:
        if kind not in VALID_KINDS:
            raise ValidationError(f"kind must be one of {VALID_KINDS}")
        name = name.strip()
        if not name:
            raise ValidationError("Name is required.")
        now = time.time()
        did = "d_" + uuid.uuid4().hex[:12]

        prior = self.get_document(kind, name)
        if prior is not None and prior["body"] != body:
            from ..kernel.events import sha256_hex

            self.conn.execute(
                "INSERT INTO document_history(document_id,kind,name,owner,body,"
                "body_sha256,actor,replaced_at) VALUES (?,?,?,?,?,?,?,?)",
                (prior["id"], kind, name, self.owner, prior["body"],
                 sha256_hex(prior["body"]),
                 actor, now),
            )
        self.conn.execute(
            "INSERT INTO documents(id,kind,name,owner,body,active,created_at,updated_at) "
            "VALUES (?,?,?,?,?,1,?,?) "
            "ON CONFLICT(owner,kind,name) DO UPDATE SET "
            "body=excluded.body, active=1, updated_at=excluded.updated_at",
            (did, kind, name, self.owner, body, now, now),
        )
        self.conn.commit()
        return self.get_document(kind, name)  # type: ignore[return-value]

    def get_document(self, kind: str, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT id,kind,name,body,active,updated_at FROM documents "
            "WHERE owner=? AND kind=? AND name=?",
            (self.owner, kind, name),
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "kind": row[1], "name": row[2], "body": row[3],
                "active": bool(row[4]), "updated_at": row[5]}

    def document_history(self, kind: str, name: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT body_sha256, actor, replaced_at, length(body) FROM document_history "
            "WHERE owner=? AND kind=? AND name=? ORDER BY replaced_at DESC, rowid DESC LIMIT ?",
            (self.owner, kind, name, limit),
        ).fetchall()
        return [{"sha256": r[0][:12], "actor": r[1], "replaced_at": r[2],
                 "bytes": r[3]} for r in rows]
