from __future__ import annotations

import ctypes
import msvcrt
import threading
from ctypes import wintypes

from . import LimitsRefused


def _terminate(pid: int, expect_start: int | None = None) -> None:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x0001 | 0x1000, False, pid)
    if h:
        try:
            if expect_start is None or _creation_time(h) == expect_start:
                k32.TerminateProcess(h, 1)
        finally:
            k32.CloseHandle(h)


def kill_tree_signal(pid: int, timeout_s: float, expect_start: int | None = None) -> None:
    from ..proc import descendants, process_start_time

    kids = descendants(pid)
    starts = {p: process_start_time(p) for p in kids}
    for p in reversed(kids):
        if starts[p] is not None:
            _terminate(p, starts[p])
    _terminate(pid, expect_start)


def alive(pid: int) -> bool:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return ctypes.get_last_error() == 5
    try:
        code = ctypes.c_uint32()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == 259
    finally:
        k32.CloseHandle(h)


class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


def process_table() -> list[tuple[int, int, str]]:
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snap in (-1, 0xFFFFFFFF, None):
        return []
    rows: list[tuple[int, int, str]] = []
    try:
        e = _PE32()
        e.dwSize = ctypes.sizeof(_PE32)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            rows.append((int(e.th32ProcessID), int(e.th32ParentProcessID),
                         e.szExeFile.decode("utf-8", "replace")))
            ok = k32.Process32Next(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return rows


def _creation_time(h) -> int | None:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    ct, et = wintypes.FILETIME(), wintypes.FILETIME()
    kt, ut = wintypes.FILETIME(), wintypes.FILETIME()
    if not k32.GetProcessTimes(h, ctypes.byref(ct), ctypes.byref(et),
                               ctypes.byref(kt), ctypes.byref(ut)):
        return None
    return (int(ct.dwHighDateTime) << 32) | int(ct.dwLowDateTime)


def process_start_time(pid: int) -> int | None:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        return _creation_time(h)
    finally:
        k32.CloseHandle(h)


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def process_memory_bytes(pid: int, field: str = "WorkingSetSize") -> int | None:
    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    h = k32.OpenProcess(0x1000 | 0x0010, False, pid)
    if not h:
        return None
    try:
        c = _PMC()
        c.cb = ctypes.sizeof(_PMC)
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            return int(getattr(c, field))
        return None
    finally:
        k32.CloseHandle(h)


class _MS(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _mem_status() -> _MS:
    m = _MS()
    m.dwLength = ctypes.sizeof(_MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m


def memory_status() -> dict:
    m = _mem_status()
    committed = m.ullTotalPageFile - m.ullAvailPageFile
    phys = m.ullTotalPhys
    return {
        "available_gb": round(m.ullAvailPhys / 1024 ** 3, 2),
        "committed_gb": round(committed / 1024 ** 3, 2),
        "physical_gb": round(phys / 1024 ** 3, 2),
        "commit_ratio": round(committed / phys, 3) if phys else None,
    }


def free_memory_gb() -> float | None:
    return _mem_status().ullAvailPhys / 1024 ** 3


def limits_note(timeout_s: int, memory_bytes: int, max_processes: int) -> str:
    return (f"job object: memory {memory_bytes // 2**20} MB per process, CPU "
            f"{timeout_s} s, {max_processes} processes; file size not limited on "
            "this platform (Phase 6)")


class _JobRef:
    def __init__(self, job) -> None:
        self._job = job
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            job, self._job = self._job, None
        if job:
            from ..execute.winjob import close_job

            close_job(job)


def spawn_limited(argv: list[str], *, timeout_s: int, memory_bytes: int,
                  max_processes: int, fsize_bytes: int, **popen):
    from ..execute.winjob import JobError, JobLimits, spawn_in_job

    try:
        proc, job = spawn_in_job(argv, JobLimits(memory_bytes=memory_bytes,
                                                 cpu_seconds=timeout_s,
                                                 max_processes=max_processes), **popen)
    except JobError as e:
        raise LimitsRefused(str(e)) from e
    return proc, _JobRef(job).close


def exit_note(proc, timeout_s: int) -> str:
    from ..execute.winjob import CPU_LIMIT_EXIT

    return (f"\nsandbox: CPU time limit reached ({timeout_s} s)"
            if proc.returncode == CPU_LIMIT_EXIT else "")


def adopted(pid: int, ppid: int) -> bool:
    return False


def spawn_denied_processes() -> int:
    import os
    import sys

    from ..frozen import is_frozen

    if is_frozen():
        return 1
    base = getattr(sys, "_base_executable", sys.executable)
    same = (os.path.normcase(os.path.realpath(base))
            == os.path.normcase(os.path.realpath(sys.executable)))
    return 1 if same else 2


def link_names(path: str) -> list[str] | None:
    import os

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    first = k32.FindFirstFileNameW
    first.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                      wintypes.LPWSTR]
    first.restype = wintypes.HANDLE
    nxt = k32.FindNextFileNameW
    nxt.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
    nxt.restype = wintypes.BOOL
    k32.FindClose.argtypes = [wintypes.HANDLE]
    volume = os.path.splitdrive(os.path.abspath(path))[0]
    buf = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buf))
    h = first(path, 0, ctypes.byref(size), buf)
    if h in (None, wintypes.HANDLE(-1).value):
        return None
    names = []
    try:
        while True:
            names.append(volume + buf.value)
            size = wintypes.DWORD(len(buf))
            if not nxt(h, ctypes.byref(size), buf):
                break
    finally:
        k32.FindClose(h)
    return names


def has_data_streams() -> bool:
    return True


def background_popen_flags() -> dict:
    return {"creationflags": 0x08000000}


def lock_file(fh) -> None:
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)


def unlock_file(fh) -> None:
    fh.seek(0)
    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
