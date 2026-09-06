"""A2 — web search, keyless.

WHY DUCKDUCKGO'S HTML ENDPOINT. Every search API worth using needs a key, and
a key means `--profile local` refuses to start (correctly — see profiles.py).
G4's whole problem is that research-grounded skill generation has never been
able to ground anything, and it will stay ungrounded if the only search path
requires the thing that makes a local run illegal. The html endpoint needs no
key and returns titles, snippets and URLs.

It is scraping, and it is fragile in the specific way scraping is fragile:
the markup can change and the parser stops finding results. That failure is
LOUD here — zero results with the page fetched successfully is reported as a
parse failure rather than as "nothing matched" — because the quiet version
would look like an unhelpful web and send someone hunting in the wrong place.

WHAT COMES BACK IS UNTRUSTED. Titles and snippets are attacker-authored text
on any query an attacker can influence; a result list is a web page that
happens to be shaped like data.

The searcher is swappable: set `ctx.web_searcher` to a callable taking
(query, limit) to use a real API. There is deliberately no
`default_searcher` wrapper — `tool_web_search` calls `search()` directly
so it can read the failure KIND off the exception, and a wrapper that
flattened errors into result rows would throw that away. An override
supplies its own callable by definition, so a helper for it had no user.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, quote_plus, urlparse

import httpx

from .politeness import USER_AGENT, wait_for_turn

ENDPOINT = "https://html.duckduckgo.com/html/"
MAX_RESULTS = 10
TIMEOUT_S = 20.0

_RESULT_RX = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S | re.I)
_SNIPPET_RX = re.compile(
    r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_TAG_RX = re.compile(r"<[^>]+>")


PARSE_FAILURE = "parse_failure"
TRANSPORT_FAILURE = "transport_failure"
BAD_QUERY = "bad_query"


class SearchError(RuntimeError):
    """Carries WHY, because the two failures have opposite fixes.

    `transport_failure` is the network being the network. `parse_failure` is
    "the page arrived and this parser found nothing in it", which means the
    markup changed and the scraper is broken. To a caller they look the same
    and to a run record they must not: a scraper that silently breaks
    otherwise reads as a topic with no coverage, and every later search on
    that run looks like an honest miss.
    """

    def __init__(self, msg: str, kind: str = TRANSPORT_FAILURE):
        super().__init__(msg)
        self.kind = kind


def _text(fragment: str) -> str:
    return html.unescape(_TAG_RX.sub("", fragment)).strip()


def _real_url(href: str) -> str:
    """DuckDuckGo wraps results in a redirect; unwrap to the actual target.

    An agent handed `duckduckgo.com/l/?uddg=...` would fetch the redirector,
    and the provenance recorded for the artifact would name the wrong site —
    which matters, because provenance is the point of recording it.
    """
    if "duckduckgo.com/l/" in href or href.startswith("/l/"):
        q = parse_qs(urlparse(href).query).get("uddg")
        if q:
            return q[0]
    return href


def search(query: str, limit: int = 5, *, client=None) -> list[dict]:
    """Titles, URLs and snippets. Raises SearchError; never returns junk."""
    query = (query or "").strip()
    if not query:
        raise SearchError("empty query", BAD_QUERY)
    limit = max(1, min(int(limit), MAX_RESULTS))

    wait_for_turn(ENDPOINT)
    try:
        c = client or httpx
        r = c.get(f"{ENDPOINT}?q={quote_plus(query)}", timeout=TIMEOUT_S,
                  headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        r.raise_for_status()
        body = r.text
    except Exception as e:  # noqa: BLE001
        raise SearchError(f"search failed: {type(e).__name__}: {e}",
                          TRANSPORT_FAILURE) from e

    links = _RESULT_RX.findall(body)
    snippets = [_text(s) for s in _SNIPPET_RX.findall(body)]
    if not links:
        # Loud, not quiet. "No results" and "the parser no longer matches the
        # markup" look identical to a caller and have opposite fixes.
        raise SearchError(
            f"fetched {len(body)} bytes from the search endpoint and parsed 0 "
            "results. Either the query genuinely matched nothing or the "
            "result markup changed and this parser needs updating — check the "
            "endpoint by hand before assuming the former.",
            PARSE_FAILURE,
        )

    out = []
    for i, (href, title) in enumerate(links[:limit]):
        out.append({
            "title": _text(title),
            "url": _real_url(html.unescape(href)),
            "snippet": snippets[i] if i < len(snippets) else "",
        })
    return out
