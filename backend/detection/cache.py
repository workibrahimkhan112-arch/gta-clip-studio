"""Disposable, atomic feature chunks, capped at 64 MiB per project.

Only completed chunks are committed. Interrupted/invalid chunks are rescanned;
completed chunks participate in progress and scoring exactly like fresh ones.
Eviction bounds disk, not just RAM: extremely long recordings may have their
oldest chunks evicted and consequently need some rescanning on a later resume.
There is no claim that the cache is a permanent analysis database. A separate
project-level events.json atomically replaces the last complete event summary,
with a 2 MiB entry limit; it never grows by appending recording-wide features.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

CACHE_VERSION = 3
MAX_CHUNK_BYTES = 2 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024


def fingerprint(project, scan_settings):
    """Invalidate on source identity/stat, indexed metadata or scan settings.

    Stat checks avoid reading/hash-decoding an entire multi-hour recording.
    In-place edits preserving both size and mtime cannot be detected this way.
    Clip count/duration are ranking-only settings and do not invalidate features.
    """
    source = Path(project["source_path"]).resolve()
    stat = source.stat()
    identity = {
        "version": CACHE_VERSION,
        "project_id": str(project["id"]),
        "path": str(source),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "metadata": project["metadata"],
        "settings": scan_settings,
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _reject_constant(value):
    raise ValueError("Non-finite cache number: " + value)


def _atomic_json(path, document, ctx=None, prefix=".chunk-"):
    if ctx is not None:
        ctx.check_cancelled()
    raw = json.dumps(document, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_CHUNK_BYTES:
        raise ValueError("Detection cache entry exceeds bounded cache size")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=prefix, suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if ctx is not None:
            ctx.check_cancelled()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def store_events(cache_dir, document, ctx=None):
    """Publish a complete bounded timeline to project cache_dir/events.json.

    The coordinator can serve the document's events array from its project
    events endpoint. Source/cache identity, settings and heuristic caveats are
    retained in the envelope. Only a completed analysis replaces this file;
    cancellation/failure preserves the previous complete snapshot. API readers
    should use the envelope's cache_key to identify the analyzed source, not
    interpret a previous snapshot as a new or in-progress analysis.
    """
    directory = Path(cache_dir)
    directory.mkdir(parents=True, exist_ok=True)
    # Only one heavy job is active: leftovers from a crashed writer are stale.
    for path in directory.glob(".events-*.tmp"):
        path.unlink(missing_ok=True)
    _atomic_json(directory / "events.json", document, ctx, prefix=".events-")


class ChunkCache:
    def __init__(self, cache_dir, key, max_bytes=MAX_CACHE_BYTES):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Cache key must be a SHA-256 hex digest")
        self.root = Path(cache_dir) / "detection"
        self.directory = self.root / key
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key = key
        self.max_bytes = max_bytes
        self.prune()

    def path(self, kind, index):
        if kind not in ("audio", "video") or index < 0:
            raise ValueError("Invalid chunk address")
        return self.directory / f"{kind}-{index:08d}.json"

    def load(self, kind, index, start, end):
        path = self.path(kind, index)
        try:
            if path.stat().st_size > MAX_CHUNK_BYTES:
                return None
            with path.open("rb") as handle:
                raw = handle.read(MAX_CHUNK_BYTES + 1)
            if len(raw) > MAX_CHUNK_BYTES:
                return None
            document = json.loads(raw, parse_constant=_reject_constant)
            if not isinstance(document, dict):
                return None
            if (document.get("version"), document.get("key"), document.get("kind"), document.get("start"), document.get("end")) != (CACHE_VERSION, self.key, kind, start, end):
                return None
            payload = document["payload"]
            return payload if isinstance(payload, dict) else None
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return None

    def store(self, kind, index, start, end, payload, ctx=None):
        if not all(math.isfinite(value) for value in (start, end)) or end <= start:
            raise ValueError("Invalid chunk boundaries")
        document = {"version": CACHE_VERSION, "key": self.key, "kind": kind, "start": start, "end": end, "payload": payload}
        _atomic_json(self.path(kind, index), document, ctx)
        self.prune()

    def prune(self):
        """Only inspect this detector's files; never remove other job caches."""
        files = []
        total = 0
        for path in self.root.glob("*/*.json"):
            try:
                stat = path.stat()
                files.append((stat.st_mtime_ns, path, stat.st_size))
                total += stat.st_size
            except OSError:
                continue
        for _, path, size in sorted(files):
            if total <= self.max_bytes:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass
        # A crashed writer cannot contribute features. Single-heavy-job
        # serialization in the coordinator makes stale temporary cleanup safe.
        for path in self.root.glob("*/.chunk-*.tmp"):
            try:
                path.unlink()
            except OSError:
                pass
