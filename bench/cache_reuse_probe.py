"""Phase 9.6 follow-up: WHY P3 failed. Where does this server's reuse stop?

P3 (committed in ca990ed) predicted that when the rebuilt prefix differs at the
memory block, cache reuse on the next call stops at that byte - about 1662
tokens in. It stopped at 698. The prefix bytes BEFORE the memory block were
compared directly and are identical (first differing character 2860; memory
block begins at 2837), so the rebuild did what it claims. What was wrong was
the prediction's model of the SERVER, which assumed exact longest-common-prefix
reuse. This probe injects divergences at chosen points to find the real one.

Three candidate models, with different signatures:

  exact-prefix   reuse stops at the divergence, wherever it is (the model the
                 prediction assumed; P1 and P2 are consistent with it)
  message        a divergence inside an earlier message falls back to the
                 start of that message; 698 would be where the system message
                 begins, after the template's tools block
  checkpoint     the model keeps recurrent state that cannot be truncated at an
                 arbitrary token, so reuse falls back to the nearest saved state
                 checkpoint at or before the divergence

The discriminating case is a divergence in the MIDDLE of the user message, at a
point no earlier prompt ended: exact-prefix and message both reuse to it;
checkpoint snaps back.

Writes docs/evidence/phase9-cache-reuse-probe.json. Temporary state only.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent.config import load_config  # noqa: E402
from agent.context import AgentContext  # noqa: E402
from agent.kernel.types import TaskContract  # noqa: E402
from agent.ledger.prefix import SYSTEM_CONTRACT, PrefixBuilder  # noqa: E402
from agent.memory.store import MemoryStore, tier1_snapshot  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "phase9-cache-reuse-probe.json"
RUN = ROOT / "docs" / "evidence" / "phase9-cache-boundary.json"
GOAL = "Read the five files in work/ and report what they contain."
USER = "File a. " + " ".join(f"a-{k} value {k * 7}" for k in range(90))
T = 64


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="p9-probe-"))
    cfg = load_config(ROOT / "config.openai.yaml")
    cfg.root, cfg.db_path = str(tmp), str(tmp / "agent.db")
    cfg.anchor_path, cfg.skills_path = str(tmp / "a" / "c.anchor"), str(tmp / "s")
    cfg.isolate_retrieval = False
    ctx = AgentContext(cfg)
    real, tools = ctx.backends["cloud"], ctx.tools.schemas()

    # The run's pre-boundary prefix, rebuilt from the same inputs - and
    # checked against the hash the run recorded, so the probe measures the
    # bytes the run actually sent rather than a lookalike.
    snap = tier1_snapshot(MemoryStore(tmp / "memory"), isolated=False)
    contract = json.loads(TaskContract(goal=GOAL, artifact_scope=["work"])
                          .model_dump_json())
    pre = PrefixBuilder(SYSTEM_CONTRACT, [], contract,
                        tool_manual=ctx.tools.manual(), memory=snap).build()
    ran = json.loads(RUN.read_text(encoding="utf-8"))
    want = ran["conditions"]["B_memory_unchanged"]["rows"][0]["prefix_sha256"]
    got = hashlib.sha256(pre.encode("utf-8")).hexdigest()[:16]
    if got != want:
        raise SystemExit(f"rebuilt prefix {got} != the run's {want}; refusing "
                         "to probe a different prompt than the one measured")

    def req(system, user):
        r = real.complete(system, user, tools, max_tokens=1, temperature=0.0)
        return {"prompt": r.usage.input_tokens, "cached": r.usage.cache_read_tokens,
                "wall_ms": r.wall_ms}

    def flip(s, i):
        return s[:i] + ("Z" if s[i] != "Z" else "Q") + s[i + 1:]

    mem = ran["references"]["first_diff_char"]
    mid_u = len(USER) // 2
    real.complete("You are a calculator. Reply with digits only.", "17 * 3 =",
                  None, max_tokens=1, temperature=0.0)          # evict

    seq = []
    for label, system, user in [
        ("base (cold)", pre, USER),
        ("identical", pre, USER),
        ("diverge mid-USER", pre, flip(USER, mid_u)),
        ("restore", pre, USER),
        ("diverge in the tool manual (char 1000)", flip(pre, 1000), USER),
        ("restore", pre, USER),
        (f"diverge at the memory block (char {mem})", flip(pre, mem), USER),
        ("restore", pre, USER),
    ]:
        seq.append({"label": label, **req(system, user)})
        print(f"{label:44s} {seq[-1]}")

    # References LAST: the token position of each divergence, and of the start
    # of the system text.
    refs = {
        "system_text_starts": req("", "")["prompt"],
        "mid_user": req(pre, USER[:mid_u])["prompt"],
        "char_1000": req(pre[:1000], "")["prompt"],
        "memory_block": req(pre[:mem], "")["prompt"],
    }
    cached = {s["label"]: s["cached"] for s in seq}
    mu = cached["diverge mid-USER"]
    tm = cached["diverge in the tool manual (char 1000)"]
    mb = cached[f"diverge at the memory block (char {mem})"]

    def near(a, b):
        return abs(a - b) <= T

    signatures = {
        "exact-prefix": near(mu, refs["mid_user"]) and near(tm, refs["char_1000"])
        and near(mb, refs["memory_block"]),
        "message": near(mu, refs["mid_user"]) and near(tm, refs["system_text_starts"])
        and near(mb, refs["system_text_starts"]),
        "checkpoint": mu < refs["mid_user"] - T,
    }
    arch = json.loads(urllib.request.urlopen(urllib.request.Request(
        real.cfg.resolved_base_url.rsplit("/v1", 1)[0] + "/api/show",
        data=json.dumps({"model": real.cfg.model}).encode()), timeout=20).read()
    ).get("model_info", {}).get("general.architecture")

    report = {"when": time.strftime("%Y-%m-%d %H:%M"), "model": real.cfg.model,
              "architecture": arch, "T": T, "prefix_sha256": got,
              "sequence": seq, "references": refs,
              "reuse_on_divergence": {"mid_user": mu, "tool_manual": tm,
                                      "memory_block": mb},
              "signatures_matched": [k for k, v in signatures.items() if v]}
    OUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in (
        "architecture", "references", "reuse_on_divergence",
        "signatures_matched")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
