"""Write-only provider keys, never serialized into settings/jobs/API responses.

Windows storage is encrypted with the current user's DPAPI. Other platforms use
a private 0600 JSON file. Environment variables override locally saved keys.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from backend.core.config import STORAGE_DIR

_LOCK = threading.RLock()
_PROVIDERS = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}
_PREFIX = b"GTAKEY1\0"


def _validate_provider(provider: str):
    if provider not in _PROVIDERS:
        raise ValueError("Supported providers: gemini, groq and openai")


def _crypt(data: bytes, decrypt=False) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        args = (ctypes.byref(source), "GTA Clip Studio credentials", None, None, None, 1, ctypes.byref(target))
    function.restype = wintypes.BOOL
    if not function(*args):
        raise RuntimeError("Provider credential encryption failed for this Windows account")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def _path() -> Path:
    return STORAGE_DIR / "private" / "provider-keys.bin"


def _read() -> dict:
    path = _path()
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > 65536:
            raise ValueError("Oversized credential file")
        raw = path.read_bytes()
        if raw.startswith(_PREFIX):
            if os.name != "nt":
                raise ValueError("Windows account required")
            raw = _crypt(raw[len(_PREFIX):], decrypt=True)
        data = json.loads(raw)
        if not isinstance(data, dict) or any(key not in _PROVIDERS or not isinstance(value, str) for key, value in data.items()):
            raise ValueError("Invalid credentials")
        return data
    except (OSError, ValueError, RuntimeError, UnicodeError):
        raise RuntimeError("Saved provider credentials cannot be read. Remove storage/private/provider-keys.bin and re-enter keys, or use environment variables.") from None


def get_api_key(provider: str) -> str | None:
    _validate_provider(provider)
    configured = os.environ.get(_PROVIDERS[provider], "").strip()
    if configured:
        return configured
    with _LOCK:
        return _read().get(provider) or None


def public_integrations() -> dict:
    result = {}
    for provider, variable in _PROVIDERS.items():
        environment = bool(os.environ.get(variable, "").strip())
        try:
            configured = bool(get_api_key(provider))
        except RuntimeError:
            configured = False
        result[provider] = {"configured": configured, "source": "environment" if environment else "local" if configured else "none"}
    return result


def set_api_key(provider: str, key: str) -> dict:
    _validate_provider(provider)
    key = key.strip()
    if key and (len(key) > 4096 or not key.isascii() or any(ord(char) < 33 or ord(char) == 127 for char in key)):
        raise ValueError("API key must contain only printable ASCII without spaces")
    with _LOCK:
        keys = _read()
        if key:
            keys[provider] = key
        else:
            keys.pop(provider, None)
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = json.dumps(keys).encode("utf-8")
        if os.name == "nt":
            payload = _PREFIX + _crypt(payload)
        temporary = path.with_name(f".keys-{uuid.uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return public_integrations()
