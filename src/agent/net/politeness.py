"""robots.txt and rate limiting. A4.4.

"A benchmark that hammers a site is a problem regardless of what it measures"
is the brief, and it is also self-interested: a suite that gets a host to
block it produces a run whose failures are network failures wearing task
costumes, which is the same class of error as scoring a machine stall as a
task outcome.

Both controls FAIL CLOSED in the direction that matters and OPEN in the
direction that does not:

  * robots.txt that cannot be fetched -> ALLOW. A site with no robots.txt has
    not refused anything, and treating an unreachable file as a prohibition
    would make one flaky request break every later one.
  * robots.txt that says no -> REFUSE, and say which rule.
  * the rate limiter always waits. There is no "skip the wait" path, because
    the only reason to add one is to go faster against a host that already
    asked you not to.

Deliberately not here: a crawl frontier, concurrency, retries with backoff.
This is a fetcher for a personal agent, and every one of those turns it into
a crawler.
"""

from __future__ import annotations

import threading
import time
import urllib.robotparser
from urllib.parse import urlparse

import httpx

USER_AGENT = "alpha-test-1/0.1 (personal agent)"

# Minimum seconds between requests to the SAME host. Per host rather than
# global: one slow site should not throttle an unrelated one, and a global
# limiter would make the delay depend on what else the task happened to read.
MIN_INTERVAL_S = 1.0

# robots.txt is re-read after this long. Long enough that a task does not
# re-fetch it constantly, short enough that a site changing its mind is
# noticed within a session.
ROBOTS_TTL_S = 900.0

# D3. The most robots.txt anyone needs, and the bound that makes the
# fetch finite: a server that dribbles bytes cannot hold the run open.
ROBOTS_MAX_BYTES = 64 * 1024

_lock = threading.Lock()
_last_request: dict[str, float] = {}
_robots: dict[str, tuple[float, object | None]] = {}


class RobotsRefused(RuntimeError):
    """The site's robots.txt disallows this path for this agent."""


def _root(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _robots_for(url: str, timeout_s: float = 5.0):
    """The parsed robots.txt for this host, or None if there isn't a usable one."""
    root = _root(url)
    now = time.time()
    with _lock:
        cached = _robots.get(root)
        if cached and now - cached[0] < ROBOTS_TTL_S:
            return cached[1]

    parser: object | None = None
    try:
        # D3's sweep. A plain `get` reads until the server stops, and httpx's
        # read timeout measures the gap BETWEEN bytes - the shape that let a
        # 600 s timeout run for 2539 s. Every other blocking path in this
        # project streams with a cap; this one did not.
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
        # Any other status - 404, 403, 5xx - means the site published no
        # usable rules. Not a prohibition.
    except Exception:  # noqa: BLE001 - an unreachable robots.txt is not a refusal
        parser = None

    with _lock:
        _robots[root] = (now, parser)
    return parser


def check_robots(url: str, user_agent: str = USER_AGENT) -> None:
    """Raise RobotsRefused if the site disallows this path."""
    rp = _robots_for(url)
    if rp is None:
        return
    if not rp.can_fetch(user_agent, url):  # type: ignore[attr-defined]
        raise RobotsRefused(
            f"{_root(url)}/robots.txt disallows {urlparse(url).path or '/'} "
            f"for {user_agent!r}. Not fetched."
        )


def wait_for_turn(url: str, min_interval_s: float = MIN_INTERVAL_S) -> float:
    """Sleep until this host may be called again. Returns seconds waited.

    `crawl-delay` from robots.txt wins when it is LONGER than our own floor.
    A site asking for more space gets it; a site asking for less does not
    lower the floor, because the floor is our politeness and not theirs.
    """
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
        now = time.time()
        wait = 0.0 if last is None else max(0.0, delay - (now - last))
        # Reserve the slot BEFORE sleeping, so two callers cannot both see an
        # old timestamp, both compute a short wait, and both fire at once.
        _last_request[host] = now + wait
    if wait > 0:
        time.sleep(wait)
    return wait


def reset() -> None:
    """Test hook. Clears both caches."""
    with _lock:
        _last_request.clear()
        _robots.clear()
