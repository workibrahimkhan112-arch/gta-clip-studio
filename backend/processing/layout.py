"""Validated normalized source crops and single-decode portrait split filters."""

from __future__ import annotations

import math


_FULL_FRAME = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
CAPTION_POSITIONS = ("lower_middle", "center", "bottom")


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number.")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number


def _crop(value, name: str) -> dict:
    if not isinstance(value, dict) or not all(key in value for key in _FULL_FRAME):
        raise ValueError(f"{name} must contain normalized x, y, width and height coordinates.")
    crop = {key: _number(value[key], f"{name}.{key}") for key in _FULL_FRAME}
    if (
        not 0 <= crop["x"] < 1 or not 0 <= crop["y"] < 1
        or not 0 < crop["width"] <= 1 or not 0 < crop["height"] <= 1
        or crop["x"] + crop["width"] > 1 + 1e-9
        or crop["y"] + crop["height"] > 1 + 1e-9
    ):
        raise ValueError(f"{name} must have positive normalized size and stay inside the source frame.")
    return crop


def validate_layout(layout: object) -> dict:
    """Return independent defaults; absent legacy layouts keep the blurred preset.

    This also guards direct renderer calls, which do not pass through API models.
    An unconfigured split must fail before transcription or an encoder is started.
    """
    if layout is None:
        layout = {"mode": "blurred"}
    if not isinstance(layout, dict):
        raise ValueError("Layout must be an object or null for the legacy blurred preset.")
    mode = layout.get("mode", "split")
    if mode not in ("split", "blurred"):
        raise ValueError("Layout mode must be split or blurred.")
    facecam = layout.get("facecam")
    if mode == "split" and facecam is None:
        raise ValueError(
            "Select the embedded streamer camera crop in the project layout before exporting "
            "a split Reel, or choose the blurred layout."
        )
    facecam = _crop(facecam, "Facecam crop") if facecam is not None else None
    gameplay = _crop(layout.get("gameplay", _FULL_FRAME), "Gameplay crop")
    ratio = _number(layout.get("split_ratio", 0.5), "Split ratio")
    if not 0.2 <= ratio <= 0.7:
        raise ValueError("Split ratio must be between 0.2 and 0.7 (the top camera fraction).")
    fits = {key: layout.get(key, default) for key, default in (("facecam_fit", "fill"), ("gameplay_fit", "fit"))}
    if any(value not in ("fit", "fill") for value in fits.values()):
        raise ValueError("Facecam and gameplay sizing must be fit or fill.")
    position = layout.get("caption_position", "lower_middle")
    if position not in CAPTION_POSITIONS:
        raise ValueError("Caption position must be lower_middle, center or bottom.")
    return {
        "mode": mode, "facecam": facecam, "gameplay": gameplay, "split_ratio": ratio,
        **fits, "caption_position": position,
    }


def split_panel_sizes(width: int, height: int, ratio: float) -> tuple[tuple[int, int], tuple[int, int]]:
    """Round the top height to the nearest even pixel; never lose a bottom row."""
    if any(type(value) is not int or value < 2 or value % 2 for value in (width, height)) or height < 4:
        raise ValueError("Split output dimensions must be even and leave room for both panels.")
    ratio = _number(ratio, "Split ratio")
    if not 0.2 <= ratio <= 0.7:
        raise ValueError("Split ratio must be between 0.2 and 0.7.")
    top_height = max(2, min(height - 2, math.floor(height * ratio / 2 + 0.5) * 2))
    return (width, top_height), (width, height - top_height)


def _crop_filter(crop: dict) -> str:
    # Preserve sub-chroma normalized selections, then scale to even output sizes.
    # Clamping the origin accounts for independent pixel rounding at the edges.
    return (
        f"crop=w='max(1,round(iw*{crop['width']:.17g}))':"
        f"h='max(1,round(ih*{crop['height']:.17g}))':"
        f"x='min(iw-ow,round(iw*{crop['x']:.17g}))':"
        f"y='min(ih-oh,round(ih*{crop['y']:.17g}))':exact=1"
    )


def _panel_filter(crop: dict, size: tuple[int, int], fit: str) -> str:
    width, height = size
    if fit == "fit":
        sizing = (
            f"scale=w='max(2,trunc(min({width},{height}*dar)/2)*2)':"
            f"h='max(2,trunc(min({height},{width}/dar)/2)*2)':flags=bicubic,"
            f"setsar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    else:
        sizing = (
            f"scale=w='max({width},ceil({height}*dar/2)*2)':"
            f"h='max({height},ceil({width}/dar/2)*2)':flags=bicubic,"
            f"setsar=1,crop={width}:{height}"
        )
    return f"{_crop_filter(crop)},{sizing}"


def build_split_graph(video_input: str, width: int, height: int, layout: dict) -> str:
    """Build a graph ending in an unlabeled output, ready for caption filters."""
    top, bottom = split_panel_sizes(width, height, layout["split_ratio"])
    return (
        f"[{video_input}]fps=30:start_time=0,split=2[facecam_in][gameplay_in];"
        f"[facecam_in]{_panel_filter(layout['facecam'], top, layout['facecam_fit'])}[facecam];"
        f"[gameplay_in]{_panel_filter(layout['gameplay'], bottom, layout['gameplay_fit'])}[gameplay];"
        "[facecam][gameplay]vstack=inputs=2:shortest=1,setsar=1,format=yuv420p"
    )
