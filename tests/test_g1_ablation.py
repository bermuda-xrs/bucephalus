from __future__ import annotations

import json
from pathlib import Path

import pytest

import agent.execute.loop as loop
from agent.context import AgentContext
from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.kernel.types import Budget, TaskContract
from agent.memory.store import MemoryStore, record_fingerprint

from conftest import make_config

FINAL = '<final>{"answer": "done"}</final>'


def READ(name):
    return ('<action tool="read_file">'
            + json.dumps({"path": f"work/{name}"}) + "</action>")


def REMEMBER(text):
    return '<action tool="memory_write">' + json.dumps({"text": text}) + "</action>"


def _ctx(tmp_path, **mechanisms):
    cfg = make_config(tmp_path)
    for k, v in mechanisms.items():
        setattr(cfg.mechanisms, k, v)
    cfg.long_files = "elide"
    ctx = AgentContext(cfg)
    ctx.memory = MemoryStore(tmp_path / "memory")
    record_fingerprint(ctx.memory)
    ctx.config.autonomy.auto_review = False
    ctx.config.autonomy.approve_scoped_writes = True
    work = Path(ctx.config.root) / "work"
    work.mkdir(exist_ok=True)
    for name in "abcde":
        (work / f"{name}.txt").write_text(
            f"File {name}. " + " ".join(f"{name}-{k} value {k * 7}"
                                        for k in range(90)),
            encoding="utf-8")
    return ctx


def _task(ctx, goal="ablation"):
    return create_task(ctx.conn, ctx.log,
                       TaskContract(goal=goal, artifact_scope=["work"]),
                       Budget(usd=1.0, seconds=600))


def _events(ctx, task_id, etype):
    return [json.loads(p) for (p,) in ctx.conn.execute(
        "SELECT payload_json FROM events WHERE task_id=? AND type=? ORDER BY seq",
        (task_id, etype)).fetchall()]


@pytest.fixture
def boundary(monkeypatch):
    def at(n):
        seen = {"k": 0}

        def fake(prefix_tokens, tail_tokens, window, ratio):
            seen["k"] += 1
            return "compact" if seen["k"] == n else "ok"
        monkeypatch.setattr(loop, "enforce_ceiling", fake)
    return at


def test_every_mechanism_is_on_unless_a_config_says_otherwise(tmp_path):
    from agent.config import MechanismsConfig

    m = MechanismsConfig()
    assert (m.compaction, m.semantic_cache, m.prefix_stability,
            m.code_as_action) == (True, True, True, True)
    assert m.ablated() == [], "the default must be the shipped system"


def test_the_arm_travels_in_the_run_record(tmp_path):
    import yaml

    from agent.config import load_config

    raw = {
        "profile": "mixed",
        "backends": {"cloud": {"kind": "mock", "model": "m"},
                     "review": {"kind": "mock", "model": "r"}},
        "mechanisms": {"compaction": False, "semantic_cache": False},
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.mechanisms.compaction is False
    assert cfg.mechanisms.prefix_stability is True
    assert cfg.profile_provenance["ablated"] == ["compaction", "semantic_cache"]
    assert cfg.profile_provenance["mechanisms"]["code_as_action"] is True


def _run_to_boundary(ctx, boundary):
    task = _task(ctx)
    boundary(3)
    ctx.backends["cloud"].queue(READ("a.txt"), READ("b.txt"), READ("c.txt"),
                                READ("d.txt"), FINAL)
    run_task(ctx, task.id)
    return task


def test_compaction_on_tombstones_at_the_boundary(tmp_path, boundary):
    ctx = _ctx(tmp_path)
    task = _run_to_boundary(ctx, boundary)
    assert [e for e in _events(ctx, task.id, "session.opened") if e.get("parent_id")], \
        "no boundary was crossed"
    assert not _events(ctx, task.id, "compaction.skipped")
    ctx.close()


def test_a_real_crossing_brings_the_context_under_the_ceiling(tmp_path):
    from agent.tokens import count_tokens

    ctx = _ctx(tmp_path)
    ctx.config.limits.max_steps = 20
    probe = _task(ctx)
    ctx.backends["cloud"].queue(FINAL)
    run_task(ctx, probe.id)
    prefix = count_tokens(ctx.backends["cloud"].calls[-1]["prefix"])
    file_tok = count_tokens((Path(ctx.config.root) / "work" / "a.txt").read_text(encoding="utf-8"))
    window = int((prefix + 2.5 * file_tok) / 0.28)
    assert prefix > 0.08 * window, "the geometry must put the prefix over 8%"
    ctx.backends["cloud"].cfg.usable_context_window = window

    task = _task(ctx)
    turns = iter([READ(f"{n}.txt") for n in "abcde"] + [READ("a.txt")] * 3)
    ctx.backends["cloud"]._responder = lambda p, t: (  # noqa: SLF001
        "NOTHING" if "reply with the single word NOTHING" in t else next(turns, FINAL))
    run_task(ctx, task.id)
    rotations = [e for e in _events(ctx, task.id, "session.opened") if e.get("parent_id")]
    assert rotations, "the real ceiling was never crossed"
    ceiling = int(window * ctx.config.limits.context_fill_ratio)
    calls = ctx.backends["cloud"].calls
    after = [count_tokens(nxt["prefix"]) + count_tokens(nxt["tail"])
             for cur, nxt in zip(calls, calls[1:])
             if "reply with the single word NOTHING" in cur["tail"]]
    assert after and max(after) <= ceiling, (
        f"after compaction the context was {after} tokens against a ceiling of "
        f"{ceiling}: a boundary did not bring it under")
    ctx.close()


def test_compaction_off_crosses_the_ceiling_and_drops_nothing(tmp_path, boundary):
    ctx = _ctx(tmp_path, compaction=False)
    task = _run_to_boundary(ctx, boundary)
    skipped = _events(ctx, task.id, "compaction.skipped")
    assert skipped, "the ceiling fired and nothing recorded that it did"
    assert skipped[0]["where"] == "session_boundary"
    assert skipped[0]["tail_tokens"] > 0
    rotations = [e for e in _events(ctx, task.id, "session.opened")
                 if e.get("parent_id")]
    assert not rotations, (
        "compaction is off, so no session may be ROTATED. Every task opens a "
        "first session at start; only a CHILD of one is a boundary crossing, "
        "and asserting on the bare event count would pass for the wrong "
        "reason in both directions")
    ctx.close()


def test_compaction_off_also_stops_the_refused_delegation_route(tmp_path):
    from agent.ledger.entries import LedgerEntry

    ctx = _ctx(tmp_path, compaction=False)
    task = _task(ctx)
    ctx.backends["cloud"].cfg.usable_context_window = 3000
    entries = [LedgerEntry.note("pinned objective", pinned=True)] + [
        LedgerEntry.observation("x " * 4000, label=f"obs{i}") for i in range(2)]
    out = ctx.handle_overflow_by_delegation(task, entries)
    assert [e.kind for e in out] == [e.kind for e in entries], \
        "the delegation fallback compacted while compaction was ablated"
    assert _events(ctx, task.id, "compaction.skipped")[0]["where"] == \
        "delegation_refused"
    ctx.close()


def test_compaction_on_keeps_the_refused_delegation_route(tmp_path):
    from agent.ledger.entries import LedgerEntry

    ctx = _ctx(tmp_path)
    task = _task(ctx)
    ctx.backends["cloud"].cfg.usable_context_window = 3000
    entries = [LedgerEntry.note("pinned objective", pinned=True)] + [
        LedgerEntry.observation("x " * 4000, label=f"obs{i}") for i in range(2)]
    out = ctx.handle_overflow_by_delegation(task, entries)
    assert any(e.kind == "tombstone" for e in out), \
        "the shipped system must still tombstone here"
    ctx.close()


def _run_two_identical_compressions(ctx):
    ctx.config.compress_observations_over_tokens = 50
    task = _task(ctx, "read the same file twice")
    ctx.backends["cloud"].queue(READ("a.txt"), READ("a.txt"), FINAL)
    run_task(ctx, task.id)
    return task


def test_semantic_cache_on_serves_the_second_identical_compression(
        tmp_path):
    ctx = _ctx(tmp_path)
    _run_two_identical_compressions(ctx)
    hits = ctx.conn.execute(
        "SELECT COALESCE(SUM(hits),0) FROM semantic_cache").fetchone()[0]
    assert hits > 0, "the shipped system did not serve a repeat compression"
    ctx.close()


def test_semantic_cache_off_neither_reads_nor_writes(tmp_path):
    ctx = _ctx(tmp_path, semantic_cache=False)
    _run_two_identical_compressions(ctx)
    rows = ctx.conn.execute("SELECT COUNT(*) FROM semantic_cache").fetchone()[0]
    assert rows == 0, f"{rows} cache rows written by the arm that has no cache"
    ctx.close()


def _prefix_fingerprints(ctx, task_id):
    return [e["prefix_fp"] for e in _events(ctx, task_id, "model.called")
            if "prefix_fp" in e]


def _run_with_a_mid_task_memory_write(ctx):
    task = _task(ctx, "remember something, then carry on")
    ctx.backends["cloud"].queue(READ("a.txt"),
                                REMEMBER("The deploy port is 8443."),
                                READ("b.txt"), FINAL)
    run_task(ctx, task.id)
    return task


def test_prefix_stability_on_holds_the_prefix_across_a_memory_write(tmp_path):
    ctx = _ctx(tmp_path)
    task = _run_with_a_mid_task_memory_write(ctx)
    fps = _prefix_fingerprints(ctx, task.id)
    assert len(fps) >= 3, fps
    assert len(set(fps)) == 1, "the prefix moved under the shipped system"
    ctx.close()


def test_prefix_stability_off_lets_a_memory_write_move_the_prefix(tmp_path):
    ctx = _ctx(tmp_path, prefix_stability=False)
    task = _run_with_a_mid_task_memory_write(ctx)
    fps = _prefix_fingerprints(ctx, task.id)
    assert len(fps) >= 3, fps
    assert len(set(fps)) > 1, (
        "the prefix held even with stability ablated: the arm is measuring "
        "the shipped system under another name")
    ctx.close()


def test_code_as_action_on_offers_run_code_everywhere(tmp_path):
    from agent.ledger.prefix import system_contract

    ctx = _ctx(tmp_path)
    assert "run_code" in ctx.tools.names()
    assert "run_code" in ctx.tools.manual()
    assert "<code>" in system_contract(True)
    ctx.close()


def test_code_as_action_off_removes_the_tool_and_its_instruction(tmp_path):
    from agent.ledger.prefix import system_contract

    ctx = _ctx(tmp_path, code_as_action=False)
    assert "run_code" not in ctx.tools.names()
    assert "run_code" not in {s["name"] for s in ctx.tools.schemas()}
    assert "run_code" not in ctx.tools.manual(), \
        "still described in the prefix's tool manual"
    assert "<code>" not in system_contract(False)
    assert "<action tool=" in system_contract(False), \
        "the ablated contract lost more than the code clause"
    ctx.close()


def test_code_as_action_off_refuses_a_code_block_that_arrives_anyway(tmp_path):
    ctx = _ctx(tmp_path, code_as_action=False)
    task = _task(ctx, "try to run code")
    ctx.backends["cloud"].queue("<code>\nprint(1)\n</code>", FINAL)
    run_task(ctx, task.id)
    assert not _events(ctx, task.id, "tool.receipt") or all(
        e.get("tool") != "run_code" for e in _events(ctx, task.id, "tool.receipt")), \
        "code ran in the arm that has no code-as-action"
    ctx.close()


def test_the_ablated_registry_does_not_mutate_the_shared_default(tmp_path):
    from agent.execute.tools import default_registry

    ablated = _ctx(tmp_path / "a", code_as_action=False)
    assert "run_code" in default_registry().names()
    shipped = _ctx(tmp_path / "b")
    assert "run_code" in shipped.tools.names()
    ablated.close()
    shipped.close()


def test_without_the_probe_nothing_is_forced(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.config.compaction_probe_step is None
    task = _task(ctx)
    ctx.backends["cloud"].queue(READ("a.txt"), READ("b.txt"), FINAL)
    run_task(ctx, task.id)
    assert not _events(ctx, task.id, "compaction.skipped")
    assert not [e for e in _events(ctx, task.id, "session.opened")
                if e.get("parent_id")]
    ctx.close()


def test_the_probe_forces_a_boundary_the_fill_would_not(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config.compaction_probe_step = 2
    task = _task(ctx)
    ctx.backends["cloud"].queue(READ("a.txt"), READ("b.txt"), READ("c.txt"),
                                FINAL)
    run_task(ctx, task.id)
    probes = [e for e in _events(ctx, task.id, "compaction.skipped")
              if e.get("where") == "probe"]
    assert probes, "the probe did not fire"
    assert probes[0]["step"] == 2
    assert probes[0]["real_action"] == "ok", (
        "the probe fired on a step the real ceiling had ALREADY crossed, so "
        "it proves nothing about forcing")
    assert [e for e in _events(ctx, task.id, "session.opened")
            if e.get("parent_id")], "forced, but no boundary was crossed"
    ctx.close()


def test_the_probe_and_the_ablation_arm_agree(tmp_path):
    ctx = _ctx(tmp_path, compaction=False)
    ctx.config.compaction_probe_step = 2
    task = _task(ctx)
    ctx.backends["cloud"].queue(READ("a.txt"), READ("b.txt"), READ("c.txt"),
                                FINAL)
    run_task(ctx, task.id)
    where = [e.get("where") for e in _events(ctx, task.id, "compaction.skipped")]
    assert "probe" in where and "session_boundary" in where, where
    assert not [e for e in _events(ctx, task.id, "session.opened")
                if e.get("parent_id")], "compaction is off; nothing may rotate"
    ctx.close()


def test_a_synthetic_boundary_is_declared_in_the_record(tmp_path):
    import yaml

    from agent.config import load_config

    raw = {"profile": "mixed",
           "backends": {"cloud": {"kind": "mock", "model": "m"},
                        "review": {"kind": "mock", "model": "r"}}}
    p = tmp_path / "real.yaml"
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_config(p).profile_provenance["compaction_boundary"] == \
        "real: the fill ratio"

    raw["compaction_probe_step"] = 2
    q = tmp_path / "probe.yaml"
    q.write_text(yaml.safe_dump(raw), encoding="utf-8")
    note = load_config(q).profile_provenance["compaction_boundary"]
    assert note.startswith("SYNTHETIC"), note
    assert "step 2" in note and "how often it fires" in note
