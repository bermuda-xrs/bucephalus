from __future__ import annotations

import threading
import time
import urllib.robotparser
from urllib.parse import urlparse

import httpx

USER_AGENT = "bucephalus/0.1 (personal agent)"

MIN_INTERVAL_S = 1.0

ROBOTS_TTL_S = 900.0

ROBOTS_MAX_BYTES = 64 * 1024

_lock = threading.Lock()
_last_request: dict[str, float] = {}
_robots: dict[str, tuple[float, object | None]] = {}


class RobotsRefused(RuntimeError):
    ...


def _root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _robots_for(url: str, timeout_s: float = 5.0):
    root = _root(url)
    now = time.monotonic()
    with _lock:
        cached = _robots.get(root)
        if cached and now - cached[0] < ROBOTS_TTL_S:
            return cached[1]

    parser: object | None = None
    try:
        body = ""
        with httpx.stream("GET", f"{root}/robots.txt", timeout=timeout_s,
                          headers={"User-Agent": USER_AGENT},
                          follow_redirects=True) as r:
            if r.status_code == 200:
                for chunk in r.iter_text():
                    body += chunk
                    if len(body) >= ROBOTS_MAX_BYTES:
                        break
        if body.strip():
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(body[:ROBOTS_MAX_BYTES].splitlines())
            parser = rp
    except Exception:  # noqa: BLE001
        parser = None

    with _lock:
        _robots[root] = (now, parser)
    return parser


def check_robots(url: str, user_agent: str = USER_AGENT) -> None:
    rp = _robots_for(url)
    if rp is None:
        return
    if not rp.can_fetch(user_agent, url):  # type: ignore[attr-defined]
        raise RobotsRefused(
            f"{_root(url)}/robots.txt disallows {urlparse(url).path or '/'} "
            f"for {user_agent!r}. Not fetched."
        )


def wait_for_turn(url: str, min_interval_s: float = MIN_INTERVAL_S) -> float:
    rp = _robots_for(url)
    delay = min_interval_s
    if rp is not None:
        try:
            declared = rp.crawl_delay(USER_AGENT)  # type: ignore[attr-defined]
            if declared:
                delay = max(delay, float(declared))
        except Exception:  # noqa: BLE001
            pass

    host = urlparse(url).netloc
    with _lock:
        last = _last_request.get(host)
        now = time.monotonic()
        wait = 0.0 if last is None else max(0.0, delay - (now - last))
        _last_request[host] = now + wait
    if wait > 0:
        time.sleep(wait)
    return wait


def reset() -> None:
    with _lock:
        _last_request.clear()
        _robots.clear()
