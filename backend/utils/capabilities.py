from __future__ import annotations

import importlib.util
import os
from functools import lru_cache

import psutil
from backend.core.config import get_binary


def available(name: str) -> bool:
    try:
        get_binary(name)
        return True
    except RuntimeError:
        return False


@lru_cache(maxsize=1)
def hardware() -> dict:
    return {"physical_cores": psutil.cpu_count(logical=False) or 1,
            "logical_cores": os.cpu_count() or 1,
            "memory_gb": round(psutil.virtual_memory().total / 1024**3, 2)}


def capabilities() -> dict:
    from backend.processing.transcription import model_is_cached
    return {**hardware(), "ffmpeg": available("ffmpeg"), "ffprobe": available("ffprobe"),
            "yt_dlp": importlib.util.find_spec("yt_dlp") is not None,
            "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
                        "caption_model_cached": model_is_cached()}
