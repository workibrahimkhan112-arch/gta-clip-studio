"""Optional, offline tiny.en speech recognition on short, seeked clips only.

Model installation is deliberately separate from analysis/export. An explicit
``setup_model_cache()`` call downloads the model; no other entry point does.
"""

from __future__ import annotations

import importlib
import math
import os
import subprocess
import tempfile
from pathlib import Path

from backend.core.config import ROOT, get_binary
from backend.core.process import Cancelled, run_process

from ._media import clip_bounds, seconds_arg, worker_threads
from .subtitles import transcript_cues


MODEL_DIR = ROOT / "storage" / "models" / "tiny.en"
MAX_REFINEMENT_CLIPS = 3
MAX_REFINEMENT_SECONDS = 120.0
MAX_CLIP_SECONDS = 90.0
_MODEL_HELP = (
    "Enter a transcript in the clip's captions editor or export with captions off. "
    "Run run.dev.cmd --install-only to install the local caption model, or configure Groq captions "
    "with an API key and cloud consent in Settings."
)


class TranscriptionUnavailable(RuntimeError):
    """An optional model, audio stream, or usable speech transcript is unavailable."""


def setup_model_cache(cache_dir: str | Path | None = None) -> str:
    """Explicit opt-in network download of tiny.en (never called by a job).

    The default Hugging Face cache is used by runtime calls. If a custom cache is
    supplied, set HF_HUB_CACHE to that directory before starting the backend too.
    """
    try:
        download_model = importlib.import_module("faster_whisper.utils").download_model
    except (ImportError, OSError) as exc:
        raise RuntimeError("Install the optional dependency first: python -m pip install faster-whisper") from exc
    kwargs = {"cache_dir": str(cache_dir)} if cache_dir is not None else {}
    return str(download_model("tiny.en", **kwargs))


def model_is_cached() -> bool:
    path = Path(os.environ.get("GTA_WHISPER_MODEL_PATH") or MODEL_DIR)
    return all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))


def _cached_model_path() -> str:
    if model_is_cached():
        return str(Path(os.environ.get("GTA_WHISPER_MODEL_PATH") or MODEL_DIR).resolve())
    download_model = importlib.import_module("faster_whisper.utils").download_model

    path = Path(download_model("tiny.en", local_files_only=True))
    # Some faster-whisper versions fetch a tokenizer separately if it is absent,
    # even with local_files_only on the model. Reject partial snapshots first.
    required = ("model.bin", "config.json", "tokenizer.json")
    if not all((path / filename).is_file() for filename in required):
        raise FileNotFoundError("The cached tiny.en snapshot is incomplete (model/config/tokenizer required).")
    return str(path)


def _load_model(settings: dict):
    try:
        WhisperModel = importlib.import_module("faster_whisper").WhisperModel
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise TranscriptionUnavailable(f"Local speech recognition is not installed or cannot load. {_MODEL_HELP}") from exc
    try:
        return WhisperModel(
            _cached_model_path(),
            device="cpu",
            compute_type="int8",
            cpu_threads=worker_threads(settings),
            num_workers=1,
            local_files_only=True,
        )
    except (OSError, RuntimeError, ValueError, ImportError, TypeError) as exc:
        if any(token in str(exc).lower() for token in ("allocate memory", "out of memory", "paging file", "mkl_malloc")):
            raise TranscriptionUnavailable("Not enough available RAM/page-file space for local captions. Close other memory-heavy applications, or choose Groq captions with consent, or No captions. Existing highlights are preserved.") from exc
        raise TranscriptionUnavailable(f"The local tiny.en model could not be loaded from cache. {_MODEL_HELP}") from exc


def _explicitly_silent(project: dict) -> bool:
    metadata = project.get("metadata") or {}
    streams = metadata.get("audio_streams")
    if isinstance(streams, list):
        return not streams
    return "audio_codec" in metadata and not metadata["audio_codec"]


def _finite_interval(start, end, duration: float) -> tuple[float, float] | None:
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(start) or not math.isfinite(end):
        return None
    start, end = max(0.0, start), min(duration, end)
    return (start, end) if end > start else None


def transcribe_clip(
    project: dict,
    highlight: dict,
    settings: dict,
    ctx,
    *,
    model=None,
    progress_offset: float = 0.0,
    progress_total: float | None = None,
) -> list[dict]:
    """Return actual clip-relative segments/words; never synthesize a fallback."""
    ctx.check_cancelled()
    start, duration = clip_bounds(project, highlight)
    if duration > MAX_CLIP_SECONDS + 0.001:
        raise TranscriptionUnavailable(
            f"Local transcription is limited to {MAX_CLIP_SECONDS:g}-second clips. {_MODEL_HELP}"
        )
    if _explicitly_silent(project):
        raise TranscriptionUnavailable(f"This source has no audio stream to transcribe. {_MODEL_HELP}")
    source = Path(project["source_path"]).expanduser().resolve()
    if not source.is_file():
        raise TranscriptionUnavailable(f"The source video is missing: {source}. Relink/import the video first.")
    if settings.get("caption_provider", "local") == "groq":
        from .captioning import transcribe_groq
        return transcribe_groq(project, highlight, settings, ctx, progress_offset=progress_offset, progress_total=progress_total)
    if model is None:
        model = _load_model(settings)
    ctx.check_cancelled()
    total = progress_total if progress_total is not None else duration
    ctx.progress("transcription", progress_offset, total, "Extracting selected clip audio locally")
    try:
        binary = get_binary("ffmpeg")
    except (OSError, RuntimeError) as exc:
        raise TranscriptionUnavailable(f"FFmpeg is required to extract clip audio: {exc}. {_MODEL_HELP}") from exc
    work_dir = Path(ctx.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="speech-", dir=work_dir) as temporary:
        audio_path = Path(temporary) / "clip.wav"
        command = [
            binary, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-filter_threads", "1", "-filter_complex_threads", "1",
            "-ss", seconds_arg(start), "-t", seconds_arg(duration),
            "-threads", str(worker_threads(settings)), "-i", str(source),
            "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-t", seconds_arg(duration), "-af", "aresample=16000:async=1:first_pts=0", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", "-threads:a", "1", str(audio_path),
        ]
        try:
            result = run_process(command, ctx=ctx, timeout=max(60, duration * 3))
            if result.returncode:
                error = (result.stderr or "")[-3000:]
                raise TranscriptionUnavailable(f"Could not extract this clip's audio: {error}. {_MODEL_HELP}")
            ctx.check_cancelled()
            segments, _info = model.transcribe(
                str(audio_path),
                language="en",
                beam_size=1,
                best_of=1,
                temperature=0.0,
                word_timestamps=True,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            transcript = []
            processed = 0.0
            for segment in segments:
                ctx.check_cancelled()
                timing = _finite_interval(segment.start, segment.end, duration)
                text = str(segment.text).strip() if segment.text is not None else ""
                if timing is None or not text:
                    continue
                words = []
                for word in segment.words or []:
                    word_timing = _finite_interval(word.start, word.end, duration)
                    word_text = str(word.word).strip() if word.word is not None else ""
                    if word_timing is not None and word_text:
                        word_start = max(timing[0], word_timing[0])
                        word_end = min(timing[1], word_timing[1])
                        if word_end > word_start:
                            words.append({"start": word_start, "end": word_end, "word": word_text})
                transcript.append({"start": timing[0], "end": timing[1], "text": text, "words": words})
                processed = max(processed, timing[1])
                ctx.progress("transcription", progress_offset + processed, total, "Transcribing tiny.en on CPU (INT8)")
            ctx.check_cancelled()
        except Cancelled:
            raise
        except TranscriptionUnavailable:
            raise
        except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError) as exc:
            raise TranscriptionUnavailable(f"Local speech recognition failed: {exc}. {_MODEL_HELP}") from exc
    if not transcript:
        raise TranscriptionUnavailable(f"No speech transcript was produced for this clip. {_MODEL_HELP}")
    ctx.progress("transcription", progress_offset + duration, total, "Local transcript ready")
    return transcript


def refine(project: dict, highlights: list[dict], settings: dict, ctx) -> list[dict]:
    """Enrich at most three candidates/120 seconds, preserving order and scores.

    Unavailable optional recognition is nonfatal to heuristic analysis. Real
    cancellation still propagates. Existing user/model transcripts are retained.
    """
    if not settings.get("ai_refinement") or not highlights:
        return highlights
    candidates = []
    for index, highlight in enumerate(highlights):
        ctx.check_cancelled()
        if not highlight.get("selected", True):
            continue
        try:
            _start, duration = clip_bounds(project, highlight)
        except ValueError:
            continue
        if duration > MAX_CLIP_SECONDS or transcript_cues(highlight.get("transcript", []), duration):
            continue
        try:
            score = float(highlight.get("score", 0))
        except (TypeError, ValueError, OverflowError):
            score = 0.0
        candidates.append((index, duration, score if math.isfinite(score) else 0.0))
    shortlist = []
    total = 0.0
    for candidate in sorted(candidates, key=lambda item: (-item[2], item[0])):
        if len(shortlist) >= MAX_REFINEMENT_CLIPS:
            break
        if total + candidate[1] <= MAX_REFINEMENT_SECONDS:
            shortlist.append(candidate)
            total += candidate[1]
    if not shortlist:
        return highlights
    ctx.progress("transcription", 0.0, total, "Checking for locally cached tiny.en; no downloads")
    try:
        ctx.check_cancelled()
        model = _load_model(settings)
    except Cancelled:
        raise
    except TranscriptionUnavailable as exc:
        ctx.progress("transcription", 0.0, total, f"Optional transcription skipped: {exc}")
        return highlights
    refined = list(highlights)
    processed = 0.0
    for index, duration, _score in shortlist:
        ctx.check_cancelled()
        try:
            transcript = transcribe_clip(
                project, highlights[index], settings, ctx,
                model=model, progress_offset=processed, progress_total=total,
            )
        except Cancelled:
            raise
        except (TranscriptionUnavailable, OSError) as exc:
            ctx.progress("transcription", processed, total, f"Optional transcription skipped: {exc}")
            continue
        refined[index] = {**highlights[index], "transcript": transcript}
        processed += duration
    return refined
