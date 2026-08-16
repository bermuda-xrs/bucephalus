"""Child process for the M1 kill-and-resume test.

Run once with "hang": it appends tool.intent, performs a side effect through
execute_once, then blocks forever so the parent can kill -9 it between the
side effect and the receipt being useful to anyone.

Run again with "resume": it verifies the chain and tries the identical call,
which must NOT re-run the side effect.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent.kernel.db import open_db  # noqa: E402
from agent.kernel.events import EventLog  # noqa: E402
from agent.kernel.receipts import execute_once, idempotency_key  # noqa: E402

TASK_ID = "t_crashtest"
STEP = 0
TOOL = "send_message"
ARGS = {"to": "someone@example.com", "body": "hello"}


def main() -> int:
    db_path, marker_path, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    conn = open_db(db_path)
    log = EventLog(conn)

    conn.execute(
        """INSERT OR IGNORE INTO tasks(id, parent_id, contract_json, status,
                                       budget_json, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (TASK_ID, None, '{"goal":"crash test"}', "running",
         '{"usd":1,"seconds":60}', time.time(), time.time()),
    )
    conn.commit()

    key = idempotency_key(TASK_ID, STEP, TOOL, ARGS)
    log.append(TASK_ID, "tool.intent", {"tool": TOOL, "step": STEP, "key": key})

    ran = {"value": False}

    def side_effect() -> dict:
        ran["value"] = True
        with open(marker_path, "a", encoding="utf-8") as f:
            f.write("sent\n")
        return {"ok": True}

    result = execute_once(conn, key, TOOL, ARGS, side_effect)
    log.append(TASK_ID, "tool.receipt", {"tool": TOOL, "key": key, "ok": True})

    if mode == "hang":
        while True:  # the parent kills us here
            time.sleep(0.1)

    ok, bad = log.verify(TASK_ID)
    print(json.dumps({"replayed": not ran["value"], "chain_ok": ok, "bad_seq": bad,
                      "result": result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
