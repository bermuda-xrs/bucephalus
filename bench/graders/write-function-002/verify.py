"""Grader for write-function-002. Held out: grader_dir lies outside
artifact_scope, and verify_program sends only this file's TEXT into the
sandbox. Runs with the workspace (work/) as its working directory.

The cases avoid the spec's ambiguities - whether an apostrophe or an inner
hyphen splits a word - so that any reasonable reading of "lowercases, strips
punctuation, and joins words with hyphens" passes and only a wrong one fails.
"""

CASES = [
    ("Hello, World!", "hello-world"),
    ("  Multiple   spaces  here ", "multiple-spaces-here"),
    ("UPPER case: Mixed; Words.", "upper-case-mixed-words"),
    ("single", "single"),
    ("tabs\tand\nnewlines", "tabs-and-newlines"),
    ("(parenthesised) words, too!", "parenthesised-words-too"),
]


def check():
    import importlib.util
    import os

    if not os.path.exists("strings.py"):
        return False, 0.0
    spec = importlib.util.spec_from_file_location("strings_under_test", "strings.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, "slugify", None)
    if not callable(fn):
        return False, 0.0
    ok = 0
    for given, want in CASES:
        try:
            ok += fn(given) == want
        except Exception:  # noqa: BLE001 - a raising slugify fails the case
            pass
    return ok == len(CASES), ok / len(CASES)
