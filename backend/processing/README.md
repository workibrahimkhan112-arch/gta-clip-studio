# Local clip export and speech recognition

## Integration

- `render.render_clip(project, highlight, settings, ctx)` returns exactly
  `{output_path, filename}`. It does not write the database or mutate a highlight.
- The caller owns the contract's single-heavy-job serialization. `cpu_workers`
  caps codec threads at 1–2, not the number of concurrent jobs. FFmpeg filter
  pools, audio and x264 lookahead are explicitly single-threaded.
- Shared dependencies are `backend.core.config.EXPORTS_DIR/get_binary` and
  `backend.core.process.Cancelled/managed_process/run_process` from `CONTRACTS.md`.
- Install an FFmpeg build with `libx264`, AAC, `libass`, and the standard
  scale/overlay/boxblur filters. FFprobe is used only when source audio metadata
  is absent. Existing import metadata normally avoids that probe.
- Output settings accept `720x1280` or `1080x1920`. Output is 30 fps H.264/yuv420p
  plus AAC; sources without audio get a silent AAC track. The full gameplay
  frame fits at center, with a low-resolution blurred background filling the
  portrait canvas. Audio length cannot shorten the video.
- Seeking and duration are input-side options on the original video. Only the
  selected interval is decoded (plus decoder keyframe preroll). Temp MP4 files
  are created in the destination directory for same-filesystem atomic rename;
  failure/cancellation removes them. Subtitle/audio scratch files are scoped to
  `ctx.work_dir` and removed on exit.
- Render progress comes from FFmpeg's actual output timestamps, not wall-clock
  estimates. Cancellation is polled even if the progress pipe stops producing
  data; the shared process manager handles process-group termination.

## Transcripts and captions

Transcript segment **and word timestamps are relative to the clip start**. Do
not subtract the source-video highlight start again. Out-of-clip intervals are
clipped/ignored; invalid/empty captions fail with instructions for the editor.
Text is not fabricated, and a generated export-time transcript is not persisted
by the renderer (analysis refinement returns it to its caller).

Standard captions support segment-only transcripts. Word-timed transcripts are
broken into short caption groups. Animated captions require actual word timing;
without it, choose standard or supply word-level timestamps. We do not estimate
word timing from text. Animated groups use white bold text with a lime current
word and a dark outline, above the lower UI safe area. ASS control characters
from user text are neutralized; literal braces/backslashes use fullwidth
lookalikes to prevent override/escape injection. Subtitle filenames are escaped
through both FFmpeg parsers, including Windows drive colons and special paths.

When captions are enabled without a transcript, only a locally cached `tiny.en`
model is tried. Missing optional packages/cache or no recognized speech causes
an actionable export failure; the user can enter a transcript or disable
captions instead. No analysis/export path downloads a model.

## Optional model setup (explicit network opt-in)

From the repository root, in the backend's Python environment:

```sh
python -m pip install faster-whisper
python -c "from backend.processing.transcription import setup_model_cache; print(setup_model_cache())"
```

The second command **downloads** the public `tiny.en` model into the default
Hugging Face cache. Model setup contacts the model host; runtime transcription
uses local files only and never uploads audio. For a custom cache, configure
`HF_HUB_CACHE` consistently before both setup and backend startup. The helper
also accepts a cache-directory argument.

`transcription.refine(project, highlights, settings, ctx)` is a no-op unless
`ai_refinement` is true. It enriches at most three highest-scoring selected
candidates without existing usable transcripts, capped at 120 seconds total
and 90 seconds per clip. It preserves order, scores, and existing transcripts.
Missing recognition/audio dependencies are nonfatal for analysis; cancellation
still propagates. Recognition is CPU INT8 with at most two CPU threads, one
worker, beam size one, and no network-dependent VAD. Inference cancellation is
cooperative at segment boundaries; FFmpeg extraction is independently monitored
by the shared cancellation-aware runner.

## Validation

```sh
python -m unittest discover -s tests -p test_rendering.py -v
```

Unit tests mock process/model boundaries and need no FFmpeg/model installation.
A small real-FFmpeg integration test automatically runs when FFmpeg and FFprobe
are available, covering both resolutions, standard/animated ASS paths with
apostrophes and filter metacharacters, and silent AAC output. No test downloads
anything. Shared coordinator core modules must be present to import the suite.
