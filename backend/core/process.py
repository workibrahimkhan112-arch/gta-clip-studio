"""Own subprocess trees, including descendants after their leader exits.

On Windows children start suspended, enter a kill-on-close Job Object, then
resume. This closes the create/assign race and also reaps FFmpeg mergers if the
backend is forcibly killed. On POSIX each child owns a fresh process group.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from typing import Any, cast


class Cancelled(Exception):
    """Cooperative cancellation; completed analysis chunks remain valid."""


def _windows_job(process):
    import ctypes
    from ctypes import wintypes as wt
    import psutil

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    kernel.CreateJobObjectW.restype = wt.HANDLE
    kernel.SetInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
    kernel.SetInformationJobObject.restype = wt.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    kernel.AssignProcessToJobObject.restype = wt.BOOL
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.CloseHandle.restype = wt.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits = EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(handle, wt.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        process._studio_job = (kernel, handle)
        psutil.Process(process.pid).resume()
    except BaseException:
        kernel.CloseHandle(handle)
        process._studio_job = None
        raise


def _spawn(command, **kwargs: Any) -> subprocess.Popen[bytes]:
    options: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004,
    }
    process = subprocess.Popen([str(arg) for arg in command], stdin=subprocess.DEVNULL, **kwargs, **options)
    if os.name == "nt":
        try:
            _windows_job(process)
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()
            raise
    return cast(subprocess.Popen[bytes], process)


def terminate(process: subprocess.Popen) -> None:
    # Closing the Job Object is required even if the direct child has exited.
    if os.name == "nt":
        job = getattr(process, "_studio_job", None)
        if job:
            setattr(process, "_studio_job", None)
            job[0].CloseHandle(job[1])
        elif process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if os.name != "nt":
        # A leader can exit before an uncooperative descendant; own the group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@contextlib.contextmanager
def managed_process(command: list[str], ctx=None) -> Iterator[subprocess.Popen]:
    if ctx:
        ctx.check_cancelled()
    with tempfile.TemporaryFile(mode="w+b") as errors:
        process = _spawn(command, stdout=subprocess.PIPE, stderr=errors)
        process.stderr = errors
        try:
            yield process
        finally:
            terminate(process)
            if process.stdout:
                process.stdout.close()


def run_process(command: list[str], ctx=None, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Bounded capture for probe/single-image commands; no blocked pipe reads."""
    if ctx:
        ctx.check_cancelled()
    limit = 8 * 1024 * 1024
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = _spawn(command, stdout=output, stderr=errors)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if ctx:
                    ctx.check_cancelled()
                if time.monotonic() - started > timeout:
                    raise subprocess.TimeoutExpired(command, timeout)
                if os.fstat(output.fileno()).st_size > limit or os.fstat(errors.fileno()).st_size > limit:
                    raise RuntimeError("Media process exceeded the 8 MiB diagnostic output limit")
                time.sleep(0.05)
            if ctx:
                ctx.check_cancelled()
            output.seek(0)
            errors.seek(max(0, os.fstat(errors.fileno()).st_size - limit))
            return subprocess.CompletedProcess(command, process.returncode,
                output.read(limit).decode("utf-8", errors="replace"),
                errors.read(limit).decode("utf-8", errors="replace"))
        finally:
            terminate(process)
