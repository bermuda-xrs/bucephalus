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
    def __init__(self, msg: str, kind: str = TRANSPORT_FAILURE):
        super().__init__(msg)
        self.kind = kind


def _text(fragment: str) -> str:
    return html.unescape(_TAG_RX.sub("", fragment)).strip()


def _real_url(href: str) -> str:
    if "duckduckgo.com/l/" in href or href.startswith("/l/"):
        q = parse_qs(urlparse(href).query).get("uddg")
        if q:
            return q[0]
    return href


def search(query: str, limit: int = 5, *, client=None) -> list[dict]:
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
