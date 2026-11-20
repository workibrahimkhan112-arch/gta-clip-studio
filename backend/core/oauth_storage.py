"""Private, atomic SIWC storage and an OS lock for rotating refresh tokens."""
from __future__ import annotations

import json
import os
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from backend.core.config import STORAGE_DIR

_PREFIX = b"GTASIW1\0"
_MAX_BYTES = 4 * 1024 * 1024
_LOCK = threading.RLock()
_LOCK_TIMEOUT = 40.0
_ERROR = "Saved ChatGPT credentials are unavailable. Check private storage permissions and the Windows user account."


class StorageError(RuntimeError):
    pass


def _path() -> Path:
    return STORAGE_DIR / "private" / "chatgpt-auth.bin"


def _windows() -> bool:
    return os.name == "nt"


def _crypt(data: bytes, decrypt: bool = False) -> bytes:
    # The provider-key store owns the shared Windows DPAPI implementation.
    from backend.core.integrations import _crypt as protect
    return protect(data, decrypt=decrypt)


def _private_directory() -> Path:
    path = _path().parent
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise StorageError(_ERROR)
    if not _windows() and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022):
        raise StorageError(_ERROR)
    return path


def _open(path: Path, flags: int, mode: int = 0o600) -> int:
    if path.is_symlink():
        raise StorageError(_ERROR)
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise StorageError(_ERROR)
        if not _windows() and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise StorageError(_ERROR)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def locked():
    """Hold across read, refresh HTTP, and atomic write, including across processes.

    The lock file is deliberately never unlinked: replacing it would let another
    process lock a different inode while a refresh is still in progress.
    """
    if not _LOCK.acquire(timeout=_LOCK_TIMEOUT):
        raise StorageError("ChatGPT credentials are busy. Try again shortly.")
    descriptor = None
    acquired = False
    try:
        path = _path()
        _private_directory()
        descriptor = _open(path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                if _windows():
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StorageError("ChatGPT credentials are busy. Try again shortly.") from None
                time.sleep(0.025)
        yield
    except StorageError:
        raise
    except OSError:
        raise StorageError(_ERROR) from None
    finally:
        try:
            if descriptor is not None:
                try:
                    if acquired:
                        if _windows():
                            import msvcrt
                            os.lseek(descriptor, 0, os.SEEK_SET)
                            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        finally:
            _LOCK.release()


def read() -> dict:
    """Caller must hold locked(); never recover corruption by losing registrations."""
    try:
        path = _path()
        try:
            descriptor = _open(path, os.O_RDONLY)
        except FileNotFoundError:
            return {"version": 1, "host_id": None, "active_account_id": None, "accounts": []}
        with os.fdopen(descriptor, "rb") as source:
            raw = source.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("size")
        if _windows():
            if not raw.startswith(_PREFIX):
                raise ValueError("unprotected")
            raw = _crypt(raw[len(_PREFIX):], decrypt=True)
        elif raw.startswith(_PREFIX):
            raise ValueError("Windows account required")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("version")
        return data
    except (OSError, ValueError, RuntimeError, UnicodeError):
        raise StorageError(_ERROR) from None


def write(data: dict) -> None:
    """Caller must hold locked(); persist the whole rotated token set together."""
    temporary = None
    try:
        path = _path()
        payload = json.dumps(data, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if _windows():
            payload = _PREFIX + _crypt(payload)
        if len(payload) > _MAX_BYTES:
            raise ValueError("size")
        temporary = path.with_name(f".chatgpt-{uuid.uuid4().hex}.tmp")
        descriptor = _open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if not _windows():
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except (OSError, ValueError, RuntimeError, UnicodeError):
        raise StorageError(_ERROR) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
