"""Small FFmpeg helpers shared by the export and transcription paths."""

from __future__ import annotations

import math
import queue
import threading
import time
from typing import BinaryIO

from backend.core.process import managed_process


def worker_threads(settings: dict) -> int:
    try:
        return max(1, min(2, int(settings.get("cpu_workers", 2))))
    except (TypeError, ValueError, OverflowError):
        return 2


def clip_bounds(project: dict, highlight: dict) -> tuple[float, float]:
    try:
        start = float(highlight["start"])
        end = float(highlight["end"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("The clip must have numeric start and end times.") from exc
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
        raise ValueError("The clip must have finite times with 0 <= start < end.")
    try:
        source_duration = float((project.get("metadata") or {}).get("duration", 0))
    except (TypeError, ValueError, OverflowError):
        source_duration = 0
    if math.isfinite(source_duration) and source_duration > 0:
        end = min(end, source_duration)
        if end <= start:
            raise ValueError("The selected clip starts beyond the end of the source video.")
    return start, end - start


def seconds_arg(seconds: float) -> str:
    return f"{seconds:.6f}"


def _stderr_tail(process, limit: int = 6000) -> str:
    stream = process.stderr
    if stream is None:
        return "No FFmpeg error output was available."
    try:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - limit))
        value = stream.read(limit)
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        return value.strip() or "No FFmpeg error output was available."
    except (OSError, ValueError, AttributeError):
        return "FFmpeg error output could not be read."


def _progress_seconds(key: str, value: str) -> float | None:
    try:
        if key in {"out_time_us", "out_time_ms"}:
            # FFmpeg's historical out_time_ms field is also in microseconds.
            result = float(value) / 1_000_000
        elif key == "out_time":
            hours, minutes, seconds = value.split(":")
            result = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        else:
            return None
        return result if math.isfinite(result) else None
    except (ValueError, OverflowError):
        return None


def run_ffmpeg(command: list[str], duration: float, ctx, stage: str = "rendering") -> None:
    """Read bounded progress without blocking cancellation on a Windows pipe.

    The shared process manager owns process-group termination and stderr spooling.
    A pipe reader only transports progress; the caller thread polls cancellation
    even when FFmpeg emits nothing (or closes stdout before finishing the mux).
    """
    messages: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
    stopped = threading.Event()
    reader: threading.Thread | None = None

    def send(value: bytes | None) -> None:
        while not stopped.is_set():
            try:
                messages.put(value, timeout=0.1)
                return
            except queue.Full:
                continue

    def read_progress(stream: BinaryIO) -> None:
        try:
            while not stopped.is_set():
                line = stream.readline(4096)
                if not line:
                    break
                send(line)
        except (OSError, ValueError):
            pass
        finally:
            send(None)

    ctx.check_cancelled()
    ctx.progress(stage, 0.0, duration)
    processed = 0.0
    try:
        with managed_process(command, ctx=ctx) as process:
            if process.stdout is None:
                raise RuntimeError("FFmpeg progress pipe was not created.")
            reader = threading.Thread(
                target=read_progress,
                args=(process.stdout,),
                name="ffmpeg-progress",
                daemon=True,
            )
            reader.start()
            try:
                while True:
                    ctx.check_cancelled()
                    try:
                        message = messages.get(timeout=0.1)
                    except queue.Empty:
                        if process.poll() is not None and not reader.is_alive():
                            break
                        continue
                    if message is None:
                        while process.poll() is None:
                            ctx.check_cancelled()
                            time.sleep(0.1)
                        break
                    key, separator, value = message.decode("utf-8", errors="replace").strip().partition("=")
                    if not separator:
                        continue
                    timestamp = _progress_seconds(key, value)
                    if timestamp is not None:
                        processed = max(processed, min(duration, max(0.0, timestamp)))
                    elif key == "progress":
                        ctx.progress(stage, processed, duration)
                returncode = process.wait(timeout=5)
                ctx.check_cancelled()
                if returncode:
                    raise RuntimeError(f"FFmpeg {stage} failed (exit {returncode}): {_stderr_tail(process)}")
            finally:
                stopped.set()
    finally:
        stopped.set()
        if reader is not None:
            reader.join(timeout=1)
