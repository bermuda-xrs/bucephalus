"""Nothing the grader uses may reach the model's prompt.

THE DEFECT THIS EXISTS FOR, 2026-09-22. `TaskContract` was rendered whole into
the cache-stable prefix, so a task declaring `output_schema["x-expected"]`
handed the model its own answer, and `grader_dir` handed it the grader's
address. On `logs-triage-005` the 3B answered correctly on turn one having
read no files; across the five arms of the 2026-09-17 ablation the three tasks
that passed in EVERY arm were exactly the three carrying a grading key, and
the one without a key failed in every arm. 0.75 was the fraction of tasks with
an answer in the prompt.

WHY THESE TESTS ARE SHAPED BY ALLOWLIST. This is the grader-reachability
defect recurring by a new route - the first one was a `verify.py` the agent
could read, closed at HARNESS_VERSION 2 by moving the grader outside
`artifact_scope`. That control was real, and it did nothing here, because the
leak was not a file. A test that greps for `x-expected` would pass the day
someone adds `expected_output`, so:

  * `test_only_allowlisted_contract_fields_are_rendered` fails when a field is
    ADDED to `TaskContract` and not classified as visible or hidden. A new
    grading field fails the suite before it can leak.
  * `test_no_harness_extension_reaches_the_prompt` fails for any `x-` key at
    any depth, named or not.
  * `test_a_sentinel_never_appears` stuffs every hidden field with a unique
    string and greps the rendered bytes for it.

The claim these replace lived in `ExpectedValueVerifier`'s docstring - "and
nothing in the agent's context ever sees it" - which was false from the day it
was written. It read as protection, which is why nobody checked it.
"""

from __future__ import annotations

import json

from agent.kernel.types import TaskContract
from agent.ledger.prefix import (PROMPT_VISIBLE_CONTRACT_FIELDS, PrefixBuilder,
                                 prompt_contract)

# Fields of TaskContract that must NEVER be rendered, each with why. Adding a
# field to TaskContract without listing it here or in the visible set fails
# `test_only_allowlisted_contract_fields_are_rendered`.
HIDDEN_CONTRACT_FIELDS = {
    "grader_dir": "the grader's path; telling the model where the answer "
                  "lives is the leak artifact_scope was meant to close",
}


def _render(contract: TaskContract) -> str:
    return PrefixBuilder("SYSTEM", [], json.loads(contract.model_dump_json()),
                         tool_manual="", memory={}).build()


def test_only_allowlisted_contract_fields_are_rendered():
    """The guard that survives the next grading key, whatever it is called."""
    declared = set(TaskContract.model_fields)
    classified = set(PROMPT_VISIBLE_CONTRACT_FIELDS) | set(HIDDEN_CONTRACT_FIELDS)
    unclassified = declared - classified
    assert not unclassified, (
        f"TaskContract grew {sorted(unclassified)} and nothing says whether "
        "the model may see it. Add it to PROMPT_VISIBLE_CONTRACT_FIELDS in "
        "ledger/prefix.py, or to HIDDEN_CONTRACT_FIELDS here with the reason. "
        "Defaulting either way is how the last one got in.")
    assert not (set(PROMPT_VISIBLE_CONTRACT_FIELDS) & set(HIDDEN_CONTRACT_FIELDS))
    # And the allowlist must not have quietly grown past what exists.
    assert set(PROMPT_VISIBLE_CONTRACT_FIELDS) <= declared


def test_the_expected_answer_is_not_in_the_prompt():
    """The exact defect, on the exact shape that produced it."""
    c = TaskContract(
        goal="Count the ERROR lines per service and name the worst.",
        artifact_scope=["work"], success_criterion="expected_value",
        output_schema={
            "type": "object",
            "required": ["service", "errors"],
            "properties": {"service": {"type": "string"},
                           "errors": {"type": "integer"}},
            "x-expected": {"service": "billing", "errors": 20},
        })
    p = _render(c)
    assert "x-expected" not in p
    assert "billing" not in p, "the answer is in the prompt"
    # The SHAPE still is, because the model needs it to answer at all.
    assert '"service"' in p and '"errors"' in p
    assert "Count the ERROR lines" in p


def test_no_harness_extension_reaches_the_prompt():
    """Any `x-` key, at any depth, named or not. `x-contains` and `x-exact`
    are graders too, and `x-split` is the suite's business, not the model's."""
    c = TaskContract(
        goal="G", artifact_scope=["work"], success_criterion="contains_all",
        output_schema={
            "type": "object",
            "x-contains": ["compressor", "filter"],
            "x-exact": True,
            "x-split": "eval",
            "x-not-invented-yet": "SENTINEL_FUTURE_KEY",
            "properties": {
                "summary": {"type": "string",
                            "x-nested-grader": "SENTINEL_NESTED"},
            },
            "anyOf": [{"x-deep-in-a-list": "SENTINEL_IN_LIST"}],
        })
    p = _render(c)
    for leak in ("x-contains", "compressor", "x-exact", "x-split",
                 "SENTINEL_FUTURE_KEY", "SENTINEL_NESTED", "SENTINEL_IN_LIST"):
        assert leak not in p, f"{leak!r} reached the prompt"
    assert '"summary"' in p, "stripping took the schema's real content with it"


def test_a_sentinel_in_every_hidden_field_never_appears():
    """Mechanical, so it keeps working when someone adds a hidden field."""
    values = {name: f"SENTINEL_{name.upper()}" for name in HIDDEN_CONTRACT_FIELDS}
    c = TaskContract(goal="G", artifact_scope=["work"], **values)
    p = _render(c)
    for name, sentinel in values.items():
        assert sentinel not in p, (
            f"{name} reached the prompt: {HIDDEN_CONTRACT_FIELDS[name]}")


def test_the_verifier_still_reads_what_the_prompt_lost():
    """The object keeps the key; only the RENDERING drops it. A fix that broke
    grading would trade a leak for a suite that cannot score anything."""
    from agent.assure.verifiers import ExpectedValueVerifier

    c = TaskContract(
        goal="G", artifact_scope=["work"], success_criterion="expected_value",
        output_schema={"type": "object", "required": ["total"],
                       "properties": {"total": {"type": "integer"}},
                       "x-expected": {"total": 4825}})
    assert c.output_schema["x-expected"] == {"total": 4825}
    v = ExpectedValueVerifier()
    assert v.check('{"total": 4825}', c).passed
    assert not v.check('{"total": 0}', c).passed
    assert "x-expected" not in json.dumps(prompt_contract(
        json.loads(c.model_dump_json())))
