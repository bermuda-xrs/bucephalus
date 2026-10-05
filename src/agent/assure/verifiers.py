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
    inspects: str

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult: ...


def inspects(verifier) -> str:
    return getattr(verifier, "inspects", "answer")


def _refuse(kind: str, why: str) -> VerifyResult:
    return VerifyResult(passed=False, score=0.0, observation=why, verifier_id=kind)


def _workdir(contract: TaskContract, root: str | None) -> Path:
    base = Path(root) if root else Path.cwd()
    return base / (contract.artifact_scope or ["."])[0]


class AlwaysPass:
    kind = "always_pass"
    inspects = "nothing"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        return VerifyResult(
            passed=True, score=1.0, observation="no criterion set", verifier_id=self.kind
        )


class PytestVerifier:
    kind = "pytest"
    inspects = "workspace"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        from ..execute.sandbox import run_sandboxed

        workdir = _workdir(contract, root)
        if not workdir.is_dir():
            return _refuse(self.kind, f"no workspace at {workdir}")
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
        mine = normalize(artifact)
        agreement = sum(normalize(s) == mine for s in samples) / self.k
        return VerifyResult(
            passed=agreement >= DEFAULT_AGREEMENT,
            score=agreement,
            observation=f"{agreement:.2f} of {self.k} samples agree with the answer",
            verifier_id=self.kind,
        )


class ExpectedValueVerifier:
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
    if isinstance(expected, dict):
        return any(_checks_something(v) for v in expected.values())
    return True


def _norm_text(s: str) -> str:
    return " ".join(str(s).split()).strip().lower()


def _compare(got, expected, exact: bool, path: str):
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
    kind = "contains_all"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        needles = (contract.output_schema or {}).get("x-contains") or []
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
    kind = "verify_program"
    inspects = "workspace"
    SCRIPT = "verify.py"

    def check(self, artifact: str, contract: TaskContract,
              root: str | None = None) -> VerifyResult:
        from ..execute.sandbox import run_sandboxed

        base = Path(root) if root else Path.cwd()
        scope = contract.artifact_scope or ["."]
        workdir = base / scope[0]

        script = None
        if contract.grader_dir:
            cand = base / contract.grader_dir / self.SCRIPT
            if cand.exists():
                script = cand
        if script is None:
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
