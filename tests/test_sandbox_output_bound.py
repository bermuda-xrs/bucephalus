from __future__ import annotations

from agent.execute import sandbox


def test_the_output_tail_holds_about_its_cap_however_much_is_written():
    t = sandbox._Tail(100)  # noqa: SLF001
    for i in range(10_000):
        t.add(f"line {i}" + chr(10))
    assert t.held() <= 100 + len("line 9999") + 1
    assert t.text().endswith("line 9999" + chr(10)) and len(t.text()) <= 100


def test_a_flood_of_output_comes_back_as_its_last_part(monkeypatch):
    monkeypatch.setattr(sandbox, "OUTPUT_CAP", 1000)
    code = "import sys" + chr(10) + "for i in range(200000): sys.stdout.write('x' * 50 + str(i) + chr(10))" + chr(10)
    out = sandbox.run_sandboxed(code, timeout_s=60)
    assert out["returncode"] == 0 and out["stdout"].endswith("199999" + chr(10)) and len(out["stdout"]) <= 1000
