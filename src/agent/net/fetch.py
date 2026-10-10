from __future__ import annotations

import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from ..kernel.events import sha256_hex

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
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError(f"cannot resolve {host}: {e}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def fetch_with_provenance(url: str, *, max_bytes: int = MAX_BYTES,
                          timeout_s: float = TIMEOUT_S,
                          allow_private: bool = False,
                          polite: bool = True) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise FetchError(f"scheme {parsed.scheme!r} not allowed; use http or https")
    if not parsed.hostname:
        raise FetchError("no host in URL")
    if not allow_private and not _is_public(parsed.hostname):
        raise FetchError(f"refusing to fetch a private/loopback address: {parsed.hostname}")

    waited = 0.0
    if polite:
        from .politeness import RobotsRefused, check_robots, wait_for_turn
        try:
            check_robots(url)
        except RobotsRefused as e:
            raise FetchError(str(e)) from e
        waited = wait_for_turn(url)

    fetched_at = time.time()
    final_url = url
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout_s,
                          headers={"User-Agent": "bucephalus/0.1 (personal agent)"}) as c:
            with c.stream("GET", url) as r:
                r.raise_for_status()
                final_url = str(r.url)
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

    text = raw
    if "html" in ctype or raw.lstrip()[:1] == "<":
        p = _Text()
        try:
            p.feed(raw)
            text = p.text()
        except Exception:  # noqa: BLE001
            text = raw
    return {
        "text": text,
        "provenance": {
            "url": url,
            "final_url": final_url,
            "fetched_at": fetched_at,
            "content_sha256": sha256_hex(raw),
            "bytes": len(raw.encode("utf-8")),
            "content_type": ctype,
            "rate_limit_waited_s": round(waited, 3),
        },
    }
