"""Saved subtitle styles: pure guards, legacy compatibility, and tiny pixel tests."""

from __future__ import annotations

import math
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.processing import render
from backend.processing.subtitle_style import normalize_subtitle_style
from backend.processing.subtitles import build_ass, escape_filter_filename


def transcript():
    return [{
        "start": 0, "end": 1, "text": "mellow river", "words": [
            {"start": 0, "end": 0.4, "word": "mellow"},
            {"start": 0.6, "end": 1, "word": "river"},
        ],
    }]


def foreground_events(content):
    return [line for line in content.splitlines() if line.startswith("Dialogue:") and ",Caption," in line]


def ass_style(content, name="Caption"):
    return next(line.split(": ", 1)[1].split(",") for line in content.splitlines() if line.startswith(f"Style: {name},"))


class SubtitleStyleValidationTests(unittest.TestCase):
    def test_none_is_legacy_but_empty_object_supplies_all_defaults(self):
        self.assertIsNone(normalize_subtitle_style(None))
        expected = {
            "font_family": "Arial", "font_size": 48, "bold": True,
            "text_color": "#FFFFFF", "highlight_color": "#00FF00",
            "outline_color": "#141414", "outline_width": 3,
            "background_color": "#000000", "background_opacity": 0,
            "x": 0.5, "y": 0.73, "width": 0.84,
        }
        self.assertEqual(normalize_subtitle_style({}), expected)
        first = normalize_subtitle_style({})
        assert first is not None
        first["text_color"] = "changed"
        self.assertEqual(normalize_subtitle_style({}), expected)

    def test_normalizes_colors_without_mutating_input_and_is_idempotent(self):
        source = {"font_family": "Impact", "text_color": "#a1b2c3", "bold": False, "outline_width": 0}
        result = normalize_subtitle_style(source)
        assert result is not None
        self.assertEqual(result["text_color"], "#A1B2C3")
        self.assertEqual(source["text_color"], "#a1b2c3")
        self.assertFalse(result["bold"])
        self.assertEqual(result["outline_width"], 0)
        self.assertEqual(normalize_subtitle_style(result), result)

    def test_rejects_non_objects_unknown_fields_and_injection(self):
        invalid = [[], "Arial", True, 1, {"unexpected": 1}, {"font_family": []},
                   {"font_family": "Arial,96\nStyle: injected"},
                   {"font_family": r"Arial{\pos(0,0)}"}, {"font_family": "arial"}]
        for field in ("text_color", "highlight_color", "outline_color", "background_color"):
            for color in (None, 123, "red", "#FFF", "#00000000", "#123456\n", r"#FFFFFF{\p1}", "#12GG00"):
                invalid.append({field: color})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_subtitle_style(value)

    def test_numeric_fields_are_strict_finite_and_bounded(self):
        ranges = {
            "font_size": (18, 96), "outline_width": (0, 8),
            "background_opacity": (0, 0.85), "x": (0, 1),
            "y": (0.05, 0.95), "width": (0.2, 1),
        }
        for field, (minimum, maximum) in ranges.items():
            for value in (None, True, False, "0.5", [], math.nan, math.inf, -math.inf,
                          10 ** 1000, minimum - 0.001, maximum + 0.001):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    normalize_subtitle_style({field: value})
        for value in (0, 1, "true", None):
            with self.subTest(bold=value), self.assertRaises(ValueError):
                normalize_subtitle_style({"bold": value})

    def test_horizontal_containment_and_valid_endpoints(self):
        for x in (0.199, 0.801):
            with self.subTest(x=x), self.assertRaisesRegex(ValueError, "horizontally"):
                normalize_subtitle_style({"x": x, "width": 0.4})
        for x in (0.2, 0.8):
            result = normalize_subtitle_style({"x": x, "width": 0.4})
            assert result is not None
            self.assertEqual(result["x"], x)
        for font in ("Arial", "Impact", "Verdana"):
            result = normalize_subtitle_style({
                "font_family": font, "font_size": 96, "outline_width": 8,
                "background_opacity": 0.85, "width": 1, "x": 0.5, "y": 0.95,
            })
            assert result is not None
            self.assertEqual(result["font_family"], font)
        result = normalize_subtitle_style({"font_size": 18, "y": 0.05})
        assert result is not None
        self.assertEqual(result["font_size"], 18)


class SubtitleStyleSchemaContractTests(unittest.TestCase):
    def test_old_patch_and_export_shapes_do_not_opt_in_to_visual_styles(self):
        from backend.models.schemas import ExportRequest, HighlightPatch

        legacy = {"title": "Authored caption", "selected": True, "transcript": transcript()}
        parsed = HighlightPatch.model_validate(legacy)
        self.assertEqual(parsed.model_dump(exclude_unset=True), legacy)
        self.assertIsNone(parsed.subtitle_style)
        self.assertEqual(HighlightPatch().model_dump(exclude_unset=True), {})
        for animation in ("none", "standard", "animated"):
            with self.subTest(animation=animation):
                self.assertEqual(ExportRequest(caption_style=animation).model_dump(exclude_unset=True),
                                 {"caption_style": animation})

    def test_explicit_reset_defaults_and_serialized_saved_style_match_renderer(self):
        from backend.models.schemas import HighlightPatch

        reset = HighlightPatch.model_validate({"subtitle_style": None})
        self.assertEqual(reset.model_dump(exclude_unset=True), {"subtitle_style": None})
        for saved in ({}, {"font_family": "Verdana", "text_color": "#aabbcc", "bold": False,
                              "x": 0.3, "y": 0.2, "width": 0.4}):
            with self.subTest(saved=saved):
                patch = HighlightPatch.model_validate({"subtitle_style": saved})
                snapshot = patch.model_dump(mode="json")["subtitle_style"]
                self.assertEqual(snapshot, normalize_subtitle_style(saved))
                self.assertEqual(build_ass(transcript(), 1, (720, 1280), subtitle_style=snapshot),
                                 build_ass(transcript(), 1, (720, 1280), subtitle_style=saved))


class StyledAssTests(unittest.TestCase):
    def test_none_retains_byte_exact_legacy_document(self):
        source = [{"start": 0.25, "end": 1, "text": "Actual words"}]
        expected = (
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 720\nPlayResY: 1280\n"
            "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Caption,Arial,48,&H00FFFFFF,&H0000FF00,&H00141414,&H80000000,"
            "-1,0,0,0,100,100,0,0,1,3,1,2,58,58,346,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            "Dialogue: 0,0:00:00.25,0:00:01.00,Caption,,0,0,0,,Actual words\n"
        )
        self.assertEqual(build_ass(source, 1, (720, 1280), subtitle_style=None), expected)
        for size in ((720, 1280), (1080, 1920)):
            for animation in ("standard", "animated"):
                for position in ("lower_middle", "center", "bottom"):
                    with self.subTest(size=size, animation=animation, position=position):
                        self.assertEqual(
                            build_ass(transcript(), 1, size, animation, position),
                            build_ass(transcript(), 1, size, animation, position, subtitle_style=None),
                        )

    def test_saved_font_colors_weight_outline_position_and_720_scaling(self):
        saved = {
            "font_family": "Verdana", "font_size": 36.5, "bold": False,
            "text_color": "#123456", "highlight_color": "#aabbcc",
            "outline_color": "#987654", "outline_width": 2.5,
            "background_color": "#112233", "background_opacity": 0.5,
            "x": 0.35, "y": 0.3, "width": 0.4,
        }
        source = [{"start": 0, "end": 1, "text": "wow mellow"}]
        for width, height in ((720, 1280), (1080, 1920)):
            with self.subTest(width=width):
                scale = width / 720
                content = build_ass(source, 1, (width, height), subtitle_style=saved)
                fields = ass_style(content)
                self.assertEqual(fields[1:8], ["Verdana", f"{36.5 * scale:g}", "&H00563412",
                                              "&H00CCBBAA", "&H00547698", "&H80332211", "0"])
                self.assertEqual(fields[15:19], ["1", f"{2.5 * scale:g}", "0", "5"])
                self.assertEqual(int(fields[19]), math.ceil((0.35 - 0.4 / 2) * width + 3 * scale))
                self.assertIn(r"{\c&HCCBBAA&}wow{\c&H563412&}", content)
                self.assertIn(rf"\an5\pos({0.35 * width:.4f},{0.3 * height:.4f})\q0", content)
                box = ass_style(content, "Background")
                self.assertEqual(box[3], "&HFF563412")
                self.assertEqual(box[5], "&H80332211")
                self.assertEqual(box[15], "3")
                self.assertEqual(content.count("Dialogue:"), 2)
                self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.00,Background", content)
                self.assertIn("Dialogue: 1,0:00:00.00,0:00:01.00,Caption", content)

    def test_translucent_background_does_not_inherit_word_color_runs(self):
        for animation in ("standard", "animated"):
            with self.subTest(animation=animation):
                content = build_ass(transcript(), 1, (720, 1280), animation, subtitle_style={
                    "background_opacity": 0.5, "text_color": "#FF0000", "highlight_color": "#0000FF",
                })
                boxes = [line for line in content.splitlines()
                         if line.startswith("Dialogue:") and ",Background," in line]
                foreground = foreground_events(content)
                self.assertEqual(len(boxes), len(foreground))
                for box, caption in zip(boxes, foreground):
                    self.assertEqual(box.split(",")[1:3], caption.split(",")[1:3])
                    self.assertNotIn(r"\c", box)
                    self.assertTrue(box.endswith(r"\q0}mellow river"))

    def test_custom_anchor_overrides_legacy_position_and_no_background_means_no_box(self):
        first = build_ass(transcript(), 1, (720, 1280), position="bottom", subtitle_style={})
        second = build_ass(transcript(), 1, (720, 1280), position="center", subtitle_style={})
        self.assertEqual(first, second)
        self.assertIn(r"\an5\pos(360.0000,934.4000)", first)
        self.assertNotIn("Style: Background", first)
        self.assertEqual(ass_style(first)[16:19], ["3", "0", "5"])

    def test_both_modes_use_width_and_font_size_aware_groups_without_new_timestamps(self):
        words = [
            {"start": index / 10, "end": (index + 1) / 10, "word": "mellow"}
            for index in range(8)
        ]
        source = [{"start": 0, "end": 0.8, "text": " ".join(word["word"] for word in words), "words": words}]
        actual_boundaries = {f"0:00:00.{index * 10:02d}" for index in range(9)}
        for animation in ("standard", "animated"):
            with self.subTest(animation=animation):
                wide = build_ass(source, 1, (720, 1280), animation, subtitle_style={"width": 1, "font_size": 18})
                narrow = build_ass(source, 1, (720, 1280), animation, subtitle_style={"width": 0.2, "font_size": 96})
                self.assertLess(max(line.count("mellow") for line in foreground_events(narrow)),
                                max(line.count("mellow") for line in foreground_events(wide)))
                for line in foreground_events(narrow):
                    self.assertIn(line.split(",")[1], actual_boundaries)
                    self.assertIn(line.split(",")[2], actual_boundaries)

    def test_unspaced_words_wrap_without_fabricating_segment_or_word_times(self):
        text = "mellow" * 12
        source = [{"start": 0.13, "end": 0.97, "text": text}]
        content = build_ass(source, 1, (720, 1280), subtitle_style={"width": 0.2, "font_size": 96})
        events = foreground_events(content)
        self.assertEqual(len(events), 1)
        self.assertIn("0:00:00.13,0:00:00.97", events[0])
        visible = events[0].split(",,", 2)[-1]
        visible = re.sub(r"\{[^}]*\}", "", visible).replace(r"\N", "")
        self.assertEqual(visible, text)
        with self.assertRaisesRegex(ValueError, "real word-level timestamps"):
            build_ass(source, 1, (720, 1280), "animated", subtitle_style={})

    def test_animated_uses_saved_highlight_and_returns_to_saved_primary_during_gap(self):
        content = build_ass(transcript(), 1, (720, 1280), "animated", subtitle_style={
            "text_color": "#FF0000", "highlight_color": "#0000FF",
        })
        events = foreground_events(content)
        self.assertEqual(len(events), 3)
        self.assertIn(r"{\c&HFF0000&}mellow{\c&H0000FF&} river", events[0])
        self.assertIn("0:00:00.40,0:00:00.60", events[1])
        self.assertNotIn(r"\c", events[1])
        self.assertIn(r"mellow {\c&HFF0000&}river{\c&H0000FF&}", events[2])

    def test_custom_style_dimensions_cannot_inject_ass_headers(self):
        for size in (("720\n[Events]", 1280), (720, "1280\nStyle: injected"),
                     (True, 1280), (720.0, 1280), (0, 1280), (720, -1), (720, math.inf)):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "positive integers"):
                build_ass(transcript(), 1, size, subtitle_style={})

    def test_direct_ass_validation_and_text_injection_guards(self):
        for saved in ({"font_family": "Arial\n[Events]"}, {"text_color": r"#FFFFFF{\p1}"},
                      {"font_size": math.nan}, {"x": 0}, {"width": 1.1}):
            with self.subTest(saved=saved), self.assertRaises(ValueError):
                build_ass(transcript(), 1, (720, 1280), subtitle_style=saved)
        source = [{"start": 0, "end": 1, "text": "safe", "words": [
            {"start": 0, "end": 1, "word": "{\\pos(0,0)}bad\nDialogue: injection"},
        ]}]
        for animation in ("standard", "animated"):
            content = build_ass(source, 1, (720, 1280), animation, subtitle_style={})
            self.assertEqual(len(foreground_events(content)), 1)
            self.assertNotIn(r"{\pos(0,0)}", content)
            self.assertIn("｛＼pos(0,0)｝", content)


class RenderSubtitleStyleTests(unittest.TestCase):
    def test_invalid_saved_style_fails_before_model_binary_or_directory_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ctx = Mock(work_dir=root / "work")
            project = {"source_path": str(root / "missing.mp4"), "metadata": {"duration": 1}}
            for caption_style in ("none", "standard", "animated"):
                with self.subTest(caption_style=caption_style), \
                        patch.object(render, "get_binary") as binary, \
                        patch.object(render, "transcribe_clip") as recognize, \
                        patch.object(render, "run_ffmpeg") as encode:
                    with self.assertRaisesRegex(ValueError, "font_family"):
                        render.render_clip(project, {"start": 0, "end": 1, "subtitle_style": {
                            "font_family": "Arial,garbage",
                        }}, {"caption_style": caption_style, "output_folder": str(root / "out")}, ctx)
                    binary.assert_not_called()
                    recognize.assert_not_called()
                    encode.assert_not_called()
                    self.assertFalse((root / "work").exists())
                    self.assertFalse((root / "out").exists())

    def test_old_highlights_and_explicit_null_export_identical_legacy_ass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"mock source")
            project = {"source_path": str(source), "metadata": {"duration": 1, "audio_streams": []},
                       "layout": {"mode": "blurred", "caption_position": "center"}}
            highlight = {"start": 0, "end": 1, "transcript": transcript()}
            ctx = Mock(work_dir=root / "work", id="legacy-style-job")
            captured = []

            def encode(command, *_args):
                captured.append(next(ctx.work_dir.glob("render-*/captions.ass")).read_text(encoding="utf-8"))
                Path(command[-1]).write_bytes(b"mock mp4")

            for animation in ("standard", "animated"):
                for saved in ({}, {"subtitle_style": None}):
                    with self.subTest(animation=animation, saved=saved), \
                            patch.object(render, "get_binary", return_value="ffmpeg"), \
                            patch.object(render, "transcribe_clip") as recognize, \
                            patch.object(render, "run_ffmpeg", side_effect=encode):
                        render.render_clip(project, {**highlight, **saved}, {
                            "caption_style": animation, "output_folder": str(root / "exports"),
                        }, ctx)
                        recognize.assert_not_called()
                        self.assertEqual(captured[-1], build_ass(transcript(), 1, (720, 1280),
                                                                animation, position="center"))
                        self.assertNotIn(r"\pos(", captured[-1])
                self.assertEqual(captured[-2], captured[-1])

    def test_render_passes_normalized_highlight_style_but_none_captions_stay_disabled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            source.write_bytes(b"mock source")
            project = {"source_path": str(source), "metadata": {"duration": 1, "audio_streams": []},
                       "layout": {"mode": "blurred", "caption_position": "bottom"}}
            saved = {"font_family": "Impact", "text_color": "#aabbcc", "x": 0.3, "width": 0.4, "y": 0.2}
            highlight = {"start": 0, "end": 1, "transcript": transcript(), "subtitle_style": saved}
            ctx = Mock(work_dir=root / "work", id="style-job")

            def encode(command, *_args):
                if settings["caption_style"] != "none":
                    content = next(ctx.work_dir.glob("render-*/captions.ass")).read_text(encoding="utf-8")
                    self.assertIn("Style: Caption,Impact,48,&H00CCBBAA", content)
                    self.assertIn(r"\pos(216.0000,256.0000)", content)
                Path(command[-1]).write_bytes(b"mock mp4")

            for animation in ("standard", "animated", "none"):
                settings = {"caption_style": animation, "output_folder": str(root / "exports")}
                with self.subTest(animation=animation), \
                        patch.object(render, "get_binary", return_value="ffmpeg"), \
                        patch.object(render, "transcribe_clip") as recognize, \
                        patch.object(render, "build_ass", wraps=build_ass) as build, \
                        patch.object(render, "run_ffmpeg", side_effect=encode):
                    render.render_clip(project, highlight, settings, ctx)
                    recognize.assert_not_called()
                    if animation == "none":
                        build.assert_not_called()
                    else:
                        self.assertEqual(build.call_args.kwargs["subtitle_style"], normalize_subtitle_style(saved))
                        self.assertEqual(build.call_args.kwargs["position"], "bottom")
            self.assertEqual(saved["text_color"], "#aabbcc")


class SubtitleStylePixelTests(unittest.TestCase):
    """Sample authored one-second sources with single-thread FFmpeg/libass."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = render.get_binary("ffmpeg")
            subprocess.run([cls.ffmpeg, "-version"], check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise unittest.SkipTest(f"Local FFmpeg unavailable: {exc}") from exc

    def frame(self, content, size=(720, 1280), at=0.2):
        import numpy as np

        width, height = size
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "authored captions.ass"
            path.write_text(content, encoding="utf-8")
            result = subprocess.run([
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-filter_threads", "1", "-filter_complex_threads", "1", "-threads", "1",
                "-f", "lavfi", "-i", f"color=black:size={width}x{height}:rate=10:duration=1",
                "-vf", f"select=gte(t\\,{at}),ass=filename={escape_filter_filename(path)}",
                "-frames:v", "1", "-an", "-pix_fmt", "rgb24", "-threads:v", "1",
                "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1",
            ], check=True, capture_output=True, timeout=15)
        self.assertEqual(len(result.stdout), width * height * 3)
        return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width, 3)

    def bounds(self, mask):
        import numpy as np

        ys, xs = np.nonzero(mask)
        self.assertGreater(len(xs), 0, "Expected authored caption pixels, not a blank frame")
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1

    @staticmethod
    def red(frame):
        return (frame[:, :, 0] > 150) & (frame[:, :, 1] < 60) & (frame[:, :, 2] < 60)

    @staticmethod
    def blue(frame):
        return (frame[:, :, 2] > 120) & (frame[:, :, 0] < 60) & (frame[:, :, 1] < 60)

    def test_authored_text_actual_center_width_wrapping_and_1080_scaling(self):
        source = [{"start": 0, "end": 1, "text": "mellow river willow"}]
        saved = {"font_size": 60, "bold": False, "outline_width": 0,
                 "text_color": "#FF0000", "x": 0.42, "y": 0.31}
        results = {}
        for size, box_width in (((720, 1280), 0.28), ((720, 1280), 0.84), ((1080, 1920), 0.28)):
            with self.subTest(size=size, box_width=box_width):
                content = build_ass(source, 1, size, subtitle_style={**saved, "width": box_width})
                frame = self.frame(content, size)
                x0, y0, x1, y1 = self.bounds(self.red(frame))
                width, height = size
                self.assertLessEqual(x1 - x0, width * box_width + 4)
                self.assertGreaterEqual(x0, (saved["x"] - box_width / 2) * width - 3)
                self.assertLessEqual(x1, (saved["x"] + box_width / 2) * width + 3)
                self.assertAlmostEqual((x0 + x1) / 2, saved["x"] * width, delta=5)
                self.assertAlmostEqual((y0 + y1) / 2, saved["y"] * height, delta=60 * width / 720 * 0.4)
                results[(size[0], box_width)] = (x1 - x0, y1 - y0)
        self.assertGreater(results[(720, 0.28)][1], results[(720, 0.84)][1] * 1.5)
        for scaled, base in zip(results[(1080, 0.28)], results[(720, 0.28)]):
            self.assertAlmostEqual(scaled / base, 1.5, delta=0.12)

    def test_wide_unspaced_glyphs_stay_inside_all_font_boxes_at_frame_edges(self):
        source = [{"start": 0, "end": 1, "text": "mmmmwwww"}]
        for font, x in (("Arial", 0.1), ("Impact", 0.5), ("Verdana", 0.9)):
            with self.subTest(font=font, x=x):
                content = build_ass(source, 1, (720, 1280), subtitle_style={
                    "font_family": font, "font_size": 96, "bold": True,
                    "text_color": "#FF0000", "outline_width": 8, "outline_color": "#0000FF",
                    "background_color": "#00FF00", "background_opacity": 0.5,
                    "x": x, "y": 0.5, "width": 0.2,
                })
                frame = self.frame(content)
                x0, _, x1, _ = self.bounds(frame.max(axis=2) > 40)
                self.assertGreaterEqual(x0, (x - 0.1) * 720 - 2)
                self.assertLessEqual(x1, (x + 0.1) * 720 + 2)
                self.assertAlmostEqual((x0 + x1) / 2, x * 720, delta=5)

    def test_authored_text_saved_bold_outline_and_translucent_background_pixels(self):
        source = [{"start": 0, "end": 1, "text": "mellow"}]
        saved = {"font_size": 64, "text_color": "#FF0000", "outline_width": 0, "bold": False,
                 "x": 0.5, "y": 0.3}
        regular = self.frame(build_ass(source, 1, (720, 1280), subtitle_style=saved))
        bold = self.frame(build_ass(source, 1, (720, 1280), subtitle_style={**saved, "bold": True}))
        styled = self.frame(build_ass(source, 1, (720, 1280), subtitle_style={
            **saved, "outline_color": "#0000FF", "outline_width": 5,
            "background_color": "#00FF00", "background_opacity": 0.5,
        }))
        self.assertGreater(int(self.red(bold).sum()), int(self.red(regular).sum()) * 1.05)
        self.assertGreater(int(self.red(styled).sum()), 100)
        self.assertGreater(int(self.blue(styled).sum()), 100)
        green = ((styled[:, :, 1] > 80) & (styled[:, :, 1] < 160)
                 & (styled[:, :, 0] < 40) & (styled[:, :, 2] < 40))
        self.assertGreater(int(green.sum()), 100)
        x0, y0, x1, y1 = self.bounds(green | self.blue(styled) | self.red(styled))
        self.assertLess(x1 - x0, 720 * 0.84)
        self.assertAlmostEqual((x0 + x1) / 2, 360, delta=5)
        self.assertAlmostEqual((y0 + y1) / 2, 384, delta=25)

    def test_actual_one_second_export_uses_saved_style_not_legacy_layout_anchor(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "authored black.mp4"
            subprocess.run([
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-filter_threads", "1", "-filter_complex_threads", "1", "-threads", "1",
                "-f", "lavfi", "-i", "color=black:size=320x180:rate=10:duration=1",
                "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-threads:v", "1", str(source),
            ], check=True, capture_output=True, timeout=15)
            saved = {"font_family": "Impact", "font_size": 60, "bold": False,
                     "text_color": "#FF0000", "outline_color": "#0000FF", "outline_width": 2,
                     "background_color": "#00FF00", "background_opacity": 0.5,
                     "x": 0.25, "y": 0.25, "width": 0.3}
            project = {"source_path": str(source), "metadata": {"duration": 1, "audio_streams": []},
                       "layout": {"mode": "blurred", "caption_position": "bottom"}}
            highlight = {"start": 0, "end": 1, "subtitle_style": saved, "transcript": [
                {"start": 0, "end": 1, "text": "mellow river willow"},
            ]}
            ctx = Mock(work_dir=root / "work", id="subtitle-style-pixels")
            output = render.render_clip(project, highlight, {
                "caption_style": "standard", "cpu_workers": 1,
                "output_resolution": "720x1280", "output_folder": str(root / "exports"),
            }, ctx)
            decoded = subprocess.run([
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                "-filter_threads", "1", "-threads", "1", "-ss", "0.2", "-i", output["output_path"],
                "-frames:v", "1", "-an", "-pix_fmt", "rgb24", "-threads:v", "1",
                "-f", "rawvideo", "pipe:1",
            ], check=True, capture_output=True, timeout=15)
            self.assertEqual(len(decoded.stdout), 720 * 1280 * 3)
            frame = np.frombuffer(decoded.stdout, dtype=np.uint8).reshape(1280, 720, 3)
            x0, y0, x1, y1 = self.bounds(self.red(frame))
            self.assertGreater(int(self.red(frame).sum()), 100)
            self.assertGreater(int(self.blue(frame).sum()), 100)
            green = ((frame[:, :, 1] > 70) & (frame[:, :, 1] < 170)
                     & (frame[:, :, 0] < 60) & (frame[:, :, 2] < 60))
            self.assertGreater(int(green.sum()), 100)
            self.assertGreaterEqual(x0, (0.25 - 0.15) * 720 - 3)
            self.assertLessEqual(x1, (0.25 + 0.15) * 720 + 3)
            self.assertAlmostEqual((x0 + x1) / 2, 180, delta=6)
            self.assertAlmostEqual((y0 + y1) / 2, 320, delta=25)
            self.assertFalse(list(ctx.work_dir.iterdir()))

    def test_animated_real_word_highlight_pixels_and_unhighlighted_gap(self):
        import numpy as np

        content = build_ass(transcript(), 1, (720, 1280), "animated", subtitle_style={
            "font_size": 64, "text_color": "#FF0000", "highlight_color": "#0000FF",
            "outline_width": 0, "x": 0.5, "y": 0.3,
        })
        first = self.frame(content, at=0.2)
        gap = self.frame(content, at=0.5)
        second = self.frame(content, at=0.7)
        self.assertGreater(int(self.blue(first).sum()), 100)
        self.assertGreater(int(self.blue(second).sum()), 100)
        self.assertEqual(int(self.blue(gap).sum()), 0)
        self.assertGreater(int(self.red(gap).sum()), 100)
        self.assertLess(float(np.nonzero(self.blue(first))[1].mean()),
                        float(np.nonzero(self.blue(second))[1].mean()))


if __name__ == "__main__":
    unittest.main()
