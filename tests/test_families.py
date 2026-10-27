from __future__ import annotations

import csv
import io
import json
import re

import pytest

from agent import families as F
from agent.assure.verifiers import ContainsAllVerifier, ExpectedValueVerifier, VerifyProgramVerifier
from agent.kernel.types import TaskContract

ALL = sorted(F.FAMILIES)


def _contract(spec):
    return TaskContract(goal=spec["goal"], success_criterion=spec["success_criterion"], output_schema=spec["output_schema"],
                        artifact_scope=spec["artifact_scope"], grader_dir=spec["grader_dir"])


def test_eight_families_of_forty_seeds_split_by_seed():
    assert len(ALL) >= 8 and F.SEEDS >= 40
    for fam in ALL:
        dev, held = F.instances([fam], "dev"), F.instances([fam], "heldout")
        assert len(dev) + len(held) == F.SEEDS and len(dev) == len(held)
        assert not {s["seed"] for s in dev} & {s["seed"] for s in held}
        assert len({s["key"] for s in dev + held}) == F.SEEDS


def test_skip_takes_the_seeds_after_the_ones_already_measured():
    needle, arith = F.instances(["needle"], "heldout"), F.instances(["arithmetic"], "heldout")
    got = F.instances(["needle", "arithmetic"], "heldout", 14, skip=6)
    assert got == needle[6:20] + arith[6:20]
    assert [s["seed"] for s in got[:14]] == list(range(13, 40, 2))


def test_fresh_held_out_seeds_follow_the_forty_already_used():
    got = F.instances(["needle"], "heldout", 14, skip=20)
    assert [s["seed"] for s in got] == list(range(41, 68, 2))


@pytest.mark.parametrize("fam", ALL)
def test_generation_is_deterministic(fam):
    assert F.generate(fam, 7) == F.generate(fam, 7) and F.generate(fam, 7) != F.generate(fam, 8)


@pytest.mark.parametrize("fam", ["needle", "extract_json", "multi_file", "arithmetic", "unanswerable"])
def test_the_expected_answer_passes_and_a_wrong_one_fails(fam):
    for seed in range(0, F.SEEDS, 5):
        spec = F.generate(fam, seed)
        want = spec["output_schema"]["x-expected"]
        v = ExpectedValueVerifier()
        assert v.check(json.dumps(want), _contract(spec)).passed
        wrong = {k: (not val if isinstance(val, bool) else (val + 1 if isinstance(val, (int, float)) else str(val) + "x"))
                 for k, val in want.items()}
        assert not v.check(json.dumps(wrong), _contract(spec)).passed


def test_the_needle_is_there_once_and_the_distractors_differ():
    for seed in range(F.SEEDS):
        spec = F.generate("needle", seed)
        doc, mark = spec["fixtures"]["work/catalogue.md"], spec["output_schema"]["x-expected"]["shelf_mark"]
        assert doc.count(mark) == 1 and len(re.findall(r"Shelf mark for ", doc)) == 8


def test_multipage_refs_are_spread_and_only_they_look_like_refs():
    for seed in range(F.SEEDS):
        spec = F.generate("multipage", seed)
        doc, refs = spec["fixtures"]["work/journal.md"], spec["output_schema"]["x-contains"]
        found = re.findall(r"[A-Z]{2}-\d{4}", doc)
        assert sorted(found) == sorted(refs) and len(set(refs)) == 3
        pos = [doc.index(r) / len(doc) for r in refs]
        assert pos[0] < 0.4 and pos[2] > 0.6
        assert ContainsAllVerifier().check(json.dumps({"refs": refs}), _contract(spec)).passed
        assert not ContainsAllVerifier().check(json.dumps({"refs": refs[:2]}), _contract(spec)).passed


def test_multi_file_has_exactly_one_definition_and_several_callers():
    for seed in range(F.SEEDS):
        spec = F.generate("multi_file", seed)
        target = re.search(r"call the function (\w+)", spec["goal"]).group(1)
        defs = [p for p, t in spec["fixtures"].items() if f"def {target}(" in t]
        calls = [p for p, t in spec["fixtures"].items() if f"return {target}(" in t]
        assert defs == [spec["output_schema"]["x-expected"]["file"]] and len(calls) == 3


def test_arithmetic_total_is_recomputed_from_the_csv():
    for seed in range(F.SEEDS):
        spec = F.generate("arithmetic", seed)
        crop = re.search(r"of all (\w+)", spec["goal"]).group(1)
        rows = list(csv.DictReader(io.StringIO(spec["fixtures"]["work/harvest.csv"])))
        total = sum(int(r["kg"]) * int(r["price_cents_per_kg"]) for r in rows if r["crop"] == crop)
        assert total == spec["output_schema"]["x-expected"]["total"]


def test_unanswerable_has_both_kinds_in_each_split():
    for split in ("dev", "heldout"):
        kinds = {s["output_schema"]["x-expected"]["answerable"] for s in F.instances(["unanswerable"], split)}
        assert kinds == {True, False}


def _grade(tmp_path, spec, files):
    root = tmp_path / spec["key"]
    for rel, text in {**spec["fixtures"], **files}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for name, text in spec["graders"].items():
        (root / spec["grader_dir"]).mkdir(parents=True, exist_ok=True)
        (root / spec["grader_dir"] / name).write_text(text, encoding="utf-8")
    return VerifyProgramVerifier().check("{}", _contract(spec), root=str(root))


@pytest.mark.parametrize("fam", ["write_function", "config_edit"])
def test_the_reference_solution_passes_the_grader_and_the_untouched_workspace_fails(tmp_path, fam):
    for seed in (0, 3, 5, 6, 9, 10, 12, 15):
        spec = F.generate(fam, seed)
        assert _grade(tmp_path / "ref", spec, spec["reference"]).passed, spec["key"]
        assert not _grade(tmp_path / "bare", spec, {}).passed, spec["key"]


def test_every_write_function_kind_appears_in_each_split():
    for split in ("dev", "heldout"):
        kinds = {re.search(r"function (\w+)\(", s["goal"]).group(1) for s in F.instances(["write_function"], split)}
        assert len(kinds) == len(F.REFERENCE)
