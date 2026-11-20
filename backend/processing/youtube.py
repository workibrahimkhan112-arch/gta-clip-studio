"""Public YouTube VOD downloads through yt-dlp; no credentials or DRM bypass."""
from __future__ import annotations

import queue
import re
import shutil
import sys
import threading
import time

from urllib.parse import parse_qs, urlparse

from backend.core.config import PROJECTS_DIR, get_binary
from backend.core.process import managed_process
from backend.utils.media import probe, validate_source


def validate_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("Use a public HTTPS YouTube video URL")
    if host == "youtu.be":
        video_id = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        parts = parsed.path.strip("/").split("/")
        video_id = parse_qs(parsed.query).get("v", [""])[0] if parsed.path == "/watch" else (parts[1] if len(parts) == 2 and parts[0] in {"live", "shorts", "embed"} else "")
    else:
        raise ValueError("Only public youtube.com and youtu.be VOD URLs are accepted")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError("The URL must identify one YouTube video, not a playlist or channel")
    return f"https://www.youtube.com/watch?v={video_id}"


def download(project: dict, settings: dict, ctx) -> dict:
    url = validate_url(project["source_url"])
    binary = get_binary("ffmpeg")
    directory = PROJECTS_DIR / project["id"]
    directory.mkdir(parents=True, exist_ok=True)
    ctx.progress("download", 0, 0, "Downloading public VOD; duration-based ETA is unavailable during import")
    command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-warnings",
               "--newline", "--no-colors", "--continue", "--restrict-filenames", "--no-mtime",
               "--match-filter", "!is_live & !is_upcoming", "--merge-output-format", "mkv",
               "--ffmpeg-location", binary,
               "--format", "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
               "--output", str(directory / "source.%(ext)s"), "--", url]
    messages = queue.Queue(maxsize=64)
    stop = threading.Event()
    reader = None
    started = time.monotonic()
    def send(value):
        while not stop.is_set():
            try:
                messages.put(value, timeout=0.1)
                return
            except queue.Full:
                pass
    try:
        with managed_process(command, ctx=ctx) as process:
            stdout, stderr = process.stdout, process.stderr
            if stdout is None or stderr is None:
                raise RuntimeError("Download progress pipes could not be created")

            def read():
                try:
                    for line in iter(lambda: stdout.readline(4096), b""):
                        send(line)
                finally:
                    send(None)
            reader = threading.Thread(target=read, name="vod-download-progress", daemon=True)
            reader.start()
            while True:
                ctx.check_cancelled()
                if time.monotonic() - started > 48 * 3600:
                    raise TimeoutError("YouTube import exceeded 48 hours; try a local file")
                if shutil.disk_usage(directory).free < 512 * 1024**2:
                    raise RuntimeError("Download stopped: less than 512 MiB disk space remains")
                try:
                    line = messages.get(timeout=0.2)
                except queue.Empty:
                    continue
                if line is None:
                    while process.poll() is None:
                        ctx.check_cancelled()
                        time.sleep(0.1)
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith("[download]"):
                    ctx.progress("download", 0, 0, text[:400])
            if process.returncode:
                stderr.seek(0)
                error = stderr.read(6000).decode("utf-8", errors="replace")
                raise RuntimeError(f"Public VOD import failed: {error}. Private/DRM-restricted media is not supported; try a legal local recording. Updating yt-dlp may resolve extractor errors.")
    finally:
        stop.set()
        if reader:
            reader.join(timeout=1)
    ctx.check_cancelled()
    files = [p for p in directory.glob("source.*") if p.suffix.lower() in {".mp4", ".mkv", ".mov", ".avi", ".webm"}]
    if not files:
        raise RuntimeError("yt-dlp did not produce a supported VOD; live or upcoming streams are not supported")
    source = max(files, key=lambda path: path.stat().st_size)
    source = validate_source(str(source.resolve()))
    ctx.progress("index", 0, 0, "Indexing the downloaded recording")
    metadata = probe(source, ctx)
    return {"source_path": str(source), "metadata": metadata}
