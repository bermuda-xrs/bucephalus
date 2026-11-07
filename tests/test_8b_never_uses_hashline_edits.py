from __future__ import annotations

from pathlib import Path

import yaml

from agent.config import config_from_raw

ROOT = Path(__file__).resolve().parents[1]


def test_no_shipped_backend_runs_the_8b_with_hashline_edits():
    # the 8B scored 0/14 on config_edit with hashline edits and 14/14 without (HANDOVER, cloud-8b-cfg-3)
    seen = 0
    for f in sorted(ROOT.glob("config*.yaml")):
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        raw.update(root=str(ROOT), state_dir="")
        for name, b in config_from_raw(raw).backends.items():
            if "8b" in b.model:
                seen += 1
                assert b.edit_style == "default", f"{f.name}:{name}"
    assert seen >= 4
