"""ASS captions from clip-relative transcript timestamps, never inferred timings."""

from __future__ import annotations

import math
import os
import re
import textwrap
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .layout import CAPTION_POSITIONS
from .subtitle_style import normalize_subtitle_style


LIME = "&H0000FF00"
WHITE = "&H00FFFFFF"
_MAX_LINE = 28
_MAX_WORDS = 10


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str
    words: tuple[Word, ...]


def escape_ass_text(text: str) -> str:
    """Neutralize override blocks and escapes, including user-supplied \\N.

    Literal brace/backslash escaping differs between ASS renderers. Fullwidth
    lookalikes preserve readability without letting transcript text become ASS
    instructions. Only newlines introduced here become actual ASS line breaks.
    """
    text = text.replace("\\", "＼").replace("{", "｛").replace("}", "｝")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(character for character in text if character in "\n\t" or ord(character) >= 32)
    text = text.replace("\u2028", "\n").replace("\u2029", "\n")
    return text.replace("\t", " ").replace("\n", r"\N")


def escape_filter_filename(path: str | Path) -> str:
    """Escape both FFmpeg option and filtergraph parsing, without shell quoting.

    Backslashes in Windows drive/UNC paths are normalized first. Colons (drive
    letters), apostrophes, commas, brackets and semicolons must survive *both*
    parsers. The returned value is used unquoted after ``ass=filename=``.
    """
    value = str(path)
    if os.name == "nt" or re.match(r"^[a-zA-Z]:[\\/]", value) or value.startswith("\\\\"):
        value = value.replace("\\", "/")
    if any(ord(character) < 32 for character in value):
        raise ValueError("The subtitle work directory cannot contain control characters.")

    def escape(value: str, special: str) -> str:
        return "".join("\\" + character if character in special else character for character in value)

    option = escape(value, "\\': ")
    return escape(option, "\\'[],; ")


def ass_timestamp(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("Subtitle times must be finite and nonnegative.")
    centiseconds = int(seconds * 100 + 0.5)
    hours, remaining = divmod(centiseconds, 360_000)
    minutes, remaining = divmod(remaining, 6000)
    whole_seconds, fraction = divmod(remaining, 100)
    return f"{hours}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"


def _interval(item: dict, duration: float) -> tuple[float, float] | None:
    try:
        start, end = float(item["start"]), float(item["end"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(start) or not math.isfinite(end):
        return None
    start, end = max(0.0, start), min(duration, end)
    return (start, end) if end > start else None


def transcript_cues(transcript: list[dict], duration: float) -> list[Cue]:
    """Clip, but do not offset, the contract's already-relative timestamps."""
    cues = []
    if not isinstance(transcript, list):
        return cues
    for segment in transcript:
        if not isinstance(segment, dict):
            continue
        interval = _interval(segment, duration)
        if interval is None:
            continue
        start, end = interval
        words = []
        raw_words = segment.get("words") or []
        if isinstance(raw_words, list):
            for item in raw_words:
                if not isinstance(item, dict) or not isinstance(item.get("word"), str):
                    continue
                timing = _interval(item, duration)
                text = item["word"].strip()
                if timing is None or not text:
                    continue
                word_start, word_end = max(start, timing[0]), min(end, timing[1])
                if word_end > word_start:
                    words.append(Word(word_start, word_end, text))
        words.sort(key=lambda word: (word.start, word.end))
        text = segment.get("text", "")
        text = text.strip() if isinstance(text, str) else ""
        if not text and words:
            text = " ".join(word.text for word in words)
        if text:
            cues.append(Cue(start, end, text, tuple(words)))
    return sorted(cues, key=lambda cue: (cue.start, cue.end))


def _word_groups(words: tuple[Word, ...]) -> list[list[Word]]:
    groups: list[list[Word]] = []
    for word in words:
        if not groups:
            groups.append([])
        group = groups[-1]
        characters = sum(len(item.text) + 1 for item in group) + len(word.text)
        if group and (len(group) >= _MAX_WORDS or characters > _MAX_LINE * 2 or word.start - group[-1].end > 0.8):
            groups.append([])
        groups[-1].append(word)
    return groups


def _emphasized(text: str) -> bool:
    # Typography only: emphasize words present in the supplied transcript,
    # never claim that a semantic gameplay event was recognized.
    token = text.strip(".,!?;:\"'()[]").lower()
    return token in {"wow", "what", "no", "run", "stop", "crazy", "insane", "look", "help", "boom"} or (len(text) > 2 and text.isupper())


def _standard_text(text: str) -> str:
    wrapped = "\n".join(textwrap.wrap(text, width=_MAX_LINE, break_long_words=False))
    return "".join(
        (r"{\c&H00FF00&}" + escape_ass_text(token) + r"{\c&HFFFFFF&}")
        if not token.isspace() and _emphasized(token) else escape_ass_text(token)
        for token in re.split(r"(\s+)", wrapped)
    )


def _word_line(words: list[Word], active: int | None = None, emphasize: bool = False) -> str:
    parts = []
    line_length = 0
    for index, word in enumerate(words):
        length = len(word.text)
        if index:
            if line_length + 1 + length > _MAX_LINE:
                parts.append(r"\N")
                line_length = 0
            else:
                parts.append(" ")
                line_length += 1
        text = escape_ass_text(word.text)
        if index == active or (emphasize and _emphasized(word.text)):
            text = r"{\c&H00FF00&}" + text + r"{\c&HFFFFFF&}"
        parts.append(text)
        line_length += length
    return "".join(parts)


def _dialogue(start: float, end: float, text: str) -> str | None:
    first, last = ass_timestamp(start), ass_timestamp(end)
    if first == last:
        return None
    return f"Dialogue: 0,{first},{last},Caption,,0,0,0,,{text}"


def _ass_rgb(color: str) -> str:
    return color[5:7] + color[3:5] + color[1:3]


def _glyph_units(character: str) -> float:
    # Only a grouping/long-token budget. Libass shapes the selected font and
    # performs the actual width-constrained wrapping, not this approximation.
    if unicodedata.combining(character):
        return 0
    if character.isspace() or character in "il.,:;'!|":
        return 0.4
    if ord(character) > 127 or character in "MWmw@%&＼｛｝":
        return 1.1
    return 0.9 if character.isupper() else 0.7


def _styled_token(text: str, capacity: float) -> str:
    # ASS does not reliably break an overlong unspaced word. Insert only visual
    # line breaks (never new events/timestamps), after escaping authored text.
    lines = []
    for line in escape_ass_text(text).split(r"\N"):
        parts = []
        used = 0.0
        for character in line:
            units = _glyph_units(character)
            if used and used + units > capacity:
                parts.append(r"\N")
                used = 0.0
            parts.append(character)
            used += units
        lines.append("".join(parts))
    return r"\N".join(lines)


def _styled_word_groups(words: tuple[Word, ...], capacity: float) -> list[list[Word]]:
    groups: list[list[Word]] = []
    used = 0.0
    for word in words:
        units = sum(_glyph_units(character) for character in word.text)
        if not groups or (
            groups[-1] and (
                len(groups[-1]) >= _MAX_WORDS or used + 0.35 + units > capacity * 2
                or word.start - groups[-1][-1].end > 0.8
            )
        ):
            groups.append([])
            used = 0.0
        used += (0.35 if groups[-1] else 0) + units
        groups[-1].append(word)
    return groups


def _styled_text(
    text: str, capacity: float, primary: str, highlight: str,
    *, emphasize: bool = False,
) -> str:
    parts = []
    for token in re.split(r"(\s+)", text):
        visible = _styled_token(token, capacity)
        if emphasize and _emphasized(token):
            visible = rf"{{\c&H{highlight}&}}" + visible + rf"{{\c&H{primary}&}}"
        parts.append(visible)
    return "".join(parts)


def _styled_ass(cues: list[Cue], resolution: tuple[int, int], animation: str, style: dict) -> str:
    width, height = resolution
    if any(type(value) is not int or value <= 0 for value in (width, height)):
        raise ValueError("Subtitle output dimensions must be positive integers.")
    scale = width / 720
    font_size = style["font_size"] * scale
    outline = style["outline_width"] * scale
    background = style["background_opacity"] > 0
    padding = max(outline, 3 * scale if background else 0)
    left = math.ceil((style["x"] - style["width"] / 2) * width + padding)
    right = math.ceil((1 - style["x"] - style["width"] / 2) * width + padding)
    capacity = max(1.1, (width - left - right) / font_size)
    primary, highlight, border, back = (
        _ass_rgb(style[name]) for name in
        ("text_color", "highlight_color", "outline_color", "background_color")
    )
    alpha = round((1 - style["background_opacity"]) * 255)
    anchor = rf"{{\an5\pos({style['x'] * width:.4f},{style['y'] * height:.4f})\q0}}"

    def style_line(name: str, box: bool = False) -> str:
        # A separate opaque-box layer preserves the foreground's independent
        # outline; BorderStyle=3 alone would replace that outline with a box.
        foreground = f"&HFF{primary}" if box else f"&H00{primary}"
        edge = f"&H{alpha:02X}{back}" if box else f"&H00{border}"
        return (
            f"Style: {name},{style['font_family']},{font_size:g},{foreground},&H00{highlight},"
            f"{edge},&H{alpha:02X}{back},{-1 if style['bold'] else 0},0,0,0,100,100,0,0,"
            f"{3 if box else 1},{padding if box else outline:g},0,5,{left},{right},0,1\n"
        )

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + style_line("Caption") + (style_line("Background", True) if background else "")
        + "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []

    def emit(start: float, end: float, text: str) -> None:
        first, last = ass_timestamp(start), ass_timestamp(end)
        if first == last:
            return
        if background:
            # Color changes split libass runs. Keep the box independent of word
            # emphasis so translucent run edges cannot overlap and darken.
            box_text = re.sub(r"\{\\c&H[0-9A-F]{6}&\}", "", text)
            events.append(f"Dialogue: 0,{first},{last},Background,,0,0,0,,{anchor}{box_text}")
        events.append(f"Dialogue: 1,{first},{last},Caption,,0,0,0,,{anchor}{text}")

    for cue in cues:
        if not cue.words:
            emit(cue.start, cue.end, _styled_text(cue.text, capacity, primary, highlight, emphasize=True))
            continue
        for group in _styled_word_groups(cue.words, capacity):
            if animation == "standard":
                text = " ".join(
                    _styled_text(word.text, capacity, primary, highlight, emphasize=True) for word in group
                )
                emit(group[0].start, max(word.end for word in group), text)
                continue
            boundaries = sorted({time for word in group for time in (word.start, word.end)})
            for start, end in zip(boundaries, boundaries[1:]):
                midpoint = (start + end) / 2
                active = next((index for index, word in enumerate(group) if word.start <= midpoint < word.end), None)
                parts = []
                for index, word in enumerate(group):
                    text = _styled_text(word.text, capacity, primary, highlight)
                    if index == active:
                        text = rf"{{\c&H{highlight}&}}" + text + rf"{{\c&H{primary}&}}"
                    parts.append(text)
                emit(start, end, " ".join(parts))
    if not events:
        raise ValueError("Caption intervals are shorter than ASS's 10 ms precision; correct the transcript times.")
    return header + "\n".join(events) + "\n"


def build_ass(
    transcript: list[dict], duration: float, resolution: tuple[int, int],
    style: str = "standard", position: str = "lower_middle", *,
    subtitle_style: dict | None = None,
) -> str:
    subtitle_style = normalize_subtitle_style(subtitle_style)
    if position not in CAPTION_POSITIONS:
        raise ValueError("Caption position must be lower_middle, center or bottom.")
    if style not in {"standard", "animated"}:
        raise ValueError("Caption style must be standard or animated.")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Caption duration must be positive and finite.")
    cues = transcript_cues(transcript, duration)
    if not cues:
        raise ValueError(
            "No usable caption text falls within this clip. Enter a transcript in the captions editor "
            "with times relative to the clip start, or export with captions off."
        )
    if style == "animated" and any(not cue.words for cue in cues):
        raise ValueError(
            "Animated captions require real word-level timestamps. Supply word timings in the "
            "captions editor or choose standard captions; word timings will not be invented."
        )
    if subtitle_style is not None:
        return _styled_ass(cues, resolution, style, subtitle_style)
    width, height = resolution
    font_size = round(width / 15)
    margin = round(width * 0.08)
    # Center anchors the whole block; bottom positions retain a UI-safe margin.
    alignment = 5 if position == "center" else 2
    bottom = 0 if position == "center" else round(height * (0.12 if position == "bottom" else 0.27))
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption,Arial,{font_size},{WHITE},{LIME},&H00141414,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{max(3, round(width / 240))},1,{alignment},{margin},{margin},{bottom},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = []
    for cue in cues:
        if style == "standard":
            if cue.words:
                for group in _word_groups(cue.words):
                    events.append(_dialogue(group[0].start, max(word.end for word in group), _word_line(group, emphasize=True)))
            else:
                # No word timings means one honest segment cue, not guessed word/phrase times.
                events.append(_dialogue(cue.start, cue.end, _standard_text(cue.text)))
            continue
        for group in _word_groups(cue.words):
            boundaries = sorted({time for word in group for time in (word.start, word.end)})
            for start, end in zip(boundaries, boundaries[1:]):
                midpoint = (start + end) / 2
                active = next((index for index, word in enumerate(group) if word.start <= midpoint < word.end), None)
                events.append(_dialogue(start, end, _word_line(group, active)))
    rendered = [event for event in events if event is not None]
    if not rendered:
        raise ValueError("Caption intervals are shorter than ASS's 10 ms precision; correct the transcript times.")
    return header + "\n".join(rendered) + "\n"
