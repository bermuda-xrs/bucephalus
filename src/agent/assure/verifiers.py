"""M7.1 — the verifier registry.

The main rule: never let the agent revise its own work based purely on its own
opinion. It needs something real to check against — run the code and read the
actual error, look up the fact. If it could spot the mistake by thinking
harder, it would not have made it.

For non-code work, citation_check does the heavy lifting. Do not skip it
because it is harder: a system that can only verify code cannot verify skills
about writing. (checker_program, its partner in the spec, was built, never
constructed by anything, and passed every answer under an empty program -
removed 2026-09-25, SPEC-DELTA C71.)

DR7: A CHECK MUST BE SHOWN ABLE TO FAIL BEFORE ITS PASSES MEAN ANYTHING.
Every kind declares what it `inspects` - the ANSWER text, the task's
WORKSPACE on disk, or (always_pass alone) NOTHING - and
`tests/test_every_verifier_can_fail.py` holds each to it: an answer kind fails
an empty answer, "{}" and a wrong answer carrying a sentinel; a workspace kind
fails all three against an empty workspace. Four kinds could not (FINDINGS
1.34): `execution` ran the answer as Python, `ruff` passed when not installed
and otherwise linted the harness, `json_schema` passed anything under an
empty schema, and `self_consistency` never read the answer at all.
Empty means nothing (DR4): an empty key is a refusal, never a pass.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from ..kernel.types import TaskContract, VerifyResult


class Verifier(Protocol):
    kind: str
    inspects: str   # "answer" | "workspace" | "nothing" - DR7, above

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult: ...


def inspects(verifier) -> str:
    """What a verifier grades. Undeclared means the answer - the strict case,
    so a kind added without a declaration is tested as one on arrival."""
    return getattr(verifier, "inspects", "answer")


def _refuse(kind: str, why: str) -> VerifyResult:
    return VerifyResult(passed=False, score=0.0, observation=why, verifier_id=kind)


def _workdir(contract: TaskContract, root: str | None) -> Path:
    """The task's workspace: `root` joined with the first artifact_scope entry.
    NOT the process working directory - which is where pytest and ruff looked
    until 2026-09-25, grading the harness's own tree instead of the task's."""
    base = Path(root) if root else Path.cwd()
    return base / (contract.artifact_scope or ["."])[0]


class AlwaysPass:
    """Not a check: the absence of one. It inspects nothing and so cannot fail,
    which is why the final sweep never consults it and why, under measurement,
    `VerificationMesh.verify` refuses it rather than counting its pass."""

    kind = "always_pass"
    inspects = "nothing"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        return VerifyResult(
            passed=True, score=1.0, observation="no criterion set", verifier_id=self.kind
        )


class PytestVerifier:
    """Run the tests in the task's workspace, in the sandbox.

    UNTIL 2026-09-25 THIS NEVER GRADED A TASK. It ran `python -m pytest tests`
    in the PROCESS working directory and ignored `root`, so from the project
    root it ran the harness's own test suite - which is why an "empty
    workspace" outran a 60 s timeout - and passed or failed on whether the
    harness was healthy. No recorded task was ever graded by it.

    Now: the tests found under the workspace, run in the sandbox because they
    import code the model wrote. No tests collected is pytest's exit 5, a
    failure. The tests live in the workspace, so the model can edit them:
    this is the user's-own-tests check, not a measurement grader - under
    measurement, grade with `verify_program`, whose grader is out of scope.
    """

    kind = "pytest"
    inspects = "workspace"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        from ..execute.sandbox import run_sandboxed

        workdir = _workdir(contract, root)
        if not workdir.is_dir():
            return _refuse(self.kind, f"no workspace at {workdir}")
        # logging and faulthandler open os.devnull for writing, which the
        # sandbox refuses as a write outside scope. The plugins go, not the rule.
        harness = (
            f"import os, sys\nos.chdir({workdir.name!r})\n"
            "sys.dont_write_bytecode = True\nsys.path.insert(0, os.getcwd())\n"
            "import pytest\nsys.exit(pytest.main(['-q', '--no-header', "
            "'-p', 'no:cacheprovider', '-p', 'no:logging', "
            "'-p', 'no:faulthandler', '.']))\n")
        r = run_sandboxed(harness, allowed_paths=[str(workdir)], timeout_s=120)
        out = (r["stdout"] + r["stderr"])[-6000:]
        passed = r["returncode"] == 0
        return VerifyResult(
            passed=passed, score=1.0 if passed else _pytest_score(out),
            observation=out or f"returncode={r['returncode']}", verifier_id=self.kind,
        )


def _pytest_score(output: str) -> float:
    m = re.search(r"(\d+) passed", output)
    f = re.search(r"(\d+) failed", output)
    p = int(m.group(1)) if m else 0
    fl = int(f.group(1)) if f else 0
    return p / (p + fl) if (p + fl) else 0.0


class RuffVerifier:
    """Lint the Python files in the task's workspace.

    Two defects until 2026-09-25, either enough to pass anything: with ruff
    not installed - as in this project's own venv - it PASSED, "skipped"; and
    installed, it linted `.` in the process working directory, the harness,
    not the task. A missing tool is now a refusal, and a workspace with no
    Python in it is nothing to lint, so also a refusal. Lint-clean is style,
    never correctness: it is not a task's grader on its own.
    """

    kind = "ruff"
    inspects = "workspace"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        if shutil.which("ruff") is None:
            return _refuse(self.kind, "ruff is not installed, so nothing was "
                                      "checked - a missing check is not a pass")
        workdir = _workdir(contract, root)
        if not workdir.is_dir() or not any(workdir.rglob("*.py")):
            return _refuse(self.kind, f"no Python files under {workdir} to lint")
        proc = subprocess.run(  # noqa: S603
            ["ruff", "check", "--no-cache", "--output-format", "concise",
             str(workdir)],
            capture_output=True, text=True, timeout=120,
        )
        out = (proc.stdout + proc.stderr)[-4000:]
        return VerifyResult(
            passed=proc.returncode == 0,
            score=1.0 if proc.returncode == 0 else 0.5,
            observation=out or "clean",
            verifier_id=self.kind,
        )


class JsonSchemaVerifier:
    kind = "json_schema"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        schema = contract.output_schema or {}
        # DR4/DR7. An empty schema passed every JSON value there is; one that
        # accepts {} passes the final sweep's old "{}" and every hollow answer
        # - it cannot tell an answer from its absence. Refused.
        if not _validate({}, schema):
            return _refuse(self.kind, "this output_schema accepts the empty "
                                      "object {}, so it cannot tell an answer "
                                      "from its absence - require a key")
        try:
            data = json.loads(artifact)
        except json.JSONDecodeError as e:
            return VerifyResult(
                passed=False, score=0.0,
                observation=f"output is not valid JSON: {e}", verifier_id=self.kind,
            )
        errors = _validate(data, schema)
        return VerifyResult(
            passed=not errors,
            score=1.0 if not errors else max(0.0, 1.0 - 0.25 * len(errors)),
            observation="; ".join(errors) or "schema satisfied",
            verifier_id=self.kind,
        )


def _validate(data, schema: dict, path: str = "$") -> list[str]:
    """A small structural validator. Enough for output_schema; not a full draft impl."""
    errs: list[str] = []
    if not schema:
        return errs
    t = schema.get("type")
    types = {
        "object": dict, "array": list, "string": str,
        "integer": int, "number": (int, float), "boolean": bool,
    }
    if t and t in types and not isinstance(data, types[t]):
        return [f"{path}: expected {t}, got {type(data).__name__}"]
    if t == "object":
        for key in schema.get("required", []):
            if key not in data:
                errs.append(f"{path}: missing required key {key!r}")
        for key, sub in (schema.get("properties") or {}).items():
            if key in data:
                errs.extend(_validate(data[key], sub, f"{path}.{key}"))
    if t == "array" and "items" in schema:
        for i, item in enumerate(data):
            errs.extend(_validate(item, schema["items"], f"{path}[{i}]"))
    if "enum" in schema and data not in schema["enum"]:
        errs.append(f"{path}: {data!r} not in enum")
    return errs


class ExecutionVerifier:
    """Run a checking script against the workspace.

    WITHOUT A SCRIPT IT RAN THE ANSWER ITSELF AS PYTHON, and any valid Python
    expression exits 0: `{"path": "work/DOES_NOT_EXIST.py"}`, `42`, `"hello"`
    and the final sweep's `{}` all passed. That is how write-function-002
    scored `done_unannounced` in every arm of the v5 run having submitted no
    answer. Running the answer checks nothing about the task, so with no
    script this refuses. The registered instance has none: grade code with
    `verify_program`.
    """

    kind = "execution"

    def __init__(self, script_path: str | None = None):
        self.script_path = script_path
        self.inspects = "workspace" if script_path else "answer"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        from ..execute.sandbox import run_sandboxed

        if not self.script_path:
            return _refuse(self.kind, "execution has no script: running the "
                                      "answer as code checks nothing about the "
                                      "task. Use verify_program.")
        code = Path(self.script_path).read_text(encoding="utf-8")
        r = run_sandboxed(code, allowed_paths=contract.artifact_scope, timeout_s=30)
        ok = r["returncode"] == 0
        return VerifyResult(
            passed=ok,
            score=1.0 if ok else 0.0,
            observation=f"returncode={r['returncode']}\nstdout:\n{r['stdout']}\nstderr:\n{r['stderr']}",
            verifier_id=self.kind,
        )


class CitationCheckVerifier:
    """Do the cited sources actually contain the claim?

    Substring/overlap grounding, not a model asking itself whether it was right.
    """

    kind = "citation_check"

    def __init__(self, sources: dict[str, str] | None = None):
        self.sources = sources or {}

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        cites = re.findall(r"\[([^\]]+)\]\(([^)]+)\)|<cite src=\"([^\"]+)\">([^<]*)</cite>", artifact)
        if not cites:
            return VerifyResult(
                passed=False, score=0.0,
                observation="no citations found; every claim needs a source",
                verifier_id=self.kind,
            )
        bad: list[str] = []
        checked = 0
        for m in cites:
            claim = m[0] or m[3]
            src = m[1] or m[2]
            body = self.sources.get(src)
            if body is None:
                bad.append(f"unknown source {src}")
                continue
            checked += 1
            if not _overlaps(claim, body):
                bad.append(f"{src} does not support {claim[:60]!r}")
        score = 0.0 if not checked else (checked - len(bad)) / checked
        return VerifyResult(
            passed=not bad,
            score=max(0.0, score),
            observation="; ".join(bad) or f"{checked} citations grounded",
            verifier_id=self.kind,
        )


def _overlaps(claim: str, body: str, threshold: float = 0.5) -> bool:
    words = {w.lower() for w in re.findall(r"\w{4,}", claim)}
    if not words:
        return False
    body_words = {w.lower() for w in re.findall(r"\w{4,}", body)}
    return len(words & body_words) / len(words) >= threshold


class SelfConsistencyVerifier:
    """Agreement across k samples. Only signal available for open prose.

    Weakest verifier in the set — it measures stability, not truth. Never the
    sole criterion for anything that matters.
    """

    kind = "self_consistency"

    def __init__(self, ctx, k: int = 3):
        self.ctx = ctx
        self.k = k

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None, task=None) -> VerifyResult:
        from ..router.cascade import DEFAULT_AGREEMENT, normalize

        if not artifact.strip():
            return _refuse(self.kind, "empty answer")
        backend = self.ctx.backends[self.ctx.active_local_tier() or "cloud"]
        prompt = (
            "Answer the question. Output only the answer, no preamble.\n\n"
            f"Question: {contract.goal}"
        )
        samples = [
            self.ctx.call_model(
                task if task is not None else "unattributed", backend, "verify",
                prefix="You answer concisely.", tail=prompt, tools=None,
                max_tokens=512, temperature=0.7).text
            for _ in range(self.k)
        ]
        # Agreement WITH THE ANSWER. It measured agreement among the samples
        # alone and never read `artifact`, so a consistent model passed every
        # answer - right, wrong or empty (FINDINGS 1.34).
        mine = normalize(artifact)
        agreement = sum(normalize(s) == mine for s in samples) / self.k
        # DEFAULT_AGREEMENT, not the literal 0.67. Two of three is 0.6667,
        # which is LESS than 0.67, so the literal rejects exactly the case it
        # was written to accept. CORRECTIONS.md 1.5 records this fix being
        # made in cascade.py; this copy was missed.
        return VerifyResult(
            passed=agreement >= DEFAULT_AGREEMENT,
            score=agreement,
            observation=f"{agreement:.2f} of {self.k} samples agree with the answer",
            verifier_id=self.kind,
        )


class ExpectedValueVerifier:
    """Compare the answer against a known-correct value.

    This is the verifier that makes a benchmark mean something. `json_schema`
    checks that the answer has the right *shape*, so `{"total": 0}` passes for
    an invoice totalling 4825 — the same failure the build spec cites about
    SWE-bench, where an audit found ~59% of the hardest tasks had tests that
    would not catch the bug they were meant to catch.

    `expected` is carried on the contract's output_schema under `x-expected`,
    so a task file declares its own right answer.

    ~~"and nothing in the agent's context ever sees it."~~ **THAT SENTENCE WAS
    FALSE FROM THE DAY IT WAS WRITTEN. Corrected 2026-09-22.** The whole
    contract - output_schema included - was rendered into the cache-stable
    prefix, so `x-expected` sat in the prompt of every task that declared one.
    The model never had to read the inputs; it could read the answer.
    Measured: on `logs-triage-005` the 3B answered correctly on turn one
    having read no files, and across the five arms of the 2026-09-17 ablation
    the three tasks that passed in EVERY arm were exactly the three carrying a
    grading key, while the one with no key failed in every arm.

    The sentence is also why nobody looked: it asserted the property as a fact
    instead of naming a test that held it, and a claim with nothing behind it
    is a comment with a runtime cost. `ledger/prefix.prompt_contract` makes it
    true now, and `tests/test_grader_is_not_in_the_prompt.py` keeps it true -
    by allowlist, because the next grading key will not be called
    `x-expected`, just as this one was not called `verify.py`.

    Numbers compare with a relative tolerance; strings compare case- and
    whitespace-insensitively unless `x-exact` is set.
    """

    kind = "expected_value"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        schema = contract.output_schema or {}
        expected = schema.get("x-expected")
        if expected is None or not _checks_something(expected):
            return VerifyResult(
                passed=False, score=0.0,
                observation="no x-expected on this task; expected_value cannot judge it",
                verifier_id=self.kind,
            )

        try:
            got = json.loads(artifact)
        except json.JSONDecodeError as e:
            return VerifyResult(passed=False, score=0.0,
                                observation=f"not JSON: {e}", verifier_id=self.kind)

        # The schema's structure directly, not json_schema's VERDICT: that
        # refuses a schema accepting {}, and here x-expected is the check.
        shape = _validate(got, schema)
        if shape:
            return VerifyResult(
                passed=False, score=0.0,
                observation=f"answer is not the right shape, so it cannot be "
                            f"the right value: {'; '.join(shape)}",
                verifier_id=self.kind,
            )

        exact = bool(schema.get("x-exact", False))
        wrong = list(_compare(got, expected, exact, "$"))
        return VerifyResult(
            passed=not wrong,
            score=1.0 if not wrong else 0.0,
            observation="; ".join(wrong) or "matches the expected value",
            verifier_id=self.kind,
        )


def _checks_something(expected) -> bool:
    """DR4. `_compare` finds no mismatch against an empty object, so
    `x-expected: {}` passed any object - the final sweep's "{}" included."""
    if isinstance(expected, dict):
        return any(_checks_something(v) for v in expected.values())
    return True


def _norm_text(s: str) -> str:
    return " ".join(str(s).split()).strip().lower()


def _compare(got, expected, exact: bool, path: str):
    """Yield a description of each mismatch. Empty means correct."""
    if isinstance(expected, dict):
        if not isinstance(got, dict):
            yield f"{path}: expected an object, got {type(got).__name__}"
            return
        for k, v in expected.items():
            if k not in got:
                yield f"{path}.{k}: missing"
            else:
                yield from _compare(got[k], v, exact, f"{path}.{k}")
        return
    if isinstance(expected, list):
        if not isinstance(got, list) or len(got) != len(expected):
            yield (f"{path}: expected {len(expected)} items, got "
                   f"{len(got) if isinstance(got, list) else type(got).__name__}")
            return
        for i, v in enumerate(expected):
            yield from _compare(got[i], v, exact, f"{path}[{i}]")
        return
    if isinstance(expected, bool) or expected is None:
        if got != expected:
            yield f"{path}: expected {expected!r}, got {got!r}"
        return
    if isinstance(expected, (int, float)):
        try:
            g = float(got)
        except (TypeError, ValueError):
            yield f"{path}: expected the number {expected}, got {got!r}"
            return
        tol = max(1e-9, abs(float(expected)) * 1e-6)
        if abs(g - float(expected)) > tol:
            yield f"{path}: expected {expected}, got {got}"
        return
    a, b = (str(got), str(expected)) if exact else (_norm_text(got), _norm_text(expected))
    if a != b:
        yield f"{path}: expected {expected!r}, got {got!r}"


class ContainsAllVerifier:
    """Every required substring is present. For prose with checkable facts.

    Weaker than expected_value and stronger than nothing: it cannot tell you
    the summary is good, only that it did not drop the figures it had to keep.
    """

    kind = "contains_all"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        needles = (contract.output_schema or {}).get("x-contains") or []
        # An empty needle is in every haystack, so it passed everything.
        if not needles or any(not _norm_text(n) for n in needles):
            return _refuse(self.kind, "no x-contains on this task, or an empty "
                                      "entry in it - which every answer contains")
        hay = _norm_text(artifact)
        missing = [n for n in needles if _norm_text(n) not in hay]
        return VerifyResult(
            passed=not missing,
            score=(len(needles) - len(missing)) / len(needles),
            observation=("missing: " + ", ".join(map(repr, missing))) if missing
                        else f"all {len(needles)} required strings present",
            verifier_id=self.kind,
        )


class VerifyProgramVerifier:
    """Run the task's own `verify.py` against what the agent left on disk.

    The strongest verifier in the set, and the one a real eval suite should
    use. It judges files rather than the model's closing message, so an agent
    that announces success without producing the artifact fails — and the
    check is a deterministic program, so nothing about the grader varies
    between arms of a paired trial.

    `verify.py` prints one JSON object: {"passed": bool, "score": float}.
    Binary `passed` feeds McNemar (M9.2); continuous `score` feeds the
    ratchet (M7.2), which needs to know that 0.9 beat 0.4.
    """

    kind = "verify_program"
    inspects = "workspace"
    SCRIPT = "verify.py"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        """
        CWD-SENSITIVE. `artifact_scope` entries are relative, and they are
        resolved against the PROCESS working directory rather than
        `config.root`. The harnesses run from the project root so this is
        correct there — but run from anywhere else and no `verify.py` is
        found, every task fails with "no verify.py found", and the result
        looks exactly like a suite that is simply too hard. That is a silent
        0% rather than an error, so check the observation text before
        concluding a model cannot do the work.
        """
        from ..execute.sandbox import run_sandboxed

        base = Path(root) if root else Path.cwd()
        scope = contract.artifact_scope or ["."]
        workdir = base / scope[0]

        # The grader is looked up OUTSIDE artifact_scope. A verify.py the
        # agent can read is one it can copy EXPECTED out of, and a generated
        # skill that said "read verify.py" would score 100% and look like a
        # result. Only the grader's TEXT enters the sandbox below - never as
        # a file the task could open.
        #
        # `base` also fixes a second bug: this used to resolve against the
        # process working directory, so a harness run from anywhere but the
        # project root reported "no verify.py found" on every task - a silent
        # 0% indistinguishable from a suite that is simply too hard.
        script = None
        if contract.grader_dir:
            cand = base / contract.grader_dir / self.SCRIPT
            if cand.exists():
                script = cand
        if script is None:
            # Legacy suites shipping the grader beside the work. Kept so old
            # records replay; the leak test fails for any suite relying on it.
            for s_ in scope:
                cand = base / s_ / self.SCRIPT
                if cand.exists():
                    script, workdir = cand, base / s_
                    break
        if script is None:
            return VerifyResult(
                passed=False, score=0.0,
                observation=(f"no {self.SCRIPT} found. grader_dir="
                             f"{contract.grader_dir!r} scope={scope} root={base}"),
                verifier_id=self.kind,
            )

        # verify.py addresses its files relatively ("output/answer.json"), but
        # the sandbox links the task directory in under its own name and runs
        # with the workdir as cwd — so without this chdir every check looks in
        # the wrong place and reports a missing answer that is actually there.
        nonce = secrets.token_hex(16)
        harness = (
            "import os as _o\n"
            "from json.encoder import encode_basestring_ascii as _q\n"
            "try:\n    _o.remove(__file__)\nexcept OSError:\n    pass\n"
            f"_o.chdir({workdir.name!r})\n\n"
            + script.read_text(encoding="utf-8")
            + "\n\ndef _verdict(_w=_o.write, _x=_o._exit, _f=float, _q=_q, _s=str):\n"
            "    try:\n"
            "        _p, _sc = check()\n"
            "        _sc = _f(_sc)\n"
            "        _sc = _sc if -1e308 < _sc < 1e308 else 0.0\n"
            "        _line = '{\"passed\": %s, \"score\": %r}' % ('true' if _p is True else 'false', _sc)\n"
            "    except BaseException as _e:\n"
            "        _line = '{\"passed\": false, \"score\": 0.0, \"error\": %s}' % _q(\n"
            "            _s(type(_e).__name__) + ': ' + _s(_e))\n"
            f"    _w(1, ('\\n\\x02RESULT{nonce}' + _line + '\\n').encode())\n"
            "    _x(0)\n\n\n"
            "_verdict()\n"
        )
        r = run_sandboxed(harness, allowed_paths=[str(workdir)], timeout_s=60)
        return self._read_verdict(r, nonce)

    @classmethod
    def _read_verdict(cls, r: dict, nonce: str) -> VerifyResult:
        out = r.get("stdout", "")
        marker = "\x02RESULT" + nonce
        if marker not in out:
            return VerifyResult(
                passed=False, score=0.0,
                observation=(f"{cls.SCRIPT} produced no verdict.\n"
                             f"stdout: {out[-800:]}\nstderr: {r.get('stderr','')[-800:]}"),
                verifier_id=cls.kind,
            )
        try:
            payload = json.loads(out.rsplit(marker, 1)[1].strip().splitlines()[0])
        except (json.JSONDecodeError, IndexError) as e:
            return VerifyResult(passed=False, score=0.0,
                                observation=f"unparseable verdict: {e}",
                                verifier_id=cls.kind)

        note = payload.get("error", "")
        return VerifyResult(
            passed=payload.get("passed") is True,
            score=float(payload.get("score", 0.0)),
            observation=(f"{cls.SCRIPT}: passed={payload.get('passed')} "
                         f"score={payload.get('score')}"
                         + (f" · {note}" if note else "")
                         + (f"\n{r.get('stderr','')[-400:]}" if r.get("stderr") else "")),
            verifier_id=cls.kind,
        )


def default_verifiers(ctx=None) -> dict[str, Verifier]:
    reg: dict[str, Verifier] = {
        "always_pass": AlwaysPass(),
        "pytest": PytestVerifier(),
        "ruff": RuffVerifier(),
        "json_schema": JsonSchemaVerifier(),
        "expected_value": ExpectedValueVerifier(),
        "contains_all": ContainsAllVerifier(),
        "verify_program": VerifyProgramVerifier(),
        "execution": ExecutionVerifier(),
        "citation_check": CitationCheckVerifier(),
    }
    if ctx is not None:
        reg["self_consistency"] = SelfConsistencyVerifier(ctx)
    return reg
