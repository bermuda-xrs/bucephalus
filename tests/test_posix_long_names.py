from __future__ import annotations

from agent.platform import posix


def _proc(tmp_path, pid, comm, ppid=1, cmdline=None):
    d = tmp_path / str(pid)
    d.mkdir()
    (d / "stat").write_text(f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1", encoding="utf-8")
    if cmdline is not None:
        (d / "cmdline").write_bytes(cmdline)


def test_a_name_the_kernel_cut_at_fifteen_characters_is_read_in_full(tmp_path, monkeypatch):
    monkeypatch.setattr(posix, "PROC", str(tmp_path))
    _proc(tmp_path, 10, "bucephalus-prob", cmdline=b"/home/u/dist/bucephalus-probe\x00--x\x00")
    _proc(tmp_path, 11, "python3.13", cmdline=b"/usr/bin/python3.13\x00-c\x00")
    _proc(tmp_path, 12, "a-very-long-nam")
    rows = {pid: name for pid, _, name in posix.process_table()}
    assert rows == {10: "bucephalus-probe", 11: "python3.13", 12: "a-very-long-nam"}


def test_a_zombie_is_still_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(posix, "PROC", str(tmp_path))
    d = tmp_path / "20"
    d.mkdir()
    (d / "stat").write_text("20 (gone) Z 1 20 20", encoding="utf-8")
    assert posix.process_table() == []
