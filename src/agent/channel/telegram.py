"""A4 — the Telegram bot. The strictest surface in the project.

WHY LONG POLLING RATHER THAN A WEBHOOK.

Telegram offers both. A webhook needs a public HTTPS endpoint, which means
binding a non-loopback interface, terminating TLS, and putting a service that
can rewrite the reviewer's policy and start runs on the open internet behind
one bearer token. Long polling makes an OUTBOUND request and waits. It adds
no listening surface, needs no certificate, works behind any NAT, and leaves
8.4 — `ALPHA_ALLOW_REMOTE` gating non-loopback binding — untouched, because
this file never binds anything. There is a test that greps for that.

The cost is a held-open HTTP request and a few seconds of latency. On a
machine where one model turn takes 20-40 seconds, that is not a cost.

WHAT THIS FILE IS NOT ALLOWED TO DECIDE.

Nothing about what a channel task may do. Every restriction — the denied
tools, the untrusted goal, the budget ceiling, the refusal to auto-proceed —
lives on the task's ORIGIN and is enforced where the tool call happens, not
here. This module's only security responsibility is the allowlist, twice: at
receipt and again at approval.

That split is deliberate and it is the lesson of the phase. If the bot
enforced the restrictions, they would hold exactly as long as this process
does, and a parked task waiting for a human spends most of its life not being
run by anything. See `security/origin.py`.

STOP. This has never run against a live bot token. `HttpTransport` is written
and unexercised — the same standing as the Anthropic adapter, and marked the
same way. Everything below has been driven end to end through `FakeTransport`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from ..execute.loop import run_task
from ..kernel.tasks import create_task
from ..kernel.types import Budget, TaskContract
from ..security.origin import TELEGRAM, clamp_budget

API = "https://api.telegram.org"

# What a stranger's message costs us before we drop it. Reading a bounded
# prefix means a 4MB caption cannot be turned into a 4MB hash input, and the
# hash is only there to recognise a repeat.
MAX_RECORDED_BODY = 4096

# How many drop records to keep. Unauthenticated senders write these, so the
# table is the one place in the database an outsider can cause growth. Bounded
# hard, oldest first: the recent ones are the ones anybody looks at.
MAX_DROPS = 500

# How many claimed update ids to remember. Telegram only redelivers updates it
# has not seen confirmed - at most one batch - so this needs to cover recent
# history, not all of it. Bounded because every update, a stranger's included,
# is claimed here.
MAX_SEEN_UPDATES = 1000


class Transport(Protocol):
    """The four Telegram calls this needs. A protocol so the tests can drive
    the whole bot without a token, a network, or a bot."""

    def get_updates(self, offset: int, timeout: int) -> list[dict]: ...
    def send_message(self, chat_id, text: str, buttons: list | None = None) -> dict: ...
    def answer_callback(self, callback_id: str, text: str) -> dict: ...


class HttpTransport:
    """UNVERIFIED — never run against a live bot token. See the STOP above.

    Stdlib only. `urllib` is enough for four JSON POSTs, and a dependency
    added for four JSON POSTs is a dependency to keep updated forever.
    """

    def __init__(self, token: str, base: str = API):
        if not token:
            raise ValueError(
                "no bot token. Set the environment variable named by "
                "channels.telegram.token_env; the token must not be written "
                "into config.yaml."
            )
        self._token = token
        self._base = base

    def _call(self, method: str, payload: dict, timeout: float = 60.0) -> dict:
        # The token is in the URL because that is Telegram's API shape. It is
        # therefore never in a log line here: no request logging in this
        # module, and errors are re-raised with the METHOD name only.
        url = f"{self._base}/bot{self._token}/{method}"
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise TransportError(f"{method} failed: {e.reason}") from None
        if not body.get("ok"):
            raise TransportError(f"{method} refused: {body.get('description', '?')}")
        return body.get("result", {})

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        # `timeout + 10` on the socket: the server holds the request for
        # `timeout` seconds by design, so a socket timeout equal to it would
        # make every idle poll look like a network failure.
        return self._call("getUpdates",
                          {"offset": offset, "timeout": timeout},
                          timeout=timeout + 10) or []

    def send_message(self, chat_id, text: str, buttons: list | None = None) -> dict:
        # NO parse_mode. The text can contain a model's output, which can
        # contain whatever a fetched web page contained. Plain text renders an
        # asterisk as an asterisk; Markdown mode turns attacker punctuation
        # into formatting, and a malformed entity into a delivery failure.
        payload: dict = {"chat_id": chat_id, "text": text[:4000]}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        return self._call("sendMessage", payload)

    def answer_callback(self, callback_id: str, text: str) -> dict:
        return self._call("answerCallbackQuery",
                          {"callback_query_id": callback_id, "text": text[:200]})


class TransportError(RuntimeError):
    pass


@dataclass
class FakeTransport:
    """Records what would have been sent; replays canned updates."""

    updates: list[dict] = field(default_factory=list)
    sent: list[dict] = field(default_factory=list)
    answered: list[dict] = field(default_factory=list)

    def get_updates(self, offset: int, timeout: int) -> list[dict]:
        out = [u for u in self.updates if u.get("update_id", 0) >= offset]
        return out

    def send_message(self, chat_id, text: str, buttons: list | None = None) -> dict:
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": buttons})
        return {"message_id": len(self.sent)}

    def answer_callback(self, callback_id: str, text: str) -> dict:
        self.answered.append({"id": callback_id, "text": text})
        return {}


def record_drop(conn, channel: str, sender: str, reason: str, body: str) -> None:
    """A message from someone not on the allowlist. Evidence, not a queue.

    Nothing here creates a task, so there is nothing for a human to approve
    later by mistake — which is the difference between "dropped" and "held".
    """
    digest = hashlib.sha256(body[:MAX_RECORDED_BODY].encode("utf-8")).hexdigest()
    conn.execute(
        """INSERT INTO channel_drops(channel, sender, reason, body_sha256,
                                     body_len, created_at)
           VALUES (?,?,?,?,?,?)""",
        (channel, str(sender), reason, digest, len(body), time.time()),
    )
    conn.execute(
        """DELETE FROM channel_drops WHERE id NOT IN
           (SELECT id FROM channel_drops ORDER BY id DESC LIMIT ?)""",
        (MAX_DROPS,),
    )
    conn.commit()


def recent_drops(conn, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """SELECT channel, sender, reason, body_sha256, body_len, created_at
           FROM channel_drops ORDER BY id DESC LIMIT ?""", (limit,)
    ).fetchall()
    return [{"channel": r[0], "sender": r[1], "reason": r[2],
             "body_sha256": r[3], "body_len": r[4], "created_at": r[5]}
            for r in rows]


class TelegramBot:
    """One process, one database, one task at a time."""

    def __init__(self, ctx, transport: Transport, cfg=None):
        self.ctx = ctx
        self.tx = transport
        self.cfg = cfg or ctx.config.channels.telegram
        # FROM THE TABLE. In memory, a restart polled from 0 and Telegram
        # redelivered everything it had not seen confirmed.
        self.offset = self._resume_offset()

    def _resume_offset(self) -> int:
        row = self.ctx.conn.execute(
            "SELECT MAX(update_id) FROM channel_updates WHERE channel=?",
            (TELEGRAM,)).fetchone()
        return (row[0] + 1) if row and row[0] is not None else 0

    def _claim(self, update_id: int) -> bool:
        """True the first time an update is seen, False on a redelivery.

        CLAIM THEN HANDLE, so the failure is at-most-once: a crash between the
        claim and the handler loses that one message, and its sender re-sends.
        The other order turns a crash during a 40-second run - the long window
        in `serve_forever` - into a second task from one message, and a task
        can have side effects where a lost message has none.
        """
        cur = self.ctx.conn.execute(
            """INSERT OR IGNORE INTO channel_updates(channel, update_id, seen_at)
               VALUES (?,?,?)""", (TELEGRAM, int(update_id), time.time()))
        claimed = cur.rowcount == 1
        self.ctx.conn.execute(
            """DELETE FROM channel_updates WHERE channel=? AND rowid NOT IN
               (SELECT rowid FROM channel_updates WHERE channel=?
                ORDER BY update_id DESC LIMIT ?)""",
            (TELEGRAM, TELEGRAM, MAX_SEEN_UPDATES))
        self.ctx.conn.commit()
        return claimed

    # -- routing ------------------------------------------------------------

    def _bind(self, task_id: str, chat_id) -> None:
        self.ctx.conn.execute(
            """INSERT OR REPLACE INTO channel_routes(task_id, channel, chat_id,
                                                     created_at)
               VALUES (?,?,?,?)""",
            (task_id, TELEGRAM, str(chat_id), time.time()))
        self.ctx.conn.commit()

    def _chat(self, task_id: str):
        """Where this task's replies go. FROM THE TABLE, not from memory.

        Held in a dict this fails closed rather than open — the approval stays
        pending — but it still fails: the human is never asked, and a task
        nobody was asked about waits forever. A channel task parks far more
        often than a local one, and the point of parking is that waiting costs
        nothing, so the wait routinely outlives the process that started it.
        The same argument as `tasks.origin`, applied to routing.
        """
        row = self.ctx.conn.execute(
            "SELECT chat_id FROM channel_routes WHERE task_id=?",
            (task_id,)).fetchone()
        return row[0] if row else None

    # -- the allowlist, which is the only rule this file owns ---------------

    def allowed(self, sender) -> bool:
        """EMPTY LIST MEANS NOBODY.

        Written as an explicit membership test against a list of strings so
        the empty case falls out as False rather than being special-cased —
        the version of this that reads `if allowed and sender not in allowed`
        is the one that hands the bot to the internet the day someone enables
        the channel before filling in the list.
        """
        return str(sender) in {str(x) for x in self.cfg.allowed_senders}

    # -- inbound ------------------------------------------------------------

    def handle_update(self, update: dict) -> dict:
        if "callback_query" in update:
            return self._handle_callback(update["callback_query"])
        if "message" in update:
            return self._handle_message(update["message"])
        return {"action": "ignored", "why": "no message or callback"}

    def _handle_message(self, msg: dict) -> dict:
        sender = (msg.get("from") or {}).get("id", "")
        chat = (msg.get("chat") or {}).get("id", sender)
        text = msg.get("text") or msg.get("caption") or ""

        if not self.allowed(sender):
            # DROPPED, NOT QUEUED, and NOT ANSWERED. A bot that replies "you
            # are not authorised" tells anyone sweeping user ids that this
            # account is a live bot with an allowlist, which is one bit more
            # than silence gives them.
            record_drop(self.ctx.conn, TELEGRAM, sender, "sender not allowlisted", text)
            return {"action": "dropped", "sender": str(sender)}

        if not text.strip():
            return {"action": "ignored", "why": "empty message"}

        contract = TaskContract(
            goal=text,
            success_criterion="always_pass",
            artifact_scope=list(self.cfg.artifact_scope),
        )
        limits = self.ctx.config.limits
        budget = clamp_budget(
            Budget(usd=limits.default_task_budget_usd,
                   seconds=limits.default_task_budget_seconds),
            limits, TELEGRAM,
        )
        task = create_task(self.ctx.conn, self.ctx.log, contract, budget,
                           origin=TELEGRAM)
        self._bind(task.id, chat)
        self.tx.send_message(
            chat,
            f"started {task.id}\n"
            f"budget ${budget.usd:.2f} / {budget.seconds}s\n"
            f"restricted: {', '.join(sorted(self.ctx.tool_policy(task.id).denied))}",
        )
        return {"action": "created", "task_id": task.id, "chat_id": chat}

    # -- running ------------------------------------------------------------

    def run(self, task_id: str) -> dict:
        """Run a task to completion, a park, or a failure, and report back.

        A PARK IS A RETURN VALUE, NOT AN EXCEPTION. `TaskPaused` is raised
        deep in the gate and caught by `run_task`, which converts it into a
        `TaskResult` with `status="paused"` and an `approval_id`. Written the
        obvious way — `except TaskPaused` around the call — this method would
        have been correct-looking and never once entered: the handler that
        sends the approval buttons would have been unreachable, and the bot
        would have reported "paused" with no way to unpause it.

        That is C34's shape one phase later — a branch that reads as wired and
        cannot execute. Caught by reading `run_task`'s handlers rather than
        trusting its signature.
        """
        chat = self._chat(task_id)
        try:
            result = run_task(self.ctx, task_id)
        except Exception as e:  # noqa: BLE001 — reported, never swallowed
            if chat is not None:
                self.tx.send_message(chat, f"{task_id} failed: {type(e).__name__}: {e}")
            return {"action": "failed", "error": f"{type(e).__name__}: {e}"}
        if result.status == "paused" and result.approval_id:
            self._ask(task_id, result.approval_id)
            return {"action": "parked", "approval_id": result.approval_id}
        if chat is not None:
            self.tx.send_message(chat, f"{task_id} {result.status}\n\n{result.answer or ''}")
        return {"action": "finished", "status": result.status}

    def _ask(self, task_id: str, approval_id: str) -> None:
        """The same information the TUI shows, plus two buttons."""
        chat = self._chat(task_id)
        if chat is None:
            return
        pending = [a for a in self.ctx.pending_approvals(task_id)
                   if a["id"] == approval_id]
        p = pending[0] if pending else {"tool": "?", "preview": "", "reason": ""}
        self.tx.send_message(
            chat,
            f"approval needed for {task_id}\n"
            f"tool: {p['tool']}\n"
            f"{p['preview']}\n"
            f"why: {p['reason']}",
            buttons=[[
                {"text": "approve", "callback_data": f"approve:{approval_id}"},
                {"text": "deny", "callback_data": f"deny:{approval_id}"},
            ]],
        )

    # -- approval over the channel ------------------------------------------

    def _handle_callback(self, cb: dict) -> dict:
        sender = (cb.get("from") or {}).get("id", "")
        data = cb.get("data") or ""
        cb_id = cb.get("id", "")

        # A4.3 — RE-VERIFY AT DECISION TIME, not only at receipt. The button
        # lives in a chat message and the message outlives the allowlist: a
        # sender removed this morning can still see, and still press, a button
        # sent yesterday. Checking only at receipt would make removal take
        # effect for new requests and not for pending ones, which is the wrong
        # way round — the pending ones are the ones with an action attached.
        if not self.allowed(sender):
            record_drop(self.ctx.conn, TELEGRAM, sender,
                        "callback from non-allowlisted sender", data)
            self.tx.answer_callback(cb_id, "not authorised")
            return {"action": "refused", "why": "sender not allowlisted"}

        decision, _, approval_id = data.partition(":")
        if decision not in ("approve", "deny") or not approval_id:
            return {"action": "ignored", "why": "unparseable callback"}

        try:
            out = self.ctx.resolve_approval(approval_id, decision == "approve")
        except KeyError:
            self.tx.answer_callback(cb_id, "no such approval")
            return {"action": "ignored", "why": "no such approval"}

        task_id = out["task_id"]
        if out.get("already_decided"):
            # The button is still on screen because a chat message is
            # permanent. The decision is not re-openable.
            self.ctx.log.append(task_id, "channel.refused",
                                {"approval_id": approval_id, "sender": str(sender),
                                 "why": "already decided",
                                 "standing_decision": out.get("decision")})
            self.tx.answer_callback(cb_id, f"already {out.get('decision')}")
            return {"action": "already_decided", "decision": out.get("decision")}

        self.ctx.log.append(task_id, "channel.approved_by",
                            {"approval_id": approval_id, "sender": str(sender),
                             "decision": decision, "channel": TELEGRAM})
        from ..kernel.integrity import checkpoint
        checkpoint(self.ctx.log, self.ctx.anchor, task_id)

        self.tx.answer_callback(cb_id, decision + "d")
        if out["approved"]:
            self.run(task_id)
        return {"action": decision, "task_id": task_id, "sender": str(sender)}

    # -- the loop -----------------------------------------------------------

    def poll_once(self, timeout: int | None = None) -> list[dict]:
        """One round of updates. Returns what each one did.

        THE OFFSET ADVANCES WHETHER OR NOT THE HANDLER SUCCEEDS. Telegram
        redelivers anything below the offset, so acknowledging only on success
        means a message that reliably crashes the handler is re-fetched
        forever — a denial of service available to anyone who can send the bot
        one message, twice.

        Advancing before the call rather than after it is the same behaviour
        given the `except` below; what is load-bearing is that the update is
        acknowledged on BOTH paths. Written before the call so that stays true
        if someone later adds an early `continue`.
        """
        updates = self.tx.get_updates(
            self.offset, timeout if timeout is not None else self.cfg.poll_timeout_s)
        out = []
        for u in updates:
            uid = u.get("update_id", 0)
            self.offset = max(self.offset, uid + 1)
            if not self._claim(uid):
                out.append({"action": "duplicate", "update_id": uid})
                continue
            try:
                out.append(self.handle_update(u))
            except Exception as e:  # noqa: BLE001 — one bad update, not the bot
                out.append({"action": "error", "error": f"{type(e).__name__}: {e}"})
        return out

    def resend_pending(self) -> list[str]:
        """Re-ask for every channel approval still waiting. Called at startup.

        Without this the route table has no reader and a task parked before a
        restart is unreachable: the row is there, the approval is there, and
        nothing puts a button in front of a human again.
        """
        asked = []
        for a in self.ctx.pending_approvals():
            if self._chat(a["task_id"]) is None:
                continue           # not ours; a local task parks the same way
            self._ask(a["task_id"], a["id"])
            asked.append(a["id"])
        return asked

    def serve_forever(self) -> None:  # pragma: no cover - needs a live token
        self.resend_pending()
        while True:
            for r in self.poll_once():
                if r.get("action") == "created":
                    self.run(r["task_id"])


def build(ctx, transport: Transport | None = None) -> TelegramBot:
    """Wire a bot. `transport=None` builds the real one and needs a token."""
    cfg = ctx.config.channels.telegram
    if transport is None:
        transport = HttpTransport(os.environ.get(cfg.token_env, ""))
    return TelegramBot(ctx, transport, cfg)
