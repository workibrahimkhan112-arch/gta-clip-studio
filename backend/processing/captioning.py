"""Automatic per-highlight speech captions, cache and optional Groq ASR.

No complete-recording transcription or invented word timings. Each successful
clip is committed by the worker callback before the next clip begins.
"""
from __future__ import annotations

import hashlib
import json

import tempfile
import uuid
import wave
from pathlib import Path

from backend.core.config import get_binary
from backend.core.process import Cancelled, run_process
from backend.models.schemas import Segment
from ._media import clip_bounds, seconds_arg, worker_threads
from .transcription import TranscriptionUnavailable, _load_model, transcribe_clip

CACHE_VERSION = 1
MAX_CACHE_BYTES = 512 * 1024


def _validated(transcript, duration):
    if not isinstance(transcript, list) or not 0 < len(transcript) <= 200:
        raise TranscriptionUnavailable("No usable speech transcript was produced. Choose No captions for silent clips, or retry with clearer audio.")
    output = []
    previous = -1.0
    for raw in transcript:
        segment = Segment.model_validate(raw).model_dump()
        if segment["start"] < previous or segment["end"] > duration + .01:
            raise TranscriptionUnavailable("Speech recognition returned invalid caption timing; retry this clip")
        if not segment["text"].strip():
            raise TranscriptionUnavailable("Speech recognition returned empty caption text")
        previous = segment["start"]
        output.append(segment)
    return output


def _cache_path(project, highlight, settings, ctx):
    source = Path(project["source_path"]).resolve()
    stat = source.stat()
    identity = {"version": CACHE_VERSION, "source": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                "start": highlight["start"], "end": highlight["end"], "provider": settings.get("caption_provider", "local"),
                "model": "tiny.en-int8" if settings.get("caption_provider", "local") == "local" else "whisper-large-v3-turbo"}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return Path(ctx.cache_dir) / "captions" / f"{digest}.json"


def caption_highlight(project, highlight, settings, ctx, *, force=False, model=None, offset=0.0, total=None):
    ctx.check_cancelled()
    if highlight.get("transcript") and not force:
        return {**highlight, "caption_status": "ready", "caption_error": None}
    _, duration = clip_bounds(project, highlight)
    path = _cache_path(project, highlight, settings, ctx)
    transcript = None
    if not force and path.is_file():
        try:
            if path.stat().st_size <= MAX_CACHE_BYTES:
                transcript = _validated(json.loads(path.read_text(encoding="utf-8")), duration)
        except (OSError, ValueError, TypeError, TranscriptionUnavailable):
            transcript = None
    if transcript is not None and settings.get("caption_provider", "local") == "groq":
        from backend.core import usage
        usage.record_cache_hit("groq", "whisper-large-v3-turbo", "captions", ctx)
    if transcript is None:
        transcript = _validated(transcribe_clip(project, highlight, settings, ctx, model=model,
            progress_offset=offset, progress_total=total), duration)
        if _cache_path(project, highlight, settings, ctx) != path:
            raise TranscriptionUnavailable("Source changed during caption generation; retry using a finalized recording")
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(transcript, ensure_ascii=False, allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_CACHE_BYTES:
            raise TranscriptionUnavailable("Caption transcript exceeded its bounded cache limit")
        temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(raw, encoding="utf-8")
            ctx.check_cancelled()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {**highlight, "transcript": transcript, "caption_status": "ready", "caption_error": None}


def generate_captions(project, highlights, settings, ctx, *, force=False, on_result=None):
    output = []
    total = sum(clip_bounds(project, item)[1] for item in highlights)
    completed = 0.0
    model = None
    model_error = None
    cloud_error = None
    if settings.get("caption_provider", "local") == "local" and any(force or not h.get("transcript") for h in highlights):
        try:
            ctx.progress("transcription", 0, total, "Loading local tiny.en once for shortlisted clips")
            model = _load_model(settings)
        except TranscriptionUnavailable as exc:
            model_error = str(exc)
    for item in highlights:
        ctx.check_cancelled()
        try:
            if cloud_error and (force or not item.get("transcript")):
                raise TranscriptionUnavailable(cloud_error)
            # Existing manual text is still usable even if model setup is missing.
            if model_error and (force or not item.get("transcript")):
                # A prior valid cached transcript can be used without a model.
                path = _cache_path(project, item, settings, ctx)
                if force or not path.is_file():
                    raise TranscriptionUnavailable(model_error)
            updated = caption_highlight(project, item, settings, ctx, force=force, model=model, offset=completed, total=total)
            completed += clip_bounds(project, item)[1]
        except Cancelled:
            raise
        except (TranscriptionUnavailable, ValueError, OSError, RuntimeError) as exc:
            detail = str(exc)[:1500]
            if getattr(exc, "status_code", None) in {401, 403, 429}:
                cloud_error = "Further caption uploads stopped after a cloud authentication/quota failure. Check the key/quota and retry."
            updated = {**item, "caption_status": "no_speech" if "no speech" in detail.lower() or "no usable speech" in detail.lower() else "failed", "caption_error": detail}
        output.append(updated)
        if on_result:
            on_result(updated)
        ctx.progress("transcription", completed, total, f"Resolved {len(output)}/{len(highlights)} caption requests; {sum(h.get('caption_status') == 'ready' for h in output)} ready")
    return output


def transcribe_groq(project, highlight, settings, ctx, *, progress_offset=0.0, progress_total=None):
    if not settings.get("cloud_consent"):
        raise TranscriptionUnavailable("Groq captions upload this clip's audio. Enable explicit cloud consent in Settings first, or choose local captions.")
    from .cloud import post_json
    ctx.check_cancelled()
    start, duration = clip_bounds(project, highlight)
    total = progress_total if progress_total is not None else duration
    ctx.progress("transcription", progress_offset, total, "Extracting only this clip's audio for Groq captions")
    work = Path(ctx.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="groq-caption-", dir=work) as temporary:
        audio = Path(temporary) / "clip.wav"
        result = run_process([get_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", seconds_arg(start), "-t", seconds_arg(duration), "-threads", str(worker_threads(settings)),
            "-i", str(project["source_path"]), "-vn", "-sn", "-dn", "-map", "0:a:0", "-t", seconds_arg(duration),
            "-af", "aresample=16000:async=1:first_pts=0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)], ctx=ctx, timeout=max(60, duration * 3))
        if result.returncode:
            raise TranscriptionUnavailable("Could not extract the selected clip's audio for captions; verify the source and audio stream")
        if not audio.is_file() or audio.stat().st_size > 3_000_000:
            raise TranscriptionUnavailable("Cloud caption audio exceeds 3 MB; choose a shorter clip or local captions")
        try:
            with wave.open(str(audio), "rb") as decoded:
                audio_seconds = decoded.getnframes() / decoded.getframerate()
        except (wave.Error, EOFError, ZeroDivisionError):
            raise TranscriptionUnavailable("The extracted caption audio is invalid; no cloud upload was made") from None
        if not 0 < audio_seconds <= duration + .1:
            raise TranscriptionUnavailable("The extracted audio has no usable samples or exceeds the clip; no upload was made")
        with audio.open("rb") as stream:
            response = post_json("groq", "https://api.groq.com/openai/v1/audio/transcriptions", files={"file": ("clip.wav", stream, "audio/wav")},
                data={"model": "whisper-large-v3-turbo", "response_format": "verbose_json", "timestamp_granularities[]": "word", "temperature": 0}, ctx=ctx,
                                settings=settings, operation="captions", audio_seconds=audio_seconds)
    ctx.check_cancelled()
    words = response.get("words")
    if not isinstance(words, list) or not words:
        if str(response.get("text") or "").strip():
            raise TranscriptionUnavailable("Groq returned speech without usable word timings. Retry captions; timings will not be invented.")
        raise TranscriptionUnavailable("No speech with word timestamps was returned by Groq. Choose No captions for silent footage or retry.")
    if len(words) > 3000:
        raise TranscriptionUnavailable("Groq returned too many words for a short clip")
    grouped = []
    current = []
    last = -1.0
    for raw in words:
        if not isinstance(raw, dict):
            raise TranscriptionUnavailable("Groq returned invalid word timings")
        try:
            start_word, end_word = float(raw["start"]), float(raw["end"])
            word = str(raw["word"]).strip()
        except (KeyError, ValueError, TypeError):
            raise TranscriptionUnavailable("Groq returned invalid word timings") from None
        if not word or not 0 <= start_word < end_word <= min(duration, audio_seconds) + .01 or start_word < last:
            raise TranscriptionUnavailable("Groq returned invalid clip-relative word timings")
        if current and (len(current) >= 8 or start_word - current[-1]["end"] > .8):
            grouped.append(current)
            current = []
        current.append({"start": start_word, "end": end_word, "word": word})
        last = start_word
    if current:
        grouped.append(current)
    transcript = [{"start": group[0]["start"], "end": max(w["end"] for w in group), "text": " ".join(w["word"] for w in group), "words": group} for group in grouped]
    ctx.progress("transcription", progress_offset + duration, total, "Actual cloud word timings received")
    return _validated(transcript, duration)
