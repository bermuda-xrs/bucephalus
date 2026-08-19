"""M12 — the control plane answers over real HTTP.

Everything else about the control plane is tested by calling functions
directly, which is exactly how the thread-bound-connection bug survived: the
endpoints 500'd on every request and no in-process test could see it. These
start a server and talk to it.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJ = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Boot the ASGI app against a scratch config, on a scratch port."""
    tmp = tmp_path_factory.mktemp("serve")
    (tmp / "work").mkdir()
    (tmp / "policy.md").write_text("# policy\n\nDefault is DENY.\n", encoding="utf-8")
    cfg = {
        "db_path": str(tmp / "agent.db"),
        "anchor_path": str(tmp / "anchor" / "chain.anchor"),
        "skills_path": str(tmp / "skills_store"),
        "backends": {
            "cloud": {"kind": "mock", "model": "mock-cloud", "trust_tier": "cloud"},
            "review": {"kind": "mock", "model": "mock-review", "trust_tier": "cloud"},
        },
        "active_local_tier": None,
        "autonomy": {"plan_required": False, "auto_review": True,
                     "auto_review_backend": "review",
                     "auto_review_policy": str(tmp / "policy.md")},
    }
    cfg_path = tmp / "config.yaml"
    import yaml

    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

    port = _free_port()
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(PROJ / "src"),
        "PYTHONUTF8": "1",
        "ALPHA_CONFIG": str(cfg_path),
    }
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "uvicorn", "agent.control.asgi:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"server exited early: {proc.stderr.read()[:2000]}")
        try:
            if httpx.get(base + "/health", timeout=2).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.4)
    else:
        proc.kill()
        pytest.fail("server never became ready")

    yield base
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_health_answers(server):
    r = httpx.get(server + "/health", timeout=10)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_task_lifecycle_over_http(server):
    """Create, read back, and read metrics — the phone's whole happy path."""
    r = httpx.post(server + "/tasks", timeout=10,
                   json={"goal": "say hello", "artifact_scope": ["work"]})
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]

    got = httpx.get(f"{server}/tasks/{tid}", timeout=10).json()
    assert got["task_id"] == tid and got["goal"] == "say hello"
    assert got["budget"]["spent_usd"] == 0.0

    m = httpx.get(f"{server}/metrics/{tid}", timeout=10).json()
    assert m["task_id"] == tid and m["n_calls"] == 0


def test_alerts_and_approvals_are_separate_endpoints(server):
    """An integrity break is not a permission request; they never merge."""
    a = httpx.get(server + "/alerts", timeout=10).json()
    assert set(a) == {"integrity", "monitor"}

    p = httpx.get(server + "/approvals", timeout=10).json()
    assert "pending" in p and "integrity" not in p


def test_unknown_task_is_404(server):
    assert httpx.get(server + "/tasks/t_nope", timeout=10).status_code == 404


def test_console_is_served(server):
    r = httpx.get(server + "/", timeout=10)
    assert r.status_code == 200
    assert "alpha test 1" in r.text and "Eval suites" in r.text


def test_suite_crud_over_http(server):
    """The whole reason this layer exists: a person authors a suite in the UI."""
    s = httpx.post(server + "/suites", timeout=10,
                   json={"name": "http-suite", "description": "via api"}).json()
    sid = s["id"]

    r = httpx.post(f"{server}/suites/{sid}/tasks", timeout=10, json={
        "key": "inv-1",
        "goal": "read work/input.txt and return the total in cents",
        "success_criterion": "expected_value",
        "output_schema": {"type": "object", "required": ["total"],
                          "properties": {"total": {"type": "integer"}},
                          "x-expected": {"total": 4825}},
        "fixtures": {"work/input.txt": "TOTAL: $48.25\n"},
        "budget_seconds": 180,
    })
    assert r.status_code == 200, r.text

    got = httpx.get(f"{server}/suites/{sid}", timeout=10).json()
    assert got["n_tasks"] == 1
    assert got["tasks"][0]["output_schema"]["x-expected"] == {"total": 4825}
    assert "expected_value" in got["verifiers"]

    tid = got["tasks"][0]["id"]
    httpx.delete(f"{server}/suites/{sid}/tasks/{tid}", timeout=10)
    assert httpx.get(f"{server}/suites/{sid}", timeout=10).json()["n_tasks"] == 0

    httpx.delete(f"{server}/suites/{sid}", timeout=10)
    names = [x["name"] for x in httpx.get(server + "/suites", timeout=10).json()["suites"]]
    assert "http-suite" not in names


def test_api_refuses_a_task_that_measures_nothing(server):
    """always_pass and a missing expected answer are both rejected with a reason."""
    sid = httpx.post(server + "/suites", timeout=10,
                     json={"name": "guard-suite"}).json()["id"]

    bad = httpx.post(f"{server}/suites/{sid}/tasks", timeout=10, json={
        "key": "x", "goal": "g", "success_criterion": "always_pass"})
    assert bad.status_code == 400 and "always_pass" in bad.text

    noexp = httpx.post(f"{server}/suites/{sid}/tasks", timeout=10, json={
        "key": "y", "goal": "g", "success_criterion": "expected_value",
        "output_schema": {"type": "object"}})
    assert noexp.status_code == 400 and "x-expected" in noexp.text

    httpx.delete(f"{server}/suites/{sid}", timeout=10)


def test_policy_and_grounding_over_http(server):
    got = httpx.get(server + "/policy", timeout=10).json()
    assert "body" in got and got["body"].strip()

    httpx.put(server + "/policy", timeout=10,
              json={"body": "# mine\n\nDeny everything except reads.\n"})
    after = httpx.get(server + "/policy", timeout=10).json()
    assert "Deny everything except reads" in after["body"]
    assert after["effective_source"] == "database"

    assert httpx.put(server + "/policy", timeout=10,
                     json={"body": "  "}).status_code == 400

    httpx.put(server + "/grounding/notes", timeout=10, json={"body": "some notes"})
    docs = httpx.get(server + "/grounding", timeout=10).json()["documents"]
    assert any(d["name"] == "notes" for d in docs)
    httpx.delete(server + "/grounding/notes", timeout=10)


def test_skill_added_via_api_is_a_candidate(server):
    r = httpx.post(server + "/skills", timeout=10, json={
        "name": "ui-skill", "applies_when": "testing",
        "body": "# ui skill\nwhen testing\n\n## step\ndo it\n"})
    assert r.status_code == 200 and r.json()["status"] == "candidate"

    skills = httpx.get(server + "/skills", timeout=10).json()["skills"]
    assert any(s["name"] == "ui-skill" and s["status"] == "candidate" for s in skills)


def test_concurrent_requests_do_not_break_the_chain(server):
    """The threadpool is the point: hammer it and the chains must still verify."""
    from concurrent.futures import ThreadPoolExecutor

    def make(i: int) -> str:
        r = httpx.post(server + "/tasks", timeout=20,
                       json={"goal": f"concurrent {i}", "artifact_scope": ["work"]})
        r.raise_for_status()
        return r.json()["task_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(make, range(16)))

    assert len(set(ids)) == 16, "task ids collided under concurrency"
    alerts = httpx.get(server + "/alerts", timeout=15).json()
    assert alerts["integrity"] == [], f"chain damage under load: {alerts['integrity']}"
