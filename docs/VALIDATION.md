# Validation record

## Automatic captions and embedded-facecam split exports

The latest real-media test used a 12-second 320×180 synthetic gameplay source with an embedded colored camera region and a Windows SAPI speech fixture. It ran the real API, queue, SQLite, tiny.en CPU INT8 recognizer and FFmpeg renderer. **No transcript was injected**: the recognizer returned 14 timed words matching the authored speech. No cloud calls were made.

The default flow successfully completed detection, automatically queued caption generation, persisted its transcript, and rendered playable 720×1280/30fps H.264/AAC split MP4s with standard, animated and no captions. Separate one-second pixel assertions tested panel order, fit/fill and non-square-pixel handling at both 720×1280 and 1080×1920, including non-50/50 layouts and caption positions.

Observed times for this fixture: detection **1.392s**, automatic captions **4.200s**, then standard/animated/no-caption exports **4.669 / 4.733 / 4.683s**. These are correctness-fixture observations, not long-recording benchmarks. Raw results: [`feature-validation.json`](feature-validation.json). Reproduce with `python scripts/verify_features.py` after `run.dev.cmd --install-only`.

Validation exposed two real dependency/resource issues that were fixed: MKL's packed-allocation pool exhausted the constrained host's memory, so the default CPU backend disables that pool; faster-whisper 1.2.x used an API removed by newer PyAV, so caption dependencies now constrain PyAV to a compatible release. This host reported about 4GiB physical memory, below the original 8GB target; concurrent builds/media tests exhausted its page-file budget, so final checks were run sequentially. No user applications were stopped and no OS memory settings were changed.

## Earlier actual media smoke test

Run on Windows 10 x64, Python 3.13.7, FFmpeg 9.0.2 and FFprobe 5.1.0. The reported CPU identifier was `Intel64 Family 6 Model 60 Stepping 3, GenuineIntel`; no specific CPU marketing name or equivalence to the requested hardware is asserted.

The input was a **70-second synthetic H.264/AAC recording, 320×180 at 10 fps**, with authored audio-energy bursts. This is a correctness fixture, **not representative GTA footage or a sustained-throughput benchmark**. No inference model was installed. Caption text/word timings were explicitly authored test input, not claimed recognition output.

The real FastAPI/SQLite/background-worker/FFmpeg workflow passed:

- Local-path import and FFprobe metadata indexing.
- HTTP byte-range streaming of the source.
- Signal extraction, temporal ranking, two actual candidate intervals, and a persisted measured-event timeline.
- Caption-free, standard-caption, and animated-word-caption MP4 exports.
- FFprobe validation of 720×1280, 30 fps, H.264 and AAC; decoding of each resulting MP4 with FFmpeg.
- Cancellation of running analysis without losing the prior highlights.
- Shutdown during analysis, recovery of the same job on restart, and persistence of project/export results.

Observed analysis wall time was **2.764 seconds** for 70 source seconds (**25.33×**), with no prior feature cache for that project. The three 10-second portrait exports took **6.514 / 6.616 / 6.478 seconds**, respectively. These numbers apply only to this small low-resolution fixture and this run; they must **not** be extrapolated to 1080p/4K long-GOP gameplay or 10–20-hour recordings.

Raw observations, settings, and sampled process-tree RSS are in [`validation-results.json`](validation-results.json). RSS was sampled every 0.5 seconds: it can miss short-lived decoder peaks and is not an OS high-water mark or proof of the 1.5GB memory target.

Reproduce with installed development dependencies and FFmpeg/FFprobe on PATH:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts/verify_media.py
```

`--ffmpeg` and `--ffprobe` accept explicit executable paths. The script creates isolated temporary storage, does not touch regular projects, removes its generated media, and writes its report to `storage/validation-results.json` by default. No running API server, speech model, or internet access is needed.

## GPT, credits and visual subtitle pass

The current backend suite passed **677 Python tests** plus **310 subtests**, with four opt-in/platform-specific checks skipped. The frontend passed **160 React tests across 16 files**; TypeScript/Vite build and Prettier checks passed. Added cases cover signed-ID validation, PKCE/state/nonce and callback replay protection, refresh/account separation, budget reservations/concurrency, GPT API and plan SSE completion, uncertain billing, cached title suggestions/manual application, saved visual styles, and all eight crop handles. OAuth/provider HTTP is mocked and RSA test keys are generated locally; **no live sign-in or cloud inference was performed**.

The real local-speech/media verification also passed with saved font size, text/highlight colors, outline, translucent background, center position and text width. It recognized 14 words from the authored SAPI fixture, exported Standard/Animated/No captions MP4s, validated them with FFprobe and decoded frames with FFmpeg. The usage ledger measured **zero cloud requests**. Raw observations: [`v2-feature-validation.json`](v2-feature-validation.json). Reproduce with `python scripts/verify_features.py --styled --output storage/v2-feature-validation.json`. This remains a 12-second synthetic correctness fixture, not a gameplay or long-recording benchmark.

## Earlier automated checks

The backend-feature integration suite passed **431 Python tests** (plus 146 subtests; two opt-in startup checks skipped while user servers occupied the default ports) and **67 React tests**. Cloud provider requests were HTTP-mocked; real provider keys were not used. Earlier Python validation included opt-in real development/production launcher checks: each launcher served the UI, deep links, health and SSE successfully, then released its ports without leaving servers running. The TypeScript/Vite production build, Prettier check, Python compilation and launcher syntax checks also passed.

The subsequent UI-focused pass passed **107 React tests across 12 files**, the TypeScript/Vite production build, and Prettier. Cases cover crop gestures/keyboard entry, hidden advanced controls, Review/Layout navigation and browser history, draft preservation, a single subtitle media source, clip-relative scrubbing, cue jumps, actual word highlighting, no-caption export, regeneration confirmation and unsaved-edit safety. Backend code was unchanged and was not rerun in this UI-only pass. Media events are simulated in jsdom; this is not a full-browser visual check. That earlier UI-only pass did not add ChatGPT/OpenAI sign-in; the current pass above does.

To include launcher checks locally after building, with ports 8000/5173 free:

```powershell
$env:GTA_VERIFY_STARTUP = '1'
npm test
Remove-Item Env:GTA_VERIFY_STARTUP
```

Without that variable, the two port-binding checks are skipped. The installed Starlette emitted a TestClient/httpx deprecation warning; it did not affect test results.

## Remaining validation

**Real 1-hour, 5-hour, 10-hour, and 20-hour recordings were not supplied, so those benchmarks have not been run.** The requested 10× sustained processing target and low-memory target remain unverified on that workload. Use `scripts/benchmark.py` with representative recordings, ideally both cold and warm project-feature-cache runs.

A live public YouTube download, real-account ChatGPT sign-in, and live OpenAI/Gemini/Groq requests have not been exercised in this environment. Their API/configuration/failure paths are covered by offline tests; upstream restrictions, model availability, quotas and actual editorial quality still require verification with authorized media and your own account. Real faster-whisper inference was exercised on the authored speech fixture above; gameplay-noise accuracy is not established. React interaction tests use jsdom, not an automated full-browser visual acceptance test.
