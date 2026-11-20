"""Small NumPy signal descriptors; these do not identify gameplay events.

Audio is measured in half-second windows at 16 kHz. Spectral flux measures
changes in normalized short-time spectra, not speech, gunshots, or explosions.
Sparse visual differences also respond to camera movement, loading screens,
lighting and edits. They cannot distinguish player action from those effects.
"""

from __future__ import annotations

import base64
from collections import deque

import numpy as np

SAMPLE_RATE = 16000
WINDOW_SECONDS = 0.5
FFT_SIZE = 512
DESCRIPTOR_WIDTH = 40


def audio_features(samples: np.ndarray, previous_spectrum=None):
    """Return RMS, peak and positive spectral flux, plus the final spectrum.

    Memory is proportional to one PCM window, never to the recording length.
    Flux is energy-gated later: normalization alone would amplify quiet noise.
    The first FFT of a newly decoded chunk has no preceding spectrum.
    """
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if not samples.size:
        return {"rms": 0.0, "peak": 0.0, "flux": 0.0}, previous_spectrum
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    peak = float(np.max(np.abs(samples)))
    if samples.size < FFT_SIZE:
        samples = np.pad(samples, (0, FFT_SIZE - samples.size))
    frames = np.lib.stride_tricks.sliding_window_view(samples, FFT_SIZE)[:: FFT_SIZE // 2]
    spectra = np.abs(np.fft.rfft(frames * np.hanning(FFT_SIZE), axis=1))
    spectra /= np.maximum(spectra.sum(axis=1, keepdims=True), 1e-12)
    if previous_spectrum is not None:
        spectra = np.concatenate((np.asarray(previous_spectrum)[None, :], spectra), axis=0)
    flux = float(np.maximum(np.diff(spectra, axis=0), 0).sum(axis=1).mean()) if len(spectra) > 1 else 0.0
    return {"rms": min(rms, 1.0), "peak": min(peak, 1.0), "flux": min(flux, 1.0)}, spectra[-1].copy()


def frame_descriptor(frame: np.ndarray):
    """Reduce a square grayscale FFmpeg frame to 40x40 means and 16 bins.

    The decoder first fits the picture into a bounded square. Letterboxing and
    downsampling attenuate small/dark action; no full-resolution frames persist.
    """
    frame = np.asarray(frame, dtype=np.uint8)
    width = frame.shape[0]
    if frame.shape != (width, width) or width % DESCRIPTOR_WIDTH:
        raise ValueError("Expected a square grayscale frame divisible by 40")
    factor = width // DESCRIPTOR_WIDTH
    small = frame.reshape(DESCRIPTOR_WIDTH, factor, DESCRIPTOR_WIDTH, factor).mean(axis=(1, 3))
    small = np.rint(small).astype(np.uint8)
    histogram = np.bincount((frame.reshape(-1) // 16), minlength=16).astype(np.float64)
    histogram /= frame.size
    return small, histogram


def frame_difference(previous, current):
    """Return mean absolute spatial change and histogram total variation."""
    before, before_hist = previous
    after, after_hist = current
    motion = float(np.abs(after.astype(np.float32) - before).mean() / 255.0)
    scene = float(np.abs(after_hist - before_hist).sum() / 2.0)
    return {"motion": min(motion, 1.0), "scene": min(scene, 1.0)}


def encode_frame_state(time, descriptor):
    small, histogram = descriptor
    return {"time": float(time), "pixels": base64.b64encode(small.tobytes()).decode("ascii"), "histogram": histogram.tolist()}


def decode_frame_state(state):
    pixels = base64.b64decode(state["pixels"], validate=True)
    if len(pixels) != DESCRIPTOR_WIDTH ** 2:
        raise ValueError("Invalid cached descriptor size")
    small = np.frombuffer(pixels, dtype=np.uint8).reshape(DESCRIPTOR_WIDTH, DESCRIPTOR_WIDTH)
    histogram = np.asarray(state["histogram"], dtype=np.float64)
    time = float(state["time"])
    if histogram.shape != (16,) or not np.isfinite(histogram).all() or not np.isfinite(time):
        raise ValueError("Invalid cached frame state")
    if (histogram < 0).any() or abs(float(histogram.sum()) - 1.0) > 1e-5:
        raise ValueError("Invalid cached histogram")
    return time, (small, histogram)


def _unit(value):
    return max(0.0, min(1.0, float(value)))


class AdaptiveScores:
    """Rolling robust thresholds with absolute floors, bounded to 60 seconds.

    Scores are relative signal salience, not semantic confidence or calibrated
    probabilities. Sustained loudness/movement receives a small absolute score
    so an energetic recording does not become entirely invisible after warmup.
    Local adaptation can still miss slowly building events or quiet dialogue.
    """

    def __init__(self, sampling_interval=5):
        self.audio = deque(maxlen=int(60 / WINDOW_SECONDS))
        self.visual = deque(maxlen=max(6, int(60 / sampling_interval)))

    @staticmethod
    def _baseline(history, key):
        if len(history) < 6:
            return 0.0, 0.0
        values = np.asarray([row[key] for row in history], dtype=np.float64)
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        return median, mad

    def audio_score(self, row):
        rms, peak, flux = (float(row[key]) for key in ("rms", "peak", "flux"))
        rms_base, rms_mad = self._baseline(self.audio, "rms")
        peak_base, peak_mad = self._baseline(self.audio, "peak")
        flux_base, flux_mad = self._baseline(self.audio, "flux")
        energy = _unit((rms - max(0.015, 1.45 * rms_base + 3 * rms_mad)) / max(0.035, 1.5 * rms_base + 3 * rms_mad))
        crest = _unit((peak - max(0.12, 1.4 * peak_base + 3 * peak_mad)) / max(0.25, 1.5 * peak_base + 3 * peak_mad))
        novelty = _unit((flux - max(0.08, flux_base + 3 * flux_mad)) / max(0.15, flux_base + 3 * flux_mad))
        novelty *= _unit(rms / 0.02)
        energy = max(energy, 0.3 * _unit((rms - 0.06) / 0.24))
        crest = max(crest, 0.2 * _unit((peak - 0.3) / 0.65))
        self.audio.append(row)
        if rms < 0.003 and peak < 0.03:
            return 0.0
        return 100.0 * (0.5 * energy + 0.3 * crest + 0.2 * novelty)

    def visual_scores(self, row):
        motion, scene = float(row["motion"]), float(row["scene"])
        motion_base, motion_mad = self._baseline(self.visual, "motion")
        scene_base, scene_mad = self._baseline(self.visual, "scene")
        movement = _unit((motion - max(0.025, 1.35 * motion_base + 2.5 * motion_mad)) / max(0.10, 1.8 * motion_base + 3 * motion_mad))
        movement = max(movement, 0.3 * _unit((motion - 0.08) / 0.25))
        cut = _unit((scene - max(0.12, scene_base + 3 * scene_mad)) / max(0.35, scene_base + 3 * scene_mad))
        self.visual.append(row)
        return 100.0 * movement, 100.0 * cut
