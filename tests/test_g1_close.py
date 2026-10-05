from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))

import g1_close  # noqa: E402


def test_each_mechanism_saves_what_the_record_says_when_it_fires():
    m = g1_close.savings()["mechanisms"]
    u = "executor_uncached_tokens"

    c = m["compaction"]
    assert c["on"]["rotations"] and c["on"]["tombstones"], "the real ceiling was not crossed"
    assert c["on"][u] < c["off"][u]

    s = m["semantic_cache"]
    assert s["on"]["cache_hits"] == 2 and s["on"]["local_calls"] < s["off"]["local_calls"]
    assert abs(s["on"]["executor_prompt_tokens"] - s["off"]["executor_prompt_tokens"]) < 50

    p = m["prefix_stability"]
    assert p["on"]["prefix_fingerprints"] == 1 < p["off"]["prefix_fingerprints"]
    assert p["on"][u] < p["off"][u]

    k = m["code_as_action"]
    assert k["on"]["run_code"] == 1 and k["on"]["executor_calls"] < k["off"]["executor_calls"]
    assert k["on"]["executor_prompt_tokens"] < k["off"]["executor_prompt_tokens"]
    assert k["idle_on"]["executor_prompt_tokens"] > k["idle_off"]["executor_prompt_tokens"]
