from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

PROVIDER_CREDENTIALS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)

CLOUD_ONLY_KINDS = ("anthropic",)

LOCAL = "local"
MIXED = "mixed"
VALID_PROFILES = (LOCAL, MIXED)


def _host_is_local(host: str) -> bool:
    if not host:
        return False
    if host in ("localhost", "localhost.localdomain") or host.endswith(".localhost"):
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            return False
    return True


def reaches_a_remote_provider(backend) -> bool:
    if backend.kind in CLOUD_ONLY_KINDS:
        return True
    if backend.trust_tier == "cloud":
        return True
    url = getattr(backend, "base_url", None) or getattr(backend, "endpoint", None)
    if not url:
        return False
    parsed = urlparse(url if "//" in url else f"//{url}")
    return not _host_is_local(parsed.hostname or "")


class ProfileViolation(RuntimeError):
    ...


def enforce(config, profile: str, env: dict | None = None) -> dict:
    if profile not in VALID_PROFILES:
        raise ProfileViolation(
            f"unknown profile {profile!r}; expected one of {VALID_PROFILES}"
        )
    env = os.environ if env is None else env

    cloud_backends = sorted(
        name for name, b in config.backends.items()
        if reaches_a_remote_provider(b)
    )
    watched = set(PROVIDER_CREDENTIALS) | {
        b.api_key_env for b in config.backends.values() if b.api_key_env
    }
    credentials = sorted(k for k in watched if env.get(k))

    if profile == LOCAL:
        problems = []
        if cloud_backends:
            problems.append(
                "cloud backends present in the resolved config: "
                + ", ".join(
                    f"{n} (kind={config.backends[n].kind}, "
                    f"model={config.backends[n].model})" for n in cloud_backends
                )
            )
        if credentials:
            problems.append(
                "provider credentials visible in the environment: "
                + ", ".join(credentials)
                + " — unset them, or run with --profile mixed and say so in "
                  "the results"
            )
        if problems:
            raise ProfileViolation(
                "profile=local was declared but the run would not be local.\n  - "
                + "\n  - ".join(problems)
                + "\nRefusing to start: a benchmark that measured something "
                  "other than what it claims is worse than no benchmark."
            )

    return {
        "profile": profile,
        "cloud_backends": cloud_backends,
        "credentials_present": credentials,
        "backends": {n: {"kind": b.kind, "model": b.model, "tier": b.trust_tier}
                     for n, b in sorted(config.backends.items())},
    }
