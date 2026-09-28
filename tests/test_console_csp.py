"""The console cannot be framed, and runs under a CSP with no inline script
(iteration 3, 1.5 - audit F7, D45).

Before any UI rewrite, so the new console is built under the policy rather
than retrofitted to it. Two halves: every response carries the headers, even
a refusal; and the page could actually RUN under them - a CSP the page
violates is one somebody loosens the first time a button stops working.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from agent import desktop

CONTROL = Path(__file__).resolve().parents[1] / "src" / "agent" / "control"


@pytest.fixture
def base(ctx, workspace):
    server, thread, port = desktop.start_server(ctx, "127.0.0.1")
    yield f"http://127.0.0.1:{port}"
    desktop.shutdown(server, thread)


def _headers(url: str, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}


@pytest.mark.parametrize("path", ["/", "/health", "/tasks", "/ui.js", "/ui.css"])
def test_every_response_refuses_framing_and_inline_script(base, path):
    status, h = _headers(base + path)          # no token: /tasks is a 401
    assert h.get("x-frame-options") == "DENY", (path, status)
    csp = h.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp, csp
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "default-src 'none'" in csp
    assert h.get("x-content-type-options") == "nosniff"


def test_the_page_and_its_assets_load_without_a_token(base):
    for path, ctype in (("/", "text/html"), ("/ui.js", "javascript"), ("/ui.css", "text/css")):
        status, h = _headers(base + path)
        assert status == 200 and ctype in h.get("content-type", ""), (path, status)


def _page_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in CONTROL.iterdir() if p.suffix in (".html", ".js", ".css")}


def test_the_page_would_run_under_its_own_policy():
    src = _page_sources()
    html = src["ui.html"]
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script>"
    assert "<style" not in html, "inline <style>"
    for name, text in src.items():
        if name.endswith(".css"):
            continue
        # Markup in the HTML, and markup the JS builds as strings.
        assert not re.search(r"\son[a-z]+\s*=\s*[\"']", text), f"inline handler in {name}"
        assert not re.search(r"\sstyle\s*=\s*[\"']", text), f"inline style attribute in {name}"
        assert "javascript:" not in text, name
