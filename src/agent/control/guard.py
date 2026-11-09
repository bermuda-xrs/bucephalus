from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

TOKEN_ENV = "BUCEPHALUS_TOKEN"
TOKEN_FILE = ".bucephalus_token"
ALLOW_REMOTE_ENV = "BUCEPHALUS_ALLOW_REMOTE"

OPEN_PATHS = {"/health"}

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def resolve_token(root: str | Path = ".") -> str:
    import sys

    from ..paths import user_config_dir

    env = os.environ.get(TOKEN_ENV)
    if env:
        return env.strip()

    path = user_config_dir() / TOKEN_FILE
    legacy = Path(root) / TOKEN_FILE
    if legacy.exists() and legacy.resolve() != path.resolve():
        try:
            legacy.unlink()
            sys.stderr.write(
                f"bucephalus: retired the console token found at {legacy}. It lived in "
                "the project folder, which may sync to the cloud; a new one is in "
                f"{path.parent}. If the folder syncs, also delete it from the "
                "service's deleted items and version history.\n")
        except OSError:
            pass
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
        pass
    return token


def _host_only(value: str) -> str:
    if not value:
        return ""
    if value.startswith("["):
        return value.split("]")[0] + "]"
    return value.split(":")[0]


def is_loopback_host(host_header: str) -> bool:
    return _host_only(host_header).lower() in LOOPBACK_HOSTS


def _peer_is_loopback(request) -> bool:
    peer = request.client.host if request.client else ""
    return peer in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


PUBLIC_UI = frozenset({"/", "/ui.js", "/md.js", "/ui.css"})

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


class AccessGuard(BaseHTTPMiddleware):
    def __init__(self, app, token: str, allow_remote: bool = False):
        super().__init__(app)
        self.token = token
        self.allow_remote = allow_remote

    async def dispatch(self, request, call_next):
        try:
            response = await self._dispatch(request, call_next)
        except Exception:  # noqa: BLE001
            logging.getLogger("uvicorn.error").exception("unhandled error in %s", request.url.path)
            response = JSONResponse({"detail": "Internal error; see the server log."},
                                    status_code=500)
        for k, v in SECURITY_HEADERS.items():
            response.headers[k] = v
        return response

    async def _dispatch(self, request, call_next):
        path = request.url.path

        host = request.headers.get("host", "")
        if not self.allow_remote and not is_loopback_host(host):
            return JSONResponse(
                {"detail": f"Host {host!r} is not a loopback name. Set "
                           f"{ALLOW_REMOTE_ENV}=1 to serve other hosts."},
                status_code=403,
            )

        origin = request.headers.get("origin")
        if origin:
            expected = {f"http://{host}", f"https://{host}"}
            if origin not in expected:
                return JSONResponse(
                    {"detail": "Cross-origin request refused."}, status_code=403
                )

        if path in OPEN_PATHS:
            return await call_next(request)

        if path.startswith("/hooks/") and request.method == "POST":
            if not (is_loopback_host(host) and _peer_is_loopback(request)):
                return JSONResponse({"detail": "Webhooks are for this computer only."}, status_code=403)
            return await call_next(request)

        if path in PUBLIC_UI and request.method == "GET" and is_loopback_host(host):
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
            if _same(header[7:].strip(), self.token):
                return True
        supplied = request.headers.get("x-bucephalus-token") or \
            request.query_params.get("token", "")
        return bool(supplied) and _same(supplied, self.token)


def _same(supplied: str, token: str) -> bool:
    return secrets.compare_digest(supplied.encode("utf-8", "surrogateescape"),
                                  token.encode("utf-8"))
