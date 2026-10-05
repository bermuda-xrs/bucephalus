from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from ..execute.loop import run_task
from ..kernel.events import sha256_hex
from ..kernel.tasks import create_task
from ..kernel.types import Budget, TaskContract
from ..security.origin import TELEGRAM, clamp_budget

API = "https://api.telegram.org"

MAX_RECORDED_BODY = 4096

MAX_DROPS = 500

MAX_SEEN_UPDATES = 1000


class Transport(Protocol):
    def get_updates(self, offset: int, timeout: int) -> list[dict]: ...
    def send_message(self, chat_id, text: str, buttons: list | None = None) -> dict: ...
    def answer_callback(self, callback_id: str, text: str) -> dict: ...


class HttpTransport:
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
        return self._call("getUpdates",
                          {"offset": offset, "timeout": timeout},
                          timeout=timeout + 10) or []

    def send_message(self, chat_id, text: str, buttons: list | None = None) -> dict:
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
    digest = sha256_hex(body[:MAX_RECORDED_BODY])
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
    def __init__(self, ctx, transport: Transport, cfg=None):
        self.ctx = ctx
        self.tx = transport
        self.cfg = cfg or ctx.config.channels.telegram
        self.offset = self._resume_offset()

    def _resume_offset(self) -> int:
        row = self.ctx.conn.execute(
            "SELECT MAX(update_id) FROM channel_updates WHERE channel=?",
            (TELEGRAM,)).fetchone()
        return (row[0] + 1) if row and row[0] is not None else 0

    def _claim(self, update_id: int) -> bool:
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

    def _bind(self, task_id: str, chat_id) -> None:
        self.ctx.conn.execute(
            """INSERT OR REPLACE INTO channel_routes(task_id, channel, chat_id,
                                                     created_at)
               VALUES (?,?,?,?)""",
            (task_id, TELEGRAM, str(chat_id), time.time()))
        self.ctx.conn.commit()

    def _chat(self, task_id: str):
        row = self.ctx.conn.execute(
            "SELECT chat_id FROM channel_routes WHERE task_id=?",
            (task_id,)).fetchone()
        return row[0] if row else None

    def allowed(self, sender) -> bool:
        return str(sender) in {str(x) for x in self.cfg.allowed_senders}

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

    def run(self, task_id: str) -> dict:
        chat = self._chat(task_id)
        try:
            result = run_task(self.ctx, task_id)
        except Exception as e:  # noqa: BLE001
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

    def _handle_callback(self, cb: dict) -> dict:
        sender = (cb.get("from") or {}).get("id", "")
        data = cb.get("data") or ""
        cb_id = cb.get("id", "")

        if not self.allowed(sender):
            record_drop(self.ctx.conn, TELEGRAM, sender,
                        "callback from non-allowlisted sender", data)
            self.tx.answer_callback(cb_id, "not authorised")
            return {"action": "refused", "why": "sender not allowlisted"}

        decision, _, approval_id = data.partition(":")
        if decision not in ("approve", "deny") or not approval_id:
            return {"action": "ignored", "why": "unparseable callback"}

        row = self.ctx.conn.execute(
            "SELECT task_id FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if row is not None:
            routed = self._chat(row[0])
            pressed_in = ((cb.get("message") or {}).get("chat") or {}).get("id")
            if routed is None or str(routed) != str(pressed_in):
                self.ctx.log.append(row[0], "channel.refused",
                                    {"approval_id": approval_id, "sender": str(sender),
                                     "why": "approval not routed to this chat"})
                self.tx.answer_callback(cb_id, "not this chat's decision")
                return {"action": "refused", "why": "approval not routed to this chat"}

        try:
            out = self.ctx.resolve_approval(approval_id, decision == "approve")
        except KeyError:
            self.tx.answer_callback(cb_id, "no such approval")
            return {"action": "ignored", "why": "no such approval"}

        task_id = out["task_id"]
        if out.get("already_decided"):
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

    def poll_once(self, timeout: int | None = None) -> list[dict]:
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
            except Exception as e:  # noqa: BLE001
                out.append({"action": "error", "error": f"{type(e).__name__}: {e}"})
        return out

    def resend_pending(self) -> list[str]:
        asked = []
        for a in self.ctx.pending_approvals():
            if self._chat(a["task_id"]) is None:
                continue
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
    cfg = ctx.config.channels.telegram
    if transport is None:
        transport = HttpTransport(os.environ.get(cfg.token_env, ""))
    return TelegramBot(ctx, transport, cfg)
