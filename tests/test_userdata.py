from __future__ import annotations

import pytest

from agent.userdata.store import SuiteTask, ValidationError, validate_task

VERIFIERS = {"expected_value", "contains_all", "json_schema", "always_pass", "execution"}


def _task(**kw) -> SuiteTask:
    base = dict(
        task_key="t1", goal="do the thing", success_criterion="expected_value",
        output_schema={"type": "object", "x-expected": {"total": 1}},
    )
    base.update(kw)
    return SuiteTask(**base)


def test_always_pass_is_refused():
    problems = validate_task(_task(success_criterion="always_pass"), VERIFIERS)
    assert any("always_pass" in p for p in problems)


def test_expected_value_requires_an_expected_answer():
    t = _task(output_schema={"type": "object"})
    problems = validate_task(t, VERIFIERS)
    assert any("x-expected" in p for p in problems)


def test_contains_all_requires_strings():
    t = _task(success_criterion="contains_all", output_schema={"type": "object"})
    assert any("x-contains" in p for p in validate_task(t, VERIFIERS))


def test_unknown_verifier_is_named_with_the_alternatives():
    problems = validate_task(_task(success_criterion="vibes"), VERIFIERS)
    assert any("vibes" in p and "expected_value" in p for p in problems)


def test_a_well_formed_task_has_no_problems():
    assert validate_task(_task(), VERIFIERS) == []


def test_suite_round_trip(ctx):
    s = ctx.userdata.create_suite("mine", "my own tasks")
    ctx.userdata.upsert_task(s.id, _task(), VERIFIERS)
    ctx.userdata.upsert_task(s.id, _task(task_key="t2"), VERIFIERS)

    got = ctx.userdata.get_suite(s.id)
    assert [t.task_key for t in got.tasks] == ["t1", "t2"]

    specs = got.to_specs()
    assert specs[0]["success_criterion"] == "expected_value"
    assert specs[0]["output_schema"]["x-expected"] == {"total": 1}


def test_upsert_is_idempotent_on_key(ctx):
    s = ctx.userdata.create_suite("mine")
    ctx.userdata.upsert_task(s.id, _task(goal="first"), VERIFIERS)
    ctx.userdata.upsert_task(s.id, _task(goal="second"), VERIFIERS)
    tasks = ctx.userdata.get_suite(s.id).tasks
    assert len(tasks) == 1 and tasks[0].goal == "second"


def test_duplicate_suite_name_is_refused(ctx):
    ctx.userdata.create_suite("mine")
    with pytest.raises(ValidationError, match="already exists"):
        ctx.userdata.create_suite("mine")


def test_invalid_task_never_reaches_the_database(ctx):
    s = ctx.userdata.create_suite("mine")
    with pytest.raises(ValidationError):
        ctx.userdata.upsert_task(s.id, _task(success_criterion="always_pass"), VERIFIERS)
    assert ctx.userdata.get_suite(s.id).tasks == []


def test_delete_suite_takes_its_tasks(ctx):
    s = ctx.userdata.create_suite("mine")
    ctx.userdata.upsert_task(s.id, _task(), VERIFIERS)
    ctx.userdata.delete_suite(s.id)
    assert ctx.userdata.get_suite(s.id) is None
    assert ctx.conn.execute("SELECT COUNT(*) FROM suite_tasks").fetchone()[0] == 0


def test_suite_reports_readiness_against_the_gate(ctx):
    s = ctx.userdata.create_suite("mine")
    for i in range(3):
        ctx.userdata.upsert_task(s.id, _task(task_key=f"t{i}"), VERIFIERS)
    row = ctx.userdata.list_suites()[0]
    assert row["n_tasks"] == 3 and row["ready"] is False


def test_import_from_folder(ctx):
    folder = ctx.config.resolve("bench_import")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.json").write_text(
        '{"key":"imported-1","goal":"g","success_criterion":"expected_value",'
        '"output_schema":{"type":"object","x-expected":{"n":1}},"fixtures":{"work/x":"hi"}}',
        encoding="utf-8",
    )
    s = ctx.userdata.import_suite_from_dir("imported", folder)
    assert [t.task_key for t in s.tasks] == ["imported-1"]
    assert s.tasks[0].fixtures == {"work/x": "hi"}


def test_policy_defaults_to_deny_when_nothing_is_stored(ctx):
    from agent.plan.review import load_policy

    ctx.config.autonomy.auto_review_policy = "./does-not-exist.md"
    text = load_policy(ctx)
    assert "Deny every action" in text


def test_stored_policy_wins_over_the_file(ctx):
    from agent.plan.review import load_policy

    ctx.userdata.set_policy("# mine\n\nDeny everything except reading.\n")
    assert "Deny everything except reading" in load_policy(ctx)


def test_empty_policy_is_refused(ctx):
    with pytest.raises(ValidationError, match="empty"):
        ctx.userdata.set_policy("   ")


def test_grounding_notes_are_gone(ctx):
    with pytest.raises(ValidationError):
        ctx.userdata.put_document("grounding", "tps", "thermal protection notes about tiles")


@pytest.mark.parametrize("key", ["../escape", "a/b", "a" + chr(92) + "b", "..", " "])
def test_a_key_that_is_not_a_plain_name_is_refused(key):
    assert any("Key" in p for p in validate_task(_task(task_key=key), VERIFIERS))


def test_a_plain_key_is_fine():
    assert validate_task(_task(task_key="E01-retry_2.v1"), VERIFIERS) == []


def test_an_import_with_one_bad_task_writes_nothing(ctx, tmp_path):
    import json

    folder = tmp_path / "suite"
    folder.mkdir()
    good = {"key": "a1", "goal": "g", "success_criterion": "expected_value",
            "output_schema": {"type": "object", "x-expected": {"v": 1}}}
    (folder / "a1.json").write_text(json.dumps(good), encoding="utf-8")
    (folder / "b2.json").write_text(json.dumps({**good, "key": "b2", "success_criterion": "always_pass"}),
                                    encoding="utf-8")
    with pytest.raises(ValidationError, match="b2"):
        ctx.userdata.import_suite_from_dir("imported", folder)
    assert ctx.userdata.get_suite_by_name("imported") is None
