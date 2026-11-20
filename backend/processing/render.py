"""CPU-bounded portrait exports with transactional files and real captions."""

from __future__ import annotations

import re
import tempfile
import unicodedata
import uuid
from pathlib import Path

from backend.core.config import EXPORTS_DIR, ROOT, get_binary
from backend.core.process import run_process

from ._media import clip_bounds, run_ffmpeg, seconds_arg, worker_threads
from .layout import build_split_graph, validate_layout
from .subtitle_style import normalize_subtitle_style
from .subtitles import build_ass, escape_filter_filename
from .transcription import transcribe_clip


_RESOLUTIONS = {"720x1280": (720, 1280), "1080x1920": (1080, 1920)}


def _resolution(settings: dict) -> tuple[int, int]:
    try:
        return _RESOLUTIONS[settings.get("output_resolution", "720x1280")]
    except (KeyError, TypeError) as exc:
        raise ValueError("Output resolution must be 720x1280 or 1080x1920.") from exc


def _source_has_audio(project: dict, source: Path, ctx) -> bool:
    metadata = project.get("metadata") or {}
    if isinstance(metadata.get("audio_streams"), list):
        return bool(metadata["audio_streams"])
    if "audio_codec" in metadata:
        return bool(metadata["audio_codec"])
    # Normally import metadata answers this. Probe rather than silently discard
    # audio or fail an otherwise valid silent source if old metadata is sparse.
    result = run_process([
        get_binary("ffprobe"), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(source),
    ], ctx=ctx, timeout=30)
    if result.returncode:
        raise RuntimeError(f"Could not inspect source audio: {(result.stderr or '')[-2000:]}")
    return bool(result.stdout.strip())


def build_render_command(
    binary: str,
    source: Path,
    output: Path,
    start: float,
    duration: float,
    settings: dict,
    *,
    subtitle_path: Path | None = None,
    fonts_dir: Path | None = None,
    has_audio: bool = True,
    video_stream_index: int | None = None,
    layout: dict | None = None,
) -> list[str]:
    """Build an argv list; source paths and titles never enter a shell/filter."""
    width, height = _resolution(settings)
    layout = validate_layout(layout)
    small_width, small_height = width // 4, height // 4
    threads = str(worker_threads(settings))
    if video_stream_index is not None and (type(video_stream_index) is not int or video_stream_index < 0):
        raise ValueError("The indexed video stream must be a nonnegative integer.")
    video_input = f"0:{video_stream_index}" if video_stream_index is not None else "0:V:0"
    if layout["mode"] == "split":
        graph = build_split_graph(video_input, width, height, layout)
    else:
        graph = (
            # Input seeking already gives every stream the same clip-relative origin.
            # Pad/drop at that origin instead of resetting each stream independently.
            f"[{video_input}]fps=30:start_time=0,split=2[background_in][gameplay_in];"
            f"[background_in]scale=w='max({small_width},ceil({small_height}*dar/2)*2)':"
            f"h='max({small_height},ceil({small_width}/dar/2)*2)':flags=fast_bilinear,"
            f"setsar=1,crop={small_width}:{small_height},boxblur=10:2,"
            f"scale={width}:{height}:flags=bilinear[background];"
            # Fitting with the display aspect ratio keeps the *entire* gameplay
            # image centered, including non-square-pixel sources; only the BG crops.
            f"[gameplay_in]scale=w='max(2,trunc(min({width},{height}*dar)/2)*2)':"
            f"h='max(2,trunc(min({height},{width}/dar)/2)*2)':flags=bicubic,"
            "setsar=1[gameplay];"
            "[background][gameplay]overlay=x=(W-w)/2:y=(H-h)/2:shortest=1,"
            "setsar=1,format=yuv420p"
        )
    if subtitle_path is not None:
        graph += f",ass=filename={escape_filter_filename(subtitle_path)}"
        if fonts_dir is not None:
            graph += f":fontsdir={escape_filter_filename(fonts_dir)}"
    graph += "[video]"
    command = [
        str(binary), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-filter_threads", "1", "-filter_complex_threads", "1",
        # Both seek and duration are input options: never decode the full source.
        "-ss", seconds_arg(start), "-t", seconds_arg(duration),
        "-threads", threads, "-i", str(source),
    ]
    if not has_audio:
        command += ["-threads", "1", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    command += [
        "-filter_complex", graph, "-map", "[video]", "-map", "0:a:0" if has_audio else "1:a:0",
        "-map_metadata", "-1", "-map_chapters", "-1", "-sn", "-dn",
        "-t", seconds_arg(duration), "-r", "30", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-threads:v", threads,
        "-x264-params", f"threads={threads}:lookahead_threads=1:sync_lookahead=0",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-threads:a", "1",
        "-af", "aresample=async=1:first_pts=0",
        "-movflags", "+faststart", "-max_muxing_queue_size", "256",
        "-progress", "pipe:1", "-nostats", "-f", "mp4", str(output),
    ]
    return command


def _filename(highlight: dict, job_id: str | None = None) -> str:
    title = unicodedata.normalize("NFKD", str(highlight.get("title") or "clip"))
    title = title.encode("ascii", errors="ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()[:48].rstrip("-") or "clip"
    return f"gta-{slug}-{job_id or uuid.uuid4().hex}.mp4"


def render_clip(project: dict, highlight: dict, settings: dict, ctx) -> dict:
    ctx.check_cancelled()
    start, duration = clip_bounds(project, highlight)
    resolution = _resolution(settings)
    layout = validate_layout(project.get("layout"))
    subtitle_style = normalize_subtitle_style(highlight.get("subtitle_style"))
    caption_style = settings.get("caption_style", "standard")
    if caption_style not in {"none", "standard", "animated"}:
        raise ValueError("Caption style must be none, standard or animated.")
    source = Path(project["source_path"]).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"The source video is missing: {source}. Relink/import it before exporting.")
    binary = get_binary("ffmpeg")
    work_dir = Path(ctx.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir = Path(settings.get("output_folder") or EXPORTS_DIR).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    job_id = str(getattr(ctx, "id", None) or uuid.uuid4().hex)
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", job_id):
        raise ValueError("The export job ID must be safe to use in a filename.")
    filename = _filename(highlight, job_id)
    final_path = output_dir / filename
    # Exact job ownership permits startup cleanup without touching another job's
    # partial export. The final suffix also identifies a post-publication crash.
    temporary_output = output_dir / f".gta-{job_id}.part.mp4"
    result = {"output_path": str(final_path), "filename": filename}
    try:
        with tempfile.TemporaryDirectory(prefix="render-", dir=work_dir) as temporary:
            subtitle_path = None
            if caption_style != "none":
                transcript = highlight.get("transcript") or []
                if not transcript:
                    transcript = transcribe_clip(project, highlight, settings, ctx)
                content = build_ass(
                    transcript, duration, resolution, caption_style,
                    position=layout["caption_position"], subtitle_style=subtitle_style,
                )
                subtitle_path = Path(temporary) / "captions.ass"
                subtitle_path.write_text(content, encoding="utf-8")
            ctx.check_cancelled()
            has_audio = _source_has_audio(project, source, ctx)
            video_streams = (project.get("metadata") or {}).get("video_streams") or []
            video_stream_index = video_streams[0]["index"] if video_streams else None
            # Truncate only this job's stale partial, on the destination volume.
            with temporary_output.open("wb"):
                pass
            command = build_render_command(
                binary, source, temporary_output, start, duration, settings,
                subtitle_path=subtitle_path,
                fonts_dir=ROOT / "frontend" / "public" / "fonts" if subtitle_style and subtitle_style["font_family"] in {"Inter", "Montserrat"} else None,
                has_audio=has_audio, video_stream_index=video_stream_index,
                layout=layout,
            )
            run_ffmpeg(command, duration, ctx)
            ctx.check_cancelled()
            if not temporary_output.is_file() or temporary_output.stat().st_size == 0:
                raise RuntimeError("FFmpeg finished without producing a nonempty MP4 file.")
        # Scratch cleanup and all cancellation-aware callbacks must finish before
        # publication. Once renamed, completion wins over a late cancellation.
        ctx.progress("rendering", duration, duration, "Publishing vertical clip")
        ctx.check_cancelled()
        temporary_output.replace(final_path)
        return result
    except BaseException:
        temporary_output.unlink(missing_ok=True)
        raise
