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

import os

# Credentials that would let a cloud backend work. Their presence is not an
# error by itself — it is an error in the local profile, because it means a
# misconfigured backend would silently succeed instead of failing closed.
PROVIDER_CREDENTIALS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)

LOCAL = "local"
MIXED = "mixed"
VALID_PROFILES = (LOCAL, MIXED)


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
        if b.kind == "anthropic" or b.trust_tier == "cloud"
    )
    credentials = sorted(k for k in PROVIDER_CREDENTIALS if env.get(k))

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


def describe(config, profile: str) -> str:
    """One line for a report header or a CLI banner."""
    names = ", ".join(f"{n}={b.model}" for n, b in sorted(config.backends.items()))
    return f"profile={profile} · {names}"
