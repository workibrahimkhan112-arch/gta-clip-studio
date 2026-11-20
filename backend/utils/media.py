from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path

from backend.core.config import VIDEO_EXTENSIONS, get_binary
from backend.core.process import run_process


def validate_source(value: str) -> Path:
    path = Path(value.strip().strip('"')).expanduser()
    if not path.is_absolute():
        raise ValueError("Choose an absolute path on this computer")
    path = path.resolve()
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError("Supported video files: MP4, MKV, MOV, AVI and WebM")
    if not path.is_file():
        raise ValueError("Video file not found on the server computer")
    if path.stat().st_size == 0:
        raise ValueError("The selected file is empty")
    return path


def probe(path: Path, ctx=None) -> dict:
    result = run_process([
        get_binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
        "-show_format", "-show_streams", "-of", "json", str(path),
    ], ctx=ctx, timeout=60)
    if result.returncode:
        raise ValueError(f"FFprobe could not index this recording: {result.stderr[-2000:]}")
    try:
        raw = json.loads(result.stdout)
        streams = raw.get("streams", [])
        videos = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
        if not videos:
            raise ValueError("The file has no playable video stream")
        video = videos[0]
        duration = float(raw.get("format", {}).get("duration") or video.get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("A finite positive video duration is required; finalize the recording before importing")
        if int(video.get("width", 0)) <= 0 or int(video.get("height", 0)) <= 0:
            raise ValueError("The recording has invalid video dimensions")
        audios = [{"index": s["index"], "codec": s.get("codec_name", "unknown"),
                   "sample_rate": int(s.get("sample_rate") or 0), "channels": int(s.get("channels") or 0)}
                  for s in streams if s.get("codec_type") == "audio"]
        try:
            fps = float(Fraction(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"))
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        return {"duration": duration, "width": int(video["width"]), "height": int(video["height"]),
                "fps": fps, "video_codec": video.get("codec_name", "unknown"),
                "audio_codec": audios[0]["codec"] if audios else None, "audio_streams": audios,
                "file_size": path.stat().st_size,
                "video_streams": [{"index": s["index"], "codec": s.get("codec_name")} for s in videos]}
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("FFprobe returned incomplete or invalid media metadata") from exc
