"""M9.3 / M8.4 — content-addressed skill storage and the supply chain rules.

Audits of shared agent skills have found flaw rates around a third with
confirmed malicious entries; lab work shows poisoning a few percent of a
library succeeds nearly always. So:

  1. Skills are content-addressed. The ID IS the hash of the body.
  2. Version-pinned. No auto-update, ever.
  3. Externally-sourced skills are UNTRUSTED and go through the SAME gate.
  4. Skill bodies never enter the prefix without a hash check against the store.
  5. skills_store/ is not writable from the sandbox.
  6. Promoting a skill is a TRUSTED capability requiring human approval.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..kernel.events import canonical, sha256_hex

MAX_MODULES = 3


class SkillSetChanged(RuntimeError):
    """A resumed task's recorded skill set cannot be loaded byte-for-byte.

    Running it under a different set would silently turn one arm of a paired
    trial into the other, so the task does not run.
    """


class SkillIntegrityError(RuntimeError):
    """A skill body on disk does not match its content hash."""


def count_modules(body: str) -> int:
    """A module is a top-level `## ` section. Three or fewer, always.

    Focused skills of three modules or fewer worked best in the literature;
    "comprehensive" ones measurably hurt.
    """
    return sum(1 for line in body.splitlines() if line.startswith("## "))


class SkillStore:
    def __init__(self, conn: sqlite3.Connection, root: str | Path):
        self.conn = conn
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- writing -----------------------------------------------------------

    def store(
        self,
        name: str,
        body: str,
        contract: dict,
        provenance: dict,
        visible_tests: list[str] | None = None,
        holdout_tests: list[str] | None = None,
        status: str = "candidate",
        version: int | None = None,
    ) -> str:
        if count_modules(body) > MAX_MODULES:
            raise ValueError(
                f"skill exceeds module limit ({count_modules(body)} > {MAX_MODULES}); "
                "comprehensive skills measurably hurt"
            )
        skill_id = sha256_hex(body)
        version = version if version is not None else self._next_version(name)

        d = self.root / skill_id
        (d / "tests" / "visible").mkdir(parents=True, exist_ok=True)
        (d / "tests" / "holdout").mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        (d / "contract.json").write_text(json.dumps(contract, indent=2), encoding="utf-8")
        (d / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
        (d / "meta.json").write_text(
            json.dumps(
                {"name": name, "version": version, "status": status,
                 "created_at": time.time()},
                indent=2,
            ),
            encoding="utf-8",
        )
        for i, t in enumerate(visible_tests or []):
            (d / "tests" / "visible" / f"check_{i}.txt").write_text(t, encoding="utf-8")
        for i, t in enumerate(holdout_tests or []):
            # On disk but NEVER read into a prompt. See mesh.HoldoutRegistry.
            (d / "tests" / "holdout" / f"check_{i}.txt").write_text(t, encoding="utf-8")

        self.conn.execute(
            """INSERT OR REPLACE INTO skills(id, name, version, body, contract_json,
                                             provenance_json, status, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                skill_id, name, version, body, canonical(contract),
                canonical(provenance), status, time.time(),
            ),
        )
        self.conn.commit()
        return skill_id

    def _next_version(self, name: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version),0) FROM skills WHERE name=?", (name,)
        ).fetchone()
        return int(row[0]) + 1

    # -- reading -----------------------------------------------------------

    def load(self, skill_id: str, verify: bool = True) -> dict:
        row = self.conn.execute(
            """SELECT id, name, version, body, contract_json, provenance_json, status
               FROM skills WHERE id=?""",
            (skill_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"no such skill: {skill_id}")

        body = row[3]
        disk = self.root / skill_id / "SKILL.md"
        if disk.exists():
            on_disk = disk.read_text(encoding="utf-8")
            if verify and sha256_hex(on_disk) != skill_id:
                # Rule 4: never let a body into the prefix without this check.
                raise SkillIntegrityError(
                    f"skill {skill_id[:12]} on disk does not match its content hash "
                    f"(got {sha256_hex(on_disk)[:12]}) — refusing to load"
                )
            body = on_disk
        elif verify and sha256_hex(body) != skill_id:
            raise SkillIntegrityError(f"skill {skill_id[:12]} body hash mismatch in db")

        return {
            "id": row[0], "name": row[1], "version": row[2], "body": body,
            "contract": json.loads(row[4]), "provenance": json.loads(row[5]),
            "status": row[6],
        }

    def promoted(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id FROM skills WHERE status='promoted' ORDER BY name, version"
        ).fetchall()
        return [self.load(r[0]) for r in rows]

    def by_status(self, status: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id FROM skills WHERE status=? ORDER BY name, version", (status,)
        ).fetchall()
        return [self.load(r[0], verify=False) for r in rows]

    def holdout_tests(self, skill_id: str) -> list[str]:
        """Read from disk only by the gate. Never by anything that builds a prompt."""
        d = self.root / skill_id / "tests" / "holdout"
        if not d.exists():
            return []
        return [p.read_text(encoding="utf-8") for p in sorted(d.glob("*.txt"))]

    def visible_tests(self, skill_id: str) -> list[str]:
        d = self.root / skill_id / "tests" / "visible"
        if not d.exists():
            return []
        return [p.read_text(encoding="utf-8") for p in sorted(d.glob("*.txt"))]

    # -- lifecycle ---------------------------------------------------------

    def set_status(self, skill_id: str, status: str, log=None, task_id: str | None = None) -> None:
        self.conn.execute("UPDATE skills SET status=? WHERE id=?", (status, skill_id))
        self.conn.commit()
        meta_path = self.root / skill_id / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["status"] = status
            meta["status_changed_at"] = time.time()
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        if log and task_id and status in ("promoted", "demoted"):
            log.append(task_id, f"skill.{status}", {"skill_id": skill_id})

    def record_invocation(self, skill_id: str, task_id: str, success: bool) -> None:
        self.conn.execute(
            "INSERT INTO skill_invocations(skill_id, task_id, success, created_at) "
            "VALUES (?,?,?,?)",
            (skill_id, task_id, int(success), time.time()),
        )
        self.conn.commit()


def import_external_skill(store: SkillStore, name: str, body: str, source: str) -> str:
    """Rule 3 — anything from elsewhere is UNTRUSTED and starts as a candidate.

    It goes through the same paired-trial gate as something the system wrote.
    """
    return store.store(
        name=name,
        body=body,
        contract={"applies_when": "unknown — externally sourced"},
        provenance={
            "sources": [{"url": source, "retrieved": time.strftime("%Y-%m-%d")}],
            "drafted_by": "external",
            "trust": "untrusted",
        },
        status="candidate",
    )
