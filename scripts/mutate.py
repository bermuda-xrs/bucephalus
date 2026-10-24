from __future__ import annotations

import argparse
import ast
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_FLIP = {ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.GtE, ast.GtE: ast.Lt,
         ast.Gt: ast.LtE, ast.LtE: ast.Gt, ast.In: ast.NotIn, ast.NotIn: ast.In,
         ast.Is: ast.IsNot, ast.IsNot: ast.Is}


def _nodes(tree: ast.AST, only: set[str] | None) -> list[ast.AST]:
    roots: list[ast.AST] = []
    for n in tree.body:
        name = getattr(n, "name", None)
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            tg = n.targets if isinstance(n, ast.Assign) else [n.target]
            name = next((t.id for t in tg if isinstance(t, ast.Name)), None)
        if only is None or name in only:
            roots.append(n)
        elif isinstance(n, ast.ClassDef):
            roots += [m for m in n.body
                      if f"{n.name}.{getattr(m, 'name', '')}" in only]
    out: list[ast.AST] = []

    def walk(n: ast.AST) -> None:
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant):
            return
        out.append(n)
        for c in ast.iter_child_nodes(n):
            walk(c)
    for r in roots:
        walk(r)
    return out


def _ops(n: ast.AST) -> list[str]:
    ops: list[str] = []
    if isinstance(n, ast.Compare):
        ops += [f"cmp{i}" for i, o in enumerate(n.ops) if type(o) in _FLIP]
    elif isinstance(n, ast.BoolOp):
        ops.append("boolop")
    elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
        ops.append("drop_not")
    elif isinstance(n, (ast.If, ast.While)) and not isinstance(n.test, (ast.Compare, ast.UnaryOp)):
        ops.append("negate")
    elif isinstance(n, ast.Constant) and isinstance(n.value, bool):
        ops.append("bool")
    elif isinstance(n, ast.Constant) and type(n.value) is int:
        ops.append("int")
    elif isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub)):
        ops.append("addsub")
    elif isinstance(n, ast.Return) and n.value is not None and not (
            isinstance(n.value, ast.Constant) and n.value.value is None):
        ops.append("return_none")
    elif isinstance(n, ast.Raise):
        ops.append("raise_pass")
    elif isinstance(n, (ast.Tuple, ast.List, ast.Set)) and len(n.elts) >= 2 and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in n.elts):
        ops += [f"drop{i}" for i in range(len(n.elts))]
    return ops


def mutants(source: str, only: set[str] | None = None) -> list[dict]:
    tree = ast.parse(source)
    return [{"node": i, "op": op, "line": getattr(n, "lineno", 0)}
            for i, n in enumerate(_nodes(tree, only)) for op in _ops(n)]


class _Swap(ast.NodeTransformer):
    def __init__(self, target: ast.AST, new: ast.AST):
        self.target, self.new = target, new

    def visit(self, node):
        if node is self.target:
            return self.new
        return self.generic_visit(node)


def apply(source: str, m: dict, only: set[str] | None = None) -> str:
    tree = ast.parse(source)
    n = _nodes(tree, only)[m["node"]]
    op = m["op"]
    new: ast.AST
    if op.startswith("cmp"):
        new = copy.copy(n)
        new.ops = list(n.ops)
        i = int(op[3:])
        new.ops[i] = _FLIP[type(n.ops[i])]()
    elif op == "boolop":
        new = ast.BoolOp(op=ast.Or() if isinstance(n.op, ast.And) else ast.And(), values=n.values)
    elif op == "drop_not":
        new = n.operand
    elif op == "negate":
        new = copy.copy(n)
        new.test = ast.UnaryOp(op=ast.Not(), operand=n.test)
    elif op == "bool":
        new = ast.Constant(value=not n.value)
    elif op == "int":
        new = ast.Constant(value=n.value + 1)
    elif op == "addsub":
        new = ast.BinOp(left=n.left, op=ast.Sub() if isinstance(n.op, ast.Add) else ast.Add(),
                        right=n.right)
    elif op == "return_none":
        new = ast.Return(value=ast.Constant(value=None))
    elif op == "raise_pass":
        new = ast.Pass()
    elif op.startswith("drop"):
        new = copy.copy(n)
        i = int(op[4:])
        new.elts = [e for j, e in enumerate(n.elts) if j != i]
    else:
        raise ValueError(op)
    tree = _Swap(n, new).visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def _run(tree_dir: Path, tests: list[str], timeout: float,
         why: list | None = None) -> tuple[str, float]:
    from agent import proc as _proc

    env = dict(os.environ, PYTHONPATH=str(tree_dir / "src"), PYTHONDONTWRITEBYTECODE="1")
    env.pop("ANTHROPIC_API_KEY", None)
    t0 = time.time()
    with tempfile.TemporaryFile() as out:  # one per mutant: an unclosed one leaked a handle each
        p = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "pytest", "-x", "-q", "-rf", "-p", "no:cacheprovider",
             "-o", "addopts=", "-o", "pythonpath=", *tests],
            cwd=str(ROOT), env=env, stdout=out, stderr=subprocess.STDOUT)
        try:
            rc = p.wait(timeout)
        except subprocess.TimeoutExpired:
            _proc.kill_tree(p.pid)
            return "timeout", time.time() - t0
        if why is not None:
            out.seek(0)
            why.extend(line.split()[1] for line in out.read().decode("utf-8", "replace").splitlines()
                       if line.startswith(("FAILED ", "ERROR ")) and len(line.split()) > 1)
    return ("survived" if rc == 0 else "killed"), time.time() - t0


def _copy(dst: Path) -> Path:
    shutil.copytree(ROOT / "src", dst / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    for f in [ROOT / "pyproject.toml", *ROOT.glob("config*.yaml")]:
        shutil.copy2(f, dst / f.name)
    return dst


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("module")
    ap.add_argument("tests", nargs="+")
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out")
    ap.add_argument("--recheck", action="store_true",
                    help="re-run each survivor against the WHOLE suite before reporting it")
    ap.add_argument("--timeout-factor", type=float, default=4.0)
    ap.add_argument("--min-timeout", type=float, default=60.0)
    ap.add_argument("--rerun", help="a previous --out: re-run only its timed-out mutants")
    ap.add_argument("--survivors-from", help="a previous --out: recheck only its survivors "
                    "against the whole suite (implies --recheck)")
    a = ap.parse_args(argv)
    only = set(a.only.split(",")) if a.only else None
    mod = Path(a.module).resolve()
    rel = mod.relative_to(ROOT)
    source = mod.read_text(encoding="utf-8")
    ms = mutants(source, only)
    if a.rerun:
        prev = json.loads(Path(a.rerun).read_text(encoding="utf-8"))["results"]
        again = {(r["node"], r["op"]) for r in prev if r["status"] in ("timeout", "killed-timeout")}
        ms = [m for m in ms if (m["node"], m["op"]) in again]
    work = Path(tempfile.mkdtemp(prefix="bucephalus-mut-"))
    trees = [_copy(work / f"w{i}") for i in range(a.jobs)]
    (trees[0] / rel).write_text(ast.unparse(ast.parse(source)), encoding="utf-8")
    status, base = _run(trees[0], a.tests, 600)
    if status != "survived":
        print(f"baseline FAILED ({status}); fix that first")
        return 2
    timeout = max(a.min_timeout, a.timeout_factor * base)
    print(f"{rel}: {len(ms)} mutants, baseline {base:.1f}s, timeout {timeout:.0f}s")
    free = list(trees)
    import threading
    lock = threading.Lock()

    def one(m: dict) -> dict:
        with lock:
            t = free.pop()
        try:
            (t / rel).write_text(apply(source, m, only), encoding="utf-8")
            m["status"], m["seconds"] = _run(t, a.tests, timeout)
        finally:
            with lock:
                free.append(t)
        return m

    if a.survivors_from:
        prev = json.loads(Path(a.survivors_from).read_text(encoding="utf-8"))["results"]
        keep = {(r["node"], r["op"]) for r in prev if r["status"] == "survived"}
        results = [dict(r) for r in prev]
        for r in results:
            if (r["node"], r["op"]) in keep:
                r["status"] = "survived"
        a.recheck = True
    else:
        with ThreadPoolExecutor(a.jobs) as ex:
            results = list(ex.map(one, ms))
    survivors = [r for r in results if r["status"] == "survived"]
    if a.recheck and survivors:
        t = trees[0]
        (t / rel).write_text(ast.unparse(ast.parse(source)), encoding="utf-8")
        ok, _ = _run(t, ["tests", "-n", "12", "--dist", "loadgroup"], 1800)
        if ok != "survived":
            print("recheck baseline FAILED: the whole suite does not pass unmutated here; "
                  "survivors are reported as survived")
            survivors = []
        for r in survivors:
            (t / rel).write_text(apply(source, r, only), encoding="utf-8")
            why: list[str] = []
            st, _ = _run(t, ["tests", "-n", "12", "--dist", "loadgroup"], 900, why)
            if st == "killed":
                r["status"], r["killed_by"] = "killed-by-suite", why[:3]
            elif st == "timeout":
                r["status"] = "timeout"
    shutil.rmtree(work, ignore_errors=True)
    lines = source.splitlines()
    survived = [r for r in results if r["status"] == "survived"]
    timeouts = [r for r in results if r["status"] == "timeout"]
    for r in survived:
        print(f"  SURVIVED {rel}:{r['line']} {r['op']:<12} {lines[r['line'] - 1].strip()[:90]}")
    for r in timeouts:
        print(f"  TIMEOUT  {rel}:{r['line']} {r['op']:<12} {lines[r['line'] - 1].strip()[:90]}")
    decided = len(results) - len(timeouts)
    killed = decided - len(survived)
    score = killed / decided if decided else 1.0
    print(f"{rel}: {killed}/{decided} killed, {len(timeouts)} undecided (timeout), score {score:.1%}")
    if a.out:
        Path(a.out).write_text(json.dumps({"module": str(rel), "only": sorted(only or []),
                                           "tests": a.tests, "score": score,
                                           "results": results}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
