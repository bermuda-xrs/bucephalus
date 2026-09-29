"""The Textual app. Thin on purpose - the rules live in `model.py`.

Iteration 3 (4.5): the console's layout in terminal form, and its flow. The
TUI used to create a task and run it at once; it is plan-first now, like
every surface: a message drafts a plan, `s` signs it and only then does
anything run, `b` sends it back with a reason. Conversations down the side,
the thread with the live plan panel under the message that asked for it, a
tool trail tree with delegated children nested, and one status bar with the
model, the context meter against its compaction ceiling, the budget and
the integrity count. Ctrl+K is Textual's command palette.

ALERTS ARE NOT APPROVALS. They live in a separate tab, in a colour used
nowhere else, and they are counted on the status bar so the count is visible
without switching to it. A broken hash chain arriving in the same list as
"may I write this file" is how you approve one by muscle memory.
"""

from __future__ import annotations

import threading

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    Tree,
)

from .. import stream as bus
from .model import ALERT_ROLE, RunState
from .theme import banner_text, load, supports_glyphs


class AlphaTUI(App):
    """Reads the kernel in-process; renders through the shared vocabulary."""

    TITLE = "alpha"
    COMMAND_PALETTE_BINDING = "ctrl+k"
    CSS = """
    Screen { layout: vertical; }
    #banner { height: auto; color: $accent; }
    #side { width: 32; border-right: solid $panel-lighten-2; }
    #side Label.head { color: $text-muted; padding: 1 1 0 1; }
    #needs { height: auto; max-height: 12; }
    #main { width: 1fr; }
    #main TabbedContent { height: 1fr; }
    #thread { height: auto; padding: 0 1; }
    #plan { height: auto; padding: 0 1; border: round $accent; margin: 0 1; }
    #output { height: 12; border: round $panel-lighten-2; margin: 0 1; }
    #trail { height: auto; max-height: 14; margin: 0 1; }
    #ask { height: 3; }
    #goal { width: 1fr; }
    #budget { height: 1; background: $panel; }
    #alerts { color: red; text-style: bold; }
    """

    BINDINGS = [("q", "quit", "quit"), ("s", "sign", "sign and run"),
                ("b", "send_back", "send back"), ("a", "allow", "allow once"),
                ("d", "deny", "refuse"), ("n", "new", "new conversation")]

    def __init__(self, ctx, task_id: str | None = None):
        super().__init__()
        self.ctx = ctx
        self.task_id = task_id
        self.conv_id: str | None = None
        self.plan = None                     # the drafted plan awaiting a signature
        self.mode = "ask"                    # or "sendback": the input is the reason
        self.state = RunState()
        self.glyphs = supports_glyphs()
        self.theme_data = load(ctx.config.ui.theme, ctx.config.root)
        self._unsubscribe = None
        self._tick = 0
        self._model = self._executor_model()

    def _executor_model(self) -> str:
        b = self.ctx.backends.get("cloud") or next(iter(self.ctx.backends.values()), None)
        return b.cfg.model if b else "?"

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        # DISPLAY ONLY. See tui/theme.py - no prompt-building code imports
        # this module, and a test greps every prompt sent to prove it.
        yield Static(banner_text(self.theme_data, self.ctx.config.root,
                                 self.glyphs), id="banner")
        with Horizontal():
            with Vertical(id="side"):
                yield Label("Needs you", classes="head")
                yield ListView(id="needs")
                yield Label("Conversations  (n: new)", classes="head")
                yield ListView(id="convs")
            with Vertical(id="main"):
                with TabbedContent(initial="run"):
                    with TabPane("thread", id="run"):
                        with VerticalScroll():
                            yield Static("", id="thread")
                            yield Static("", id="plan")
                            yield RichLog(id="output", wrap=True, markup=False)
                            yield Tree("tool trail", id="trail")
                    with TabPane("approvals", id="approvals"):
                        yield RichLog(id="queue", markup=False)
                    with TabPane("!! alerts", id="alerts_tab"):
                        yield RichLog(id="alerts", markup=False)
                with Horizontal(id="ask"):
                    yield Input(placeholder="what should it do? Enter drafts a plan; nothing runs until you sign",
                                id="goal")
                    yield Button("Draft a plan", id="start", variant="primary")
        yield Static("", id="budget")
        yield Footer()

    def on_mount(self) -> None:
        # Only to animate the spinner during prompt evaluation. Slow on
        # purpose: this is a progress hint, not a frame budget.
        self.set_interval(0.12, self._spin)
        self.set_interval(5.0, self.refresh_side)
        self.refresh_side()
        self.refresh_budget()
        self.query_one("#plan", Static).display = False
        self.query_one("#goal", Input).focus()
        if self.task_id:
            self.attach(self.task_id)

    # -- the side -----------------------------------------------------------

    def refresh_side(self) -> None:
        from ..control import conversations as conv

        needs = self.query_one("#needs", ListView)
        needs.clear()
        for a in self.ctx.pending_approvals():
            needs.append(ListItem(Label(f"wants {a['tool']}: parked"), name=f"task:{a['task_id']}"))
        convs = self.query_one("#convs", ListView)
        convs.clear()
        for c in conv.listing(self.ctx.conn):
            mark = f" ({c['needs_you']} for you)" if c["needs_you"] else ""
            convs.append(ListItem(Label(c["title"][:28] + mark), name=f"conv:{c['id']}"))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        kind, _, ident = (event.item.name or "").partition(":")
        if kind == "conv":
            self.open_conversation(ident)
        elif kind == "task":
            self.attach(ident)

    def open_conversation(self, cid: str) -> None:
        from ..control import conversations as conv

        self.conv_id = cid
        th = conv.thread(self.ctx, cid)
        items = th["items"]
        if items:
            self.attach(items[-1]["task_id"])
        self.render_thread(th)

    def render_thread(self, th: dict | None = None) -> None:
        from ..control import conversations as conv

        if th is None and self.conv_id:
            th = conv.thread(self.ctx, self.conv_id)
        t = Text()
        for it in (th or {}).get("items", []):
            t.append("you  ", style="bold")
            t.append(it["goal"] + "\n")
            if it.get("answer"):
                t.append("  answer  ", style="bold green")
                t.append(it["answer"] + "\n")
            elif it["status"] not in ("pending",):
                t.append(f"  {it['status']}\n", style="dim")
        self.query_one("#thread", Static).update(t)

    # -- wiring -----------------------------------------------------------

    def attach(self, task_id: str) -> None:
        """Replay the chain, then tail the bus."""
        from ..plan.planner import load_plan

        self.task_id = task_id
        self.state = RunState()
        for e in self.ctx.log.events(task_id):
            self.state.apply_event(e)
        for kind, data in bus.backlog(task_id):
            self.state.apply_live(kind, data)
        plan = load_plan(self.ctx, task_id)
        row = self.ctx.conn.execute(
            "SELECT status FROM plans WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
            (task_id,)).fetchone()
        self.plan = plan if row and row[0] == "drafted" else None
        self.show_plan(plan, row[0] if row else None)
        self.redraw()

        if self._unsubscribe is not None:
            self._unsubscribe()
        self._unsubscribe = bus.subscribe(task_id, self._on_live)

    def _on_live(self, kind: str, data: dict) -> None:
        # The bus fires on the worker thread. Textual widgets are not thread
        # safe, so hop back rather than touching them here - a display that
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

        tree = self.query_one("#trail", Tree)
        tree.clear()
        tree.root.expand()
        parent = tree.root
        for call in self.state.trail:
            node = (parent if call.depth else tree.root).add_leaf(call.render(self.glyphs))
            if not call.depth:
                parent = node if node.allow_expand else tree.root
        tree.root.label = f"tool trail  ({len(self.state.trail)} calls)"

        queue = self.query_one("#queue", RichLog)
        queue.clear()
        for a in self.ctx.pending_approvals(self.task_id):
            queue.write(f"{a['id']}  {a['tool']}  {a['preview']}   (a: allow once, d: refuse)")

        alerts = self.query_one("#alerts", RichLog)
        alerts.clear()
        for al in self.state.alerts:
            assert al["role"] == ALERT_ROLE
            alerts.write(f"!! {al['event']}  {al['data']}")
        self.refresh_budget()

    def show_plan(self, plan, status: str | None) -> None:
        """The live plan panel, under the message that asked for it."""
        panel = self.query_one("#plan", Static)
        if plan is None:
            panel.display = False
            return
        t = Text()
        head = {"drafted": "Plan for your signature  (s: sign and run, b: send back)",
                "approved": "Signed plan", "rejected": "Sent back"}.get(status or "", "Plan")
        t.append(head + "\n", style="bold")
        t.append(plan.objective + "\n\n", style="bold")
        for label, xs in (("settle first", plan.open_questions),
                          ("it will stop and ask", plan.human_approvals_expected),
                          ("unknown", plan.unknowns)):
            for x in xs:
                t.append(f"  ? {label}: {x}\n", style="yellow")
        for i, s in enumerate(plan.steps, 1):
            mark = "!" if not s.reversible else " "
            t.append(f" {mark}{i:>2}. {s.intent}", style="bold" if not s.reversible else "")
            t.append(f"   [{', '.join(s.tools) or 'no tools'}]\n", style="dim")
            if not s.reversible:
                t.append("       can't be undone once it runs\n", style="bold")
        if not plan.steps:
            t.append("  (no steps)\n", style="dim")
        t.append(f"\n  plan {plan.id[:16]}...", style="dim")
        panel.update(t)
        panel.display = True

    def _spin(self) -> None:
        if self.state.waiting:
            self._tick += 1
            self.refresh_budget()

    def refresh_budget(self) -> None:
        from ..control.panels import context_meter

        b = self.state.budget
        bar_w = 16
        filled = min(bar_w, int(b.fraction() * bar_w))
        glyph = "█" if self.glyphs else "#"
        empty = "░" if self.glyphs else "."
        frames = self.theme_data["spinner"]
        spin = frames[self._tick % len(frames)] + " " if self.state.waiting else ""
        n_alerts = len(self.state.alerts)
        flag = f"   !! {n_alerts} INTEGRITY ALERT(S)" if n_alerts else ""
        m = context_meter(self.ctx, self.task_id)
        cf = min(bar_w, int(bar_w * m["used"] / max(1, m["ceiling"])))
        self.query_one("#budget", Static).update(
            f"{spin}{self._model}   ctx [{glyph * cf}{empty * (bar_w - cf)}] "
            f"{m['used'] // 1000}k/{m['ceiling'] // 1000}k ceiling ({m['window'] // 1000}k window)"
            f"   budget [{glyph * filled}{empty * (bar_w - filled)}] {b.render()}"
            f"   status={self.state.status}{flag}")

    # -- the two touches ----------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "start":
            self.submit(self.query_one("#goal", Input).value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.submit(event.value)

    def submit(self, text: str) -> None:
        text = text.strip()
        goal = self.query_one("#goal", Input)
        goal.value = ""
        if not text:
            return
        if self.mode == "sendback":
            self._send_back(text)
            return
        self.ask(text)

    def ask(self, text: str) -> None:
        """Touch zero: a message becomes a task in this conversation, and a
        plan is drafted for it. Nothing runs."""
        from ..control import conversations as conv

        if self.conv_id is None:
            self.conv_id = conv.create(self.ctx.conn, "")["id"]
        m = conv.add_message(self.ctx, self.conv_id, text, ["work"], None, None)
        self.attach(m["task_id"])
        self.render_thread()
        self.query_one("#plan", Static).update(Text("Drafting a plan. It reads, and changes nothing.",
                                                    style="dim"))
        self.query_one("#plan", Static).display = True
        self.query_one("#trail", Tree).focus()           # keys, not typing, from here
        tid = m["task_id"]
        threading.Thread(target=self._draft, args=(tid,), daemon=True, name="tui-plan").start()

    def _draft(self, tid: str) -> None:
        from ..kernel.tasks import load_task
        from ..plan.planner import draft_plan

        try:
            draft_plan(self.ctx, load_task(self.ctx.conn, tid))
        except Exception as e:  # noqa: BLE001 - surfaced, never swallowed
            self.call_from_thread(self.notify, f"drafting failed: {e}", severity="error")
        self.call_from_thread(self._after_change, tid)

    def _after_change(self, tid: str) -> None:
        if tid == self.task_id:
            self.attach(tid)
        self.render_thread()
        self.refresh_side()

    def action_sign(self) -> None:
        """Touch one: pin the plan's hash, then run."""
        from ..kernel.tasks import load_task
        from ..plan.planner import approve_plan

        if self.plan is None or self.task_id is None:
            self.notify("No drafted plan to sign.")
            return
        task = load_task(self.ctx.conn, self.task_id)
        approve_plan(self.ctx, task, self.plan, [])
        self.plan = None
        self.notify("Signed. It's running.")
        self._run(task.id)

    def action_send_back(self) -> None:
        if self.plan is None:
            self.notify("No drafted plan to send back.")
            return
        self.mode = "sendback"
        goal = self.query_one("#goal", Input)
        goal.placeholder = "Why send it back? Enter records it with the plan"
        goal.focus()

    def _send_back(self, why: str) -> None:
        from ..kernel.tasks import load_task
        from ..plan.planner import reject_plan

        reject_plan(self.ctx, load_task(self.ctx.conn, self.task_id), self.plan, why)
        self.mode = "ask"
        self.query_one("#goal", Input).placeholder = "what should it do? Enter drafts a plan"
        self.notify("Sent back. Write a new message to draft again.")
        self.attach(self.task_id)

    def action_allow(self) -> None:
        self._decide(True)

    def action_deny(self) -> None:
        self._decide(False)

    def _decide(self, approve: bool) -> None:
        # Only an approval of the task ON SCREEN, whose preview the approvals
        # tab shows. Falling back to any task's approval decided - and ran -
        # something nobody was shown (AUDIT-ITERATION-3 S2). Pick the other
        # task from Needs you first.
        pending = self.ctx.pending_approvals(self.task_id) if self.task_id else []
        if not pending:
            self.notify("Nothing on this task is waiting. Open the one under Needs you.")
            return
        a = pending[0]
        self.ctx.resolve_approval(a["id"], approve)
        self.notify("Allowed once. It carries on." if approve else "Refused. It carries on without it.")
        self.attach(a["task_id"])
        self._resume(a["task_id"])

    def _resume(self, tid: str) -> None:
        """A decided, parked task resumes - under the same rule the server's
        /run applies: with plans required, only on a signed plan."""
        from ..kernel.tasks import load_task

        if (self.ctx.config.autonomy.plan_required
                and self.ctx.approved_plan(load_task(self.ctx.conn, tid)) is None):
            self.notify("Decided. It has no signed plan, so it does not resume from here.")
            return
        self._run(tid)

    def action_new(self) -> None:
        self.conv_id = None
        self.task_id = None
        self.plan = None
        self.state = RunState()
        self.query_one("#thread", Static).update("")
        self.query_one("#plan", Static).display = False
        self.redraw()
        self.query_one("#goal", Input).focus()

    def _run(self, tid: str) -> None:
        def work() -> None:
            from ..execute.loop import run_task
            try:
                run_task(self.ctx, tid)
            except Exception as e:  # noqa: BLE001 - surfaced, never swallowed
                bus.publish(tid, "token",
                            {"text": f"{chr(10)}[{type(e).__name__}] {e}{chr(10)}"})
            self.call_from_thread(self._after_change, tid)

        threading.Thread(target=work, daemon=True, name="tui-run").start()

    def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()


def run(ctx, task_id: str | None = None) -> None:
    AlphaTUI(ctx, task_id).run()
