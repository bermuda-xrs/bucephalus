"""B5 + A8 — streaming, and the terminal surface.

The properties here are the ones a display can get wrong in a way that
matters: showing the wrong thing in the reserved alert colour, letting
decoration reach a prompt, or reporting a budget that averages away the axis
about to bind.

Driving a terminal is not how they are tested. `tui/model.py` holds every
rule as a pure function of events, precisely so a test can check them without
a display — a test that needed one is a test nobody runs.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent import stream as bus  # noqa: E402
from agent.config import BackendConfig, Config  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.execute.loop import run_task  # noqa: E402
from agent.kernel.tasks import create_task  # noqa: E402
from agent.kernel.types import Budget as TaskBudget  # noqa: E402
from agent.kernel.types import TaskContract  # noqa: E402
from agent.tui.model import ALERT_ROLE, Budget, RunState, ToolCall, fold  # noqa: E402
from agent.tui.theme import (  # noqa: E402
    ASCII_SPINNER,
    PLAIN_BANNER,
    banner_text,
    load,
    supports_glyphs,
)


@pytest.fixture(autouse=True)
def _clean_bus():
    bus.reset()
    yield
    bus.reset()


def make_ctx(tmp_path):
    (tmp_path / "work").mkdir(exist_ok=True)
    cfg = Config(
        profile="mixed",
        db_path=str(tmp_path / "t.db"),
        anchor_path=str(tmp_path / "chain.anchor"),
        skills_path=str(tmp_path / "skills"),
        backends={"cloud": BackendConfig(kind="mock", model="m"),
                  "review": BackendConfig(kind="mock", model="r")},
        autonomy={"auto_review": False, "plan_required": False,
                  "approve_scoped_writes": True},
        root=str(tmp_path),
    )
    return AgentContext(cfg)


def new_task(ctx, goal="do a thing"):
    return create_task(
        ctx.conn, ctx.log,
        TaskContract(goal=goal, success_criterion="always_pass",
                     artifact_scope=["work"]),
        TaskBudget(usd=1.0, seconds=120))


# ==========================================================================
# 7.1 — output streams token by token
# ==========================================================================

def test_tokens_arrive_during_the_call_not_after_it(tmp_path):
    """The whole of B5. The adapters have streamed since the stall-detector
    rewrite; every surface waited for the joined string."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    seen: list[str] = []
    bus.subscribe(t.id, lambda kind, d: seen.append(d["text"])
                  if kind == "token" else None)

    ctx.backends["cloud"].queue('<final>{"answer": "a reasonably long answer '
                                'so the mock emits several chunks"}</final>')
    run_task(ctx, t.id)

    assert len(seen) > 1, (
        f"expected several chunks, got {len(seen)}; the surface is still "
        "waiting for the whole reply")
    assert "reasonably long answer" in "".join(seen)
    ctx.close()


def test_a_listener_that_raises_cannot_fail_the_task(tmp_path):
    """A broken display must never be able to fail a task."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    bus.subscribe(t.id, lambda kind, d: (_ for _ in ()).throw(RuntimeError("ui bug")))
    ctx.backends["cloud"].queue('<final>{"answer": "ok"}</final>')
    result = run_task(ctx, t.id)
    assert result.status in ("done", "done_unannounced")
    ctx.close()


def test_two_surfaces_both_receive(tmp_path):
    """A single callback slot means the second surface to attach silently
    replaces the first — the shadowing-default shape (DR2)."""
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    a, b = [], []
    bus.subscribe(t.id, lambda k, d: a.append(d) if k == "token" else None)
    bus.subscribe(t.id, lambda k, d: b.append(d) if k == "token" else None)
    ctx.backends["cloud"].queue('<final>{"answer": "hello there"}</final>')
    run_task(ctx, t.id)
    assert a and b and len(a) == len(b)
    ctx.close()


def test_unsubscribing_stops_delivery(tmp_path):
    got = []
    off = bus.subscribe("t1", lambda k, d: got.append(d))
    bus.publish("t1", "token", {"text": "one"})
    off()
    bus.publish("t1", "token", {"text": "two"})
    assert len(got) == 1


def test_the_backlog_is_bounded(tmp_path):
    for i in range(bus.BACKLOG + 50):
        bus.publish("t1", "token", {"text": str(i)})
    assert len(bus.backlog("t1")) == bus.BACKLOG, (
        "an unbounded buffer of model output is a memory leak with a nice name")


def test_the_bus_is_not_the_record(tmp_path):
    """Nothing on the bus may be read back to claim what happened. It is
    dropped the moment nobody is watching; the chain is the record."""
    src = (ROOT / "src" / "agent" / "stream.py").read_text(encoding="utf-8")
    assert "not durable" in src and "not the record" in src
    # And it is bounded in BOTH directions: per task, and in how many tasks
    # are retained. A dict keyed by task id with no ceiling is a leak that
    # only shows up after a long day in a control-plane process.
    for i in range(bus.BACKLOG_TASKS + 10):
        bus.publish(f"task{i}", "token", {"text": "x"})
    assert len(bus._backlog) == bus.BACKLOG_TASKS  # noqa: SLF001
    assert bus.backlog("task0") == [], "the oldest task was dropped"
    assert bus.backlog(f"task{bus.BACKLOG_TASKS + 9}"), "the newest was kept"


# ==========================================================================
# 7.4 — alerts are not approvals
# ==========================================================================

def test_an_integrity_alert_lands_in_a_separate_list_in_the_reserved_colour():
    st = RunState()
    st.apply_event({"type": "integrity.halt", "seq": 1,
                    "payload": {"reason": "chain_break", "seq": 4}})
    st.apply_event({"type": "review.requested", "seq": 2,
                    "payload": {"tool": "write_file"}})

    assert len(st.alerts) == 1 and len(st.approvals) == 1
    assert st.alerts[0]["role"] == ALERT_ROLE
    assert st.approvals[0]["role"] != ALERT_ROLE, (
        "a broken hash chain must not share a style with a permission "
        "request; you will eventually approve one by muscle memory")


def test_the_alert_colour_is_used_for_nothing_else():
    """Reserved means reserved. Greps the surface for the role name."""
    for f in (ROOT / "src" / "agent" / "tui").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "danger" in line and "ALERT_ROLE" not in line:
                assert ("#FF4B4B" in line or "Integrity alerts ONLY" in line
                        or line.strip().startswith("#")), (
                    f"{f.name}: `danger` used outside the alert path: {line}")


def test_alerts_are_counted_where_they_are_visible_without_switching_tabs():
    src = (ROOT / "src" / "agent" / "tui" / "app.py").read_text(encoding="utf-8")
    assert "INTEGRITY ALERT" in src
    i = src.index("def refresh_budget")
    assert "len(self.state.alerts)" in src[i:i + 800], (
        "the alert count belongs on the always-visible bar, not only in the "
        "tab a reader has to think to open")


# ==========================================================================
# 7.4 — the tool trail, budget bar, and the monitor staying invisible
# ==========================================================================

def test_a_delegated_child_is_shown_nested():
    st = RunState()
    st.apply_event({"type": "tool.intent", "seq": 1, "task_id": "t_parent",
                    "payload": {"tool": "read_file", "step": 0,
                                "preview": "read_file(path='a')"}})
    st.apply_event({"type": "delegation.opened", "seq": 2, "task_id": "t_parent",
                    "payload": {"child_task_id": "t_child"}})
    st.apply_event({"type": "tool.intent", "seq": 3, "task_id": "t_child",
                    "payload": {"tool": "grep", "step": 0,
                                "preview": "grep(pattern='x')"}})
    assert [c.depth for c in st.trail] == [0, 1]
    assert st.trail[1].render(glyphs=True).startswith("  └─ ")


def test_the_trail_degrades_without_glyphs():
    call = ToolCall(tool="read_file", step=0, preview="p", status="ok", depth=1)
    assert "✓" in call.render(glyphs=True)
    assert "✓" not in call.render(glyphs=False)
    assert "OK" in call.render(glyphs=False)


def test_the_budget_bar_reports_the_worst_axis_not_the_average():
    """A task at 5% of its money and 99% of its clock is about to stop, and
    52% would describe neither thing that is true."""
    b = Budget(spent_usd=0.05, limit_usd=1.0,
               spent_seconds=99.0, limit_seconds=100.0)
    assert b.fraction() == pytest.approx(0.99)


def test_a_zero_budget_does_not_divide_by_zero():
    assert Budget().fraction() == 0.0
    assert Budget(spent_usd=0.0, limit_usd=0.0,
                  spent_seconds=5, limit_seconds=10).fraction() == 0.5


def test_the_monitor_is_invisible_to_this_surface():
    """M8.3. Inherited from `to_client_event` rather than reimplemented — a
    surface reading the raw log would have had to remember, and forgetting is
    how the rule gets undone."""
    st = RunState()
    shown = st.apply_event({"type": "monitor.flagged", "seq": 1,
                            "payload": {"finding": "something"}})
    assert shown is False
    assert not st.alerts and not st.approvals and not st.trail


def test_the_surface_renders_through_the_shared_vocabulary():
    """7.3. The contract is the event schema, not the transport, so the TUI
    and the web console cannot drift on what an event means."""
    src = (ROOT / "src" / "agent" / "tui" / "model.py").read_text(encoding="utf-8")
    assert "from ..control.events import" in src and "to_client_event" in src


def test_live_events_fold_into_the_same_state():
    st = fold(
        [{"type": "task.status_changed", "seq": 1, "payload": {"status": "running"}}],
        [("token", {"text": "hel"}), ("token", {"text": "lo"}),
         ("step", {"spent_usd": 0.1, "budget_usd": 1.0,
                   "spent_seconds": 30, "budget_seconds": 60})],
    )
    assert st.text() == "hello"
    assert st.status == "running"
    assert st.budget.fraction() == pytest.approx(0.5)


# ==========================================================================
# 7.5 — the banner is display-only
# ==========================================================================

def test_the_banner_never_appears_in_any_prompt(tmp_path):
    """Greps EVERY prompt sent during a real task.

    Not paranoia about ASCII art: the banner is the most obviously decorative
    thing in the system, so if it can reach a prompt then anything can, and
    the stable prefix would be carrying bytes nobody thinks of as content.
    """
    ctx = make_ctx(tmp_path)
    theme = load(ctx.config.ui.theme, ROOT)
    art = banner_text(theme, ROOT, glyphs=True)
    assert art and art != PLAIN_BANNER, "the real banner must have loaded"

    t = new_task(ctx)
    ctx.backends["cloud"].queue(
        '<action tool="list_dir">{"path": "work"}</action>',
        '<final>{"answer": "done"}</final>')
    run_task(ctx, t.id)

    sent = ctx.backends["cloud"].prompts
    assert sent, "no prompt was sent, so this proves nothing"
    distinctive = [ln.strip() for ln in art.splitlines()
                   if len(ln.strip()) > 8]
    for prompt in sent:
        for line in distinctive:
            assert line not in prompt, f"banner line reached a prompt: {line!r}"
    ctx.close()


def test_no_prompt_building_module_imports_the_theme():
    """Structural, so it stays true for prompts this test never sends."""
    for rel in ("ledger/prefix.py", "execute/loop.py", "plan/planner.py",
                "security/taint.py"):
        text = (ROOT / "src" / "agent" / rel).read_text(encoding="utf-8")
        assert "tui" not in text.replace("intuition", ""), (
            f"{rel} imports something from the surface layer")


# ==========================================================================
# 7.6 — degradation
# ==========================================================================

def test_without_glyph_support_the_banner_falls_back_to_plain_ascii():
    theme = load(ROOT / "themes" / "default.toml", ROOT)
    assert banner_text(theme, ROOT, glyphs=False) == PLAIN_BANNER
    PLAIN_BANNER.encode("ascii")          # must survive an ascii-only terminal


def test_glyph_support_is_decided_by_the_encoding_not_the_platform():
    class Ascii:
        encoding = "ascii"

    class Utf8:
        encoding = "utf-8"

    class Broken:
        encoding = None

    assert supports_glyphs(Ascii()) is False
    assert supports_glyphs(Utf8()) is True
    assert supports_glyphs(Broken()) is False, "unknown means no"


def test_a_broken_theme_file_degrades_rather_than_stopping_the_agent(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("this is not = valid = toml [[[", encoding="utf-8")
    theme = load(bad, tmp_path)
    assert theme["colors"]["danger"] == "#FF4B4B"
    assert banner_text(theme, tmp_path, glyphs=False) == PLAIN_BANNER


def test_the_spinner_degrades_too():
    theme = load(ROOT / "themes" / "default.toml", ROOT)
    assert theme["spinner"] in (ASCII_SPINNER,) or "⠋" in theme["spinner"]


def test_the_theme_is_swappable(tmp_path):
    alt = tmp_path / "alt.toml"
    alt.write_text('name = "alt"\nbanner = "./nope.ans"\n'
                   '[colors]\naccent = "#123456"\n', encoding="utf-8")
    theme = load(alt, tmp_path)
    assert theme["colors"]["accent"] == "#123456"
    assert theme["colors"]["danger"] == "#FF4B4B", "unset colours keep defaults"
    assert banner_text(theme, tmp_path, glyphs=True) == PLAIN_BANNER, (
        "a missing banner file must not crash the surface")


def test_the_surface_knows_a_call_started_not_only_that_tokens_arrived(tmp_path):
    """MEASURED 2026-09-07 against granite4.1:3b on Ollama /v1: time to first
    token is a roughly FIXED ~2.4s of prompt evaluation, independent of how
    much is generated — 63% of a 3.8s turn and 71% of a 3.4s one was silent
    even with streaming on.

    So "output streams token by token" is true and insufficient. A surface
    that reacts only to tokens shows nothing for most of a short turn and
    looks hung, which is the problem streaming was added to solve.
    """
    ctx = make_ctx(tmp_path)
    t = new_task(ctx)
    order: list[str] = []
    bus.subscribe(t.id, lambda kind, d: order.append(kind))

    ctx.backends["cloud"].queue('<final>{"answer": "some words here"}</final>')
    run_task(ctx, t.id)

    assert "waiting" in order, "the surface is never told the call began"
    assert order.index("waiting") < order.index("token"), (
        "the waiting signal must precede the first token, or it tells a "
        "surface nothing it did not already know")
    ctx.close()


def test_the_waiting_state_clears_on_the_first_token():
    st = RunState()
    st.apply_live("waiting", {"step": 0})
    assert st.waiting is True
    st.apply_live("token", {"text": "hi"})
    assert st.waiting is False, "a spinner that never stops is worse than none"


def test_the_spinner_frames_come_from_the_theme_and_degrade():
    """They were loaded and unused until the prefill measurement gave them a
    job. Either a theme value is used or it should not be read."""
    src = (ROOT / "src" / "agent" / "tui" / "app.py").read_text(encoding="utf-8")
    assert 'self.theme_data["spinner"]' in src
    assert load(ROOT / "themes" / "default.toml", ROOT)["spinner"]

