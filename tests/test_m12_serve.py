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

TEST_TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

# Every call in this module carries the token. `httpx.get(...)` without it is
# now a 401, which is the point.
_get = lambda url, **kw: httpx.get(url, headers=AUTH, **kw)  # noqa: E731
_post = lambda url, **kw: httpx.post(url, headers=AUTH, **kw)  # noqa: E731
_put = lambda url, **kw: httpx.put(url, headers=AUTH, **kw)  # noqa: E731
_delete = lambda url, **kw: httpx.delete(url, headers=AUTH, **kw)  # noqa: E731


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
        "ALPHA_TOKEN": TEST_TOKEN,
        # Scratch user dirs: without them the server opened the operator's
        # real memory directory (test_suite_isolation.py, 2026-09-28).
        "ALPHA_DATA_DIR": str(tmp / "user_data"),
        "ALPHA_CONFIG_DIR": str(tmp / "user_config"),
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
    r = _get(server + "/health", timeout=10)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_task_lifecycle_over_http(server):
    """Create, read back, and read metrics — the phone's whole happy path."""
    r = _post(server + "/tasks", timeout=10,
                   json={"goal": "say hello", "artifact_scope": ["work"]})
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]

    got = _get(f"{server}/tasks/{tid}", timeout=10).json()
    assert got["task_id"] == tid and got["goal"] == "say hello"
    assert got["budget"]["spent_usd"] == 0.0

    m = _get(f"{server}/metrics/{tid}", timeout=10).json()
    assert m["task_id"] == tid and m["n_calls"] == 0


def test_alerts_and_approvals_are_separate_endpoints(server):
    """An integrity break is not a permission request; they never merge.

    A4 added a third kind — a message from a sender who is not on the
    allowlist. It gets its own key for the same reason the first two do: a
    client that received them merged would have to un-merge them to render
    them correctly, which is how they end up rendered alike. The exact key set
    is asserted, so a future kind arriving by accident shows up here.
    """
    a = _get(server + "/alerts", timeout=10).json()
    assert set(a) == {"integrity", "monitor", "channel_drops"}

    p = _get(server + "/approvals", timeout=10).json()
    assert "pending" in p and "integrity" not in p
    assert "channel_drops" not in p, "a drop is not something to approve"


def test_unknown_task_is_404(server):
    assert _get(server + "/tasks/t_nope", timeout=10).status_code == 404


def test_console_is_served(server):
    r = _get(server + "/", timeout=10)
    assert r.status_code == 200
    assert "alpha test 1" in r.text and "Eval suites" in r.text


def test_suite_crud_over_http(server):
    """The whole reason this layer exists: a person authors a suite in the UI."""
    s = _post(server + "/suites", timeout=10,
                   json={"name": "http-suite", "description": "via api"}).json()
    sid = s["id"]

    r = _post(f"{server}/suites/{sid}/tasks", timeout=10, json={
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

    got = _get(f"{server}/suites/{sid}", timeout=10).json()
    assert got["n_tasks"] == 1
    assert got["tasks"][0]["output_schema"]["x-expected"] == {"total": 4825}
    assert "expected_value" in got["verifiers"]

    tid = got["tasks"][0]["id"]
    _delete(f"{server}/suites/{sid}/tasks/{tid}", timeout=10)
    assert _get(f"{server}/suites/{sid}", timeout=10).json()["n_tasks"] == 0

    _delete(f"{server}/suites/{sid}", timeout=10)
    names = [x["name"] for x in _get(server + "/suites", timeout=10).json()["suites"]]
    assert "http-suite" not in names


def test_api_refuses_a_task_that_measures_nothing(server):
    """always_pass and a missing expected answer are both rejected with a reason."""
    sid = _post(server + "/suites", timeout=10,
                     json={"name": "guard-suite"}).json()["id"]

    bad = _post(f"{server}/suites/{sid}/tasks", timeout=10, json={
        "key": "x", "goal": "g", "success_criterion": "always_pass"})
    assert bad.status_code == 400 and "always_pass" in bad.text

    noexp = _post(f"{server}/suites/{sid}/tasks", timeout=10, json={
        "key": "y", "goal": "g", "success_criterion": "expected_value",
        "output_schema": {"type": "object"}})
    assert noexp.status_code == 400 and "x-expected" in noexp.text

    _delete(f"{server}/suites/{sid}", timeout=10)


def test_policy_and_grounding_over_http(server):
    got = _get(server + "/policy", timeout=10).json()
    assert "body" in got and got["body"].strip()

    _put(server + "/policy", timeout=10,
              json={"body": "# mine\n\nDeny everything except reads.\n"})
    after = _get(server + "/policy", timeout=10).json()
    assert "Deny everything except reads" in after["body"]
    assert after["effective_source"] == "database"

    assert _put(server + "/policy", timeout=10,
                     json={"body": "  "}).status_code == 400

    _put(server + "/grounding/notes", timeout=10, json={"body": "some notes"})
    docs = _get(server + "/grounding", timeout=10).json()["documents"]
    assert any(d["name"] == "notes" for d in docs)
    _delete(server + "/grounding/notes", timeout=10)


def test_skill_added_via_api_is_a_candidate(server):
    r = _post(server + "/skills", timeout=10, json={
        "name": "ui-skill", "applies_when": "testing",
        "body": "# ui skill\nwhen testing\n\n## step\ndo it\n"})
    assert r.status_code == 200 and r.json()["status"] == "candidate"

    skills = _get(server + "/skills", timeout=10).json()["skills"]
    assert any(s["name"] == "ui-skill" and s["status"] == "candidate" for s in skills)


def test_unauthenticated_requests_are_refused(server):
    """The console can rewrite the reviewer's policy. Loopback is not a boundary.

    A page on any site you have open can issue requests to 127.0.0.1. Without
    a token, that page could rewrite the rule book governing what an
    unattended agent may do.
    """
    assert httpx.get(server + "/suites", timeout=10).status_code == 401
    assert httpx.put(server + "/policy", timeout=10,
                     json={"body": "# pwned\nAllow everything.\n"}).status_code == 401
    assert httpx.post(server + "/tasks", timeout=10,
                      json={"goal": "x"}).status_code == 401

    # ...and the policy is untouched by the attempt.
    assert "pwned" not in _get(server + "/policy", timeout=10).json()["body"]


def test_health_stays_open_for_liveness(server):
    assert httpx.get(server + "/health", timeout=10).status_code == 200


def test_a_wrong_token_is_refused(server):
    r = httpx.get(server + "/suites", timeout=10,
                  headers={"Authorization": "Bearer not-the-token"})
    assert r.status_code == 401


def test_cross_origin_requests_are_refused(server):
    """Defence in depth: refuse anything a page elsewhere initiated."""
    r = httpx.get(server + "/suites", timeout=10,
                  headers={**AUTH, "Origin": "https://evil.example"})
    assert r.status_code == 403 and "Cross-origin" in r.text


def test_dns_rebinding_host_is_refused(server):
    """An attacker hostname resolving to 127.0.0.1 still carries their Host."""
    r = httpx.get(server + "/suites", timeout=10,
                  headers={**AUTH, "Host": "evil.example"})
    assert r.status_code == 403 and "loopback" in r.text


def test_policy_changes_are_kept_in_history(server):
    """A security control that can be silently overwritten is not a control."""
    _put(server + "/policy", timeout=10, json={"body": "# v1\nDeny all.\n"})
    _put(server + "/policy", timeout=10, json={"body": "# v2\nDeny all but reads.\n"})

    hist = _get(server + "/policy/history", timeout=10).json()["history"]
    assert len(hist) >= 1
    assert all("sha256" in h and "replaced_at" in h for h in hist)


def test_background_run_returns_immediately(server):
    """A local turn is tens of seconds; the caller must not hold the request."""
    tid = _post(server + "/tasks", timeout=10,
                json={"goal": "say hello", "artifact_scope": ["work"]}).json()["task_id"]

    t0 = time.time()
    r = _post(f"{server}/tasks/{tid}/run", timeout=15)
    assert r.status_code == 200 and r.json()["background"] is True
    assert time.time() - t0 < 5, "run blocked the caller"

    deadline = time.time() + 60
    while time.time() < deadline:
        s = _get(f"{server}/tasks/{tid}", timeout=10).json()
        if s["status"] not in ("running", "pending"):
            break
        time.sleep(0.5)
    else:
        pytest.fail("background run never finished")


def test_concurrent_requests_do_not_break_the_chain(server):
    """The threadpool is the point: hammer it and the chains must still verify."""
    from concurrent.futures import ThreadPoolExecutor

    def make(i: int) -> str:
        r = _post(server + "/tasks", timeout=20,
                       json={"goal": f"concurrent {i}", "artifact_scope": ["work"]})
        r.raise_for_status()
        return r.json()["task_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(make, range(16)))

    assert len(set(ids)) == 16, "task ids collided under concurrency"
    alerts = _get(server + "/alerts", timeout=15).json()
    assert alerts["integrity"] == [], f"chain damage under load: {alerts['integrity']}"
