from __future__ import annotations

import json
import re
import sys
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

GUESS = "GUESS7731"


def _config(root: Path) -> Path:
    mock = {"kind": "mock", "trust_tier": "cloud", "context_window": 200000,
            "usable_context_window": 200000}
    raw = {
        "profile": "mixed",
        "db_path": str(root / "agent.db"),
        "anchor_path": str(root / "anchor" / "chain.anchor"),
        "skills_path": str(root / "skills_store"),
        "backends": {"cloud": {**mock, "model": "mock-cloud"},
                     "review": {**mock, "model": "mock-review"}},
        "active_local_tier": None,
        "limits": {"max_steps": 8, "default_task_budget_usd": 1.0},
        "autonomy": {"plan_required": False, "auto_review": True,
                     "auto_review_backend": "review",
                     "auto_review_policy": str(root / "policy.md"),
                     "approve_scoped_writes": False},
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "policy.md").write_text(
        "# policy\n\nDefault is DENY.\n\n## Approve when all of these hold\n"
        "- tool is write_file\n- paths inside artifact_scope\n",
        encoding="utf-8")
    p = root / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return p


def _run(root: Path, secret: str, measured: bool, monkeypatch):
    from agent.context import AgentContext
    from agent.execute.loop import run_task
    from agent.kernel.tasks import create_task
    from agent.kernel.types import Budget, TaskContract
    from agent.memory.store import MemoryStore, record_fingerprint

    monkeypatch.setenv("BUCEPHALUS_DATA_DIR", str(root))
    cfg_path = _config(root)
    if measured:
        from measurement import open_measurement

        ctx = open_measurement(cfg_path, profile=None, isolate=True,
                               why="grader non-interference test")
    else:
        ctx = AgentContext.open(cfg_path)
        ctx.config.isolate_retrieval = False
    ctx.memory = MemoryStore(root / "memory")
    record_fingerprint(ctx.memory)
    (root / "work").mkdir(exist_ok=True)

    prompts: dict[str, list[str]] = {}
    for name, backend in ctx.backends.items():
        orig = backend.complete

        def spy(prefix, tail, tools=None, *a, _n=name, _o=orig, **k):
            prompts.setdefault(_n, []).append(
                prefix + "\n<<TAIL>>\n" + tail + "\n<<TOOLS>>\n"
                + json.dumps(tools, sort_keys=True))
            return _o(prefix, tail, tools, *a, **k)
        backend.complete = spy

    task = create_task(
        ctx.conn, ctx.log,
        TaskContract(
            goal="Report the reference code for this account as JSON.",
            success_criterion="expected_value", artifact_scope=["work"],
            grader_dir=f"grading/{secret}",
            output_schema={"type": "object", "required": ["total"],
                           "properties": {"total": {"type": "string"}},
                           "x-expected": {"total": secret},
                           "x-contains": [secret]}),
        Budget(usd=1.0, seconds=600))

    def act(tool, **args):
        return f'<action tool="{tool}">' + json.dumps(args) + "</action>"

    ctx.backends["cloud"].queue(
        '<final>{"total": "first wrong answer"}</final>',
        act("search_history", query=GUESS),
        act("write_file", path="work/notes.txt", content="x"),
        '<final>{"total": "second wrong answer"}</final>',
        '<final>{"total": "third wrong answer"}</final>',
    )
    ctx.backends["review"].queue("VERDICT: approve\nBECAUSE: scoped write")
    run_task(ctx, task.id)
    ctx.close()
    return prompts


def _normalise(text: str, root: Path) -> str:
    text = text.replace(str(root), "<ROOT>").replace(
        str(root).replace("\\", "\\\\"), "<ROOT>")
    text = re.sub(r"\b[a-z]_[0-9a-f]{8,}\b", "<ID>", text)
    text = re.sub(r"\b[0-9a-f]{12,}\b", "<HEX>", text)
    return re.sub(r"\b\d{10}(\.\d+)?\b", "<TS>", text)


def test_p1_the_grader_s_secret_appears_in_no_prompt(tmp_path, monkeypatch):
    sentinel = "SENTINEL" + uuid.uuid4().hex.upper()
    prompts = _run(tmp_path, sentinel, measured=True, monkeypatch=monkeypatch)
    assert prompts.get("cloud"), "the executor was never called"
    assert prompts.get("review"), (
        "the deputy was never called, so the compact_transcript channel was "
        "not exercised and this test would pass for the wrong reason")
    for backend, sent in prompts.items():
        for i, p in enumerate(sent):
            assert sentinel not in p, (
                f"the grader's secret reached {backend}'s prompt #{i + 1}:\n"
                f"...{p[max(0, p.find(sentinel) - 200):p.find(sentinel) + 80]}...")


def test_p1_the_model_is_told_only_that_it_failed(tmp_path, monkeypatch):
    prompts = _run(tmp_path, "SENTINEL" + uuid.uuid4().hex, measured=True,
                   monkeypatch=monkeypatch)
    later = prompts["cloud"][1]
    assert "Verification failed" in later
    assert "held out" in later
    assert "score" not in later.split("<<TAIL>>")[1].lower(), (
        "a score reached the model: under measurement it is pass/fail only")


def test_p2_the_executor_sees_the_same_bytes_whatever_the_secret(
        tmp_path, monkeypatch):
    a = _run(tmp_path / "a", GUESS, measured=True, monkeypatch=monkeypatch)
    b = _run(tmp_path / "b", "OTHER4402", measured=True, monkeypatch=monkeypatch)
    pa = [_normalise(p, tmp_path / "a") for p in a["cloud"]]
    pb = [_normalise(p, tmp_path / "b") for p in b["cloud"]]
    assert len(pa) == len(pb), (
        f"{len(pa)} executor calls under one secret, {len(pb)} under the "
        "other: the grader's secret changed the COURSE of the task")
    for i, (x, y) in enumerate(zip(pa, pb)):
        if x != y:
            at = next((j for j, (c, d) in enumerate(zip(x, y)) if c != d), min(len(x), len(y)))
            raise AssertionError(
                f"executor prompt #{i + 1} depends on the grader's secret. "
                f"First difference at byte {at}:\n"
                f"  secret={GUESS!r}:  ...{x[max(0, at - 120):at + 120]}...\n"
                f"  secret='OTHER4402': ...{y[max(0, at - 120):at + 120]}...")


def test_p3_outside_measurement_the_detailed_feedback_still_arrives(
        tmp_path, monkeypatch):
    sentinel = "SENTINEL" + uuid.uuid4().hex.upper()
    prompts = _run(tmp_path, sentinel, measured=False, monkeypatch=monkeypatch)
    assert any(sentinel in p for p in prompts["cloud"]), (
        "outside measurement the verifier's detailed feedback no longer "
        "reaches the model - the product was weakened to fix the benchmark")


def test_an_existing_database_stops_indexing_the_answer_key(tmp_path):
    import time

    from agent.kernel.db import open_db

    path = tmp_path / "old.db"
    conn = open_db(path)
    conn.executescript("""
        DROP TRIGGER trg_search_task_ai_v2;
        CREATE TRIGGER trg_search_task_ai AFTER INSERT ON tasks
        BEGIN
            INSERT INTO search_index(body, source, ref, task_id, kind, created_at)
            VALUES (new.contract_json, 'contract', new.id, new.id, 'contract',
                    new.created_at);
        END;
    """)
    contract = json.dumps({"goal": "old goal", "success_criterion": "expected_value",
                           "output_schema": {"x-expected": {"total": "OLDSECRET99"}}})
    conn.execute("INSERT INTO tasks(id, contract_json, budget_json, status, "
                 "created_at, updated_at) VALUES ('t_old', ?, '{}', 'done', ?, ?)",
                 (contract, time.time(), time.time()))
    conn.commit()
    hits = conn.execute("SELECT COUNT(*) FROM search_index WHERE search_index "
                        "MATCH 'OLDSECRET99'").fetchone()[0]
    assert hits == 1, "the fixture is not a pre-fix database"
    conn.close()

    conn = open_db(path)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert "trg_search_task_ai" not in names, "the old trigger is still firing"
    assert "trg_search_task_ai_v2" in names
    assert conn.execute("SELECT COUNT(*) FROM search_index WHERE search_index "
                        "MATCH 'OLDSECRET99'").fetchone()[0] == 0, (
        "the rows the old trigger wrote still carry the answer key")
    assert conn.execute("SELECT COUNT(*) FROM search_index WHERE search_index "
                        "MATCH 'old goal'").fetchone()[0] == 1, (
        "re-indexing lost the goal, which is what search is FOR")
    conn.close()
