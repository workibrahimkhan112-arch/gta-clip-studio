"""Bounded FFmpeg readers using the coordinator's cancellation/process owner.

Audio alone is decoded continuously, one seeked chunk at a time. Video uses
one input seek and one decoded frame per configured sample, never an fps filter
on a full recording. Long-GOP sources still require decoding from the preceding
keyframe for each seek; seek cost and sparse sampling can miss very brief action.
"""

from __future__ import annotations

import math
import queue
import subprocess
import threading
import time

import numpy as np

from .features import (
    SAMPLE_RATE, WINDOW_SECONDS, audio_features, decode_frame_state,
    encode_frame_state, frame_descriptor, frame_difference,
)


def output_blocks(command, ctx, block_bytes, max_bytes, timeout):
    """Read a binary pipe with a bounded queue and cancellable waiting.

    A pipe read can block even when the child is alive but stalled. A daemon
    reader and timed queue waits let the owning thread check cancellation; the
    shared managed_process kills/reaps the child on every exit path. Stderr is
    spooled by that helper rather than piped here, preventing stderr deadlocks.
    """
    from ..core.process import managed_process

    messages = queue.Queue(maxsize=2)
    stop = threading.Event()
    reader = None
    started = time.monotonic()
    try:
        with managed_process(command, ctx=ctx) as process:
            stdout = process.stdout
            if stdout is None:
                raise RuntimeError("FFmpeg output pipe was not created")

            def send(message):
                while not stop.is_set():
                    try:
                        messages.put(message, timeout=0.1)
                        return
                    except queue.Full:
                        pass

            def read():
                try:
                    while not stop.is_set():
                        data = stdout.read(block_bytes)
                        if not data:
                            break
                        send(("data", data))
                    send(("end", None))
                except (OSError, ValueError) as error:
                    send(("error", error))

            reader = threading.Thread(target=read, name="detection-pcm-reader", daemon=True)
            reader.start()
            size = 0
            try:
                while True:
                    ctx.check_cancelled()
                    if time.monotonic() - started > timeout:
                        raise TimeoutError(f"FFmpeg scan stalled or exceeded {timeout:g}s")
                    try:
                        kind, value = messages.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if kind == "error":
                        raise value
                    if kind == "end":
                        break
                    size += len(value)
                    if size > max_bytes:
                        raise RuntimeError("FFmpeg output exceeded the bounded scan size")
                    yield value
                ctx.check_cancelled()
                # EOF on stdout need not imply exit; keep cancellation live.
                deadline = time.monotonic() + 5
                while process.poll() is None:
                    ctx.check_cancelled()
                    if time.monotonic() > deadline:
                        raise subprocess.TimeoutExpired(command, 5)
                    time.sleep(0.02)
                if process.returncode:
                    raise RuntimeError(f"FFmpeg exited with status {process.returncode} while scanning; check source integrity and installed codecs")
            finally:
                stop.set()
    finally:
        stop.set()
        if reader is not None:
            reader.join(timeout=1)


def _input_command(binary, source, seek, workers):
    return [str(binary), "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", str(workers),
            "-filter_threads", str(workers), "-ss", f"{seek:.9f}", "-i", str(source)]


def scan_audio(binary, source, start, end, workers, ctx, on_progress, stream_index=None):
    """Decode at most one chunk of 16k mono float PCM, window by window.

    Timestamp-aware resampling fills actual leading/internal audio gaps with
    zero evidence; without it a delayed track's peaks would shift earlier in
    the video. There is no tail padding. Mono downmix can attenuate opposing
    stereo phases and this inexpensive detector deliberately accepts that loss.
    """
    seconds = end - start
    mapping = "0:a:0" if stream_index is None else f"0:{int(stream_index)}"
    command = _input_command(binary, source, start, workers) + [
        "-t", f"{seconds:.9f}", "-map", mapping, "-vn", "-sn", "-dn",
        "-af", f"aresample={SAMPLE_RATE}:async=1:first_pts=0",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_f32le",
        "-threads", str(workers), "-f", "f32le", "pipe:1",
    ]
    window_samples = int(SAMPLE_RATE * WINDOW_SECONDS)
    limit_samples = max(1, round(seconds * SAMPLE_RATE))
    pending = bytearray()
    rows = []
    previous = None
    consumed = 0

    def consume(data):
        nonlocal previous, consumed
        samples = np.frombuffer(data, dtype="<f4")
        samples = samples[: max(0, limit_samples - consumed)]
        if not samples.size:
            return
        features, previous = audio_features(samples, previous)
        window_duration = min(samples.size / SAMPLE_RATE, end - start - consumed / SAMPLE_RATE)
        if window_duration <= 0:
            return
        rows.append({"time": start + consumed / SAMPLE_RATE + window_duration / 2,
                     "duration": window_duration, **features})
        consumed += samples.size
        on_progress(min(seconds, consumed / SAMPLE_RATE))

    stream = output_blocks(command, ctx, window_samples * 4, (limit_samples + SAMPLE_RATE) * 4,
                           max(60.0, seconds * 2.0))
    try:
        for block in stream:
            pending.extend(block)
            while len(pending) >= window_samples * 4:
                consume(bytes(pending[: window_samples * 4]))
                del pending[: window_samples * 4]
        if len(pending) % 4:
            raise RuntimeError("FFmpeg returned a partial float audio sample")
        if pending:
            consume(bytes(pending))
    finally:
        stream.close()
    return {"rows": rows, "decoded_seconds": min(seconds, consumed / SAMPLE_RATE)}


def sample_times(start, end, interval):
    index = math.ceil(start / interval - 1e-9)
    found = False
    while index * interval < end - 1e-9:
        timestamp = max(start, index * interval)
        found = True
        yield timestamp
        index += 1
    if not found and end > start:
        yield start + (end - start) / 2


def read_frame(binary, source, timestamp, width, workers, ctx):
    command = _input_command(binary, source, timestamp, workers) + [
        "-map", "0:V:0", "-an", "-sn", "-dn", "-frames:v", "1",
        "-vf", f"scale={width}:{width}:force_original_aspect_ratio=decrease,pad={width}:{width}:(ow-iw)/2:(oh-ih)/2,format=gray",
        "-threads", str(workers), "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1",
    ]
    data = bytearray()
    stream = output_blocks(command, ctx, width * width, width * width, 45)
    try:
        for block in stream:
            data.extend(block)
    finally:
        stream.close()
    if not data:
        return None
    if len(data) != width * width:
        raise RuntimeError("FFmpeg returned an incomplete grayscale frame")
    return np.frombuffer(data, dtype=np.uint8).reshape(width, width)


def scan_video(binary, source, start, end, interval, width, workers, ctx, on_progress, previous_state=None):
    from ..core.process import Cancelled

    rows = []
    failures = 0
    attempts = 0
    last = decode_frame_state(previous_state) if previous_state else None
    state = previous_state
    for timestamp in sample_times(start, end, interval):
        ctx.check_cancelled()
        attempts += 1
        try:
            frame = read_frame(binary, source, timestamp, width, workers, ctx)
        except Cancelled:
            raise
        except (RuntimeError, OSError, subprocess.SubprocessError):
            frame = None
        if frame is None:
            failures += 1
        else:
            descriptor = frame_descriptor(frame)
            features = {"motion": 0.0, "scene": 0.0}
            event_time = timestamp
            if last is not None and 0 < timestamp - last[0] <= interval * 1.5:
                features = frame_difference(last[1], descriptor)
                # Difference occurred somewhere between the two seeks, not
                # necessarily at the later frame; use the interval midpoint.
                event_time = (last[0] + timestamp) / 2
            rows.append({"time": event_time, **features})
            last = (timestamp, descriptor)
            state = encode_frame_state(timestamp, descriptor)
        on_progress(min(end, timestamp + interval), attempts, failures)
    return {"rows": rows, "last_frame": state, "attempts": attempts, "failures": failures,
            "previous_frame": previous_state}


def valid_audio_cache(payload, start, end):
    try:
        rows = payload["rows"]
        decoded = float(payload["decoded_seconds"])
        if not isinstance(rows, list) or len(rows) > math.ceil((end - start) / WINDOW_SECONDS) + 1:
            return False
        if not math.isfinite(decoded) or not 0 <= decoded <= end - start + 1e-6:
            return False
        covered = 0.0
        for row in rows:
            timestamp = float(row["time"])
            duration = float(row["duration"])
            if not 0 < duration <= WINDOW_SECONDS + 1e-6 or not math.isfinite(timestamp):
                return False
            if abs(timestamp - (start + covered + duration / 2)) > 1e-5 or timestamp > end:
                return False
            if not all(math.isfinite(float(row[key])) and 0 <= row[key] <= 1 for key in ("rms", "peak", "flux")):
                return False
            covered += duration
        return abs(covered - decoded) <= 1e-5
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def valid_video_cache(payload, start, end, interval, previous_state=None):
    try:
        rows = payload["rows"]
        attempts = payload["attempts"]
        if not isinstance(rows, list) or not isinstance(attempts, int):
            return False
        if payload["failures"] != 0 or attempts != len(rows) or attempts != sum(1 for _ in sample_times(start, end, interval)):
            return False
        previous = start - interval
        for row in rows:
            timestamp = float(row["time"])
            if not previous <= timestamp < end:
                return False
            if not all(math.isfinite(float(row[key])) and 0 <= row[key] <= 1 for key in ("motion", "scene")):
                return False
            previous = timestamp
        state = payload["last_frame"]
        if attempts and state is None:
            return False
        if state is not None:
            timestamp, _ = decode_frame_state(state)
            if not start <= timestamp < end:
                return False
        # A cached first difference depends on the preceding chunk's final
        # descriptor. Invalidating that predecessor must invalidate this chunk.
        return payload.get("previous_frame") == previous_state
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
