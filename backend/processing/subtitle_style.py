"""Pure validation shared by saved subtitle styles and direct renderer calls."""

from __future__ import annotations

import math
import re


_DEFAULTS = {
    "font_family": "Arial",
    "font_size": 48,
    "bold": True,
    "text_color": "#FFFFFF",
    "highlight_color": "#00FF00",
    "outline_color": "#141414",
    "outline_width": 3,
    "background_color": "#000000",
    "background_opacity": 0,
    "x": 0.5,
    "y": 0.73,
    "width": 0.84,
}
_RANGES = {
    "font_size": (18, 96),
    "outline_width": (0, 8),
    "background_opacity": (0, 0.85),
    "x": (0, 1),
    "y": (0.05, 0.95),
    "width": (0.2, 1),
}


def normalize_subtitle_style(value: object) -> dict | None:
    """Return a fresh, complete style, or None for the exact legacy renderer.

    Do not coerce strings or booleans into numbers: this also runs on queued
    snapshots and callers that bypass the API's schema validation.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Subtitle style must be an object or null for legacy captions.")
    if any(key not in _DEFAULTS for key in value):
        raise ValueError("Subtitle style contains unsupported fields.")
    style = {**_DEFAULTS, **value}
    if style["font_family"] not in ("Arial", "Impact", "Verdana"):
        raise ValueError("Subtitle font_family must be Arial, Impact or Verdana.")
    if type(style["bold"]) is not bool:
        raise ValueError("Subtitle bold must be a boolean.")
    for name, (minimum, maximum) in _RANGES.items():
        number = style[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise ValueError(f"Subtitle {name} must be a finite number between {minimum} and {maximum}.")
        try:
            number = float(number)
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"Subtitle {name} must be a finite number.") from exc
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"Subtitle {name} must be between {minimum} and {maximum}.")
        style[name] = number
    for name in ("text_color", "highlight_color", "outline_color", "background_color"):
        color = style[name]
        if not isinstance(color, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None:
            raise ValueError(f"Subtitle {name} must be a #RRGGBB hex color.")
        style[name] = color.upper()
    half_width = style["width"] / 2
    if not half_width - 1e-9 <= style["x"] <= 1 - half_width + 1e-9:
        raise ValueError("Subtitle width must fit horizontally around its center x inside the frame.")
    style["x"] = min(1 - half_width, max(half_width, style["x"]))
    return style
