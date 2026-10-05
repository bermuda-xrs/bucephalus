from __future__ import annotations

import json

from agent.kernel.types import TaskContract
from agent.ledger.prefix import (PROMPT_VISIBLE_CONTRACT_FIELDS, PrefixBuilder,
                                 prompt_contract)

HIDDEN_CONTRACT_FIELDS = {
    "grader_dir": "the grader's path; telling the model where the answer "
                  "lives is the leak artifact_scope was meant to close",
}


def _render(contract: TaskContract) -> str:
    return PrefixBuilder("SYSTEM", [], json.loads(contract.model_dump_json()),
                         tool_manual="", memory={}).build()


def test_only_allowlisted_contract_fields_are_rendered():
    declared = set(TaskContract.model_fields)
    classified = set(PROMPT_VISIBLE_CONTRACT_FIELDS) | set(HIDDEN_CONTRACT_FIELDS)
    unclassified = declared - classified
    assert not unclassified, (
        f"TaskContract grew {sorted(unclassified)} and nothing says whether "
        "the model may see it. Add it to PROMPT_VISIBLE_CONTRACT_FIELDS in "
        "ledger/prefix.py, or to HIDDEN_CONTRACT_FIELDS here with the reason. "
        "Defaulting either way is how the last one got in.")
    assert not (set(PROMPT_VISIBLE_CONTRACT_FIELDS) & set(HIDDEN_CONTRACT_FIELDS))
    assert set(PROMPT_VISIBLE_CONTRACT_FIELDS) <= declared


def test_the_expected_answer_is_not_in_the_prompt():
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
    assert '"service"' in p and '"errors"' in p
    assert "Count the ERROR lines" in p


def test_no_harness_extension_reaches_the_prompt():
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
    values = {name: f"SENTINEL_{name.upper()}" for name in HIDDEN_CONTRACT_FIELDS}
    c = TaskContract(goal="G", artifact_scope=["work"], **values)
    p = _render(c)
    for name, sentinel in values.items():
        assert sentinel not in p, (
            f"{name} reached the prompt: {HIDDEN_CONTRACT_FIELDS[name]}")


def test_the_verifier_still_reads_what_the_prompt_lost():
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
