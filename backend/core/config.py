from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORAGE_DIR = Path(os.environ.get("GTA_STORAGE_DIR", str(ROOT / "storage"))).expanduser().resolve()
PROJECTS_DIR = STORAGE_DIR / "projects"
CACHE_DIR = STORAGE_DIR / "cache"
EXPORTS_DIR = STORAGE_DIR / "exports"
DATABASE_PATH = STORAGE_DIR / "studio.sqlite3"
VERSION = "1.0.0"
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}


def init_directories() -> None:
    for path in (STORAGE_DIR, PROJECTS_DIR, CACHE_DIR, EXPORTS_DIR, CACHE_DIR / "work"):
        path.mkdir(parents=True, exist_ok=True)


def get_binary(name: str) -> str:
    if name not in {"ffmpeg", "ffprobe"}:
        raise ValueError("Unsupported media executable")
    configured = os.environ.get(f"{name.upper()}_PATH")
    if configured:
        path = Path(configured.strip('"')).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise RuntimeError(f"{name.upper()}_PATH points to a missing file: {path}")
    # Tool installation belongs to the application, not a custom project-data
    # directory. This also works for manual backend/npm starts after setup.
    bundled = ROOT / "storage" / "tools" / "ffmpeg" / "bin" / (name + (".exe" if os.name == "nt" else ""))
    if bundled.is_file():
        return str(bundled)
    executable = shutil.which(name)
    if executable:
        return executable
    raise RuntimeError(f"{name} is missing. Run run.dev.cmd to install local media tools automatically, or install FFmpeg and set {name.upper()}_PATH.")
