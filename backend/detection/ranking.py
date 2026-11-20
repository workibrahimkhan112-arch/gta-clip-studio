"""Streaming fusion, bounded candidate retention, overlap suppression/diversity.

All labels describe measured signals, not GTA semantics. A loud sound, a jump
cut or a fast camera pan is not evidence of a kill, crash, stunt or mission.
Scores/confidence rank signal salience; they are not event probabilities.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math

from .features import AdaptiveScores

MIN_SCORE = 22.0
MAX_TIMELINE_EVENTS = 2000


@dataclass(frozen=True)
class Candidate:
    time: float
    start: float
    end: float
    score: float
    audio: float
    visual: float
    scene: float
    category: str


def clip_bounds(timestamp, duration, clip_duration):
    length = min(duration, clip_duration)
    start = max(0.0, min(duration - length, timestamp - length * 0.6))
    return start, min(duration, start + length)


def combine_scores(audio, visual, scene):
    """Permit strong single-modality evidence; reward coincident evidence."""
    score = max(0.65 * audio + 0.25 * visual + 0.10 * scene,
                0.80 * audio, 0.65 * visual + 0.35 * scene)
    return min(100.0, score + 0.10 * min(audio, max(visual, scene)))


def category_for(audio, visual, scene):
    if audio >= 25 and max(visual, scene) >= 25:
        return "mixed_activity"
    if audio >= max(visual, scene):
        return "audio_activity"
    return "scene_change" if scene > visual else "visual_activity"


class CandidatePool:
    """Keep at most eight temporally binned peaks per broad timeline region.

    Unlike a global top-N heap, a long loud section cannot evict every quieter
    region. Both candidate count and rolling baselines are independent of video
    length. The lossy regional cap is an intentional speed/memory tradeoff.
    """

    def __init__(self, duration, clip_duration=35, max_clips=10):
        self.duration = duration
        self.clip_duration = clip_duration
        self.regions = max(4, max_clips * 4)
        self.buckets = {}
        self.per_region = 8
        self.time_bin = max(1.0, clip_duration / 2)

    def add(self, timestamp, audio, visual, scene):
        score = combine_scores(audio, visual, scene)
        if not math.isfinite(score) or score < MIN_SCORE:
            return
        start, end = clip_bounds(timestamp, self.duration, self.clip_duration)
        candidate = Candidate(timestamp, start, end, score, audio, visual, scene,
                              category_for(audio, visual, scene))
        region = min(self.regions - 1, max(0, int(timestamp / self.duration * self.regions)))
        bucket = self.buckets.setdefault(region, {})
        time_bin = int(timestamp / self.time_bin)
        previous = bucket.get(time_bin)
        if previous is None or (candidate.score, -candidate.time) > (previous.score, -previous.time):
            bucket[time_bin] = candidate
        if len(bucket) > self.per_region:
            worst = min(bucket, key=lambda key: (bucket[key].score, -bucket[key].time))
            del bucket[worst]

    def candidates(self):
        return [candidate for bucket in self.buckets.values() for candidate in bucket.values()]


def overlapping(first, second):
    intersection = max(0.0, min(first.end, second.end) - max(first.start, second.start))
    shorter = min(first.end - first.start, second.end - second.start)
    return shorter > 0 and intersection / shorter >= 0.5


def select_candidates(candidates, max_clips=10, duration=None):
    """Greedy nonmaximum suppression with modest temporal/category diversity.

    Diversity affects selection, not the reported score, and never fills empty
    slots with quiet/static footage. A very short source produces at most one
    overlapping clip. Returned clips remain sorted by measured score.
    """
    remaining = sorted((c for c in candidates if c.score >= MIN_SCORE and c.end > c.start),
                       key=lambda c: (-c.score, c.time))
    if duration is None:
        duration = max((c.end for c in remaining), default=0)
    selected = []
    categories = set()
    while remaining and len(selected) < max_clips:
        def utility(candidate):
            if not selected:
                return candidate.score
            distance = min(abs(candidate.time - other.time) for other in selected)
            temporal_bonus = 8.0 * min(1.0, distance / max(1.0, duration / max_clips))
            category_bonus = 5.0 if candidate.category not in categories else 0.0
            return candidate.score + temporal_bonus + category_bonus

        best = max(remaining, key=lambda c: (utility(c), c.score, -c.time))
        selected.append(best)
        categories.add(best.category)
        remaining = [candidate for candidate in remaining if not overlapping(candidate, best)]
    return sorted(selected, key=lambda c: (-c.score, c.time))


class StreamingRanker:
    def __init__(self, duration, clip_duration, max_clips, interval):
        self.scores = AdaptiveScores(interval)
        self.pool = CandidatePool(duration, clip_duration, max_clips)
        self.interval = interval
        self.max_clips = max_clips
        self.duration = duration

    def consume(self, audio_rows, visual_rows):
        """Join nearby measured signals without retaining earlier chunks.

        Fusion is approximate within half a sparse sample interval. Across a
        chunk boundary only the frame difference (not audio/visual fusion) uses
        the preceding chunk. Clip pre-roll and overlap suppression absorb most
        boundary ambiguity; localization cannot exceed the sampling precision.
        """
        audio = [(row["time"], self.scores.audio_score(row)) for row in audio_rows]
        visual = [(row["time"], *self.scores.visual_scores(row)) for row in visual_rows]
        audio_times = [row[0] for row in audio]
        visual_times = [row[0] for row in visual]
        tolerance = max(0.5, self.interval / 2)
        for timestamp, score in audio:
            index = bisect_left(visual_times, timestamp)
            nearby = [visual[i] for i in (index - 1, index) if 0 <= i < len(visual) and abs(visual[i][0] - timestamp) <= tolerance]
            match = max(nearby, key=lambda row: max(row[1:]), default=(0.0, 0.0, 0.0))
            self.pool.add(timestamp, score, match[1], match[2])
        for timestamp, movement, scene in visual:
            left = bisect_left(audio_times, timestamp - tolerance)
            right = bisect_right(audio_times, timestamp + tolerance)
            sound = max((row[1] for row in audio[left:right]), default=0.0)
            self.pool.add(timestamp, sound, movement, scene)

    def timeline(self):
        """Return retained measured peaks in source-time order, before clip NMS.

        Reuses the existing regional pool, rather than collecting more events
        during scanning. At most 1600 peaks currently survive the pool's cap;
        the explicit 2000-event ceiling also protects future pool changes.
        This is a lossy activity summary, not an exhaustive gameplay log. Its
        density depends on clip duration/count settings and quiet or discarded
        regions do not prove an absence of gameplay events.
        """
        strongest = sorted(self.pool.candidates(), key=lambda candidate: (-candidate.score, candidate.time))[:MAX_TIMELINE_EVENTS]
        return sorted(strongest, key=lambda candidate: candidate.time)

    def finish(self):
        return select_candidates(self.pool.candidates(), self.max_clips, self.duration)
