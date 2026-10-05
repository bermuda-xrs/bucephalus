CASES = [
    ("Hello, World!", "hello-world"),
    ("  Multiple   spaces  here ", "multiple-spaces-here"),
    ("UPPER case: Mixed; Words.", "upper-case-mixed-words"),
    ("single", "single"),
    ("tabs\tand\nnewlines", "tabs-and-newlines"),
    ("(parenthesised) words, too!", "parenthesised-words-too"),
]

# The graded code runs in a child: in this process it could rewrite check() or its verdict.
CHILD = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("strings_under_test", "strings.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
fn = getattr(mod, "slugify", None)
out = []
for given in json.loads(sys.argv[1]):
    try:
        r = fn(given)
        out.append(str.__str__(r) if isinstance(r, str) else None)
    except Exception:
        out.append(None)
sys.stdout.write("\x00" + json.dumps(out))
"""


def check():
    import json
    import os
    import subprocess
    import sys

    if not os.path.exists("strings.py"):
        return False, 0.0
    try:
        r = subprocess.run([sys.executable, "-c", CHILD, json.dumps([g for g, _ in CASES])],
                           capture_output=True, text=True, encoding="utf-8", timeout=30)
        got = json.loads(r.stdout.rsplit("\x00", 1)[1])
    except (subprocess.TimeoutExpired, IndexError, ValueError):
        return False, 0.0
    if not isinstance(got, list) or len(got) != len(CASES):
        return False, 0.0
    ok = sum(type(g) is str and g == want for g, (_, want) in zip(got, CASES))
    return ok == len(CASES), ok / len(CASES)
