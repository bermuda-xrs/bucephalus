"""The console token lives outside the checkout, in every mode (AUDIT F2
second half, D41).

In checkout mode the state root is the repository - here under a OneDrive
folder - so `.alpha_token` was synced to a third party's servers. `.gitignore`
does not stop OneDrive. The token now lives under platformdirs' user config
directory (local, not roaming) whatever the mode, and one found in the
checkout is RETIRED, not migrated: it has already been off the machine.
"""

from __future__ import annotations

from pathlib import Path

from agent.control.guard import TOKEN_FILE, resolve_token
from agent.paths import user_config_dir


def test_the_token_is_written_outside_the_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.delenv("ALPHA_TOKEN", raising=False)
    token = resolve_token(checkout)
    assert not (checkout / TOKEN_FILE).exists(), "the token was written into the checkout"
    assert (user_config_dir() / TOKEN_FILE).read_text(encoding="utf-8").strip() == token


def test_a_token_found_in_the_checkout_is_retired_not_reused(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / TOKEN_FILE).write_text("synced-to-the-cloud", encoding="utf-8")
    monkeypatch.delenv("ALPHA_TOKEN", raising=False)
    token = resolve_token(checkout)
    assert token != "synced-to-the-cloud", "a token that left the machine is still in use"
    assert not (checkout / TOKEN_FILE).exists(), "the synced copy was left in place"


def test_the_token_is_protected_state_where_it_now_lives(ctx):
    from agent.security.capabilities import state_paths

    paths = {Path(p) for p in state_paths(ctx)}
    assert (user_config_dir() / TOKEN_FILE).resolve() in {p.resolve() for p in paths}
