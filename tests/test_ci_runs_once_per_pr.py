from __future__ import annotations

from pathlib import Path

import yaml

CI = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_a_pr_branch_is_tested_once_not_on_push_and_pull_request():
    raw = yaml.safe_load(CI.read_text(encoding="utf-8"))
    on = raw.get("on", raw.get(True))  # YAML 1.1 reads the bare key `on` as True
    assert "pull_request" in on
    assert on["push"]["branches"] == ["main"]
    assert on["push"]["paths-ignore"] == ["runs/**"]
