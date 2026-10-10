from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from conftest import serve_on_free_port

PROJ = Path(__file__).resolve().parents[1]

TEST_TOKEN = "test-token-not-a-secret"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

_get = lambda url, **kw: httpx.get(url, headers=AUTH, **kw)  # noqa: E731
_post = lambda url, **kw: httpx.post(url, headers=AUTH, **kw)  # noqa: E731
_put = lambda url, **kw: httpx.put(url, headers=AUTH, **kw)  # noqa: E731
_delete = lambda url, **kw: httpx.delete(url, headers=AUTH, **kw)  # noqa: E731


@pytest.fixture(scope="module")
def server(tmp_path_factory):
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

    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
        "PYTHONPATH": str(PROJ / "src"),
        "PYTHONUTF8": "1",
        "BUCEPHALUS_CONFIG": str(cfg_path),
        "BUCEPHALUS_TOKEN": TEST_TOKEN,
        "BUCEPHALUS_DATA_DIR": str(tmp / "user_data"),
        "BUCEPHALUS_CONFIG_DIR": str(tmp / "user_config"),
    }
    log_path = tmp / "server.log"

    def launch(port):
        with log_path.open("a", encoding="utf-8") as log:
            return subprocess.Popen(  # noqa: S603
                [sys.executable, "-m", "uvicorn", "agent.control.asgi:app",
                 "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                env=env, stdout=log, stderr=subprocess.STDOUT)

    def answers(port):
        try:
            return httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    try:
        proc, port = serve_on_free_port(launch, answers)
    except (RuntimeError, TimeoutError) as e:
        pytest.fail(f"{e}: {log_path.read_text(encoding='utf-8')[-2000:]}")
    base = f"http://127.0.0.1:{port}"

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
    assert "Bucephalus" in r.text and "Eval suites" in r.text


def test_suite_crud_over_http(server):
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


def test_policy_over_http(server):
    got = _get(server + "/policy", timeout=10).json()
    assert "body" in got and got["body"].strip()

    _put(server + "/policy", timeout=10,
              json={"body": "# mine\n\nDeny everything except reads.\n"})
    after = _get(server + "/policy", timeout=10).json()
    assert "Deny everything except reads" in after["body"]
    assert after["effective_source"] == "database"

    assert _put(server + "/policy", timeout=10,
                     json={"body": "  "}).status_code == 400

    assert _get(server + "/grounding", timeout=10).status_code == 404


def test_skill_added_via_api_is_a_candidate(server):
    r = _post(server + "/skills", timeout=10, json={
        "name": "ui-skill", "applies_when": "testing",
        "body": "# ui skill\nwhen testing\n\n## step\ndo it\n"})
    assert r.status_code == 200 and r.json()["status"] == "candidate"

    skills = _get(server + "/skills", timeout=10).json()["skills"]
    assert any(s["name"] == "ui-skill" and s["status"] == "candidate" for s in skills)


def test_unauthenticated_requests_are_refused(server):
    assert httpx.get(server + "/suites", timeout=10).status_code == 401
    assert httpx.put(server + "/policy", timeout=10,
                     json={"body": "# pwned\nAllow everything.\n"}).status_code == 401
    assert httpx.post(server + "/tasks", timeout=10,
                      json={"goal": "x"}).status_code == 401

    assert "pwned" not in _get(server + "/policy", timeout=10).json()["body"]


def test_health_stays_open_for_liveness(server):
    assert httpx.get(server + "/health", timeout=10).status_code == 200


def test_a_wrong_token_is_refused(server):
    r = httpx.get(server + "/suites", timeout=10,
                  headers={"Authorization": "Bearer not-the-token"})
    assert r.status_code == 401


def test_cross_origin_requests_are_refused(server):
    r = httpx.get(server + "/suites", timeout=10,
                  headers={**AUTH, "Origin": "https://evil.example"})
    assert r.status_code == 403 and "Cross-origin" in r.text


def test_dns_rebinding_host_is_refused(server):
    r = httpx.get(server + "/suites", timeout=10,
                  headers={**AUTH, "Host": "evil.example"})
    assert r.status_code == 403 and "loopback" in r.text


def test_policy_changes_are_kept_in_history(server):
    _put(server + "/policy", timeout=10, json={"body": "# v1\nDeny all.\n"})
    _put(server + "/policy", timeout=10, json={"body": "# v2\nDeny all but reads.\n"})

    hist = _get(server + "/policy/history", timeout=10).json()["history"]
    assert len(hist) >= 1
    assert all("sha256" in h and "replaced_at" in h for h in hist)


def test_background_run_returns_immediately(server):
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
