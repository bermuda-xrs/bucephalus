"""A local HTTP fetcher.

Why this exists rather than a provider's server-side fetch tool:

Anthropic ships `web_fetch` and `web_search` as server tools, and they work
well. But their results arrive *inside the model's reply*, as tool-result
blocks. Content that arrives that way never passes through `write_artifact`,
never gets `taint=UNTRUSTED` stamped on it, and never reaches
`TaintTracker.observe()` — so the capability gate has no idea those bytes came
from the open internet. Every defense in M8 keys off provenance the agent's
own tool path establishes. Adopting a server-side fetcher would quietly delete
that, which is the one guarantee this build actually demonstrates.

So the bytes come in through here, and go straight into the ledger as
untrusted, like any other tool output.

This is a fetcher for a personal agent, not a crawler:
  * plain http/https only — no file://, no ftp://, no data:
  * private and loopback addresses refused, so a page cannot walk the agent
    into the local network
  * hard byte cap, enforced while streaming rather than after
  * text-ish content types only
  * HTML reduced to text, because the agent reads prose and tags are tokens
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

MAX_BYTES = 400_000
TIMEOUT_S = 20.0
ALLOWED_SCHEMES = ("http", "https")
TEXTY = (
    "text/", "application/json", "application/xml", "application/xhtml",
    "application/rss", "application/atom",
)
_DROP_TAGS = {"script", "style", "noscript", "template", "svg", "head"}


class FetchError(RuntimeError):
    pass


class _Text(HTMLParser):
    """Tags out, text and link targets in. No dependency for this."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_TAGS:
            self._skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "section"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _DROP_TAGS and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        out = " ".join(self.parts)
        out = re.sub(r"[ \t]{2,}", " ", out)
        return re.sub(r"\n{3,}", "\n\n", out).strip()


def _is_public(host: str) -> bool:
    """Refuse loopback, link-local, and RFC1918 targets.

    A fetched page can name the next URL. Without this, 'summarize this page'
    is one hop away from reading the router's admin console.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError(f"cannot resolve {host}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def fetch_url(url: str, *, max_bytes: int = MAX_BYTES, timeout_s: float = TIMEOUT_S,
              allow_private: bool = False) -> str:
    """Fetch one URL and return readable text. Raises FetchError on refusal."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise FetchError(f"scheme {parsed.scheme!r} not allowed; use http or https")
    if not parsed.hostname:
        raise FetchError("no host in URL")
    if not allow_private and not _is_public(parsed.hostname):
        raise FetchError(f"refusing to fetch a private/loopback address: {parsed.hostname}")

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_s,
                          headers={"User-Agent": "alpha-test-1/0.1 (personal agent)"}) as c:
            with c.stream("GET", url) as r:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").lower()
                if ctype and not any(t in ctype for t in TEXTY):
                    raise FetchError(f"content-type {ctype!r} is not text")
                chunks, total = [], 0
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        chunks.append(chunk[: max_bytes - (total - len(chunk))])
                        break
                    chunks.append(chunk)
                raw = b"".join(chunks).decode("utf-8", errors="replace")
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"fetch failed: {type(e).__name__}: {e}") from e

    if "html" in ctype or raw.lstrip()[:1] == "<":
        p = _Text()
        try:
            p.feed(raw)
            return p.text()
        except Exception:  # noqa: BLE001 - malformed HTML still yields its text
            return raw
    return raw


def default_fetcher(url: str) -> str:
    """The callable assigned to ctx.web_fetcher. Errors come back as text.

    A failed fetch is an observation the agent should see and route around,
    not an exception that kills the task.
    """
    try:
        return fetch_url(url)
    except FetchError as e:
        return f"[fetch failed] {e}"
