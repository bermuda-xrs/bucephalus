from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..kernel.events import canonical, sha256_hex

MAX_MODULES = 3


class SkillSetChanged(RuntimeError):
    ...


class SkillIntegrityError(RuntimeError):
    ...


def count_modules(body: str) -> int:
    return sum(1 for line in body.splitlines() if line.startswith("## "))


class SkillStore:
    def __init__(self, conn: sqlite3.Connection, root: str | Path):
        self.conn = conn
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def store(
        self,
        name: str,
        body: str,
        contract: dict,
        provenance: dict,
        status: str = "candidate",
        version: int | None = None,
    ) -> str:
        if count_modules(body) > MAX_MODULES:
            raise ValueError(
                f"skill exceeds module limit ({count_modules(body)} > {MAX_MODULES}); "
                "comprehensive skills measurably hurt"
            )
        skill_id = sha256_hex(body)
        held = self.conn.execute("SELECT name, version, status FROM skills WHERE id=?", (skill_id,)).fetchone()
        if held:
            raise ValueError(f"this skill body is already stored as {held[0]!r} v{held[1]} ({held[2]}); a stored "
                             "skill is not replaced - its status and drafting declaration stand")
        version = version if version is not None else self._next_version(name)

        d = self.root / skill_id
        d.mkdir(parents=True, exist_ok=True)
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

    def declare_drafted_from(self, skill_id: str, keys: list[str]) -> dict:
        row = self.conn.execute(
            "SELECT provenance_json FROM skills WHERE id=?", (skill_id,)).fetchone()
        if row is None:
            raise KeyError(f"no such skill: {skill_id}")
        prov = json.loads(row[0])
        if prov.get("drafted_from") is not None:
            raise ValueError(
                f"skill {skill_id[:12]} already declares drafted_from="
                f"{prov['drafted_from']!r}; a declaration is never overwritten")
        trials = self.conn.execute(
            "SELECT COUNT(*) FROM skill_trials WHERE skill_id=?", (skill_id,)).fetchone()[0]
        prov.update(drafted_from=list(keys), drafted_from_declared_at=time.time(),
                    trials_before_declaration=trials)
        self.conn.execute("UPDATE skills SET provenance_json=? WHERE id=?",
                          (canonical(prov), skill_id))
        self.conn.commit()
        disk = self.root / skill_id / "provenance.json"
        if disk.parent.exists():
            disk.write_text(json.dumps(prov, indent=2), encoding="utf-8")
        return prov

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


def split_skill_file(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("the front matter has no closing ---")
    import yaml

    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"the front matter is not valid YAML: {e}") from None
    if not isinstance(meta, dict):
        raise ValueError("the front matter must be key: value lines")
    if any(k in meta and not isinstance(meta[k], str) for k in ("name", "description", "applies_when")):
        raise ValueError("the front matter name and description must be plain text")
    return meta, text[end + 4:].lstrip("\n")


def import_external_skill(store: SkillStore, name: str, body: str, source: str,
                          applies_when: str | None = None, extra: dict | None = None) -> str:
    return store.store(
        name=name,
        body=body,
        contract={"applies_when": applies_when or "unknown — externally sourced"},
        provenance={
            "sources": [{"url": source, "retrieved": time.strftime("%Y-%m-%d")}],
            "drafted_by": "external",
            "trust": "untrusted",
            **(extra or {}),
        },
        status="candidate",
    )
