from __future__ import annotations

import json

import agent.execute.loop as loop
from agent import tokens
from agent.execute.loop import run_task


def _line(n, slope=1.108, intercept=193.0):
    return [(e, int(slope * e + intercept)) for e in range(900, 900 + 30 * n, 30)]


def test_the_fit_recovers_a_line():
    cal = tokens.fit(_line(40))
    assert cal.fitted and cal.n == 40
    assert abs(cal.slope - 1.108) < 0.01 and abs(cal.intercept - 193) < 15


def test_too_few_calls_keep_the_estimate():
    cal = tokens.fit(_line(tokens.MIN_CALIBRATION_CALLS - 1))
    assert not cal.fitted and cal.apply(1000, 500) == (1000, 500)


def test_a_cached_prompt_is_not_a_measurement():
    cached = [(e, e // 10) for e, _ in _line(40)]
    cal = tokens.fit(_line(40) + cached)
    assert cal.fitted and abs(cal.slope - 1.108) < 0.01


def test_an_implausible_fit_is_refused():
    assert not tokens.fit([(e, 5 * e) for e, _ in _line(40)]).fitted


def test_the_intercept_is_counted_once():
    cal = tokens.Calibration(slope=1.1, intercept=200.0, n=40, fitted=True)
    p, t = cal.apply(1000, 500)
    assert p + t == round(1.1 * 1500 + 200)


def test_every_call_records_its_model_and_estimate(ctx, task, scripted):
    scripted("cloud", '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)
    rows = ctx.conn.execute("SELECT model, est_input_tokens FROM calls WHERE task_id=? "
                            "AND purpose='step'", (task.id,)).fetchall()
    assert rows and all(m == "mock-cloud" and e and e > 0 for m, e in rows)


def test_the_ceiling_budgets_against_the_fit(ctx, task, scripted, monkeypatch):
    ctx.conn.executemany(
        "INSERT INTO calls(task_id, backend, model, purpose, input_tokens, cache_read_tokens, "
        "est_input_tokens, created_at, outcome) VALUES ('t_old','cloud','mock-cloud','step',?,0,?,0,'returned')",
        [(s, e) for e, s in _line(40, slope=2.0, intercept=100.0)])
    seen = []
    real = loop.enforce_ceiling
    monkeypatch.setattr(loop, "enforce_ceiling",
                        lambda p, t, w, r: seen.append((p, t)) or real(p, t, w, r))
    scripted("cloud", '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)
    ev = ctx.conn.execute("SELECT payload_json FROM events WHERE task_id=? "
                          "AND type='tokens.calibration'", (task.id,)).fetchone()
    cal = json.loads(ev[0])
    assert cal["fitted"] and abs(cal["slope"] - 2.0) < 0.01 and cal["model"] == "mock-cloud"
    est = ctx.conn.execute("SELECT est_input_tokens FROM calls WHERE task_id=? AND purpose='step'",
                           (task.id,)).fetchone()[0]
    p, t = seen[0]
    assert p + t >= 2 * est


def test_an_uncalibrated_run_says_so(ctx, task, scripted):
    scripted("cloud", '<final>{"answer": "ok"}</final>')
    run_task(ctx, task.id)
    ev = ctx.conn.execute("SELECT payload_json FROM events WHERE task_id=? "
                          "AND type='tokens.calibration'", (task.id,)).fetchone()
    assert ev and json.loads(ev[0])["fitted"] is False


def test_the_inventory_counts_fitted_runs(ctx, task, scripted, tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "inventory", Path(__file__).resolve().parents[1] / "bench" / "inventory.py")
    inv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inv)
    ctx.log.append(task.id, "tokens.calibration", {"fitted": True})
    ctx.log.append(task.id, "tokens.calibration", {"fitted": False})
    row = next(r for r in inv.take(Path(ctx.config.db_path)) if r["signal"] == "tokens.calibration")
    assert row["n"] == 1 and row["state"] == "ran"
