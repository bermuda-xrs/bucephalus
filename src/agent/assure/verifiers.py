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

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult: ...


class AlwaysPass:
    kind = "always_pass"

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
        return VerifyResult(
            passed=True, score=1.0, observation="no criterion set", verifier_id=self.kind
        )


class PytestVerifier:
    kind = "pytest"

    def __init__(self, test_path: str = "tests", cwd: str | None = None):
        self.test_path = test_path
        self.cwd = cwd

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
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

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
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

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
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

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
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

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
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

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
        from ..router.cascade import max_cluster_fraction, normalize

        backend = self.ctx.backends[self.ctx.active_local_tier() or "cloud"]
        prompt = (
            "Answer the question. Output only the answer, no preamble.\n\n"
            f"Question: {contract.goal}"
        )
        samples = [
            backend.complete("You answer concisely.", prompt, None, 512, 0.7).text
            for _ in range(self.k)
        ]
        agreement = max_cluster_fraction([normalize(s) for s in samples])
        return VerifyResult(
            passed=agreement >= 0.67,
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

    def check(self, artifact: str, contract: TaskContract) -> VerifyResult:
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


def default_verifiers(ctx=None) -> dict[str, Verifier]:
    reg: dict[str, Verifier] = {
        "always_pass": AlwaysPass(),
        "pytest": PytestVerifier(),
        "ruff": RuffVerifier(),
        "json_schema": JsonSchemaVerifier(),
        "execution": ExecutionVerifier(),
        "citation_check": CitationCheckVerifier(),
    }
    if ctx is not None:
        reg["self_consistency"] = SelfConsistencyVerifier(ctx)
    return reg
