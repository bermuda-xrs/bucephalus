"""The Textual app. Thin on purpose — the rules live in `model.py`.

Five things a person needs while a task runs, in the order they need them:
submit and watch, see what it touched, see what it has spent, decide what it
is waiting on, and be told when something is wrong. Tabs rather than five
screens, because switching screens to check a budget is how you stop checking
the budget.

ALERTS ARE NOT APPROVALS. They live in a separate tab, in a colour used
nowhere else, and they are counted in the tab label so the count is visible
without switching to it. A broken hash chain arriving in the same list as
"may I write this file" is how you approve one by muscle memory.
"""

from __future__ import annotations

import threading

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
)

from .. import stream as bus
from .model import ALERT_ROLE, RunState
from .theme import banner_text, load, supports_glyphs


class AlphaTUI(App):
    """Reads the kernel in-process; renders through the shared vocabulary."""

    CSS = """
    Screen { layout: vertical; }
    #banner { height: auto; color: $accent; }
    #budget { height: 1; dock: bottom; }
    #alerts { color: red; text-style: bold; }
    RichLog { height: 1fr; }
    """

    BINDINGS = [("q", "quit", "quit"), ("r", "run", "run task")]

    def __init__(self, ctx, task_id: str | None = None):
        super().__init__()
        self.ctx = ctx
        self.task_id = task_id
        self.state = RunState()
        self.glyphs = supports_glyphs()
        self.theme_data = load(ctx.config.ui.theme, ctx.config.root)
        self._unsubscribe = None
        self._tick = 0

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        # DISPLAY ONLY. See tui/theme.py — no prompt-building code imports
        # this module, and a test greps every prompt sent to prove it.
        yield Static(banner_text(self.theme_data, self.ctx.config.root,
                                 self.glyphs), id="banner")
        with TabbedContent(initial="run"):
            with TabPane("run", id="run"):
                with Vertical():
                    with Horizontal():
                        yield Input(placeholder="what should it do?", id="goal")
                        yield Button("start", id="start", variant="primary")
                    yield RichLog(id="output", wrap=True, markup=False)
            with TabPane("tools", id="tools"):
                yield RichLog(id="trail", markup=False)
            with TabPane("approvals", id="approvals"):
                yield RichLog(id="queue", markup=False)
            with TabPane("!! alerts", id="alerts_tab"):
                yield RichLog(id="alerts", markup=False)
        yield Static("", id="budget")
        yield Footer()

    def on_mount(self) -> None:
        # Only to animate the spinner during prompt evaluation. Slow on
        # purpose: this is a progress hint, not a frame budget.
        self.set_interval(0.12, self._spin)
        self.refresh_budget()
        if self.task_id:
            self.attach(self.task_id)

    # -- wiring -----------------------------------------------------------

    def attach(self, task_id: str) -> None:
        """Replay the chain, then tail the bus."""
        self.task_id = task_id
        for e in self.ctx.log.events(task_id):
            self.state.apply_event(e)
        for kind, data in bus.backlog(task_id):
            self.state.apply_live(kind, data)
        self.redraw()

        if self._unsubscribe is not None:
            self._unsubscribe()
        self._unsubscribe = bus.subscribe(task_id, self._on_live)

    def _on_live(self, kind: str, data: dict) -> None:
        # The bus fires on the worker thread. Textual widgets are not thread
        # safe, so hop back rather than touching them here — a display that
        # corrupts under concurrency is the same class of bug Phase 10 spent
        # its time on.
        self.call_from_thread(self._apply_live, kind, data)

    def _apply_live(self, kind: str, data: dict) -> None:
        self.state.apply_live(kind, data)
        if kind == "token":
            self.query_one("#output", RichLog).write(data.get("text", ""))
        else:
            self.refresh_budget()

    def redraw(self) -> None:
        out = self.query_one("#output", RichLog)
        out.clear()
        if self.state.text():
            out.write(self.state.text())

        trail = self.query_one("#trail", RichLog)
        trail.clear()
        for call in self.state.trail:
            trail.write(call.render(self.glyphs))

        queue = self.query_one("#queue", RichLog)
        queue.clear()
        for a in self.ctx.pending_approvals(self.task_id):
            queue.write(f"{a['id']}  {a['tool']}  {a['preview']}")

        alerts = self.query_one("#alerts", RichLog)
        alerts.clear()
        for al in self.state.alerts:
            assert al["role"] == ALERT_ROLE
            alerts.write(f"!! {al['event']}  {al['data']}")
        self.refresh_budget()

    def _spin(self) -> None:
        if self.state.waiting:
            self._tick += 1
            self.refresh_budget()

    def refresh_budget(self) -> None:
        b = self.state.budget
        bar_w = 24
        filled = min(bar_w, int(b.fraction() * bar_w))
        glyph = "█" if self.glyphs else "#"
        empty = "░" if self.glyphs else "."
        frames = self.theme_data["spinner"]
        spin = frames[self._tick % len(frames)] + " " if self.state.waiting else ""
        n_alerts = len(self.state.alerts)
        flag = f"   !! {n_alerts} INTEGRITY ALERT(S)" if n_alerts else ""
        self.query_one("#budget", Static).update(
            f"{spin}[{glyph * filled}{empty * (bar_w - filled)}] {b.render()}"
            f"   status={self.state.status}{flag}")

    # -- actions ----------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.action_run()

    def action_run(self) -> None:
        goal = self.query_one("#goal", Input).value.strip()
        if not goal:
            return
        from ..kernel.tasks import create_task
        from ..kernel.types import Budget as TaskBudget
        from ..kernel.types import TaskContract

        task = create_task(
            self.ctx.conn, self.ctx.log,
            TaskContract(goal=goal, success_criterion="always_pass",
                         artifact_scope=["work"]),
            TaskBudget(usd=self.ctx.config.limits.default_task_budget_usd,
                       seconds=self.ctx.config.limits.default_task_budget_seconds))
        self.attach(task.id)

        def work() -> None:
            from ..execute.loop import run_task
            try:
                run_task(self.ctx, task.id)
            except Exception as e:  # noqa: BLE001 - surfaced, never swallowed
                bus.publish(task.id, "token",
                            {"text": f"{chr(10)}[{type(e).__name__}] {e}{chr(10)}"})

        threading.Thread(target=work, daemon=True, name="tui-run").start()

    def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()


def run(ctx, task_id: str | None = None) -> None:
    AlphaTUI(ctx, task_id).run()
