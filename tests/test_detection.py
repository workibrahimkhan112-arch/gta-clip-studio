"""Focused signal/cache/ranking tests, plus optional real-FFmpeg smoke tests.

Run: python -m unittest discover -s tests -p test_detection.py -v
Only NumPy is required. Real media tests use FFMPEG_PATH/PATH if available;
imageio_ffmpeg is an optional test-binary locator, never a runtime dependency.
Core stand-ins are scoped to tests when coordinator modules are not yet present.
"""

from __future__ import annotations

from contextlib import contextmanager
import importlib

import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
import uuid
import wave

import numpy as np

from backend.detection import cache, features, media, pipeline, ranking

try:
    from backend.core.process import Cancelled
except ImportError:
    class Cancelled(Exception):
        pass


class Context:
    def __init__(self, root):
        self.cache_dir = Path(root) / "cache"
        self.work_dir = Path(root) / "work"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.updates = []
        self.cancelled = False

    def check_cancelled(self):
        if self.cancelled:
            raise Cancelled("cancelled in detection test")

    def progress(self, stage, processed_seconds, total_seconds, detail=""):
        self.check_cancelled()
        self.updates.append((stage, processed_seconds, total_seconds, detail))


@contextmanager
def core_helpers(binary="ffmpeg", managed=None):
    """Use the real shared process owner when available, otherwise isolate it."""
    substitutes = {}
    try:
        config = importlib.import_module("backend.core.config")
    except ImportError:
        config = types.ModuleType("backend.core.config")
        config.get_binary = lambda name: binary
        substitutes[config.__name__] = config
    try:
        process = importlib.import_module("backend.core.process")
    except ImportError:
        process = types.ModuleType("backend.core.process")
        process.Cancelled = Cancelled

        @contextmanager
        def managed_process(command, ctx=None):
            with tempfile.TemporaryFile() as stderr:
                child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr)
                try:
                    yield child
                finally:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=5)
                    child.stdout.close()

        def run_process(command, ctx=None, timeout=60):
            if ctx is not None:
                ctx.check_cancelled()
            return subprocess.run(command, capture_output=True, text=True, timeout=timeout)

        process.managed_process = managed_process
        process.run_process = run_process
        substitutes[process.__name__] = process
    with patch.dict(sys.modules, substitutes), patch.object(config, "get_binary", return_value=binary):
        if managed is None:
            yield process
        else:
            with patch.object(process, "managed_process", managed):
                yield process


def audio_project(path, duration=125):
    return {"id": "ccebb907-1bb9-433b-82b5-826965a65f59", "source_path": str(path),
            "metadata": {"duration": duration, "width": 0, "height": 0, "fps": 0,
                         "video_codec": None, "audio_codec": "pcm_s16le", "audio_streams": [{"index": 0}]}}


def audio_payload(start, end):
    rows = []
    for index in range(math.ceil((end - start) / 0.5)):
        duration = min(0.5, end - start - index * 0.5)
        burst = index % 80 == 30
        rows.append({"time": start + index * 0.5 + duration / 2, "duration": duration,
                     "rms": 0.4 if burst else 0.0, "peak": 0.95 if burst else 0.0, "flux": 0.4 if burst else 0.0})
    return {"rows": rows, "decoded_seconds": end - start}


class FeatureTests(unittest.TestCase):
    def test_rms_peak_and_silence(self):
        samples = np.full(8000, 0.25, dtype=np.float32)
        result, spectrum = features.audio_features(samples)
        self.assertAlmostEqual(result["rms"], 0.25, places=6)
        self.assertAlmostEqual(result["peak"], 0.25, places=6)
        self.assertEqual(spectrum.shape, (257,))
        silence, _ = features.audio_features(np.zeros(8000, dtype=np.float32))
        self.assertEqual(silence, {"rms": 0.0, "peak": 0.0, "flux": 0.0})

    def test_spectral_change_and_short_windows(self):
        samples = np.arange(8000) / 16000
        tone = np.sin(2 * np.pi * 250 * samples).astype(np.float32)
        higher = np.sin(2 * np.pi * 3000 * samples).astype(np.float32)
        _, spectrum = features.audio_features(tone)
        steady, _ = features.audio_features(tone, spectrum)
        changed, _ = features.audio_features(higher, spectrum)
        self.assertGreater(changed["flux"], steady["flux"] + 0.01)
        short, _ = features.audio_features(np.array([0.1, -0.2], dtype=np.float32))
        self.assertAlmostEqual(short["peak"], 0.2, places=6)
        self.assertTrue(all(math.isfinite(value) for value in short.values()))

    def test_adaptive_floors_reject_quiet_noise_but_keep_loud_peak(self):
        scoring = features.AdaptiveScores()
        for _ in range(150):
            self.assertEqual(scoring.audio_score({"rms": 1e-5, "peak": 1e-4, "flux": 1}), 0)
        burst = scoring.audio_score({"rms": 0.35, "peak": 0.9, "flux": 0.5})
        self.assertGreater(burst, 70)
        self.assertLessEqual(len(scoring.audio), 120)
        for _ in range(1000):
            scoring.visual_scores({"motion": 0, "scene": 0})
        self.assertLessEqual(len(scoring.visual), 12)

    def test_visual_difference_and_state_roundtrip(self):
        black = features.frame_descriptor(np.zeros((320, 320), dtype=np.uint8))
        white = features.frame_descriptor(np.full((320, 320), 255, dtype=np.uint8))
        self.assertEqual(features.frame_difference(black, black), {"motion": 0, "scene": 0})
        self.assertEqual(features.frame_difference(black, white), {"motion": 1, "scene": 1})
        state = features.encode_frame_state(10, white)
        timestamp, descriptor = features.decode_frame_state(state)
        self.assertEqual(timestamp, 10)
        np.testing.assert_array_equal(descriptor[0], white[0])
        np.testing.assert_array_equal(descriptor[1], white[1])
        state["pixels"] = "bad!"
        with self.assertRaises(ValueError):
            features.decode_frame_state(state)


class RankingTests(unittest.TestCase):
    def candidate(self, timestamp, score, category="audio_activity"):
        start, end = ranking.clip_bounds(timestamp, 1000, 35)
        return ranking.Candidate(timestamp, start, end, score, score, 0, 0, category)

    def test_clip_bounds_cover_edges_and_short_source(self):
        self.assertEqual(ranking.clip_bounds(0, 100, 35), (0, 35))
        self.assertEqual(ranking.clip_bounds(99, 100, 35), (65, 100))
        self.assertEqual(ranking.clip_bounds(0.01, 0.05, 35), (0, 0.05))

    def test_combine_scores_is_bounded_and_rewards_agreement(self):
        self.assertEqual(ranking.combine_scores(0, 0, 0), 0)
        self.assertEqual(ranking.combine_scores(100, 100, 100), 100)
        self.assertGreater(ranking.combine_scores(80, 80, 50), ranking.combine_scores(80, 0, 0))

    def test_default_ten_and_deduplicate_overlaps(self):
        candidates = [self.candidate(20 + index * 45, 90 - index) for index in range(20)]
        candidates += [self.candidate(20.5, 95), self.candidate(21, 80)]
        result = ranking.select_candidates(candidates)
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0].time, 20.5)
        for index, candidate in enumerate(result):
            for other in result[index + 1:]:
                self.assertFalse(ranking.overlapping(candidate, other))

    def test_diversity_can_choose_other_region_and_signal_category(self):
        candidates = [self.candidate(50, 90), self.candidate(100, 82), self.candidate(900, 80, "visual_activity")]
        selected = ranking.select_candidates(candidates, max_clips=2, duration=1000)
        self.assertEqual([item.time for item in selected], [50, 900])

    def test_pool_bound_is_independent_of_recording_length(self):
        pool = ranking.CandidatePool(100000, 35, 10)
        for index in range(20000):
            pool.add(index * 5, 80, 40, 20)
        self.assertLessEqual(len(pool.candidates()), 40 * 8)
        self.assertGreater(len(pool.buckets), 30)

    def test_silent_static_is_not_filled_with_fake_highlights(self):
        ranker = ranking.StreamingRanker(60, 35, 10, 5)
        audio = audio_payload(0, 60)["rows"]
        for row in audio:
            row.update(rms=0, peak=0, flux=0)
        ranker.consume(audio, [{"time": time, "motion": 0, "scene": 0} for time in range(0, 60, 5)])
        self.assertEqual(ranker.finish(), [])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.bin"
        self.source.write_bytes(b"actual source identity")
        self.project = audio_project(self.source)
        self.settings = pipeline.DetectionSettings().cache_settings()
        self.key = cache.fingerprint(self.project, self.settings)
        self.cache = cache.ChunkCache(self.root / "cache", self.key)

    def test_fingerprint_is_deterministic_and_invalidates_source_settings_metadata(self):
        self.assertEqual(self.key, cache.fingerprint(self.project, dict(reversed(list(self.settings.items())))))
        altered = {**self.settings, "sampling_interval": 10}
        self.assertNotEqual(self.key, cache.fingerprint(self.project, altered))
        project = {**self.project, "metadata": {**self.project["metadata"], "duration": 500}}
        self.assertNotEqual(self.key, cache.fingerprint(project, self.settings))
        self.source.write_bytes(b"changed source")
        self.assertNotEqual(self.key, cache.fingerprint(self.project, self.settings))

    def test_atomic_chunk_roundtrip_and_corrupt_or_wrong_bounds_miss(self):
        payload = audio_payload(0, 60)
        self.cache.store("audio", 0, 0, 60, payload)
        self.assertEqual(self.cache.load("audio", 0, 0, 60), payload)
        self.assertIsNone(self.cache.load("audio", 0, 0, 61))
        self.cache.path("audio", 0).write_text('{"incomplete":', encoding="utf-8")
        self.assertIsNone(self.cache.load("audio", 0, 0, 60))

    def test_failed_replace_preserves_last_committed_chunk_and_cleans_temp(self):
        payload = audio_payload(0, 60)
        self.cache.store("audio", 0, 0, 60, payload)
        with patch.object(cache.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.cache.store("audio", 0, 0, 60, {"rows": []})
        self.assertEqual(self.cache.load("audio", 0, 0, 60), payload)
        self.assertEqual(list(self.cache.directory.glob("*.tmp")), [])

    def test_cancel_before_commit_leaves_no_partial_chunk(self):
        ctx = Context(self.root)
        checks = 0

        def cancel_before_replace():
            nonlocal checks
            checks += 1
            if checks == 2:
                raise Cancelled("before atomic commit")

        ctx.check_cancelled = cancel_before_replace
        with self.assertRaises(Cancelled):
            self.cache.store("audio", 0, 0, 60, audio_payload(0, 60), ctx)
        self.assertIsNone(self.cache.load("audio", 0, 0, 60))
        self.assertEqual(list(self.cache.directory.glob("*.tmp")), [])

    def test_disk_budget_and_oversized_entries(self):
        bounded = cache.ChunkCache(self.root / "bounded", self.key, max_bytes=1500)
        for index in range(10):
            bounded.store("audio", index, index * 60, (index + 1) * 60, {"data": "x" * 500})
        self.assertLessEqual(sum(path.stat().st_size for path in bounded.root.glob("*/*.json")), 1500)
        with self.cache.path("audio", 0).open("wb") as handle:
            handle.truncate(cache.MAX_CHUNK_BYTES + 1)
        self.assertIsNone(self.cache.load("audio", 0, 0, 60))

    def test_invalid_feature_payload_rejected(self):
        payload = audio_payload(0, 60)
        self.assertTrue(media.valid_audio_cache(payload, 0, 60))
        payload["rows"][0]["rms"] = math.nan
        self.assertFalse(media.valid_audio_cache(payload, 0, 60))
        payload["rows"][0]["rms"] = 0
        payload["rows"][0]["time"] = -1
        self.assertFalse(media.valid_audio_cache(payload, 0, 60))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"source identity; decoder is mocked in unit tests")
        self.project = audio_project(self.source)
        self.ctx = Context(self.root)
        self.options = {"analysis_chunk_seconds": 60}

    def scan_audio(self, binary, source, start, end, workers, ctx, on_progress, stream_index=None):
        ctx.check_cancelled()
        on_progress(end - start)
        return audio_payload(start, end)

    def test_resume_reuses_completed_chunks_and_reports_cached_seconds(self):
        def interrupt(binary, source, start, *args):
            if start >= 60:
                raise Cancelled("interrupt second chunk")
            return self.scan_audio(binary, source, start, *args)

        with core_helpers(), patch.object(pipeline, "scan_audio", side_effect=interrupt):
            with self.assertRaises(Cancelled):
                pipeline.analyze(self.project, self.options, self.ctx)
        self.assertEqual(len(list((self.ctx.cache_dir / "detection").glob("*/audio-*.json"))), 1)
        self.ctx.updates.clear()
        with core_helpers(), patch.object(pipeline, "scan_audio", side_effect=self.scan_audio) as scanner:
            highlights = pipeline.analyze(self.project, self.options, self.ctx)
        self.assertEqual([call.args[2] for call in scanner.call_args_list], [60, 120])
        self.assertTrue(any(stage == "scan" and seconds == 60 and "cache hit" in detail
                            for stage, seconds, _, detail in self.ctx.updates))
        scan_seconds = [seconds for stage, seconds, _, _ in self.ctx.updates if stage == "scan"]
        self.assertEqual(scan_seconds, sorted(scan_seconds))
        self.assertEqual(scan_seconds[-1], 125)
        self.assert_contract(highlights)
        with core_helpers(), patch.object(pipeline, "scan_audio", side_effect=AssertionError("must reuse cache")):
            cached = pipeline.analyze(self.project, self.options, Context(self.root))
        self.assertEqual(cached, highlights)

    def assert_contract(self, highlights):
        self.assertTrue(highlights)
        self.assertLessEqual(len(highlights), 10)
        expected = {"id", "project_id", "title", "start", "end", "duration", "category", "score", "confidence",
                    "audio_score", "visual_score", "scene_score", "explanation", "selected", "transcript", "thumbnail_url"}
        for highlight in highlights:
            self.assertEqual(set(highlight), expected)
            self.assertEqual(str(uuid.UUID(highlight["id"])), highlight["id"])
            self.assertEqual(highlight["transcript"], [])
            self.assertTrue(highlight["selected"])
            self.assertIn("No specific gameplay event", highlight["explanation"])
            self.assertGreaterEqual(highlight["start"], 0)
            self.assertLessEqual(highlight["end"], self.project["metadata"]["duration"])
            self.assertAlmostEqual(highlight["duration"], highlight["end"] - highlight["start"])
            for key in ("score", "audio_score", "visual_score", "scene_score"):
                self.assertTrue(0 <= highlight[key] <= 100)
            self.assertTrue(0 <= highlight["confidence"] <= 1)

    def test_short_source_and_cancelled_entry(self):
        self.project["metadata"]["duration"] = 0.04
        with core_helpers(), patch.object(pipeline, "scan_audio", side_effect=self.scan_audio):
            self.assertEqual(pipeline.analyze(self.project, self.options, self.ctx), [])
        self.ctx.cancelled = True
        with self.assertRaises(Cancelled):
            pipeline.analyze(self.project, self.options, self.ctx)

    def test_invalid_settings_and_duration_fail_before_decoding(self):
        for settings in ({"sampling_interval": 3}, {"audio_sample_rate": 8000}, {"max_clips": 51}, {"clip_duration": math.nan}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                pipeline.analyze(self.project, settings, self.ctx)
        for duration in (-1, math.inf, math.nan, None):
            self.project["metadata"]["duration"] = duration
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                pipeline.analyze(self.project, {}, self.ctx)

    def test_optional_refinement_is_nonfatal_and_keeps_shortlist_only(self):
        refinement = types.ModuleType("backend.processing.transcription")
        received = []

        def fail(project, shortlist, settings, ctx):
            received.extend(shortlist)
            shortlist[0]["transcript"] = [{"text": "must not leak failed mutations"}]
            raise RuntimeError("local model unavailable")

        refinement.refine = fail
        with core_helpers(), patch.object(pipeline, "scan_audio", side_effect=self.scan_audio), patch.dict(sys.modules, {refinement.__name__: refinement}):
            highlights = pipeline.analyze(self.project, {**self.options, "ai_refinement": True, "max_clips": 2}, self.ctx)
        self.assertLessEqual(len(received), 2)
        self.assert_contract(highlights)
        self.assertTrue(any("Optional transcription unavailable" in detail for _, _, _, detail in self.ctx.updates))

    def test_changed_source_is_not_returned_as_consistent_analysis(self):
        def change_source(*args, **kwargs):
            self.source.write_bytes(b"recording replaced while scanning")
            return self.scan_audio(*args, **kwargs)

        with core_helpers(), patch.object(pipeline, "scan_audio", side_effect=change_source):
            with self.assertRaisesRegex(RuntimeError, "Source changed"):
                pipeline.analyze(self.project, self.options, self.ctx)

    def test_optional_refinement_cancellation_propagates(self):
        refinement = types.ModuleType("backend.processing.transcription")

        def cancel(*args):
            raise Cancelled("cancel transcription")

        refinement.refine = cancel
        with core_helpers(), patch.object(pipeline, "scan_audio", side_effect=self.scan_audio), patch.dict(sys.modules, {refinement.__name__: refinement}):
            with self.assertRaises(Cancelled):
                pipeline.analyze(self.project, {**self.options, "ai_refinement": True}, self.ctx)

    def test_thumbnail_failure_is_nonfatal_and_cleans_temporary_jpeg(self):
        def fail(command, ctx=None, timeout=60):
            Path(command[-1]).write_bytes(b"incomplete jpeg")
            raise RuntimeError("thumbnail unavailable")

        with core_helpers() as process, patch.object(process, "run_process", side_effect=fail):
            pipeline._thumbnails("ffmpeg", self.source, [{"id": str(uuid.uuid4()), "start": 0, "duration": 10}],
                                 pipeline.DetectionSettings(), self.ctx)
        self.assertEqual(list((self.ctx.cache_dir / "thumbnails").iterdir()), [])

    def test_thumbnail_cache_is_bounded_without_removing_current_shortlist(self):
        directory = self.ctx.cache_dir / "thumbnails"
        directory.mkdir(parents=True)
        for index in range(300):
            (directory / f"{index}.jpg").write_bytes(b"jpeg")
        pipeline._prune_thumbnails(directory, {"0.jpg"})
        self.assertTrue((directory / "0.jpg").is_file())
        self.assertLessEqual(len(list(directory.glob("*.jpg"))), 256)

    def test_long_source_default_limit_and_bounded_ranker(self):
        self.project["metadata"]["duration"] = 3601
        with core_helpers(), patch.object(pipeline, "scan_audio", side_effect=self.scan_audio):
            highlights = pipeline.analyze(self.project, self.options, self.ctx)
        self.assertEqual(len(highlights), 10)
        self.assert_contract(highlights)
        self.assertEqual(max(seconds for stage, seconds, _, _ in self.ctx.updates if stage == "scan"), 3601)


class ReaderTests(unittest.TestCase):
    def test_sampling_grid_covers_supported_intervals_and_short_tail(self):
        for interval in (1, 2, 5, 10):
            self.assertEqual(list(media.sample_times(0, 12, interval)), list(range(0, 12, interval)))
        self.assertEqual(list(media.sample_times(60, 60.01, 10)), [60])
        self.assertAlmostEqual(list(media.sample_times(61, 61.01, 10))[0], 61.005)

    def test_every_frame_uses_input_seek_and_one_frame_only(self):
        commands = []

        def blocks(command, ctx, block_bytes, max_bytes, timeout):
            commands.append(command)
            yield bytes(160 * 160)

        with tempfile.TemporaryDirectory() as root, core_helpers(), patch.object(media, "output_blocks", side_effect=blocks):
            ctx = Context(root)
            first = media.scan_video("ffmpeg", "long.mp4", 0, 60, 10, 160, 2, ctx, lambda *args: None)
            second = media.scan_video("ffmpeg", "long.mp4", 60, 120, 10, 160, 2, ctx, lambda *args: None, first["last_frame"])
        self.assertEqual(len(commands), 12)
        self.assertEqual(len(second["rows"]), 6)
        self.assertEqual(second["rows"][0]["time"], 55)
        self.assertTrue(media.valid_video_cache(first, 0, 60, 10))
        self.assertTrue(media.valid_video_cache(second, 60, 120, 10, first["last_frame"]))
        changed_predecessor = {**first["last_frame"], "time": 49}
        self.assertFalse(media.valid_video_cache(second, 60, 120, 10, changed_predecessor))
        for command in commands:
            self.assertLess(command.index("-ss"), command.index("-i"))
            self.assertEqual(command[command.index("-frames:v") + 1], "1")
            self.assertNotIn("fps=", " ".join(command))

    def test_frame_cancellation_is_not_treated_as_a_decode_failure(self):
        with tempfile.TemporaryDirectory() as root, core_helpers(), patch.object(media, "read_frame", side_effect=Cancelled("cancel frame seek")):
            with self.assertRaises(Cancelled):
                media.scan_video("ffmpeg", "source", 0, 60, 5, 320, 2, Context(root), lambda *args: None)

    def test_byte_fragmented_pcm_is_processed_without_losing_samples(self):
        pcm = np.full(16001, 0.1, dtype="<f4").tobytes()

        def fragmented(*args, **kwargs):
            for offset in range(0, len(pcm), 1237):
                yield pcm[offset:offset + 1237]

        with tempfile.TemporaryDirectory() as root, patch.object(media, "output_blocks", side_effect=fragmented):
            result = media.scan_audio("ffmpeg", "source", 0, 1.0000625, 1, Context(root), lambda seconds: None)
        self.assertEqual(len(result["rows"]), 3)
        self.assertAlmostEqual(result["decoded_seconds"], 16001 / 16000)
        self.assertAlmostEqual(result["rows"][-1]["duration"], 1 / 16000)

    def test_stalled_pipe_is_cancellable_and_process_owner_exits(self):
        stopped = False
        closed = False

        class StalledPipe:
            def read(self, size):
                while not stopped:
                    time.sleep(0.01)
                return b""

        class Process:
            stdout = StalledPipe()
            returncode = None

        @contextmanager
        def managed(command, ctx=None):
            nonlocal stopped, closed
            try:
                yield Process()
            finally:
                stopped = True
                closed = True

        with tempfile.TemporaryDirectory() as root:
            ctx = Context(root)
            checks = 0

            def cancel():
                nonlocal checks
                checks += 1
                if checks >= 3:
                    raise Cancelled("cancel stalled process")

            ctx.check_cancelled = cancel
            started = time.monotonic()
            with core_helpers(managed=managed), self.assertRaises(Cancelled):
                list(media.output_blocks(["ffmpeg"], ctx, 1024, 2048, 60))
            self.assertLess(time.monotonic() - started, 3)
            self.assertTrue(closed)


def find_ffmpeg():
    binary = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


FFMPEG = find_ffmpeg()


@unittest.skipUnless(FFMPEG, "No FFmpeg binary available for optional real-media checks")
class RealMediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.ctx = Context(self.root)

    def test_real_16k_mono_audio_and_truncated_audio_tail(self):
        path = self.root / "burst.wav"
        samples = np.zeros(32000, dtype=np.float32)
        samples[8000:16000] = 0.8 * np.sin(2 * np.pi * 440 * np.arange(8000) / 16000)
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes((samples * 32767).astype("<i2").tobytes())
        project = audio_project(path, 4)  # audio ends before indexed timeline
        with core_helpers(FFMPEG):
            audio = media.scan_audio(FFMPEG, path, 0, 4, 1, self.ctx, lambda seconds: None)
            highlights = pipeline.analyze(project, {}, self.ctx)
        self.assertAlmostEqual(audio["decoded_seconds"], 2, places=3)
        self.assertEqual(len(audio["rows"]), 4)
        self.assertGreater(audio["rows"][1]["rms"], 0.5)
        self.assertEqual(len(highlights), 1)
        self.assertEqual(highlights[0]["transcript"], [])
        self.assertEqual(highlights[0]["visual_score"], 0)

    def test_real_stereo_resampling_seeks_and_delayed_track_alignment(self):
        path = self.root / "stereo48k.wav"
        samples = np.zeros(48000 * 2, dtype=np.float32)
        samples[:24000] = 0.8 * np.sin(2 * np.pi * 440 * np.arange(24000) / 48000)
        stereo = np.column_stack((samples, samples))
        with wave.open(str(path), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes((stereo * 32767).astype("<i2").tobytes())
        delayed = self.root / "delayed.mkv"
        command = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-f", "lavfi", "-i", "color=c=black:s=160x90:r=10:d=3",
                   "-itsoffset", "1", "-i", str(path), "-c:v", "mpeg4", "-c:a", "pcm_s16le", "-t", "3", str(delayed)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        with core_helpers(FFMPEG):
            resampled = media.scan_audio(FFMPEG, path, 0, 2, 1, self.ctx, lambda seconds: None)
            offset = media.scan_audio(FFMPEG, delayed, 0, 3, 1, self.ctx, lambda seconds: None)
            seeked = media.scan_audio(FFMPEG, delayed, 1, 2, 1, self.ctx, lambda seconds: None)
        self.assertEqual(len(resampled["rows"]), 4)
        self.assertGreater(resampled["rows"][0]["rms"], 0.5)
        self.assertLess(offset["rows"][0]["rms"], 1e-3)
        self.assertLess(offset["rows"][1]["rms"], 1e-3)
        self.assertGreater(offset["rows"][2]["rms"], 0.5)
        self.assertAlmostEqual(offset["rows"][2]["time"], 1.25)
        self.assertGreater(seeked["rows"][0]["rms"], 0.5)
        self.assertAlmostEqual(seeked["rows"][0]["time"], 1.25)

    def test_real_no_audio_sparse_frames_thumbnail_and_cache_reuse(self):
        path = self.root / "no-audio.mp4"
        source = "color=c=black:s=160x90:r=10:d=3,drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(t,1,1.9)'"
        result = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "lavfi",
                                 "-i", source, "-c:v", "mpeg4", "-q:v", "2", "-an", str(path)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        project = {"id": str(uuid.uuid4()), "source_path": str(path), "metadata": {
            "duration": 3, "width": 160, "height": 90, "fps": 10, "video_codec": "mpeg4", "audio_codec": None, "audio_streams": []}}
        with core_helpers(FFMPEG):
            highlights = pipeline.analyze(project, {"sampling_interval": 1}, self.ctx)
        self.assertEqual(len(highlights), 1)
        self.assertEqual(highlights[0]["audio_score"], 0)
        self.assertGreater(highlights[0]["visual_score"], 50)
        self.assertTrue((self.ctx.cache_dir / "thumbnails" / f"{highlights[0]['id']}.jpg").is_file())
        self.ctx.updates.clear()
        with core_helpers(FFMPEG), patch.object(media, "read_frame", side_effect=AssertionError("cached frame must not be decoded")):
            cached = pipeline.analyze(project, {"sampling_interval": 1}, self.ctx)
        self.assertEqual(cached, highlights)
        self.assertTrue(any("sparse frame cache hit" in detail and seconds == 3 for _, seconds, _, detail in self.ctx.updates))


if __name__ == "__main__":
    unittest.main()
