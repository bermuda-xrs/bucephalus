from __future__ import annotations

import json
import random

FAMILY_VERSION = 1
SEEDS = 40

PLACES = ("the north meadow", "the reading room", "the east greenhouse", "the seed library", "the river path",
          "the old orchard", "the print shop", "the herb garden", "the weather mast", "the map room")
HAPPENINGS = ("was quiet for most of the morning", "needed a second watering", "had its labels replaced",
              "drew more visitors than last week", "was closed for an hour at noon", "was measured again after lunch",
              "showed no change since Tuesday", "was swept before opening", "had a new bench installed",
              "was used for a small workshop")
ADJ = ("Silent", "Amber", "Hollow", "Bright", "Patient", "Northern", "Quiet", "Copper", "Winter", "Gentle")
NOUN = ("Orchard", "Harbour", "Lantern", "Meadow", "Compass", "Garden", "River", "Atlas", "Kestrel", "Willow")
SENDERS = ("Willow and Finch", "Harbor Lane Books", "Copper Kettle Bakery", "Greenfield Nursery", "Tidewater Prints",
           "Maple Row Studio")
CROPS = ("carrots", "beans", "leeks", "squash", "onions", "kale")
MODULES = ("textkit", "numtools", "helpers", "toolbox", "smalllib")


def split_of(seed: int) -> str:
    return "dev" if seed % 2 == 0 else "heldout"


def _rng(family: str, seed: int) -> random.Random:
    return random.Random(f"{family}:{seed}:{FAMILY_VERSION}")


def _filler(rng: random.Random, sentences: int) -> str:
    return " ".join(f"{rng.choice(PLACES).capitalize()} {rng.choice(HAPPENINGS)}." for _ in range(sentences))


def _ref(rng: random.Random, used: set) -> str:
    while True:
        r = f"{rng.choice('BCDFGHJKLMNPQRSTVWXZ')}{rng.choice('BCDFGHJKLMNPQRSTVWXZ')}-{rng.randint(1000, 9999)}"
        if r not in used:
            used.add(r)
            return r


def _spec(family, seed, goal, criterion, schema, fixtures, graders=None, version=FAMILY_VERSION):
    key = f"{family}-{seed:03d}"
    return {"key": key, "family": family, "seed": seed, "split": split_of(seed), "family_version": version,
            "goal": goal, "success_criterion": criterion, "output_schema": schema, "artifact_scope": ["work"],
            "fixtures": fixtures, "graders": graders or {},
            "grader_dir": f"grading/{key}" if graders else None, "budget_usd": 0.0, "budget_seconds": 600}


def needle(seed: int) -> dict:
    rng = _rng("needle", seed)
    used: set = set()
    titles = rng.sample([f"The {a} {n}" for a in ADJ for n in NOUN], 8)
    marks = {t: _ref(rng, used) for t in titles}
    parts = ["# Library catalogue notes\n"]
    for t in titles:
        parts.append(f"## {t}\n\n{_filler(rng, 7)}\n\nShelf mark for {t}: {marks[t]}.\n\n{_filler(rng, 7)}\n")
    target = rng.choice(titles)
    goal = (f"In work/catalogue.md, find the shelf mark for {target}. Answer as JSON with key 'shelf_mark'.")
    schema = {"type": "object", "required": ["shelf_mark"], "properties": {"shelf_mark": {"type": "string"}},
              "x-expected": {"shelf_mark": marks[target]}}
    return _spec("needle", seed, goal, "expected_value", schema, {"work/catalogue.md": "\n".join(parts)})


def multipage(seed: int) -> dict:
    rng = _rng("multipage", seed)
    used: set = set()
    refs = [_ref(rng, used) for _ in range(3)]
    sections = 24
    at = sorted(rng.sample(range(0, 8), 1) + rng.sample(range(8, 16), 1) + rng.sample(range(16, sections), 1))
    parts = ["# Field journal\n"]
    k = 0
    for i in range(sections):
        body = _filler(rng, 9)
        if i in at:
            body += f"\n\nFINDING (ref {refs[k]}): {rng.choice(PLACES)} {rng.choice(HAPPENINGS)}."
            k += 1
        parts.append(f"## Day {i + 1}\n\n{body}\n\n{_filler(rng, 9)}\n")
    goal = ("Read work/journal.md and report the reference of each of the three FINDING lines in it, as JSON with "
            "key 'refs' (a list of the three references). The findings are spread through the journal.")
    schema = {"type": "object", "required": ["refs"], "properties": {"refs": {"type": "array"}}, "x-contains": refs}
    return _spec("multipage", seed, goal, "contains_all", schema, {"work/journal.md": "\n".join(parts)})


REFERENCE = {
    "slug": "import re\n\ndef {name}(s):\n    words = re.sub(r'[^a-z0-9 ]', '', s.lower().replace('\\t', ' ').replace('\\n', ' ')).split()\n"
            "    return '{sep}'.join(words)\n",
    "vowels": "def {name}(s):\n    return sum(c in 'aeiou' for c in s.lower())\n",
    "reverse_words": "def {name}(s):\n    return ' '.join(reversed(s.split()))\n",
    "clamp": "def {name}(x):\n    return max({lo}, min({hi}, x))\n",
    "rle": "def {name}(s):\n    out, i = [], 0\n    while i < len(s):\n        j = i\n        while j < len(s) and s[j] == s[i]:\n"
           "            j += 1\n        out.append(s[i] + str(j - i))\n        i = j\n    return ''.join(out)\n",
    "digit_sum": "def {name}(n):\n    return sum(int(d) for d in str(abs(n)))\n",
    "title": "def {name}(s):\n    return ' '.join(w[:1].upper() + w[1:].lower() for w in s.split())\n",
    "fizz": "def {name}(n):\n    if n % {a} == 0 and n % {b} == 0:\n        return '{w1}{w2}'\n    if n % {a} == 0:\n"
            "        return '{w1}'\n    if n % {b} == 0:\n        return '{w2}'\n    return str(n)\n",
}
DESCRIBE = {
    "slug": "lowercases s, removes every character that is not a letter, a digit or a space (tabs and newlines count "
            "as spaces), and joins the remaining words with '{sep}'",
    "vowels": "returns how many vowels (a, e, i, o, u, either case) s contains",
    "reverse_words": "returns the words of s in reverse order, joined by single spaces",
    "clamp": "returns x limited to the range {lo} to {hi} inclusive",
    "rle": "run-length encodes s: each run of one character becomes the character followed by the run length, "
           "so 'aaab' becomes 'a3b1'",
    "digit_sum": "returns the sum of the decimal digits of the integer n, ignoring its sign",
    "title": "capitalises the first letter of each word of s, lowercases the rest, and joins the words with single spaces",
    "fizz": "returns '{w1}{w2}' if n is divisible by both {a} and {b}, '{w1}' if only by {a}, '{w2}' if only by {b}, "
            "and otherwise n as a string",
}
GRADER = '''import json

CASES = json.loads({cases!r})
CHILD = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("under_test", {file!r})
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
fn = getattr(mod, {name!r}, None)
out = []
for given in json.loads(sys.argv[1]):
    try:
        out.append(fn(given))
    except Exception:
        out.append(None)
sys.stdout.write("\\x00" + json.dumps(out))
"""


def check():
    import os
    import subprocess
    import sys

    if not os.path.exists({file!r}):
        return False, 0.0
    try:
        r = subprocess.run([sys.executable, "-c", CHILD, json.dumps([g for g, _ in CASES])],
                           capture_output=True, text=True, encoding="utf-8", timeout=30)
        got = json.loads(r.stdout.rsplit("\\x00", 1)[1])
    except (subprocess.TimeoutExpired, IndexError, ValueError):
        return False, 0.0
    ok = sum(g == want and type(g) is type(want) for g, (_, want) in zip(got, CASES))
    return ok == len(CASES), ok / len(CASES)
'''


def _cases(kind: str, rng: random.Random) -> list:
    if kind in ("slug", "vowels", "reverse_words", "title"):
        pool = ["Hello, World!", "  Many   spaces here ", "MIXED case: Words; Too.", "single", "tabs\tand\nlines",
                "(brackets) and, commas!", "Quiet  Meadow at dawn", "x"]
        return rng.sample(pool, 6)
    if kind == "rle":
        return ["".join(rng.choice("ab") * rng.randint(1, 3) for _ in range(rng.randint(1, 4))) for _ in range(6)]
    return [rng.randint(-60, 160) for _ in range(6)]


def write_function(seed: int) -> dict:
    rng = _rng("write_function", seed)
    kind = sorted(REFERENCE)[(seed // 2) % len(REFERENCE)]
    name = {"slug": "slugify", "vowels": "count_vowels", "reverse_words": "reverse_words", "clamp": "clamp",
            "rle": "run_length", "digit_sum": "digit_sum", "title": "title_words", "fizz": "fizz"}[kind]
    p = {"name": name, "sep": rng.choice("-_."), "lo": rng.randint(0, 20), "hi": rng.randint(40, 90),
         "a": rng.choice((2, 3)), "b": rng.choice((5, 7)), "w1": rng.choice(("Fizz", "Ping")), "w2": rng.choice(("Buzz", "Pong"))}
    source = REFERENCE[kind].format(**p)
    space: dict = {}
    exec(source, space)  # noqa: S102 - our own reference source, to compute the expected answers
    cases = [[c, space[name](c)] for c in _cases(kind, rng)]
    module = rng.choice(MODULES)
    file = f"{module}.py"
    goal = (f"Write work/{file} containing a function {name}({'x' if kind == 'clamp' else 'n' if kind in ('digit_sum', 'fizz') else 's'}) "
            f"that {DESCRIBE[kind].format(**p)}. Then run it to confirm it works.")
    grader = GRADER.format(cases=json.dumps(cases), file=file, name=name)
    schema = {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}
    spec = _spec("write_function", seed, goal, "verify_program", schema, {}, {"verify.py": grader})
    spec["reference"] = {f"work/{file}": source}
    return spec


def extract_json(seed: int) -> dict:
    rng = _rng("extract_json", seed)
    order = f"ORD-{rng.randint(10000, 99999)}"
    sender = rng.choice(SENDERS)
    weights = [round(rng.uniform(0.4, 9.5), 1) for _ in range(rng.randint(3, 6))]
    total = round(sum(weights), 1)
    lines = [f"Parcel {i + 1}: {w} kg" for i, w in enumerate(weights)]
    text = (f"DELIVERY NOTE\n\n{_filler(rng, 4)}\n\nOrder number: {order}\nSender: {sender}\n"
            f"Parcels: {len(weights)}\n" + "\n".join(lines) + f"\nTotal weight: {total} kg\n\n{_filler(rng, 4)}\n")
    goal = ("Read work/delivery.txt and answer as JSON with keys 'order' (the order number), 'sender', and 'total_kg' "
            "(the total weight as a number).")
    schema = {"type": "object", "required": ["order", "sender", "total_kg"],
              "properties": {"order": {"type": "string"}, "sender": {"type": "string"}, "total_kg": {"type": "number"}},
              "x-expected": {"order": order, "sender": sender, "total_kg": total}}
    return _spec("extract_json", seed, goal, "expected_value", schema, {"work/delivery.txt": text})


CONFIG_KEYS = {"irrigation": ("start_hour", "minutes", "zones", "skip_if_rain"),
               "lights": ("on_hour", "off_hour", "brightness"),
               "sensors": ("interval_s", "retries", "log_level")}
INI_GRADER = '''import configparser
import json

WANT = json.loads({want!r})
CHANGED = json.loads({changed!r})


def check():
    c = configparser.ConfigParser()
    try:
        if not c.read({file!r}, encoding="utf-8"):
            return False, 0.0
    except configparser.Error:
        return False, 0.0
    got = {{s: dict(c[s]) for s in c.sections()}}
    if got == WANT:
        return True, 1.0
    hits = sum(got.get(s, {{}}).get(k) == WANT[s][k] for s, k in CHANGED)
    return False, 0.5 * hits / len(CHANGED)
'''


def config_edit(seed: int) -> dict:
    rng = _rng("config_edit", seed)
    values = {s: {k: str(rng.randint(1, 60)) if k != "log_level" and k != "skip_if_rain" else
                  (rng.choice(("info", "warn", "debug")) if k == "log_level" else rng.choice(("yes", "no")))
                  for k in ks} for s, ks in CONFIG_KEYS.items()}
    text = "\n".join(f"[{s}]\n" + "\n".join(f"{k} = {v}" for k, v in kv.items()) + "\n" for s, kv in values.items())
    picks = rng.sample([(s, k) for s, ks in CONFIG_KEYS.items() for k in ks if k not in ("log_level", "skip_if_rain")], 2)
    want = json.loads(json.dumps(values))
    asks = []
    for s, k in picks:
        new = str(int(values[s][k]) + rng.randint(5, 30))
        want[s][k] = new
        asks.append(f"{k} in [{s}] to {new}")
    goal = (f"In work/garden.ini, set {asks[0]} and {asks[1]}. Leave every other setting exactly as it is. "
            "Answer as JSON with key 'path'.")
    grader = INI_GRADER.format(want=json.dumps(want), changed=json.dumps(picks), file="garden.ini")
    schema = {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}
    spec = _spec("config_edit", seed, goal, "verify_program", schema, {"work/garden.ini": text}, {"verify.py": grader})
    spec["reference"] = {"work/garden.ini": "\n".join(
        f"[{s}]\n" + "\n".join(f"{k} = {v}" for k, v in kv.items()) + "\n" for s, kv in want.items())}
    return spec


def multi_file(seed: int) -> dict:
    rng = _rng("multi_file", seed)
    names = [f"{a.lower()}_{n.lower()}" for a in ADJ for n in NOUN]
    rng.shuffle(names)
    mods = [f"mod_{n}" for n in rng.sample(names, 12)]
    funcs = names[20:20 + 12 * 2]
    target = funcs[0]
    home = rng.choice(mods)
    callers = rng.sample([m for m in mods if m != home], 3)
    files = {}
    for i, m in enumerate(mods):
        own = funcs[2 * i:2 * i + 2] if m != home else [target, funcs[1]]
        if m != home and target in own:
            own = funcs[2 * i + 1:2 * i + 3]
        body = [f"def {f}(value):\n    return value\n" for f in own]
        if m in callers:
            body.append(f"def use_{m}(value):\n    return {target}(value)\n")
        files[f"work/pkg/{m}.py"] = "\n\n".join(body)
    goal = (f"Several files under work/pkg call the function {target}. Find the one file that defines it "
            "(the line starting with 'def'). Answer as JSON with key 'file', the path such as work/pkg/mod_x.py.")
    schema = {"type": "object", "required": ["file"], "properties": {"file": {"type": "string"}},
              "x-expected": {"file": f"work/pkg/{home}.py"}}
    return _spec("multi_file", seed, goal, "expected_value", schema, files)


def arithmetic(seed: int) -> dict:
    rng = _rng("arithmetic", seed)
    crop = rng.choice(CROPS)
    rows = [(f"plot-{i + 1}", rng.choice(CROPS), rng.randint(10, 99), rng.randint(120, 980)) for i in range(rng.randint(14, 22))]
    if not any(r[1] == crop for r in rows):
        rows[0] = (rows[0][0], crop, rows[0][2], rows[0][3])
    total = sum(kg * cents for _, c, kg, cents in rows if c == crop)
    text = "plot,crop,kg,price_cents_per_kg\n" + "\n".join(f"{p},{c},{kg},{cents}" for p, c, kg, cents in rows) + "\n"
    goal = (f"work/harvest.csv lists plots, crops, kilograms and a price in cents per kilogram. Compute the total value "
            f"in cents of all {crop} (kilograms times price, summed over every {crop} row). Answer as JSON with key "
            "'total' (an integer).")
    schema = {"type": "object", "required": ["total"], "properties": {"total": {"type": "integer"}},
              "x-expected": {"total": total}}
    return _spec("arithmetic", seed, goal, "expected_value", schema, {"work/harvest.csv": text})


FAQ = {"opening": ("When does the museum open on weekdays?", "at {h}:00"),
       "ticket": ("How much is an adult ticket?", "{n} euros"),
       "tour": ("How long is the guided tour?", "{m} minutes"),
       "galleries": ("How many galleries are there?", "{g} galleries"),
       "cafe": ("When does the cafe close?", "at {c}:30"),
       "parking": ("How many parking spaces are there?", "{p} spaces"),
       "lockers": ("How many lockers are there?", "{l} lockers")}


def unanswerable(seed: int) -> dict:
    rng = _rng("unanswerable", seed)
    v = {"h": rng.randint(8, 10), "n": rng.randint(6, 19), "m": rng.choice((30, 45, 60, 75)), "g": rng.randint(4, 14),
         "c": rng.randint(16, 18), "p": rng.randint(20, 90), "l": rng.randint(30, 120)}
    present = rng.sample(sorted(FAQ), 4)
    lines = ["# Visitor questions\n"]
    for k in present:
        q, a = FAQ[k]
        # family_version 2: the answer ends its own line with no full stop, so "exactly as written" has one reading
        lines.append(f"## {q}\n\n{_filler(rng, 3)}\nThe answer: {a.format(**v)}\n")
    answerable = rng.random() < 0.5
    ask = rng.choice(present) if answerable else rng.choice([k for k in sorted(FAQ) if k not in present])
    question = FAQ[ask][0]
    goal = (f"Using only work/museum_faq.md, answer: {question} If the document does not say, answer as JSON "
            "{\"answerable\": false}. If it does, answer as JSON {\"answerable\": true, \"answer\": \"...\"} with the "
            "answer exactly as written in the document.")
    want = {"answerable": True, "answer": FAQ[ask][1].format(**v)} if answerable else {"answerable": False}
    schema = {"type": "object", "required": ["answerable"], "properties": {"answerable": {"type": "boolean"}},
              "x-expected": want}
    return _spec("unanswerable", seed, goal, "expected_value", schema, {"work/museum_faq.md": "\n".join(lines)}, version=2)


FAMILIES = {"needle": needle, "multipage": multipage, "write_function": write_function, "extract_json": extract_json,
            "config_edit": config_edit, "multi_file": multi_file, "arithmetic": arithmetic, "unanswerable": unanswerable}


def generate(family: str, seed: int) -> dict:
    return FAMILIES[family](seed)


def instances(families=None, split: str | None = None, n: int | None = None, skip: int = 0) -> list[dict]:
    out = []
    for fam in families or FAMILIES:
        got = [generate(fam, s) for s in range(SEEDS) if split is None or split_of(s) == split]
        out += got[skip:skip + n] if n else got[skip:]
    return out
