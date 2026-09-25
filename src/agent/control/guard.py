"""Access control for the control plane.

The console can rewrite the reviewer's policy, add skills, and start runs. On
a machine with a browser open, "it only listens on localhost" is not by itself
a boundary — a page you visit can issue requests to 127.0.0.1, and a service
that trusts every local caller trusts every website you have open. Three
overlapping checks, because each covers a case the others miss:

  1. **Bearer token.** A cross-origin page cannot read the token (same-origin
     policy stops it reading our HTML), so it cannot forge an authorised call.
     This is the check that actually stops CSRF.
  2. **Origin allow-list.** Browsers attach `Origin` to cross-site requests.
     If one arrives from somewhere that is not us, refuse before routing —
     defence in depth for the case where a token leaks into a page.
  3. **Host allow-list.** Blocks DNS rebinding, where a hostname the attacker
     controls resolves to 127.0.0.1 so their page becomes "same origin" as
     this server. The Host header still carries their name, so we refuse it.

Binding to a non-loopback interface additionally requires an explicit opt-in,
because the difference between 127.0.0.1 and 0.0.0.0 is the difference between
"my laptop" and "everyone on this café wifi".
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

TOKEN_ENV = "ALPHA_TOKEN"
TOKEN_FILE = ".alpha_token"
ALLOW_REMOTE_ENV = "ALPHA_ALLOW_REMOTE"

# Liveness only. Reveals nothing a caller could not learn by connecting.
OPEN_PATHS = {"/health"}

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def resolve_token(root: str | Path = ".") -> str:
    """The shared secret. From the environment, or persisted next to the db.

    Generated once and reused so restarting the server does not invalidate an
    open console tab.
    """
    env = os.environ.get(TOKEN_ENV)
    if env:
        return env.strip()

    path = Path(root) / TOKEN_FILE
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # best effort; Windows ACLs are not POSIX modes
    return token


def _host_only(value: str) -> str:
    """Strip the port, keeping bracketed IPv6 intact."""
    if not value:
        return ""
    if value.startswith("["):
        return value.split("]")[0] + "]"
    return value.split(":")[0]


def is_loopback_host(host_header: str) -> bool:
    return _host_only(host_header).lower() in LOOPBACK_HOSTS


class AccessGuard(BaseHTTPMiddleware):
    def __init__(self, app, token: str, allow_remote: bool = False):
        super().__init__(app)
        self.token = token
        self.allow_remote = allow_remote

    async def dispatch(self, request, call_next):
        path = request.url.path

        # --- DNS rebinding: the Host header carries the attacker's name -----
        host = request.headers.get("host", "")
        # A request with NO Host header skipped this check entirely (D29's
        # sweep): `host and ...` read an absent name as a safe one. Absent is
        # not loopback.
        if not self.allow_remote and not is_loopback_host(host):
            return JSONResponse(
                {"detail": f"Host {host!r} is not a loopback name. Set "
                           f"{ALLOW_REMOTE_ENV}=1 to serve other hosts."},
                status_code=403,
            )

        # --- cross-site: refuse anything a page elsewhere initiated ---------
        origin = request.headers.get("origin")
        if origin:
            expected = {f"http://{host}", f"https://{host}"}
            if origin not in expected:
                return JSONResponse(
                    {"detail": "Cross-origin request refused."}, status_code=403
                )

        if path in OPEN_PATHS:
            return await call_next(request)

        # --- the console itself, for a human with a browser -----------------
        # The page itself is public to a loopback caller and holds no secret:
        # the token reaches it in the URL fragment (server.console_url), never
        # in this response, which any local process can read (AUDIT F2).
        if path == "/" and request.method == "GET" and is_loopback_host(host):
            return await call_next(request)

        if not self._authorised(request):
            return JSONResponse(
                {"detail": "Missing or invalid token. Open the console at / to "
                           "get an authorised session, or send "
                           "Authorization: Bearer <token>."},
                status_code=401,
            )
        return await call_next(request)

    def _authorised(self, request) -> bool:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            if secrets.compare_digest(header[7:].strip(), self.token):
                return True
        supplied = request.headers.get("x-alpha-token") or \
            request.query_params.get("token", "")
        return bool(supplied) and secrets.compare_digest(supplied, self.token)
