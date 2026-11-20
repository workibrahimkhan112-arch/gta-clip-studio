"""Offline rendering tests, with real media checks when local FFmpeg is installed."""

from __future__ import annotations

import io
import json
import math
import subprocess
import sys
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from backend.core.process import Cancelled
from backend.processing import _media, render, transcription
from backend.processing.subtitles import (
    ass_timestamp,
    build_ass,
    escape_ass_text,
    escape_filter_filename,
    transcript_cues,
)


class Context:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir
        self.cache_dir = work_dir / "cache"
        self.cancelled = False
        self.updates = []

    def check_cancelled(self):
        if self.cancelled:
            raise Cancelled("cancelled in test")

    def progress(self, stage, processed_seconds, total_seconds, detail=""):
        self.updates.append((stage, processed_seconds, total_seconds, detail))


def sample_transcript():
    return [{
        "start": 0.25, "end": 1.5, "text": "Real gameplay!",
        "words": [
            {"start": 0.25, "end": 0.7, "word": "Real"},
            {"start": 0.9, "end": 1.5, "word": "gameplay!"},
        ],
    }]


class SubtitleTests(unittest.TestCase):
    def test_centisecond_rounding_carries_minutes_and_hours(self):
        self.assertEqual(ass_timestamp(1.234), "0:00:01.23")
        self.assertEqual(ass_timestamp(59.999), "0:01:00.00")
        self.assertEqual(ass_timestamp(3599.999), "1:00:00.00")
        for invalid in (-1, math.inf, math.nan):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ass_timestamp(invalid)

    def test_standard_captions_keep_relative_segment_timing(self):
        transcript = [{"start": 1.25, "end": 2.5, "text": "Actual words", "words": []}]
        result = build_ass(transcript, 3, (720, 1280))
        self.assertIn("Dialogue: 0,0:00:01.25,0:00:02.50", result)
        self.assertIn("PlayResX: 720\nPlayResY: 1280", result)
        self.assertIn("Actual words", result)
        self.assertIn("&H00FFFFFF", result)
        self.assertIn("&H00141414", result)
        self.assertIn(",-1,0,0,0,100,100", result)  # Bold, not fabricated text.
        self.assertIn(",2,58,58,346,1", result)  # Lower-middle safe-area anchor.

    def test_clamp_intervals_to_clip_and_reject_nonfinite_times(self):
        transcript = [
            {"start": -2, "end": 1, "text": "first"},
            {"start": 2, "end": 8, "text": "last"},
            {"start": 12, "end": 13, "text": "outside"},
            {"start": math.nan, "end": 1, "text": "not a number"},
            {"start": 1, "end": math.inf, "text": "infinite"},
        ]
        cues = transcript_cues(transcript, 3)
        self.assertEqual([(cue.start, cue.end) for cue in cues], [(0, 1), (2, 3)])
        result = build_ass(transcript, 3, (1080, 1920))
        self.assertNotIn("outside", result)
        self.assertIn("0:00:02.00,0:00:03.00", result)

    def test_animated_highlights_only_real_word_intervals(self):
        result = build_ass(sample_transcript(), 3, (720, 1280), "animated")
        self.assertIn(r"{\c&H00FF00&}Real{\c&HFFFFFF&} gameplay!", result)
        self.assertIn(r"Real {\c&H00FF00&}gameplay!{\c&HFFFFFF&}", result)
        self.assertIn("0:00:00.25,0:00:00.70", result)
        self.assertIn("0:00:00.70,0:00:00.90,Caption,,0,0,0,,Real gameplay!", result)
        self.assertIn("0:00:00.90,0:00:01.50", result)
        self.assertEqual(result.count("Dialogue:"), 3)

    def test_standard_captions_emphasize_only_supplied_attention_words(self):
        result = build_ass([{"start": 0, "end": 2, "text": "wow that was CLOSE", "words": []}], 3, (720, 1280))
        self.assertIn(r"{\c&H00FF00&}wow{\c&HFFFFFF&}", result)
        self.assertIn(r"{\c&H00FF00&}CLOSE{\c&HFFFFFF&}", result)
        self.assertIn(" that was ", result)

    def test_animated_never_guesses_word_timings(self):
        with self.assertRaisesRegex(ValueError, "real word-level timestamps"):
            build_ass([{"start": 0, "end": 2, "text": "No word timings"}], 3, (720, 1280), "animated")

    def test_missing_out_of_range_or_blank_transcript_is_actionable(self):
        for transcript in ([], [{"start": 300, "end": 302, "text": "absolute"}], [{"start": 0, "end": 2, "text": " "}]):
            with self.subTest(transcript=transcript), self.assertRaisesRegex(ValueError, "relative to the clip start"):
                build_ass(transcript, 3, (720, 1280))

    def test_ass_injection_and_new_event_injection_are_neutralized(self):
        malicious = "{\\pos(0,0)\\p1}x\\Nboom\r\nDialogue: 0,0:00:00.00\x00\tend"
        escaped = escape_ass_text(malicious)
        self.assertNotIn("{", escaped)
        self.assertNotIn("}", escaped)
        self.assertNotIn(r"\pos", escaped)
        self.assertNotIn(r"\p1", escaped)
        self.assertNotIn("\n", escaped)
        self.assertNotIn("\x00", escaped)
        self.assertIn("＼Nboom", escaped)
        self.assertIn(r"\NDialogue:", escaped)
        result = build_ass([{"start": 0, "end": 1, "text": malicious}], 3, (720, 1280))
        self.assertEqual(sum(line.startswith("Dialogue:") for line in result.splitlines()), 1)

    def test_word_text_is_also_escaped(self):
        transcript = [{"start": 0, "end": 1, "text": "safe", "words": [
            {"start": 0, "end": 1, "word": r"{\an7}unsafe"},
        ]}]
        result = build_ass(transcript, 2, (720, 1280), "animated")
        self.assertNotIn(r"{\an7}", result)
        self.assertIn("｛＼an7｝unsafe", result)

    def test_caption_positions_preserve_styles_timing_and_escaping(self):
        transcript = [{"start": 0.1, "end": 0.9, "text": "safe", "words": [
            {"start": 0.1, "end": 0.9, "word": r"{\pos(0,0)}safe"},
        ]}]
        for width, height in ((720, 1280), (1080, 1920)):
            margin = round(width * 0.08)
            for style in ("standard", "animated"):
                for position, alignment, vertical in (
                    ("lower_middle", 2, round(height * 0.27)),
                    ("center", 5, 0), ("bottom", 2, round(height * 0.12)),
                ):
                    with self.subTest(resolution=(width, height), style=style, position=position):
                        result = build_ass(transcript, 1, (width, height), style, position=position)
                        self.assertIn(f",{alignment},{margin},{margin},{vertical},1\n", result)
                        self.assertIn("0:00:00.10,0:00:00.90", result)
                        self.assertNotIn(r"{\pos(0,0)}", result)
                        self.assertIn("｛＼pos(0,0)｝safe", result)
        with self.assertRaisesRegex(ValueError, "Caption position"):
            build_ass(sample_transcript(), 3, (720, 1280), position="top")
        self.assertEqual(
            build_ass(sample_transcript(), 3, (720, 1280)),
            build_ass(sample_transcript(), 3, (720, 1280), position="lower_middle"),
        )

    def test_windows_path_escapes_both_filter_parsers(self):
        escaped = escape_filter_filename(r"C:\Users\O'Brien, [x]; work\captions.ass")
        self.assertTrue(escaped.startswith(r"C\\:/Users/"))
        self.assertIn(r"\\\'", escaped)
        self.assertIn(r"\,", escaped)
        self.assertIn(r"\[", escaped)
        self.assertIn(r"\;", escaped)

        def unescape_level(value):
            output = []
            index = 0
            while index < len(value):
                if value[index] == "\\":
                    index += 1
                output.append(value[index])
                index += 1
            return "".join(output)

        self.assertEqual(unescape_level(unescape_level(escaped)), "C:/Users/O'Brien, [x]; work/captions.ass")
        with self.assertRaises(ValueError):
            escape_filter_filename("/work/bad\npath.ass")


class CommandTests(unittest.TestCase):
    def command(self, settings=None, **kwargs):
        return render.build_render_command(
            "ffmpeg", Path("source video.mp4"), Path("temporary.mp4"), 301.25, 35,
            settings or {"caption_style": "none"}, **kwargs,
        )

    def test_seek_and_duration_are_input_side(self):
        command = self.command()
        source_index = command.index("-i")
        self.assertLess(command.index("-ss"), source_index)
        self.assertLess(command.index("-t"), source_index)
        self.assertEqual(command[command.index("-ss") + 1], "301.250000")
        self.assertEqual(command[command.index("-t") + 1], "35.000000")
        self.assertEqual(command.count("-i"), 1)
        self.assertEqual(command[-2:], ["mp4", "temporary.mp4"])
        self.assertIn("-nostdin", command)
        self.assertEqual(command[command.index("-progress") + 1], "pipe:1")

    def test_h264_aac_30fps_and_both_supported_resolutions(self):
        for resolution in ("720x1280", "1080x1920"):
            with self.subTest(resolution=resolution):
                command = self.command({"output_resolution": resolution})
                graph = command[command.index("-filter_complex") + 1]
                self.assertIn("scale=" + resolution.replace("x", ":"), graph)
                for flag, value in (("-r", "30"), ("-c:v", "libx264"), ("-c:a", "aac"), ("-pix_fmt", "yuv420p")):
                    self.assertEqual(command[command.index(flag) + 1], value)
        with self.assertRaisesRegex(ValueError, "resolution"):
            self.command({"output_resolution": "4k"})

    def test_gameplay_fits_uncropped_centered_over_blur(self):
        command = self.command()
        graph = command[command.index("-filter_complex") + 1]
        foreground = graph.split("[gameplay_in]", 2)[-1].split("[gameplay]", 1)[0]
        self.assertIn("min(720,1280*dar)", foreground)
        self.assertIn("min(1280,720/dar)", foreground)
        self.assertNotIn("crop", foreground)
        self.assertIn("boxblur=", graph)
        self.assertIn("overlay=x=(W-w)/2:y=(H-h)/2", graph)

    def test_all_codec_filter_and_lookahead_threads_are_explicitly_bounded(self):
        for requested in (1, 2, 20, 0, -1, "bad", None, math.inf):
            with self.subTest(requested=requested):
                command = self.command({"cpu_workers": requested})
                for index, token in enumerate(command):
                    if token in {"-threads", "-threads:v", "-threads:a", "-filter_threads", "-filter_complex_threads"}:
                        self.assertIn(int(command[index + 1]), (1, 2))
                params = command[command.index("-x264-params") + 1]
                self.assertIn("lookahead_threads=1", params)
                self.assertIn("sync_lookahead=0", params)

    def test_video_selection_excludes_cover_art_and_audio_keeps_clip_origin(self):
        command = self.command()
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[0:V:0]", graph)
        self.assertIn("fps=30:start_time=0", graph)
        self.assertNotIn("setpts=PTS-STARTPTS", graph)
        self.assertEqual(command[command.index("-af") + 1], "aresample=async=1:first_pts=0")
        command = self.command(video_stream_index=2)
        self.assertIn("[0:2]", command[command.index("-filter_complex") + 1])

    def test_silent_source_gets_bounded_silent_aac_input(self):
        command = self.command(has_audio=False)
        self.assertEqual(command.count("-i"), 2)
        self.assertIn("anullsrc=r=48000:cl=stereo", command)
        self.assertIn("1:a:0", command)
        self.assertNotIn("-shortest", command)  # Short audio must not truncate gameplay.

    def test_subtitle_path_is_escaped_in_graph_not_shell_quoted(self):
        path = Path("work, [x]; O'Brien") / "captions.ass"
        command = self.command(subtitle_path=path)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("ass=filename=" + escape_filter_filename(path), graph)
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("source video.mp4", command)

    def test_clip_bounds_clamp_to_source_end(self):
        self.assertEqual(_media.clip_bounds({"metadata": {"duration": 10}}, {"start": 8, "end": 15}), (8, 2))
        for start, end in ((-1, 2), (2, 2), (3, 2), (math.nan, 2), (0, math.inf), (11, 12)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                _media.clip_bounds({"metadata": {"duration": 10}}, {"start": start, "end": end})


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"original source")
        self.output = self.root / "exports"
        self.ctx = Context(self.root / "work, [x]; O'Brien")
        self.project = {"source_path": str(self.source), "metadata": {"duration": 900, "audio_streams": [{"index": 1}]}}
        self.highlight = {"title": "../CON: race!", "start": 300, "end": 303, "transcript": sample_transcript()}
        self.settings = {"output_folder": str(self.output), "caption_style": "none"}
        self.binary = patch.object(render, "get_binary", side_effect=lambda name: name)
        self.binary.start()
        self.addCleanup(self.binary.stop)

    def test_atomic_destination_unique_names_and_work_cleanup(self):
        temporary_paths = []

        def encode(command, duration, ctx):
            path = Path(command[-1])
            temporary_paths.append(path)
            self.assertEqual(path.parent, self.output)
            self.assertTrue(path.name.endswith(".part.mp4"))
            self.assertEqual(list(self.output.glob("gta-*.mp4")), [])
            path.write_bytes(b"encoded mp4")

        with patch.object(render, "run_ffmpeg", side_effect=encode):
            first = render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        self.assertTrue(Path(first["output_path"]).is_file())
        self.assertEqual(Path(first["output_path"]).name, first["filename"])
        self.assertEqual(Path(first["output_path"]).read_bytes(), b"encoded mp4")
        self.assertFalse(temporary_paths[0].exists())
        self.assertEqual(list(self.ctx.work_dir.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), b"original source")
        self.assertNotIn("..", first["filename"])
        self.assertNotEqual(render._filename(self.highlight), first["filename"])
        self.assertEqual(self.ctx.updates[-1][:3], ("rendering", 3, 3))

    def test_failure_and_cancellation_remove_partial_output(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                self.ctx.cancelled = False

                def fail(command, duration, ctx):
                    Path(command[-1]).write_bytes(b"partial")
                    if cancelled:
                        ctx.cancelled = True
                    else:
                        raise RuntimeError("encoder failed")

                with patch.object(render, "run_ffmpeg", side_effect=fail):
                    with self.assertRaises(Cancelled if cancelled else RuntimeError):
                        render.render_clip(self.project, self.highlight, self.settings, self.ctx)
                self.assertEqual(list(self.output.iterdir()), [])
                self.assertEqual(list(self.ctx.work_dir.iterdir()), [])

    def test_empty_success_is_not_published(self):
        with patch.object(render, "run_ffmpeg"), self.assertRaisesRegex(RuntimeError, "nonempty MP4"):
            render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_existing_transcript_is_used_without_model_or_source_time_offset(self):
        self.settings["caption_style"] = "animated"

        def encode(command, duration, ctx):
            files = list(ctx.work_dir.glob("render-*/captions.ass"))
            self.assertEqual(len(files), 1)
            content = files[0].read_text(encoding="utf-8")
            self.assertIn("0:00:00.25,0:00:00.70", content)
            self.assertNotIn("0:05:00", content)
            self.assertIn("ass=filename=", command[command.index("-filter_complex") + 1])
            Path(command[-1]).write_bytes(b"encoded mp4")

        with patch.object(render, "run_ffmpeg", side_effect=encode), patch.object(render, "transcribe_clip") as recognize:
            render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        recognize.assert_not_called()
        self.assertEqual(list(self.ctx.work_dir.iterdir()), [])

    def test_missing_transcript_attempts_local_only_and_does_not_make_fake_output(self):
        self.settings["caption_style"] = "standard"
        self.highlight["transcript"] = []
        error = transcription.TranscriptionUnavailable("Enter a transcript in the captions editor")
        with patch.object(render, "transcribe_clip", side_effect=error) as recognize, patch.object(render, "run_ffmpeg") as encode:
            with self.assertRaisesRegex(transcription.TranscriptionUnavailable, "captions editor"):
                render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        recognize.assert_called_once_with(self.project, self.highlight, self.settings, self.ctx)
        encode.assert_not_called()
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertEqual(list(self.ctx.work_dir.iterdir()), [])

    def test_none_style_never_loads_model(self):
        self.highlight["transcript"] = []

        def encode(command, *_args):
            self.assertNotIn("ass=", command[command.index("-filter_complex") + 1])
            Path(command[-1]).write_bytes(b"encoded mp4")

        with patch.object(render, "run_ffmpeg", side_effect=encode), patch.object(render, "transcribe_clip") as recognize:
            render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        recognize.assert_not_called()

    def test_default_export_directory_uses_shared_config(self):
        self.settings["output_folder"] = ""

        def encode(command, *_args):
            Path(command[-1]).write_bytes(b"encoded mp4")

        with patch.object(render, "EXPORTS_DIR", self.output), patch.object(render, "run_ffmpeg", side_effect=encode):
            result = render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        self.assertEqual(Path(result["output_path"]).parent, self.output)

    def test_split_requires_camera_before_transcription_or_output_creation(self):
        self.project["layout"] = {"mode": "split", "facecam": None}
        self.settings["caption_style"] = "standard"
        self.highlight["transcript"] = []
        with patch.object(render, "transcribe_clip") as recognize, patch.object(render, "run_ffmpeg") as encode:
            with self.assertRaisesRegex(ValueError, "Select the embedded streamer camera crop"):
                render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        recognize.assert_not_called()
        encode.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.ctx.work_dir.exists())

    def test_project_layout_and_caption_position_reach_export(self):
        self.project["layout"] = {
            "mode": "split", "facecam": {"x": 0, "y": 0, "width": 0.25, "height": 0.5},
            "caption_position": "center", "split_ratio": 0.3,
        }
        self.settings["caption_style"] = "standard"

        def encode(command, *_args):
            graph = command[command.index("-filter_complex") + 1]
            self.assertIn("crop=720:384", graph)
            self.assertIn("pad=720:896", graph)
            content = next(self.ctx.work_dir.glob("render-*/captions.ass")).read_text(encoding="utf-8")
            self.assertIn(",5,58,58,0,1\n", content)
            Path(command[-1]).write_bytes(b"encoded mp4")

        with patch.object(render, "run_ffmpeg", side_effect=encode):
            result = render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        self.assertTrue(Path(result["output_path"]).is_file())
        self.assertEqual(self.project["layout"]["split_ratio"], 0.3)
        self.assertNotIn("gameplay", self.project["layout"])

    def test_cancellation_at_publication_progress_removes_partial(self):
        def encode(command, *_args):
            Path(command[-1]).write_bytes(b"encoded mp4")

        def progress(*_args):
            self.ctx.cancelled = True

        with patch.object(self.ctx, "progress", side_effect=progress), patch.object(render, "run_ffmpeg", side_effect=encode), self.assertRaises(Cancelled):
            render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_no_cancellation_checks_after_atomic_publication(self):
        def encode(command, *_args):
            Path(command[-1]).write_bytes(b"encoded mp4")

        replace = Path.replace

        def publish(path, destination):
            result = replace(path, destination)
            self.ctx.cancelled = True
            return result

        with patch.object(render, "run_ffmpeg", side_effect=encode), patch.object(Path, "replace", publish):
            result = render.render_clip(self.project, self.highlight, self.settings, self.ctx)
        self.assertTrue(self.ctx.cancelled)
        self.assertEqual(Path(result["output_path"]).read_bytes(), b"encoded mp4")
        self.assertFalse(list(self.output.glob("*.part.mp4")))

    def test_sparse_metadata_probes_audio_instead_of_dropping_it(self):
        self.project["metadata"] = {}
        result = subprocess.CompletedProcess([], 0, "1\n", "")
        with patch.object(render, "run_process", return_value=result) as probe:
            self.assertTrue(render._source_has_audio(self.project, self.source, self.ctx))
        self.assertIn("ffprobe", probe.call_args.args[0][0])
        self.assertIn(str(self.source), probe.call_args.args[0])


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.ctx = Context(Path("unused"))

    @contextmanager
    def process(self, stdout=b"", returncode=0, stderr=b""):
        process = types.SimpleNamespace(
            stdout=io.BytesIO(stdout), stderr=io.BytesIO(stderr),
            poll=lambda: returncode, wait=lambda timeout: returncode,
        )
        yield process

    def test_progress_uses_microseconds_and_is_clamped_monotonic(self):
        output = (
            b"out_time_ms=1500000\nprogress=continue\n"
            b"out_time_us=-2\nprogress=continue\n"
            b"out_time=00:00:99.000\nprogress=end\n"
        )
        with patch.object(_media, "managed_process", return_value=self.process(output)):
            _media.run_ffmpeg(["ffmpeg"], 3, self.ctx)
        self.assertEqual([update[1] for update in self.ctx.updates], [0, 1.5, 1.5, 3])
        self.assertTrue(all(update[0] == "rendering" and update[2] == 3 for update in self.ctx.updates))

    def test_nonzero_exit_contains_bounded_ffmpeg_error(self):
        with patch.object(_media, "managed_process", return_value=self.process(returncode=1, stderr=b"bad encoder")):
            with self.assertRaisesRegex(RuntimeError, "bad encoder"):
                _media.run_ffmpeg(["ffmpeg"], 3, self.ctx)

    def test_cancellation_checked_even_without_progress_or_process_exit(self):
        exited = []

        @contextmanager
        def silent_process(*_args, **_kwargs):
            try:
                yield types.SimpleNamespace(stdout=io.BytesIO(), stderr=io.BytesIO(), poll=lambda: None)
            finally:
                exited.append(True)

        checks = 0

        def check():
            nonlocal checks
            checks += 1
            if checks >= 3:
                raise Cancelled("cancel without output")

        self.ctx.check_cancelled = check
        before = time.monotonic()
        with patch.object(_media, "managed_process", side_effect=silent_process), self.assertRaises(Cancelled):
            _media.run_ffmpeg(["ffmpeg"], 3, self.ctx)
        self.assertLess(time.monotonic() - before, 1)
        self.assertEqual(exited, [True])


class TranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "source.mp4"
        source.write_bytes(b"source")
        self.project = {"source_path": str(source), "metadata": {"duration": 900, "audio_streams": [{"index": 1}]}}
        self.highlight = {"start": 300, "end": 303, "score": 90, "transcript": []}
        self.ctx = Context(self.root / "work")

    def test_model_load_is_tiny_en_cpu_int8_bounded_and_offline(self):
        constructor = Mock(return_value=object())
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = constructor
        with patch.dict(sys.modules, {"faster_whisper": module}), patch.object(transcription, "_cached_model_path", return_value="cached-tiny.en"):
            transcription._load_model({"cpu_workers": 200})
        constructor.assert_called_once_with(
            "cached-tiny.en", device="cpu", compute_type="int8", cpu_threads=2,
            num_workers=1, local_files_only=True,
        )

    def test_missing_model_is_actionable_not_downloaded(self):
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = Mock(side_effect=FileNotFoundError("cache miss"))
        with patch.dict(sys.modules, {"faster_whisper": module}), patch.object(transcription, "_cached_model_path", return_value="cached-tiny.en"):
            with self.assertRaisesRegex(transcription.TranscriptionUnavailable, "run.dev.cmd"):
                transcription._load_model({})
        self.assertTrue(module.WhisperModel.call_args.kwargs["local_files_only"])

    def test_cache_lookup_is_local_only_and_rejects_missing_tokenizer(self):
        module = types.ModuleType("faster_whisper")
        module.WhisperModel = Mock()
        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = Mock(return_value=str(self.root))
        (self.root / "model.bin").write_bytes(b"model")
        (self.root / "config.json").write_text("{}", encoding="utf-8")
        with patch.dict(sys.modules, {"faster_whisper": module, "faster_whisper.utils": utils}), patch.object(transcription, "model_is_cached", return_value=False):
            with self.assertRaisesRegex(transcription.TranscriptionUnavailable, "cache"):
                transcription._load_model({})
            module.WhisperModel.assert_not_called()
            utils.download_model.assert_called_once_with("tiny.en", local_files_only=True)
            (self.root / "tokenizer.json").write_text("{}", encoding="utf-8")
            self.assertEqual(transcription._cached_model_path(), str(self.root))

    def test_model_download_is_only_an_explicit_setup_helper(self):
        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = Mock(return_value="cached-model")
        with patch.dict(sys.modules, {"faster_whisper.utils": utils}):
            self.assertEqual(transcription.setup_model_cache(), "cached-model")
        utils.download_model.assert_called_once_with("tiny.en")

    def test_transcription_seeks_audio_only_and_preserves_relative_word_times(self):
        word = types.SimpleNamespace(start=0.25, end=0.8, word=" Hello")
        segment = types.SimpleNamespace(start=0.25, end=1.0, text=" Hello", words=[word])
        model = Mock()
        model.transcribe.return_value = (iter([segment]), object())

        def extract(command, **_kwargs):
            Path(command[-1]).write_bytes(b"wav")
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(transcription, "get_binary", return_value="ffmpeg"), patch.object(transcription, "run_process", side_effect=extract) as process:
            result = transcription.transcribe_clip(self.project, self.highlight, {"cpu_workers": 100}, self.ctx, model=model)
        self.assertEqual(result, [{"start": 0.25, "end": 1.0, "text": "Hello", "words": [{"start": 0.25, "end": 0.8, "word": "Hello"}]}])
        command = process.call_args.args[0]
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertLess(command.index("-t"), command.index("-i"))
        self.assertEqual(command[command.index("-ss") + 1], "300.000000")
        self.assertEqual(command[command.index("-threads") + 1], "2")
        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertFalse(model.transcribe.call_args.kwargs["vad_filter"])
        self.assertTrue(model.transcribe.call_args.kwargs["word_timestamps"])
        self.assertEqual(list(self.ctx.work_dir.iterdir()), [])

    def test_no_speech_fails_instead_of_fabricating_a_transcript(self):
        model = Mock()
        model.transcribe.return_value = (iter([]), object())
        with patch.object(transcription, "get_binary", return_value="ffmpeg"), patch.object(transcription, "run_process", return_value=subprocess.CompletedProcess([], 0, "", "")):
            with self.assertRaisesRegex(transcription.TranscriptionUnavailable, "No speech transcript"):
                transcription.transcribe_clip(self.project, self.highlight, {}, self.ctx, model=model)
        self.assertEqual(list(self.ctx.work_dir.iterdir()), [])

    def test_audio_process_timeout_is_nonfatal_for_refinement(self):
        with patch.object(transcription, "_load_model", return_value=object()), patch.object(transcription, "get_binary", return_value="ffmpeg"), patch.object(transcription, "run_process", side_effect=subprocess.TimeoutExpired("ffmpeg", 60)):
            result = transcription.refine(self.project, [self.highlight], {"ai_refinement": True}, self.ctx)
        self.assertEqual(result, [self.highlight])
        self.assertEqual(self.highlight["transcript"], [])
        self.assertIn("skipped", self.ctx.updates[-1][3])
        self.assertEqual(list(self.ctx.work_dir.iterdir()), [])

    def test_audio_extraction_cancellation_is_not_swallowed(self):
        with patch.object(transcription, "get_binary", return_value="ffmpeg"), patch.object(transcription, "run_process", side_effect=Cancelled("cancel extraction")):
            with self.assertRaises(Cancelled):
                transcription.transcribe_clip(self.project, self.highlight, {}, self.ctx, model=object())
        self.assertEqual(list(self.ctx.work_dir.iterdir()), [])

    def test_refine_unavailable_is_nonfatal_and_does_not_change_highlights(self):
        original = [self.highlight]
        error = transcription.TranscriptionUnavailable("model not cached")
        with patch.object(transcription, "_load_model", side_effect=error):
            result = transcription.refine(self.project, original, {"ai_refinement": True}, self.ctx)
        self.assertIs(result, original)
        self.assertEqual(self.highlight["transcript"], [])
        self.assertIn("skipped", self.ctx.updates[-1][3])

    def test_refine_shortlist_only_preserves_order_scores_and_existing_transcripts(self):
        highlights = [
            {"start": index * 30, "end": index * 30 + 30, "score": index * 10, "transcript": []}
            for index in range(8)
        ]
        highlights[7]["transcript"] = sample_transcript()
        highlights[6]["selected"] = False
        model = object()
        with patch.object(transcription, "_load_model", return_value=model), patch.object(transcription, "transcribe_clip", return_value=sample_transcript()) as recognize:
            result = transcription.refine(self.project, highlights, {"ai_refinement": True}, self.ctx)
        self.assertEqual(recognize.call_count, 3)
        self.assertEqual([call.args[1]["score"] for call in recognize.call_args_list], [50, 40, 30])
        self.assertEqual([item["score"] for item in result], [item["score"] for item in highlights])
        self.assertTrue(all(not item["transcript"] for item in highlights[:7]))
        self.assertIs(result[7], highlights[7])
        self.assertTrue(all(call.kwargs["model"] is model for call in recognize.call_args_list))

    def test_refine_total_audio_budget_is_bounded(self):
        highlights = [{"start": i * 60, "end": (i + 1) * 60, "score": i} for i in range(6)]
        with patch.object(transcription, "_load_model", return_value=object()), patch.object(transcription, "transcribe_clip", return_value=sample_transcript()) as recognize:
            transcription.refine(self.project, highlights, {"ai_refinement": True}, self.ctx)
        self.assertEqual(recognize.call_count, 2)
        self.assertTrue(all(call.kwargs["progress_total"] == 120 for call in recognize.call_args_list))

    def test_refine_disabled_and_cancellation_do_not_get_swallowed(self):
        with patch.object(transcription, "_load_model") as load:
            original = [self.highlight]
            self.assertIs(transcription.refine(self.project, original, {}, self.ctx), original)
            load.assert_not_called()
        self.ctx.cancelled = True
        with self.assertRaises(Cancelled):
            transcription.refine(self.project, [self.highlight], {"ai_refinement": True}, self.ctx)


class FFmpegIntegrationTests(unittest.TestCase):
    """Automatically skipped unless local FFmpeg/FFprobe are installed."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = render.get_binary("ffmpeg")
            cls.ffprobe = render.get_binary("ffprobe")
            for binary in (cls.ffmpeg, cls.ffprobe):
                subprocess.run([binary, "-version"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise unittest.SkipTest(f"Local FFmpeg/FFprobe unavailable: {exc}") from exc

    def test_real_embedded_camera_split_pixels_both_sizes_fit_fill_and_dar(self):
        # Author an actual single source: a red embedded camera with a yellow
        # left edge, alongside blue gameplay with green/white edge markers.
        # SAR 2:1 makes ignoring display aspect ratio visibly fail the bar checks.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "embedded facecam.mp4"
            subprocess.run([
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-filter_threads", "1", "-filter_complex_threads", "1",
                "-threads", "1", "-f", "lavfi", "-i", "color=blue:size=320x180:rate=30",
                "-vf", (
                    "drawbox=x=32:y=18:w=80:h=90:color=red:t=fill,"
                    "drawbox=x=32:y=18:w=8:h=90:color=yellow:t=fill,"
                    "drawbox=x=160:y=0:w=16:h=180:color=lime:t=fill,"
                    "drawbox=x=304:y=0:w=16:h=180:color=white:t=fill,"
                    "drawbox=x=0:y=0:w=320:h=180:color=yellow:t=fill:enable='lt(t,0.5)',setsar=2/1"
                ),
                "-t", "1.5", "-c:v", "libx264", "-threads:v", "1", "-pix_fmt", "yuv420p", str(source),
            ], check=True, capture_output=True, timeout=30)
            for resolution in ("720x1280", "1080x1920"):
                width, height = map(int, resolution.split("x"))
                for facecam_fit, gameplay_fit, ratio in (("fill", "fit", 0.5), ("fit", "fill", 0.5), ("fill", "fill", 0.4333)):
                    with self.subTest(resolution=resolution, facecam_fit=facecam_fit, gameplay_fit=gameplay_fit, ratio=ratio):
                        project = {
                            "source_path": str(source), "metadata": {"duration": 1.5, "audio_streams": []},
                            "layout": {
                                "mode": "split",
                                "facecam": {"x": 0.1, "y": 0.1, "width": 0.25, "height": 0.5},
                                "gameplay": {"x": 0.5, "y": 0, "width": 0.5, "height": 1},
                                "facecam_fit": facecam_fit, "gameplay_fit": gameplay_fit,
                                "split_ratio": ratio, "caption_position": "bottom",
                            },
                        }
                        ctx = Context(root / "work")
                        style = ("standard" if width == 720 else "animated") if ratio != 0.5 else "none"
                        result = render.render_clip(project, {"start": 0.5, "end": 1.5, "transcript": sample_transcript()}, {
                            "output_folder": str(root / "exports"), "output_resolution": resolution,
                            "caption_style": style, "cpu_workers": 2,
                        }, ctx)
                        probe = subprocess.run([
                            self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", result["output_path"],
                        ], check=True, capture_output=True, text=True, timeout=10)
                        metadata = json.loads(probe.stdout)
                        video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
                        self.assertEqual((video["width"], video["height"]), (width, height))
                        self.assertEqual(video["sample_aspect_ratio"], "1:1")
                        self.assertEqual(video["display_aspect_ratio"], "9:16")
                        self.assertEqual(video["avg_frame_rate"], "30/1")
                        self.assertEqual(video["codec_name"], "h264")
                        self.assertEqual(video["pix_fmt"], "yuv420p")
                        self.assertEqual(int(video["nb_frames"]), 30)
                        self.assertLess(abs(float(metadata["format"]["duration"]) - 1), 0.1)
                        self.assertTrue(any(stream["codec_name"] == "aac" for stream in metadata["streams"]))
                        frame = subprocess.run([
                            self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                            "-filter_threads", "1", "-threads", "2", "-ss", "0.5", "-i", result["output_path"],
                            "-frames:v", "1", "-an", "-pix_fmt", "rgb24", "-threads:v", "1", "-f", "rawvideo", "pipe:1",
                        ], check=True, capture_output=True, timeout=15).stdout
                        self.assertEqual(len(frame), width * height * 3)

                        def assert_pixel(x, y, color, *, width=width, frame=frame):
                            offset = (y * width + x) * 3
                            actual = tuple(frame[offset:offset + 3])
                            self.assertTrue(
                                all(abs(channel - expected) < 45 for channel, expected in zip(actual, color)),
                                f"pixel ({x}, {y}) = {actual}, expected {color}",
                            )

                        top_height = math.floor(height * ratio / 2 + 0.5) * 2
                        gameplay_center = top_height + (height - top_height) // 2
                        assert_pixel(width // 2, top_height // 2, (255, 0, 0))
                        assert_pixel(width // 2, gameplay_center, (0, 0, 255))
                        for y in (4, top_height - 5):
                            assert_pixel(width // 2, y, (0, 0, 0) if facecam_fit == "fit" else (255, 0, 0))
                        for y in (top_height + 4, height - 5):
                            assert_pixel(width // 2, y, (0, 0, 0) if gameplay_fit == "fit" else (0, 0, 255))
                        # Fit retains edge markers; fill center-crops them away.
                        assert_pixel(round(width * 0.04), top_height // 2, (255, 255, 0) if facecam_fit == "fit" else (255, 0, 0))
                        assert_pixel(round(width * 0.04), gameplay_center, (0, 255, 0) if gameplay_fit == "fit" else (0, 0, 255))
                        assert_pixel(round(width * 0.96), gameplay_center, (255, 255, 255) if gameplay_fit == "fit" else (0, 0, 255))
                        self.assertEqual(list(ctx.work_dir.iterdir()), [])
                        self.assertFalse(list((root / "exports").glob("*.part.mp4")))

    def test_real_portrait_mp4_captions_special_paths_and_silent_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run([
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-filter_threads", "1", "-filter_complex_threads", "1",
                "-threads", "1", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                "-threads", "1", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "1.5", "-c:v", "libx264", "-threads:v", "1", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-threads:a", "1", str(source),
            ], check=True, capture_output=True, timeout=30)
            for resolution, style, has_audio in (("720x1280", "standard", True), ("1080x1920", "animated", True), ("720x1280", "none", False)):
                with self.subTest(resolution=resolution, style=style, has_audio=has_audio):
                    project = {"source_path": str(source), "metadata": {"duration": 1.5, "audio_streams": [{"index": 1}] if has_audio else []}}
                    highlight = {"start": 0.5, "end": 1.2, "transcript": [{
                        "start": 0.1, "end": 0.5, "text": "Real caption", "words": [
                            {"start": 0.1, "end": 0.25, "word": "Real"},
                            {"start": 0.25, "end": 0.5, "word": "caption"},
                        ],
                    }]}
                    ctx = Context(root / "work, [x]; O'Brien")
                    result = render.render_clip(project, highlight, {
                        "output_folder": str(root / "exports"), "output_resolution": resolution,
                        "caption_style": style, "cpu_workers": 2,
                    }, ctx)
                    probe = subprocess.run([
                        self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", result["output_path"],
                    ], check=True, capture_output=True, text=True, timeout=10)
                    metadata = json.loads(probe.stdout)
                    video = next(stream for stream in metadata["streams"] if stream["codec_type"] == "video")
                    audio = next(stream for stream in metadata["streams"] if stream["codec_type"] == "audio")
                    self.assertEqual(f"{video['width']}x{video['height']}", resolution)
                    self.assertEqual(video["codec_name"], "h264")
                    self.assertEqual(video["pix_fmt"], "yuv420p")
                    self.assertEqual(video["avg_frame_rate"], "30/1")
                    self.assertEqual(audio["codec_name"], "aac")
                    self.assertLess(abs(float(metadata["format"]["duration"]) - 0.7), 0.1)
                    self.assertEqual(list(ctx.work_dir.iterdir()), [])
                    self.assertFalse(list((root / "exports").glob("*.part.mp4")))


if __name__ == "__main__":
    unittest.main()
