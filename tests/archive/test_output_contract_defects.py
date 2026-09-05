"""The two defects that produced a 10% pass rate reading as task difficulty.

Both were found by taxonomy on the pass-1 traces, not by a test — 27 failing
tasks, of which 13 (48%) died on one of these two before any physics was
attempted.

DEFECT 1 — the objective never named the params.json keys.
    It documented the constants.json layout in detail and then said only
    "Input file: work/E06/input/params.json". The agent guessed:
    `mass_flow_rate` for `mdot_kg_s`, `thrust` for `thrust_n`, `Isp` for
    `isp_s`, `dry_mass` for `dry_mass_kg`. 8 of 30 tasks hit a KeyError.

DEFECT 2 — a stray closing brace at the end of the `code` field.
    Byte-identical across unrelated tasks:
        print('Answer written to', output_path)}
        SyntaxError: unmatched '}'
    10 of 30 tasks hit a SyntaxError. Origin established by experiment, not
    assumption: feeding a well-formed action through `parse_action` yields
    code with no trailing brace, so the wrapper does not add one. Constrained
    decoding guarantees the JSON ENVELOPE is valid, therefore the brace is
    content the model placed INSIDE the string.

The fix for defect 2 is deliberately NOT a lenient parser. Stripping the
brace would let a malformed code block score like a wrong answer, and that
confusion is precisely what made 10% look like difficulty.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _suite_module():
    spec = importlib.util.spec_from_file_location(
        "aero_suite_contract", ROOT / "bench" / "suites" / "archive" / "aero_calc.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Defect 1: the I/O contract must leave nothing to infer
# ---------------------------------------------------------------------------

# the exact wrong guesses seen in the pass-1 traces, and what they should be
OBSERVED_GUESSES = {
    "E06": ("mass_flow_rate", "mdot_kg_s"),
    "E07": ("thrust", "thrust_n"),
    "E09": ("Isp", "isp_s"),
    "H29": ("dry_mass", "dry_mass_kg"),
}


def test_every_objective_names_its_params_keys():
    """The failure was a name-guessing benchmark wearing a physics costume."""
    mod = _suite_module()
    missing = []
    for st in mod.as_suite_tasks():
        params = json.loads(st.fixtures[f"work/{st.task_key}/input/params.json"])
        for key in params:
            if key not in st.goal:
                missing.append(f"{st.task_key}:{key}")
    assert not missing, (
        "these input keys are never named in their objective, so the agent "
        f"must guess them: {missing}")


@pytest.mark.parametrize("task,guess_real", sorted(OBSERVED_GUESSES.items()))
def test_the_real_key_is_present_and_the_guess_is_not_invited(task, guess_real):
    """Each of these cost a task in pass 1."""
    guessed, real = guess_real
    mod = _suite_module()
    st = next(x for x in mod.as_suite_tasks() if x.task_key == task)
    assert real in st.goal, f"{task}: the real key {real!r} is not in the objective"
    assert "do not guess" in st.goal.lower()


def test_the_output_keys_are_named_exactly():
    mod = _suite_module()
    for st in mod.as_suite_tasks():
        for key in st.output_schema["required"]:
            assert key in st.goal, f"{st.task_key}: output key {key} not named"
        assert "spelled exactly as shown" in st.goal


# ---------------------------------------------------------------------------
# Defect 2: malformed code is refused, not executed, and not silently repaired
# ---------------------------------------------------------------------------

MODEL_CODE = "import json\nprint('Answer written to', output_path)"
BRACE_LEAK = MODEL_CODE + "}"          # exactly what the model emitted


def test_the_wrapper_does_not_add_the_brace():
    """Establishes origin. If this ever fails, the diagnosis was wrong."""
    from agent.execute.loop import parse_action

    p = parse_action(json.dumps({"kind": "code", "code": MODEL_CODE}))
    assert p.kind == "code"
    assert not p.args["code"].rstrip().endswith("}"), \
        "the wrapper injected a brace; the defect is not model-origin"


def test_the_leak_is_reproducible_through_the_real_parser():
    """The model puts the brace inside the string; JSON stays valid."""
    from agent.execute.loop import parse_action

    p = parse_action(json.dumps({"kind": "code", "code": BRACE_LEAK}))
    assert p.args["code"].rstrip().endswith("}")
    with pytest.raises(SyntaxError):
        compile(p.args["code"], "<action>", "exec")


def test_malformed_code_is_refused_before_execution(monkeypatch):
    """The fix. Nothing runs, and the failure is named as malformed."""
    from agent.execute import codeact

    ran = {"n": 0}

    def boom(*a, **k):
        ran["n"] += 1
        raise AssertionError("run_sandboxed must not be reached")

    monkeypatch.setattr(codeact, "run_sandboxed", boom)

    class _C:
        class config:
            @staticmethod
            def resolve(x):
                return x

    class _T:
        class contract:
            artifact_scope = ["work/E01"]

    out = codeact.run_code_action(_C(), _T(), BRACE_LEAK)
    assert ran["n"] == 0, "malformed code was executed anyway"
    assert out["ok"] is False
    assert out.get("malformed_action") is True
    assert "not valid Python" in out["error"]


def test_the_refusal_names_the_brace_specifically():
    """A generic 'syntax error' would not tell the model what to change."""
    from agent.execute import codeact

    class _C:
        class config:
            @staticmethod
            def resolve(x):
                return x

    class _T:
        class contract:
            artifact_scope = []

    err = codeact.run_code_action(_C(), _T(), BRACE_LEAK)["error"]
    assert "closing brace" in err
    assert "outside the string" in err


def test_the_fix_is_not_a_lenient_parser():
    """A repaired-and-run code block would score like a wrong answer.

    That is the confusion which made a plumbing failure read as difficulty,
    so the malformed case must stay distinguishable from a bad number.
    """
    from agent.execute import codeact

    class _C:
        class config:
            @staticmethod
            def resolve(x):
                return x

    class _T:
        class contract:
            artifact_scope = []

    out = codeact.run_code_action(_C(), _T(), BRACE_LEAK)
    assert "stdout" in out and out["stdout"] == "", "nothing should have run"
    assert out["returncode"] is None, \
        "a returncode would imply the program executed"
    # and valid code is still executed normally
    src = (ROOT / "src" / "agent" / "execute" / "codeact.py").read_text(encoding="utf-8")
    assert "rstrip" not in src.split("def run_code_action")[1][:1500], \
        "the code must not be silently trimmed to make it compile"
