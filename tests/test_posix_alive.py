from __future__ import annotations

from agent.platform import posix


def test_a_process_we_may_not_signal_is_alive_not_gone(monkeypatch):
    def denied(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(posix.os, "kill", denied)
    monkeypatch.setattr(posix, "_stat_fields", lambda pid: None)
    assert posix.alive(4242) is True


def test_a_process_that_does_not_exist_is_gone(monkeypatch):
    def missing(pid, sig):
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(posix.os, "kill", missing)
    assert posix.alive(4242) is False
