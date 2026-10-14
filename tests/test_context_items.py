from __future__ import annotations

import json

import pytest

from agent.execute.loop import run_task
from agent.kernel.tasks import create_task
from agent.kernel.types import Taint
from agent.ledger.compact import EVIDENCE_MAX, compact
from agent.ledger.entries import LedgerEntry

GOAL = "In work/catalogue.md, find the shelf mark for The Amber River."
FILLER = "The north meadow was quiet for most of the morning. " * 40


def _doc():
    return f"{FILLER}\nShelf mark for The Amber River: QX-4471.\n{FILLER}\nAn unrelated note about lunch.\n{FILLER}"


def _obs(text, taint=Taint.UNTRUSTED):
    return LedgerEntry.observation(text, taint=taint, label="read_file", artifact_id="a1")


def test_c1_a_dropped_observation_leaves_its_ids_and_matching_lines_verbatim():
    out = compact([_obs(_doc()), LedgerEntry.note("recent")], target_tokens=50, objective=GOAL)
    ev = [e for e in out if e.label == "evidence"]
    assert len(ev) == 1 and ev[0].pinned and ev[0].taint == Taint.UNTRUSTED
    assert "Shelf mark for The Amber River: QX-4471." in ev[0].content and "lunch" not in ev[0].content
    assert len(ev[0].content) <= EVIDENCE_MAX + 200


def test_c1_without_an_objective_nothing_is_pinned():
    assert not [e for e in compact([_obs(_doc())], target_tokens=50) if e.label == "evidence"]


def test_c1_the_evidence_is_bounded_and_merged_across_compactions():
    many = "\n".join(f"Shelf mark for The Amber River copy {i}: QX-{1000 + i}." for i in range(200))
    first = compact([_obs(many)], target_tokens=20, objective=GOAL)
    later = _obs(FILLER + "\nShelf mark for The Amber River, final: ZZ-9999.\n" + FILLER)
    second = compact(first + [later], target_tokens=20, objective=GOAL)
    ev = [e for e in second if e.label == "evidence"]
    assert len(ev) == 1 and "ZZ-9999" in ev[0].content and len(ev[0].content) <= EVIDENCE_MAX + 200


@pytest.fixture
def sm(small):
    b = small.backends["cloud"].cfg
    b.trust_tier, b.model_server = "local", "ollama"
    b.reasoning_field = b.carry_field = False
    small.config.limits.max_steps = 12
    return small


def _act(tool, **args):
    return json.dumps({"kind": "action", "tool": tool, "args": args})


def test_c1_in_a_run_the_pins_survive_the_session_boundary(sm, task, workspace):
    (workspace / "catalogue.md").write_text(_doc(), encoding="utf-8")
    sm.backends["cloud"].cfg.evidence_pins = True
    sm.config.compaction_probe_step = 2
    t = create_task(sm.conn, sm.log, task.contract.model_copy(update={"goal": GOAL}), task.budget)
    sm.backends["cloud"].queue(_act("read_file", path="work/catalogue.md"), _act("list_dir", path="work")
                               if "list_dir" in sm.tools.names() else _act("search", pattern="x", path="work"),
                               '{"kind": "final", "answer": "QX-4471"}')
    run_task(sm, t.id)
    assert "QX-4471" in sm.backends["cloud"].calls[-1]["tail"]


def test_c2_a_long_task_keeps_a_harness_written_progress_note(sm, task, workspace):
    sm.backends["cloud"].cfg.progress_file = True
    (workspace / "a.txt").write_text("hi", encoding="utf-8")
    t = create_task(sm.conn, sm.log, task.contract, task.budget)
    s = _act("search", pattern="hi", path="work")
    sm.backends["cloud"].queue(_act("read_file", path="work/a.txt"), _act("write_file", path="work/b.txt", content="x"),
                               s, s, '{"kind": "final", "answer": "ok"}')
    run_task(sm, t.id)
    tail = sm.backends["cloud"].calls[-1]["tail"]
    assert "Progress (kept by the harness)" in tail and "work/b.txt" in tail and "work/a.txt" in tail
    assert "Progress (kept by the harness)" not in sm.backends["cloud"].calls[1]["tail"], "only for longer tasks"


def test_c3_a_backend_can_set_its_own_compaction_ceiling(sm):
    from agent.config import fill_ratio
    from agent.execute.loop import _inline_cap

    before = _inline_cap(sm)
    sm.backends["cloud"].cfg.context_fill_ratio = 0.6
    assert fill_ratio(sm.config, sm) == 0.6 and _inline_cap(sm) >= before
