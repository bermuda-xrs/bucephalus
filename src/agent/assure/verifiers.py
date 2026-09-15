"""M7.1 — the verifier registry.

The main rule: never let the agent revise its own work based purely on its own
opinion. It needs something real to check against — run the code and read the
actual error, look up the fact. If it could spot the mistake by thinking
harder, it would not have made it.

For non-code work, citation_check and checker_program do the heavy lifting.
Do not skip them because they are harder: a system that can only verify code
cannot verify skills about writing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from ..kernel.types import TaskContract, VerifyResult


class Verifier(Protocol):
    kind: str

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult: ...


class AlwaysPass:
    kind = "always_pass"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        return VerifyResult(
            passed=True, score=1.0, observation="no criterion set", verifier_id=self.kind
        )


class PytestVerifier:
    kind = "pytest"

    def __init__(self, test_path: str = "tests", cwd: str | None = None):
        self.test_path = test_path
        self.cwd = cwd

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pytest", self.test_path, "-q", "--no-header"],
            capture_output=True,
            text=True,
            cwd=self.cwd,
            timeout=600,
        )
        out = (proc.stdout + proc.stderr)[-6000:]
        passed = proc.returncode == 0
        score = 1.0 if passed else _pytest_score(out)
        return VerifyResult(
            passed=passed, score=score, observation=out, verifier_id=self.kind
        )


def _pytest_score(output: str) -> float:
    m = re.search(r"(\d+) passed", output)
    f = re.search(r"(\d+) failed", output)
    p = int(m.group(1)) if m else 0
    fl = int(f.group(1)) if f else 0
    return p / (p + fl) if (p + fl) else 0.0


class RuffVerifier:
    kind = "ruff"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        if shutil.which("ruff") is None:
            return VerifyResult(
                passed=True, score=1.0, observation="ruff not installed; skipped",
                verifier_id=self.kind,
            )
        proc = subprocess.run(  # noqa: S603
            ["ruff", "check", "--output-format", "concise", "."],
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
        try:
            data = json.loads(artifact)
        except json.JSONDecodeError as e:
            return VerifyResult(
                passed=False, score=0.0,
                observation=f"output is not valid JSON: {e}", verifier_id=self.kind,
            )
        errors = _validate(data, contract.output_schema or {})
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
    kind = "execution"

    def __init__(self, script_path: str | None = None):
        self.script_path = script_path

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        from ..execute.sandbox import run_sandboxed

        code = Path(self.script_path).read_text(encoding="utf-8") if self.script_path else artifact
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
        from ..router.cascade import (
            DEFAULT_AGREEMENT, max_cluster_fraction, normalize)

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
        agreement = max_cluster_fraction([normalize(s) for s in samples])
        # DEFAULT_AGREEMENT, not the literal 0.67. Two of three is 0.6667,
        # which is LESS than 0.67, so the literal rejects exactly the case it
        # was written to accept. CORRECTIONS.md 1.5 records this fix being
        # made in cascade.py; this copy was missed.
        return VerifyResult(
            passed=agreement >= DEFAULT_AGREEMENT,
            score=agreement,
            observation=f"agreement across {self.k} samples = {agreement:.2f}",
            verifier_id=self.kind,
        )


class CheckerProgramVerifier:
    """A generated program that asserts properties of the artifact.

    The program is written by a DIFFERENT model from the drafter (M7.4 rule 1),
    and it runs in the sandbox. Exit code 0 means the properties hold.
    """

    kind = "checker_program"

    def __init__(self, program: str):
        self.program = program

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        from ..execute.sandbox import run_sandboxed

        harness = (
            "import json,sys\n"
            f"ARTIFACT = {artifact!r}\n"
            + self.program
        )
        r = run_sandboxed(harness, allowed_paths=[], timeout_s=30)
        ok = r["returncode"] == 0
        return VerifyResult(
            passed=ok,
            score=1.0 if ok else 0.0,
            observation=(r["stdout"] + r["stderr"])[-3000:] or "checker silent",
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
    so a task file declares its own right answer and nothing in the agent's
    context ever sees it.

    Numbers compare with a relative tolerance; strings compare case- and
    whitespace-insensitively unless `x-exact` is set.
    """

    kind = "expected_value"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        schema = contract.output_schema or {}
        expected = schema.get("x-expected")
        if expected is None:
            return VerifyResult(
                passed=False, score=0.0,
                observation="no x-expected on this task; expected_value cannot judge it",
                verifier_id=self.kind,
            )

        shape = JsonSchemaVerifier().check(artifact, contract)
        if not shape.passed:
            return VerifyResult(
                passed=False, score=0.0,
                observation=f"answer is not the right shape, so it cannot be "
                            f"the right value: {shape.observation}",
                verifier_id=self.kind,
            )

        try:
            got = json.loads(artifact)
        except json.JSONDecodeError as e:
            return VerifyResult(passed=False, score=0.0,
                                observation=f"not JSON: {e}", verifier_id=self.kind)

        exact = bool(schema.get("x-exact", False))
        wrong = list(_compare(got, expected, exact, "$"))
        return VerifyResult(
            passed=not wrong,
            score=1.0 if not wrong else 0.0,
            observation="; ".join(wrong) or "matches the expected value",
            verifier_id=self.kind,
        )


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
        if not needles:
            return VerifyResult(
                passed=False, score=0.0,
                observation="no x-contains on this task", verifier_id=self.kind,
            )
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
        harness = (
            f"import os as _os\n_os.chdir({workdir.name!r})\n\n"
            + script.read_text(encoding="utf-8")
            + "\n\nimport json as _j, sys as _s\n"
            "try:\n"
            "    _p, _sc = check()\n"
            "    _s.stdout.write('\\x02RESULT' + _j.dumps("
            "{'passed': bool(_p), 'score': float(_sc)}))\n"
            "except Exception as _e:\n"
            "    _s.stdout.write('\\x02RESULT' + _j.dumps("
            "{'passed': False, 'score': 0.0, 'error': type(_e).__name__ + ': ' + str(_e)}))\n"
        )
        r = run_sandboxed(harness, allowed_paths=[str(workdir)], timeout_s=60)

        out = r.get("stdout", "")
        marker = "\x02RESULT"
        if marker not in out:
            return VerifyResult(
                passed=False, score=0.0,
                observation=(f"{self.SCRIPT} produced no verdict.\n"
                             f"stdout: {out[-800:]}\nstderr: {r.get('stderr','')[-800:]}"),
                verifier_id=self.kind,
            )
        try:
            payload = json.loads(out.split(marker, 1)[1].strip().splitlines()[0])
        except (json.JSONDecodeError, IndexError) as e:
            return VerifyResult(passed=False, score=0.0,
                                observation=f"unparseable verdict: {e}",
                                verifier_id=self.kind)

        note = payload.get("error", "")
        return VerifyResult(
            passed=bool(payload.get("passed")),
            score=float(payload.get("score", 0.0)),
            observation=(f"{self.SCRIPT}: passed={payload.get('passed')} "
                         f"score={payload.get('score')}"
                         + (f" · {note}" if note else "")
                         + (f"\n{r.get('stderr','')[-400:]}" if r.get("stderr") else "")),
            verifier_id=self.kind,
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
