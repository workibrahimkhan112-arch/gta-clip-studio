"""Normalized embedded-camera layout validation and filtergraph contracts."""

from __future__ import annotations

import copy
import math
import unittest
from pathlib import Path

from backend.processing.layout import split_panel_sizes, validate_layout
from backend.processing.render import build_render_command


def split_layout(**changes):
    return {
        "mode": "split",
        "facecam": {"x": 0.1, "y": 0.1, "width": 0.25, "height": 0.5},
        "gameplay": {"x": 0.5, "y": 0, "width": 0.5, "height": 1},
        **changes,
    }


class LayoutValidationTests(unittest.TestCase):
    def test_missing_layout_is_legacy_blurred_and_explicit_split_has_defaults(self):
        self.assertEqual(validate_layout(None)["mode"], "blurred")
        layout = validate_layout({"mode": "split", "facecam": {"x": 0, "y": 0, "width": 1, "height": 1}})
        self.assertEqual(layout["gameplay"], {"x": 0, "y": 0, "width": 1, "height": 1})
        self.assertEqual(layout["split_ratio"], 0.5)
        self.assertEqual(layout["facecam_fit"], "fill")
        self.assertEqual(layout["gameplay_fit"], "fit")
        self.assertEqual(layout["caption_position"], "lower_middle")

    def test_split_requires_explicit_embedded_camera_selection(self):
        for layout in ({}, {"mode": "split"}, split_layout(facecam=None)):
            with self.subTest(layout=layout), self.assertRaisesRegex(ValueError, "Select the embedded streamer camera crop"):
                validate_layout(layout)
        self.assertIsNone(validate_layout({"mode": "blurred"})["facecam"])

    def test_validation_does_not_mutate_or_alias_queued_layout(self):
        original = split_layout()
        saved = copy.deepcopy(original)
        normalized = validate_layout(original)
        self.assertEqual(original, saved)
        normalized["facecam"]["x"] = 0
        self.assertEqual(original, saved)
        first, second = validate_layout(None), validate_layout(None)
        first["gameplay"]["x"] = 0.2
        self.assertEqual(second["gameplay"]["x"], 0)

    def test_rejects_invalid_crops_in_either_panel(self):
        invalid = [None, [], {}, {"x": 0, "y": 0, "width": 0.3}]
        for key, values in {
            "x": (-0.1, 1, math.nan, math.inf, "0", True),
            "y": (-0.1, 1),
            "width": (0, -1, 1.1, 0.95, math.inf),
            "height": (0, -1, 1.1, 0.95, math.nan),
        }.items():
            invalid.extend({"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5, key: value} for value in values)
        for panel in ("facecam", "gameplay"):
            for crop in invalid:
                with self.subTest(panel=panel, crop=crop), self.assertRaises(ValueError):
                    validate_layout(split_layout(**{panel: crop}))

    def test_rejects_invalid_layout_options_before_filter_interpolation(self):
        for invalid in ([], "split", 1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_layout(invalid)
        for key, values in {
            "mode": ("other", None, []),
            "split_ratio": (0.19, 0.71, math.nan, math.inf, "0.5", True, None),
            "facecam_fit": ("stretch", None, "fill;movie=bad"),
            "gameplay_fit": ("stretch", None),
            "caption_position": ("top", None, r"{\pos(0,0)}"),
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_layout(split_layout(**{key: value}))

    def test_normalized_edges_and_float_roundoff_are_accepted(self):
        crop = {"x": 0.9, "y": 0.8, "width": 0.1, "height": 0.2}
        self.assertEqual(validate_layout(split_layout(facecam=crop))["facecam"], crop)

    def test_even_panels_cover_both_supported_outputs_exactly(self):
        for width, height in ((720, 1280), (1080, 1920)):
            self.assertEqual(split_panel_sizes(width, height, 0.5), ((width, height // 2), (width, height // 2)))
            for ratio in (0.2, 0.3333, 0.5007, 0.7):
                with self.subTest(resolution=(width, height), ratio=ratio):
                    top, bottom = split_panel_sizes(width, height, ratio)
                    self.assertEqual(top[0], bottom[0])
                    self.assertEqual(top[1] + bottom[1], height)
                    self.assertLessEqual(abs(top[1] - height * ratio), 1)
                    self.assertTrue(all(value > 0 and value % 2 == 0 for value in (*top, *bottom)))
        for size in ((719, 1280), (720, 1279), (720, 2), (0, 1280)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                split_panel_sizes(*size, 0.5)


class SplitCommandTests(unittest.TestCase):
    def command(self, layout=None, **options):
        return build_render_command(
            "ffmpeg", Path("embedded camera.mp4"), Path("out.mp4"), 25, 1,
            {"caption_style": "none"}, layout=layout or split_layout(), **options,
        )

    def test_single_source_decode_split_crop_and_top_bottom_order(self):
        command = self.command(video_stream_index=2)
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(command.count("-i"), 1)
        self.assertEqual(command.count("embedded camera.mp4"), 1)
        self.assertIn("[0:2]fps=30:start_time=0,split=2[facecam_in][gameplay_in]", graph)
        self.assertEqual(graph.count("exact=1"), 2)
        self.assertIn("round(iw*0.25)", graph)
        self.assertIn("min(iw-ow,round(iw*", graph)
        self.assertIn("round(ih*0.5)", graph)
        self.assertIn("[facecam][gameplay]vstack=inputs=2:shortest=1", graph)
        self.assertNotIn("boxblur", graph)
        self.assertNotIn("setpts", graph)
        self.assertTrue(graph.endswith("setsar=1,format=yuv420p[video]"))
        self.assertLess(command.index("-ss"), command.index("-i"))
        self.assertLess(command.index("-t"), command.index("-i"))

    def test_fit_and_fill_use_cropped_display_aspect_ratio(self):
        for facecam_fit, gameplay_fit in (("fill", "fit"), ("fit", "fill")):
            command = self.command(split_layout(facecam_fit=facecam_fit, gameplay_fit=gameplay_fit))
            graph = command[command.index("-filter_complex") + 1]
            for name, fit in (("facecam", facecam_fit), ("gameplay", gameplay_fit)):
                panel = graph.split(f"[{name}_in]", 2)[-1].split(f"[{name}]", 1)[0]
                with self.subTest(panel=name, fit=fit):
                    self.assertIn("dar", panel)
                    self.assertIn("setsar=1", panel)
                    self.assertIn("min(720,640*dar)" if fit == "fit" else "ceil(640*dar/2)*2", panel)
                    self.assertIn("pad=720:640" if fit == "fit" else "crop=720:640", panel)
                    self.assertLess(panel.index("exact=1"), panel.index("scale="))

    def test_explicit_blurred_layout_is_identical_to_legacy_command(self):
        args = ("ffmpeg", Path("source.mp4"), Path("out.mp4"), 0, 1, {})
        self.assertEqual(build_render_command(*args), build_render_command(*args, layout={"mode": "blurred"}))

    def test_missing_camera_raises_in_command_builder(self):
        with self.assertRaisesRegex(ValueError, "camera crop"):
            self.command({"mode": "split", "facecam": None})

    def test_captions_are_applied_after_stacking_and_silence_is_audio_only(self):
        command = self.command(subtitle_path=Path("caption.ass"), has_audio=False)
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("format=yuv420p,ass=filename=caption.ass[video]", graph)
        self.assertEqual(command.count("-i"), 2)
        self.assertIn("anullsrc=r=48000:cl=stereo", command)
        self.assertIn("1:a:0", command)


if __name__ == "__main__":
    unittest.main()
