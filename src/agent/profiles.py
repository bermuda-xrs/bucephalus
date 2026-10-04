"""Backend profiles — what a run was allowed to touch.

A benchmark number is only meaningful alongside what produced it. "Local
only" being a property of whichever config file happened to be on disk is not
good enough: a stray `ANTHROPIC_API_KEY`, or one edited backend entry, and a
run silently measures something else while still landing in the same
`runs/` directory as the numbers you meant to compare it against.

So the profile is declared, enforced before any work starts, and recorded on
every artifact the run produces.

  * **local**  — no cloud backend may exist in the resolved config, and no
                 provider credential may be visible. Violations raise.
  * **mixed**  — cloud backends permitted. The default, and always explicit
                 in the provenance so a chart cannot silently blend the two.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

# Credentials that would let a cloud backend work. Their presence is not an
# error by itself — it is an error in the local profile, because it means a
# misconfigured backend would silently succeed instead of failing closed.
#
# Any `api_key_env` a backend declares is added to this set at check time, so
# a new provider does not need this tuple edited to be caught.
PROVIDER_CREDENTIALS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)

# Backend kinds that reach a provider by construction and have no local form.
CLOUD_ONLY_KINDS = ("anthropic",)

LOCAL = "local"
MIXED = "mixed"
VALID_PROFILES = (LOCAL, MIXED)


def _host_is_local(host: str) -> bool:
    """Loopback, link-local, or an RFC1918 address. Resolution failures are
    NOT local: an unresolvable name is unknown, and unknown fails closed."""
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
    """Would using this backend send bytes off this machine?

    Derived from where it POINTS, not from what it CALLS ITSELF. `trust_tier`
    is a declaration in a file a human edits; the URL is what the socket
    obeys. A config saying `kind: openai, base_url: https://api.openai.com/v1,
    trust_tier: local` is not a hypothetical — it is one careless copy of a
    working entry, and under the old class-name check it would have started a
    `--profile local` run and produced a number labelled local.

    Note the direction: this only ever ADDS to the set of backends that count
    as cloud. Nothing here can clear a backend the declared tier already
    flagged.
    """
    if backend.kind in CLOUD_ONLY_KINDS:
        return True
    if backend.trust_tier == "cloud":
        return True
    url = getattr(backend, "base_url", None) or getattr(backend, "endpoint", None)
    if not url:
        # No endpoint at all: `mock` talks to nothing, and a kind that needs
        # a URL and has none will fail loudly at construction instead.
        return False
    parsed = urlparse(url if "//" in url else f"//{url}")
    return not _host_is_local(parsed.hostname or "")


class ProfileViolation(RuntimeError):
    """The resolved config contradicts the declared profile. Never a warning.

    Downgrading this to a warning would defeat the point: the failure mode is
    a run that quietly measured something other than what its label says.
    """


def enforce(config, profile: str, env: dict | None = None) -> dict:
    """Check the resolved config against the declared profile.

    Returns a provenance dict describing what was enforced. Raises
    ProfileViolation rather than repairing anything — silently dropping a
    cloud backend would be its own kind of lie about what ran.
    """
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


