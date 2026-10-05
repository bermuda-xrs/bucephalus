from __future__ import annotations

import ctypes
import subprocess
from dataclasses import dataclass

CREATE_SUSPENDED = 0x00000004
CPU_LIMIT_EXIT = 0xC0000044

_EXTENDED_LIMIT_INFORMATION = 9
_LIMIT_JOB_TIME = 0x00000004
_LIMIT_ACTIVE_PROCESS = 0x00000008
_LIMIT_PROCESS_MEMORY = 0x00000100
_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class JobError(OSError):
    ...


@dataclass(frozen=True)
class JobLimits:
    memory_bytes: int
    cpu_seconds: float
    max_processes: int


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32)]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimits),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _api():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_void_p, ctypes.c_uint32]
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
    return k32, ntdll


def _create_job(k32, limits: JobLimits) -> int:
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise JobError(ctypes.get_last_error(), "CreateJobObjectW failed")
    info = _ExtendedLimits()
    basic = info.BasicLimitInformation
    basic.LimitFlags = (_LIMIT_PROCESS_MEMORY | _LIMIT_JOB_TIME
                        | _LIMIT_ACTIVE_PROCESS | _LIMIT_KILL_ON_JOB_CLOSE)
    basic.PerJobUserTimeLimit = int(limits.cpu_seconds * 10_000_000)
    basic.ActiveProcessLimit = limits.max_processes
    info.ProcessMemoryLimit = limits.memory_bytes
    if not k32.SetInformationJobObject(job, _EXTENDED_LIMIT_INFORMATION,
                                       ctypes.byref(info), ctypes.sizeof(info)):
        err = ctypes.get_last_error()
        k32.CloseHandle(job)
        raise JobError(err, "SetInformationJobObject failed")
    return job


def spawn_in_job(argv: list[str], limits: JobLimits,
                 **popen_kwargs) -> tuple[subprocess.Popen, int]:
    k32, ntdll = _api()
    job = _create_job(k32, limits)
    flags = popen_kwargs.pop("creationflags", 0) | CREATE_SUSPENDED
    try:
        proc = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)  # noqa: S603
    except BaseException:
        k32.CloseHandle(job)
        raise
    handle = int(proc._handle)  # noqa: SLF001
    if not k32.AssignProcessToJobObject(job, handle):
        err = ctypes.get_last_error()
        _refuse(proc, k32, job)
        raise JobError(err, "AssignProcessToJobObject failed; the child never ran")
    status = ntdll.NtResumeProcess(handle)
    if status != 0:
        _refuse(proc, k32, job)
        raise JobError(status, "NtResumeProcess failed; the child never ran")
    return proc, job


def _refuse(proc: subprocess.Popen, k32, job: int) -> None:
    try:
        proc.kill()
        proc.wait(timeout=5)
    finally:
        k32.CloseHandle(job)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream:
                stream.close()


def close_job(job: int) -> None:
    if job:
        k32, _ = _api()
        k32.CloseHandle(job)
