from __future__ import annotations


def test_a_large_scope_is_surveyed_as_its_first_names_in_order(ctx, task, workspace, monkeypatch):
    from pathlib import Path

    from agent.plan import planner

    for i in range(120):
        (workspace / f"f{i:03d}.txt").write_text("x", encoding="utf-8")
    real = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: reversed(list(real(self))))
    survey = planner._read_only_survey(ctx, task)  # noqa: SLF001
    listed = survey.split("-> ", 1)[1].split(chr(10))[0].split(", ")
    assert listed == sorted(p.name for p in real(workspace))[:80]
