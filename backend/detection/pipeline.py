"""Speed-first highlight detection entry point defined in CONTRACTS.md.

The detector ranks measured audio/visual activity, not recognized GTA events.
It needs only NumPy and local FFmpeg/FFprobe (no OpenCV, models or network).
A half-second audio window, one sparse frame, one chunk's descriptors, rolling
60-second baselines and a capped candidate pool bound memory independently of
recording duration. Cache is capped separately; it is not a hard OS memory cap.

Progress reports fully audio-scanned, sparsely video-sampled timeline seconds.
During audio decoding of a chunk that number stays at the previous completed
boundary; detail gives actual PCM seconds decoded in the current chunk. After
that, each frame seek advances coverage. Cached coverage advances identically.
This is one stable "scan" stage, not an invented overall percentage or FPS.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid

from .cache import ChunkCache, fingerprint, store_events
from .features import SAMPLE_RATE
from .media import scan_audio, scan_video, valid_audio_cache, valid_video_cache
from .ranking import StreamingRanker


@dataclass(frozen=True)
class DetectionSettings:
    sampling_interval: int = 5
    analysis_width: int = 320
    audio_sample_rate: int = SAMPLE_RATE
    cpu_workers: int = 2
    analysis_chunk_seconds: int = 300
    memory_mode: str = "low"
    max_clips: int = 10
    clip_duration: float = 35
    ai_refinement: bool = False

    @classmethod
    def parse(cls, settings):
        values = {key: settings.get(key, field.default) for key, field in cls.__dataclass_fields__.items()}
        result = cls(**values)
        for key in ("sampling_interval", "analysis_width", "audio_sample_rate", "cpu_workers"):
            if isinstance(values[key], bool) or not isinstance(values[key], int):
                raise ValueError(f"{key} must be an integer")
        if not isinstance(result.ai_refinement, bool):
            raise ValueError("ai_refinement must be a boolean")
        for key, choices in (("sampling_interval", (1, 2, 5, 10)), ("analysis_width", (160, 320, 480)),
                             ("audio_sample_rate", (SAMPLE_RATE,)), ("cpu_workers", (1, 2)),
                             ("memory_mode", ("low", "balanced"))):
            if getattr(result, key) not in choices:
                raise ValueError(f"Invalid detection setting {key}: expected one of {choices}")
        for key, lower, upper in (("analysis_chunk_seconds", 60, 600), ("max_clips", 1, 50), ("clip_duration", 10, 90)):
            value = getattr(result, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError(f"{key} must be between {lower} and {upper}")
        if not isinstance(result.max_clips, int) or not isinstance(result.analysis_chunk_seconds, int):
            raise ValueError("max_clips and analysis_chunk_seconds must be integers")
        # Cap per-chunk descriptor working sets in the low-memory profile;
        # balanced mode honors larger configured recovery chunks up to 600s.
        if result.memory_mode == "low":
            result = replace(result, analysis_chunk_seconds=min(result.analysis_chunk_seconds, 300))
        return result

    def cache_settings(self):
        return {key: getattr(self, key) for key in ("sampling_interval", "analysis_width", "audio_sample_rate",
                                                    "cpu_workers", "analysis_chunk_seconds", "memory_mode")}


def _streams(project, ctx):
    """Use indexed playable streams; exclude cover art in the probe fallback."""
    metadata = project["metadata"]
    audio = metadata.get("audio_streams")
    has_audio = bool(audio) or bool(metadata.get("audio_codec"))
    has_video = (bool(metadata["video_streams"]) if "video_streams" in metadata else
                 bool(metadata.get("video_codec")) or bool(metadata.get("width") and metadata.get("height")))
    audio_known = "audio_streams" in metadata or "audio_codec" in metadata
    video_known = "video_streams" in metadata or "video_codec" in metadata or bool(metadata.get("width") and metadata.get("height"))
    index = audio[0].get("index") if audio else None
    if not audio_known or not video_known:
        from ..core.config import get_binary
        from ..core.process import run_process

        command = [str(get_binary("ffprobe")), "-v", "error", "-show_entries",
                   "stream=index,codec_type:stream_disposition=attached_pic", "-of", "json", str(project["source_path"])]
        result = run_process(command, ctx=ctx, timeout=30)
        if result.returncode:
            raise RuntimeError("Could not probe source streams; verify the source is readable")
        try:
            streams = json.loads(result.stdout)["streams"]
            if not audio_known:
                audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
                has_audio = bool(audio_streams)
                index = audio_streams[0]["index"] if has_audio else None
            if not video_known:
                has_video = any(stream.get("codec_type") == "video" and not stream.get("disposition", {}).get("attached_pic")
                                for stream in streams)
        except (ValueError, KeyError, TypeError) as error:
            raise RuntimeError("FFprobe returned invalid stream metadata") from error
    return has_audio, has_video, index


def _highlight(candidate, project_id, cache_key, interval):
    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"gta-clip-studio:{project_id}:{cache_key}:{candidate.start:.9f}:{candidate.end:.9f}"))
    titles = {"mixed_activity": "Audio and visual activity", "audio_activity": "Audio activity peak",
              "visual_activity": "Visual activity change", "scene_change": "Scene appearance change"}
    confidence = min(0.95, 0.15 + 0.7 * candidate.score / 100 + (0.10 if candidate.category == "mixed_activity" else 0))
    explanation = (
        f"Heuristic signal peak near {candidate.time:.1f}s: audio {candidate.audio:.1f}/100, "
        f"frame movement {candidate.visual:.1f}/100, scene appearance change {candidate.scene:.1f}/100. "
        f"Audio uses RMS, peak amplitude and spectral changes against a rolling baseline; "
        f"video uses seeked frames every {interval}s. Camera motion, edits, lighting and non-gameplay "
        f"sounds can trigger these signals. No specific gameplay event is identified; confidence "
        f"describes signal salience, not a semantic probability."
    )
    return {"id": identifier, "project_id": str(project_id), "title": titles[candidate.category],
            "start": candidate.start, "end": candidate.end, "duration": candidate.end - candidate.start,
            "category": candidate.category, "score": round(candidate.score, 3), "confidence": round(confidence, 4),
            "audio_score": round(candidate.audio, 3), "visual_score": round(candidate.visual, 3),
            "scene_score": round(candidate.scene, 3), "explanation": explanation, "selected": True,
            "transcript": [], "thumbnail_url": f"/api/highlights/{identifier}/thumbnail"}


def _refine(project, highlights, settings, ctx):
    from ..core.process import Cancelled

    total = sum(highlight["duration"] for highlight in highlights)
    ctx.progress("refine", 0.0, total, "Optional local transcription of shortlisted candidates only")
    try:
        from ..processing.transcription import refine

        # A failed optional refiner must not mutate the base result halfway.
        result = refine(project, copy.deepcopy(highlights), settings, ctx)
        expected = {highlight["id"] for highlight in highlights}
        if not isinstance(result, list) or len(result) != len(highlights) or {item.get("id") for item in result} != expected:
            raise ValueError("Optional refinement returned an invalid shortlist")
        # Refinement may provide actual transcript/text, but is not a detector
        # and must not change measured timing, scores, categories or IDs.
        by_id = {item["id"]: item for item in result}
        for highlight in highlights:
            refined = by_id[highlight["id"]]
            if isinstance(refined.get("transcript"), list):
                highlight["transcript"] = refined["transcript"]
    except Cancelled:
        raise
    except Exception as error:
        ctx.check_cancelled()
        ctx.progress("refine", 0.0, total, f"Optional transcription unavailable; keeping signal-only highlights ({type(error).__name__})")
    return highlights


def _prune_thumbnails(directory, keep):
    """Bound disposable JPEGs to 256 files/16 MiB, retaining this shortlist."""
    entries = []
    for path in directory.glob("*.jpg"):
        if path.name.startswith("."):
            continue
        try:
            stat = path.stat()
            entries.append((stat.st_mtime_ns, path, stat.st_size))
        except OSError:
            continue
    total = sum(entry[2] for entry in entries)
    count = len(entries)
    for _, path, size in sorted(entries):
        if total <= 16 * 1024 * 1024 and count <= 256:
            break
        if path.name in keep:
            continue
        try:
            path.unlink()
            total -= size
            count -= 1
        except OSError:
            pass


def _thumbnails(binary, source, highlights, cfg, ctx):
    from ..core.process import Cancelled, run_process

    directory = Path(ctx.cache_dir) / "thumbnails"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    total = sum(highlight["duration"] for highlight in highlights)
    keep = {f"{highlight['id']}.jpg" for highlight in highlights}
    _prune_thumbnails(directory, keep)
    processed = 0.0
    for highlight in highlights:
        ctx.check_cancelled()
        target = directory / f"{highlight['id']}.jpg"
        temporary = directory / f".{highlight['id']}.{uuid.uuid4().hex}.tmp.jpg"
        try:
            if not target.is_file() or not target.stat().st_size:
                timestamp = highlight["start"] + highlight["duration"] * 0.6
                command = [str(binary), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                           "-threads", str(cfg.cpu_workers), "-filter_threads", str(cfg.cpu_workers),
                           "-ss", f"{timestamp:.9f}", "-i", str(source), "-map", "0:V:0", "-an", "-sn", "-dn", "-frames:v", "1",
                           "-vf", f"scale={cfg.analysis_width}:{cfg.analysis_width}:force_original_aspect_ratio=decrease",
                           "-threads", str(cfg.cpu_workers), str(temporary)]
                result = run_process(command, ctx=ctx, timeout=30)
                ctx.check_cancelled()
                if result.returncode == 0 and temporary.is_file() and temporary.stat().st_size:
                    os.replace(temporary, target)
        except Cancelled:
            raise
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # Thumbnails are optional, never a reason to discard real signals.
            ctx.check_cancelled()
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            _prune_thumbnails(directory, keep)
        processed += highlight["duration"]
        ctx.progress("thumbnails", processed, total, "Seek-based candidate thumbnails (best effort)")


def _event_timeline(ranker, project_id, cache_key, cfg):
    """Serialize retained measured peaks, not guessed semantic detections.

    Event time is the source-relative signal peak; start/end are suggested clip
    context, not the duration of a recognized action. There is no second event
    collector or media pass. The coordinator owns exposing this cache via HTTP.
    """
    events = []
    for candidate in ranker.timeline():
        identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, f"gta-clip-studio:event:{project_id}:{cache_key}:{candidate.time:.9f}"))
        events.append({"id": identifier, "time": candidate.time, "start": candidate.start, "end": candidate.end,
                       "category": candidate.category, "score": round(candidate.score, 3),
                       "audio_score": round(candidate.audio, 3), "visual_score": round(candidate.visual, 3),
                       "scene_score": round(candidate.scene, 3)})
    return {"version": 1, "project_id": str(project_id), "cache_key": cache_key, "duration": ranker.duration,
            "complete": True, "heuristic": True,
            "settings": {"sampling_interval": cfg.sampling_interval, "clip_duration": cfg.clip_duration, "max_clips": cfg.max_clips},
            "detail": "Retained regional signal peaks before highlight overlap suppression; not an exhaustive or semantic gameplay log. "
                      "Times are source-relative seconds; start/end are suggested clip context. Quiet or discarded regions do not prove absence of events.",
            "events": events}


def analyze(project: dict, settings: dict, ctx) -> list[dict]:
    """Return contract-shaped, selected highlight dictionaries; never touch DB.

    Requires indexed metadata.duration and an original local source_path. Empty
    and static/silent recordings can legitimately return no highlights. Audio
    ending before video yields no audio evidence for the uncovered tail, not
    invented silence/transcripts. Existing completed chunks survive cancellation;
    chunk reads and seeks are interruptible through shared core.process. A
    successful run atomically publishes bounded measured peaks to the project's
    cache_dir/events.json, independently of the returned highlight shortlist.
    """
    ctx.check_cancelled()
    cfg = DetectionSettings.parse(settings)
    try:
        duration = float(project["metadata"]["duration"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Analysis requires indexed metadata.duration in seconds") from error
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("Indexed duration must be finite and nonnegative")
    ctx.progress("scan", 0.0, duration, "Preparing real audio and sparse-frame scan")
    if duration == 0:
        return []

    from ..core.config import get_binary

    key = fingerprint(project, cfg.cache_settings())
    cache = ChunkCache(ctx.cache_dir, key)
    has_audio, has_video, stream_index = _streams(project, ctx)
    if not has_audio and not has_video:
        raise ValueError("Source has no indexed audio or video streams")
    binary = get_binary("ffmpeg")
    ranker = StreamingRanker(duration, cfg.clip_duration, cfg.max_clips, cfg.sampling_interval)
    previous_frame = None
    decoded_seconds = 0.0
    successful_frames = 0
    failed_frames = 0
    chunk_count = math.ceil(duration / cfg.analysis_chunk_seconds)

    for index in range(chunk_count):
        ctx.check_cancelled()
        start = float(index * cfg.analysis_chunk_seconds)
        end = min(duration, start + cfg.analysis_chunk_seconds)
        label = f"Chunk {index + 1}/{chunk_count}"
        audio_cached = False
        if has_audio:
            audio = cache.load("audio", index, start, end)
            if audio is None or not valid_audio_cache(audio, start, end):
                last_audio_update = 0.0

                def audio_progress(seconds):
                    nonlocal last_audio_update
                    now = time.monotonic()
                    if now - last_audio_update >= 0.5 or seconds >= end - start:
                        ctx.progress("scan", start, duration, f"{label}: decoded {seconds:.1f}/{end - start:.1f}s audio; video sampling follows")
                        last_audio_update = now

                audio = scan_audio(binary, project["source_path"], start, end, cfg.cpu_workers, ctx, audio_progress, stream_index)
                cache.store("audio", index, start, end, audio, ctx)
            else:
                audio_cached = True
        else:
            audio = {"rows": [], "decoded_seconds": 0.0}
        decoded_seconds += audio["decoded_seconds"]
        audio_detail = "audio cache hit" if audio_cached else ("audio scanned" if has_audio else "no audio stream")
        if has_video:
            video = cache.load("video", index, start, end)
            video_cached = False
            if video is None or not valid_video_cache(video, start, end, cfg.sampling_interval, previous_frame):
                ctx.progress("scan", start, duration, f"{label}: {audio_detail}; seeking sparse frames")

                def video_progress(covered, attempts, failures):
                    ctx.progress("scan", covered, duration, f"{label}: {audio_detail}; {attempts} sparse frame seeks, {failures} unavailable")

                video = scan_video(binary, project["source_path"], start, end, cfg.sampling_interval,
                                   cfg.analysis_width, cfg.cpu_workers, ctx, video_progress, previous_frame)
                if not video["failures"]:
                    cache.store("video", index, start, end, video, ctx)
            else:
                video_cached = True
            previous_frame = video["last_frame"]
            successful_frames += video["attempts"] - video["failures"]
            failed_frames += video["failures"]
            visual_detail = "sparse frame cache hit" if video_cached else "sparse frames scanned"
        else:
            video = {"rows": []}
            visual_detail = "no video stream"
        ctx.check_cancelled()
        ranker.consume(audio["rows"], video["rows"])
        ctx.progress("scan", end, duration, f"{label} complete: {audio_detail}; {visual_detail}")

    if decoded_seconds == 0 and successful_frames == 0:
        raise RuntimeError("No audio samples or sparse video frames could be decoded; verify source integrity and FFmpeg codecs")
    ctx.check_cancelled()
    if fingerprint(project, cfg.cache_settings()) != key:
        raise RuntimeError("Source changed during analysis; retry with an unchanged recording")
    ctx.progress("rank", 0.0, duration, "Combining measured signals and suppressing overlapping candidates")
    highlights = [_highlight(candidate, project["id"], key, cfg.sampling_interval) for candidate in ranker.finish()]
    if failed_frames:
        for highlight in highlights:
            highlight["explanation"] += f" {failed_frames} sparse frame samples could not be decoded."
    ctx.progress("rank", duration, duration, f"Selected {len(highlights)} signal-based highlights (limit {cfg.max_clips})")
    if cfg.ai_refinement and highlights:
        highlights = _refine(project, highlights, settings, ctx)
    if has_video and highlights:
        _thumbnails(binary, project["source_path"], highlights, cfg, ctx)
    ctx.check_cancelled()
    store_events(ctx.cache_dir, _event_timeline(ranker, project["id"], key, cfg), ctx)
    return highlights
