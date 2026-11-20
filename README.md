# GTA Clip Studio

A localhost web application for finding candidate highlights in long gameplay recordings and exporting portrait clips. Import a local file or an authorized public YouTube video, analyze measured audio/visual activity, review the original footage, edit captions, and export H.264/AAC MP4s. React/Vite is the UI; FastAPI, SQLite, NumPy, and FFmpeg do the local work.

**This is a heuristic detector, not a GTA-specific trained AI.** Loud audio, spectral changes, camera movement, and scene changes rank candidate intervals. They do not prove a kill, crash, stunt, or mission event. Scores and confidence describe signal salience, not calibrated gameplay-event probabilities. Quiet highlights and brief action between samples can be missed; static footage can correctly yield no candidates. Always review the results.

## One-click Windows startup

With **Python 3.11+ x64 and Node.js 20+** installed, double-click **`run.dev.cmd`**, or run:

```powershell
.\run.dev.cmd
```

This creates/reuses `.venv`, installs missing Python/npm dependencies, and downloads a **SHA-256-verified FFmpeg/FFprobe Windows x64 essentials build** from gyan.dev if compatible tools are unavailable. Tools stay in `storage/tools/ffmpeg/bin`; no administrator access or system PATH changes are needed. Both servers start automatically, and the default browser opens **http://127.0.0.1:5173** only after the UI and backend are ready. Leave the terminal open; stop with Ctrl+C.

The first setup needs internet access to PyPI, npm, and (if needed) gyan.dev and can download a few hundred MB. Subsequent launches reuse dependencies when their manifests have not changed. Interrupted FFmpeg downloads retry and preserve checksum-addressed partial files for the next launch. By default it also installs the local caption dependencies and downloads the small English `tiny.en` model from Hugging Face. This downloads model files only; your recordings are not uploaded. If optional local caption setup fails or takes longer than 90 seconds, the app still starts with manual captions and configured Groq captions available. Run `run.dev.cmd --install-only` when you want to retry the full offline caption setup. Saved **No captions** or **Groq captions** preferences skip local model setup. Existing projects and exports are not removed. Explicit `FFMPEG_PATH`/`FFPROBE_PATH` overrides remain respected; an invalid override must be corrected or removed.

```powershell
.\run.dev.cmd --install-only   # Set up without launching servers/browser
.\run.dev.cmd --no-open        # Start servers without opening the browser
.\run.dev.cmd --skip-caption-setup # Skip local model setup: use Groq/manual/No captions
python run.dev                # The same launcher, invoked directly
```

Stop any older server using ports 8000/5173 before starting this launcher. Python and Node.js themselves are prerequisites; this script does not silently install global runtimes. For manual setup or unsupported platforms, follow the instructions below.

## Windows 10 64-bit prerequisites

- **Windows 10 x64**, with a supported browser such as Edge, Chrome, or Firefox. Keep the machine patched; localhost binding is not a substitute for OS security.
- **64-bit Python 3.11 or 3.12 recommended**, from [python.org](https://www.python.org/downloads/windows/). Install the Python launcher and select **Add Python to PATH**. Newer Python versions may lack compatible optional speech-recognition wheels. Check with `py --list` and `python --version`.
- **Node.js 20+ and npm**, from [nodejs.org](https://nodejs.org/); a supported LTS version is preferable. Check `node --version` and `npm --version`.
- **FFmpeg and FFprobe** on PATH, or configured as executable paths below. Use a build with **libx264, AAC, and libass**, including `ass`, `scale`, `overlay`, and `boxblur` filters. The automatic launcher supplies a compatible project-local build; manual setups need a separate installation, not supplied by pip.
- Enough free disk for the original recordings, optional YouTube downloads, feature caches, temporary render output, and exports. Local-path import references the source instead of copying it. Rendering needs working space on the export drive; speech models add their own storage costs.
- No GPU or model is required for heuristic analysis or caption-free/manual-caption exports. Optional CPU speech recognition may require the [Microsoft Visual C++ x64 Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe).

Commands below use **PowerShell** from the repository root unless otherwise stated.

### Install FFmpeg

With Windows Package Manager (App Installer):

```powershell
winget install --id Gyan.FFmpeg --exact
```

Open a new terminal afterward so PATH changes take effect. Alternatively, follow the Windows links at [ffmpeg.org/download.html](https://ffmpeg.org/download.html) to a trusted build provider such as [Gyan](https://www.gyan.dev/ffmpeg/builds/). Download a full x64 build, extract it, and add its `bin` directory to your user PATH. Keep `ffmpeg.exe` and `ffprobe.exe` from the same build.

If you cannot change PATH, point the backend at the actual executables. Adjust these paths to the extracted directory:

```powershell
$env:FFMPEG_PATH = 'C:\Tools\ffmpeg\bin\ffmpeg.exe'
$env:FFPROBE_PATH = 'C:\Tools\ffmpeg\bin\ffprobe.exe'
```

These variables are **full executable filenames**, not directory names. They affect the current PowerShell session and processes started from it. To persist them, use Windows **Edit environment variables for your account**, then restart the terminal/backend. Do not assume a `.env` file is automatically loaded.

Verify a PATH installation with:

```powershell
ffmpeg -version
ffprobe -version
ffmpeg -hide_banner -buildconf 2>&1 | Select-String 'enable-libass|enable-libx264'
ffmpeg -hide_banner -encoders 2>&1 | Select-String 'libx264|aac'
ffmpeg -hide_banner -filters 2>&1 | Select-String 'ass|scale|overlay|boxblur'
```

For environment-variable-only installs, invoke `& $env:FFMPEG_PATH -version` and `& $env:FFPROBE_PATH -version`, and use the same FFmpeg executable for capability checks. Missing `libass` prevents caption burn-in; missing `libx264` prevents the configured exports.

## Install and start development

Create a virtual environment, activate it, and install backend dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Use `py -3.12` instead if that is your installed recommended version. If PowerShell blocks activation, either use a process-scoped policy your organization allows (`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`) or invoke `.\.venv\Scripts\python.exe` directly for every Python command. Do not weaken machine-wide execution policy. Start the development coordinator from the activated environment so its Python process uses these dependencies.

Install JavaScript dependencies, then run the **root** coordinator:

```powershell
npm install
npm run dev
```

The root installer installs the frontend dependencies too. The coordinator uses `.venv` automatically (override with `GTA_PYTHON`) and starts both the backend at **http://127.0.0.1:8000** and the frontend at **http://127.0.0.1:5173**. Open port 5173 for development. Stop with Ctrl+C. Ports must be free; do not start the manual servers at the same time.

### Manual startup / coordinator troubleshooting

In the first terminal, activate the Python environment and start the backend from the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, from the repository root:

```powershell
npm --prefix frontend run dev
```

Vite proxies relative `/api` requests to `127.0.0.1:8000`; browser media, thumbnails, and events use the API rather than filesystem paths. Check **http://127.0.0.1:8000/api/health** for dependency/capability information and **http://127.0.0.1:8000/docs** for the API. Optional-package availability does not prove that its model is cached. Use only one backend worker process; the in-process queue intentionally serializes heavy work.

### Production build, served by FastAPI

```powershell
npm --prefix frontend run build
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

The build writes `frontend/dist`; FastAPI serves that bundle when present. Open **http://127.0.0.1:8000**; no Vite server or Node process is needed for normal use after building. Rebuild after changing frontend code. Vite's preview command alone is not the production API server. Keep this a loopback-bound local app, not an internet-facing deployment.

## Workflow and captions

1. **Import** an absolute local path, upload a file, or queue a YouTube URL you are permitted to download. A local path is on the **backend machine**, not a remote browser's machine. Keep the original file accessible at that path.
2. **Analyze** and review the real source footage. Detection completes first and immediately saves highlights. With Standard/Animated captions enabled (Standard is the default), a separate caption job automatically transcribes shortlisted selected clips. Cloud review is manual by default; it queues afterward only if Auto review, a provider and upload consent are explicitly enabled. No full-recording transcription is performed.
3. **Review** clips in the Review tab. Open **Edit subtitles** to correct text beside a portrait preview. Click a subtitle's time to jump to it, scrub or replay the clip, and use **Timing and words** for precise edits. Add caption inserts a blank line at the playhead without overlapping existing lines. Times are relative to the clip start.
4. Open the **Layout** tab to crop the facecam and gameplay without the clip list in the way. New projects use streamer top / gameplay bottom at **50/50**. Draw a box, drag it to move, or resize from any of its four edges or four corners. Choose Fit/Fill and a split preset while checking the portrait preview, then **Save layout**. Coordinates and caption position are under **Advanced**. Split export requires a selected facecam; legacy projects keep their blurred layout until changed. Drafts survive tab switches, and hidden previews pause.
5. **Export** selected clips. Missing captions generate automatically on export and are saved. Standard and Animated captions are available; **No captions** explicitly disables transcription for that export. Outputs are 720×1280 or 1080×1920, 30fps H.264/AAC. Fit preserves the selected region with padding; Fill crops to fill its panel. Both panels use one decoded source. Exports go to `storage/exports` or the chosen output folder. Queued exports snapshot crop/transcript settings; later edits do not change them.

Open the subtitle editor's **Style** panel to change font, size, colors, weight, outline and background. Drag the subtitle box to position it, and use its left/right handles or Text width slider to change wrapping width. Position presets and keyboard controls are available; less-used options stay under Advanced. **Save changes** persists style per clip; Reset style restores the project's default placement. These style edits do not regenerate speech or spend AI credits.

**Manual captions require no model.** Standard preview shows phrases; Animated highlights each word using actual timestamps; No captions hides the overlay and skips transcription for that export. The preview uses the saved source crops. **Save changes** stores text/timing; **Save & export** also applies the chosen export caption style. Font wrapping may differ slightly in the final FFmpeg render.

Editing text or timing clears the affected word timestamps. Regenerate subtitles (with confirmation before replacing manual edits), use Standard, or supply timestamps under **Timing and words → Word timings**. Animated export is blocked when its word timings are missing; they are never guessed. Renaming and detection/AI score details are under **Clip details**.

Automatic captions use **local tiny.en CPU INT8** by default. Project/highlight Generate/Retry controls can regenerate missing transcripts; replacing existing manual text requires explicit selection. Captions/cache are persisted per clip, and completed clips survive cancellation. Recognition can be wrong, especially with music, overlapping speakers, or non-English speech; review results. If no speech is recognized or setup fails, the clip shows an actionable error rather than invented text. Captioned export requires a usable transcript; retry, supply a correction or choose No captions. Basic detection remains available even if captions/cloud jobs fail. The older local AI-refinement switch remains a limited optional shortlist pass; automatic caption jobs cover the final selected highlights independently.

### Local model setup / offline use

Use the **same virtual environment and account as the backend**:

```powershell
python -m pip install -r requirements-captions.txt
python scripts/download_model.py --download
```

The launcher performs this setup by default; these commands are for manual installation/repair. Downloads contact Hugging Face but never upload recordings. Runtime prefers **`storage/models/tiny.en`** and reads it offline; the Hugging Face snapshot is retained as a legacy fallback. `GTA_WHISPER_MODEL_PATH` can point to another complete local model directory. Model weights are approximately 72MiB in the tested download, not multiple gigabytes. Package/model downloads consume bandwidth and disk; keep their licenses in mind.

Analysis/export jobs never download models or install packages. Runtime recognition is English-only with 1–2 CPU threads. Caption dependency versions are constrained to compatible faster-whisper/PyAV releases. The default CPU configuration avoids MKL's large allocation pool (`CT2_USE_MKL=0`, `MKL_DISABLE_FAST_MM=1`); advanced users can override these environment values when benchmarking. Insufficient system RAM/page-file space still causes an actionable failure, not a silent cloud upload.

### AI connections, titles and usage

Settings supports **None (local-only)**, **Gemini**, **Groq**, and **OpenAI**. Add a write-only key under Connections, or set `GEMINI_API_KEY`, `GROQ_API_KEY` or `OPENAI_API_KEY` on the backend. Environment values override saved keys. Windows protects saved credentials with DPAPI; other systems use private owner-only files under `storage/private`. Do not share that folder.

For OpenAI, choose **API key** or **ChatGPT plan** mode. API mode defaults to the current low-cost `gpt-6-luna` model, with reasoning disabled for these short tasks. Plan mode uses **Continue with ChatGPT**, the official loopback/PKCE sign-in flow, verified identity and account model catalog. You can cancel pending sign-in, select/reconnect registrations or sign out. Account eligibility and plan permission are required; no API key is used as a hidden fallback. Plan access does not provide the transcription API. Live account access still needs verification with your own account.

Cloud uploads require explicit consent even after adding a key or signing in. Review uses **transcripts and measured features by default**. Optional Gemini/OpenAI vision review adds a few low-resolution JPEGs, never the recording or audio; Groq review stays text-only. Sparse frames cannot establish every action or vocal tone. Default provider models are `gemini-3.5-flash-lite`, `openai/gpt-oss-20b`, and `gpt-6-luna`; custom/unpriced models require explicit opt-in.

Title suggestions use only the clip transcript/features, return up to three short choices, and **never overwrite your title automatically**. Apply a suggestion yourself or keep/edit the current title. AI scores and rubrics also stay separate from detection scores: these are editorial judgments, not probabilities or guarantees of views.

Fresh settings cap review at **5 clips**, vision at **2 sampled frames**, and API output at **512 tokens**. Cloud review is manual by default. A durable usage ledger checks the **20-request daily limit** and **$0.10 daily API-cost guard** before requests, then records reported tokens/audio and estimated costs per provider/model. ChatGPT plan uses the request cap and its own plan limits, not the USD guard. Unknown usage remains Unknown; failed/interrupted requests can retain a conservative reservation. There are no automatic inference retries or paid-model fallbacks. Cached reviews, titles and cloud transcripts spend no new request. Lowered limits and withdrawn consent apply to remaining calls in queued/running work.

These guards are not provider billing guarantees or account balances. Check your provider's own spending/plan controls. Free tiers are limited and account/model-specific; Google free-tier data-use terms may allow product improvement. See **[`docs/AI_COSTS.md`](docs/AI_COSTS.md)** for the full pricing/credits comparison, free options, sources and limitations. Tests use mocked provider responses, not real inference keys.

## Architecture, resources, and recovery

- **API/state:** FastAPI stores projects, settings, highlights, jobs, and exports in local SQLite/storage. `CONTRACTS.md` defines the integration API. One heavy job runs at a time across analysis and export; batch exports are serialized. `cpu_workers` limits FFmpeg/recognition threads to 1–2, not simultaneous jobs. Other library and OS threads may still exist.
- **Detection:** FFmpeg decodes audio in bounded chunks into 16 kHz mono measurements. The detector uses RMS/peak/spectral change and seeked, low-resolution grayscale video samples. Rolling baselines, capped candidate retention, and overlap suppression keep the feature/ranking working set bounded rather than loading an entire recording into RAM. Low-memory mode caps feature chunks at 300 seconds; balanced mode allows the configured maximum of 600 seconds. The 1.5GB additional-RAM goal is **not a hard OS memory cap**. Monitor actual backend and FFmpeg memory, especially with optional models or 1080p rendering.
- **Sparse seeking is not free:** each video sample requests a frame near its timestamp, but inter-frame codecs may decode from the preceding keyframe. Long GOPs, slow/random-access storage, codec/resolution, and many short FFmpeg seeks can dominate time. This avoids an intentional sequential full-video frame scan; it does not mean only one compressed frame is decoded per sample. Audio is still decoded across the recording. Sparse sampling can miss short action.
- **Cache:** completed feature chunks are written atomically under the project's cache. Keys include project ID, source path/stat/metadata, and scan settings; ranking-only changes can reuse features. It is not a content hash of the entire source: edits preserving size and modification time can evade invalidation. The detector currently caps feature-cache disk use at 64 MiB per project; eviction can make a later run rescan older chunks. This is disposable acceleration, not a permanent analysis archive.
- **Resume:** interrupted queued/running jobs requeue on backend startup according to the API contract. Valid completed chunks can be reused; an incomplete/corrupt chunk is rescanned. Resuming resets current-run stage timing; it is not an accumulation of previous elapsed time. Cancelled jobs are not a promise of automatic restart: enqueue analysis again to reuse surviving chunks. `force: true` requests analysis again; it does not mean delete every feature cache.
- **Cancel/render safety:** cancel a job in the UI or with `POST /api/jobs/{id}/cancel`. Managed FFmpeg children are terminated/cleaned up; completed feature chunks remain. Recognition cancellation can wait for a segment boundary. Rendering seeks to the selected interval (plus keyframe preroll), creates a temporary MP4 beside the destination, and renames it on success. Failed/cancelled renders remove their partial output. Restart recovery removes only exact job-owned abandoned partials and recognizes atomically published exports rather than duplicating them. Windows Job Objects own subprocess trees, including mergers, even if their parent exits.
- **Progress/estimates:** jobs expose actual `processed_seconds`, `total_seconds`, elapsed time, speed, and ETA for the **current stage**. Scan progress describes timeline coverage; while audio for a chunk is decoded, coverage can hold at the prior boundary and detail shows the audio work. Cached coverage can jump rapidly. Rank, transcription, thumbnail, and render stages can have different totals. Stage percentages/ETAs are not an invented overall completion percentage or a guaranteed finish time. SSE provides updates, with UI polling fallback.
- **Files:** local-path imports **reference, do not copy**, your video. Do not rename, move, or delete that source while it is needed for preview, analysis, or export. Deleting a project never deletes its referenced source; cancel active work before deletion. Uploaded/YouTube inputs inside that project's managed folder are deleted, while exported MP4s are retained. Deleting a project removes its export metadata/download links, so keep a record of custom output locations. Cache cleanup targets inactive disposable/orphan data, not your source archive. Back up anything you need, including exports and manually edited transcripts.

## Benchmark your real recordings

**No 1-hour, 5-hour, 10-hour, or 20-hour benchmark is claimed here.** Faster-than-real-time operation and low memory use are design targets, not measured guarantees. The completed 70-second synthetic correctness test and its actual observations are documented in [`docs/VALIDATION.md`](docs/VALIDATION.md); they are not evidence of sustained gameplay throughput. Codec, duration, storage, CPU, settings, cache state, and queue contention all matter. A warm-cache run is not an uncached-throughput result.

Start the backend, choose settings in the UI, leave other heavy jobs idle, and run:

```powershell
python scripts/benchmark.py --help
python scripts/benchmark.py 'D:\Recordings\session-1h.mp4' 'D:\Recordings\session-5h.mp4' 'D:\Recordings\session-10h.mp4' --api http://127.0.0.1:8000 --repeat 2 --output '.\storage\benchmarks\local-run'
```

Those are **example paths, not supplied fixtures or completed measurements**. Replace them with real files. Filenames do not determine duration: the report uses imported project metadata. Quote paths containing spaces. Multiple inputs run sequentially; `--repeat 2` analyzes each imported project twice. The CLI imports an absolute path through `POST /api/projects/local`, enqueues `POST /api/projects/{id}/analyze` with **`force: true, auto_enhance: false`** to isolate discovery throughput, and polls the returned ID through `GET /api/jobs`.

### Measurement and report semantics

- `--output` is a **report prefix** or a `.json`/`.csv` filename: `local-run` writes `local-run.json` and `local-run.csv`. An existing directory instead receives `benchmark.json`/`benchmark.csv`. Parent directories are created. Each invocation replaces its chosen pair; use a new prefix to retain previous experiments. Reports are refreshed after each attempt, including partial/failed analysis attempts.
- `duration` is **source seconds from project metadata**. `wall_seconds` is the CLI's monotonic `perf_counter` interval from just before the analyze POST until it observes a terminal state (or stops on error/timeout). It includes queue wait, HTTP/poll latency, and all analysis stages; import/probing, settings reads, cancellation cleanup, and report writing are excluded. Very short runs are sensitive to the poll interval. This is end-to-end client-observed analysis time, not pure decoder CPU time.
- `speed_ratio = duration / wall_seconds`, source-seconds per wall-second. Above 1 means faster than real time for that completed run. Failed/cancelled/interrupted/timed-out attempts have **no full-source speed ratio**. Zero/unknown duration or invalid timing also produces null, never an invented value.
- Caption and AI review are separate follow-up jobs. Their times are **not** included in the analysis benchmark/speed ratio; profile them separately for total workflow time. The benchmark CLI explicitly suppresses follow-up jobs without changing saved settings, so repeat runs do not collide with pending captions. Normal UI analyses keep automatic enhancement enabled.
- The CLI does **not** substitute `Job.elapsed_seconds`, a stage's `speed_ratio`, or entries from `GET /api/benchmarks` for its own timing. The API's stored measurements may use a different scope/schema and need not match these client-observed reports.
- `stage_samples` in JSON preserve observed status, stage, processed/total seconds, API stage-local timing/speed/ETA, progress, detail, and the CLI observation offset. `stage_metrics` summarizes first/last observations and last reported stage values; polling can miss short stages. These are not exact stage start/finish times. CSV has one row per attempt with JSON-encoded settings, stage summaries, and memory provenance. Missing measurements are JSON null / empty numeric CSV cells.
- `cache_state=cold` means the API returned a **new project ID**, so no earlier project-keyed feature chunks are expected. `warm` means the same project/settings followed a completed run in this invocation; eviction, source changes, or invalidation may still cause misses. Reused imports, changed settings, or incomplete prior attempts are `unknown`. **OS/file-system cache state is always uncontrolled**: the script does not flush Windows caches or claim a true cold-disk benchmark. It never deletes caches or changes application settings. Keep the source/settings unchanged and avoid concurrent UI jobs/settings edits during a comparison.
- Each run records the actual settings snapshot and project metadata; the report also records the client Python/OS. Importing for a benchmark creates persistent projects/highlights; repeats may replace that project's highlights/transcripts. Review and remove benchmark projects yourself when no jobs are active. Source videos are never removed by this script.

By default, `peak_rss` is **null**, because the contract does not expose a backend memory measurement. To sample it, install `psutil` if necessary (`python -m pip install psutil`) and append `--pid` followed by the **actual backend Python PID** from Task Manager's Details tab. Do not use the frontend/npm PID or the benchmark CLI's PID. The chosen PID must be the backend running on this machine; remote API memory cannot be measured this way.

With `--pid`, `peak_rss` is the largest **observed sum of RSS bytes for that backend and its current descendants**, sampled around job polls. It includes resident baseline memory, may count shared pages twice, and can miss short-lived FFmpeg children/peaks. It is not an OS high-water mark, a per-job allocation delta, or proof of a hard RAM limit. The report includes sample count/method/errors; unavailable observations remain absent, not zero placeholders.

Useful options: `--poll-interval` defaults to 1 second, `--request-timeout` to 30 seconds per request, and `--timeout` to 86400 seconds per analysis, including queue time. An in-flight request can delay timeout handling by its request timeout. `--quiet` suppresses stage messages. Ctrl+C, timeout, or polling errors preserve collected measurements and attempt to cancel the known job; cancellation requested is not cancellation confirmed. If the enqueue response is lost, the CLI may not know the ID: inspect Jobs before retrying. Exit codes are 0 for all runs completed, 1 for failures, 2 for argument errors, and 130 for interruption. Import/settings errors stop the batch and retain earlier reports; inputs never analyzed do not get fabricated run rows.

## Tests

Server-free benchmark math, output, and mocked API/CLI tests (no FFmpeg, speech model, downloads, or real recordings required):

```powershell
python -m unittest discover -s tests -p test_benchmark.py -v
```

Other project validation, when all integration modules/dependencies are installed:

```powershell
python -m pip install -r requirements-dev.txt
npm test
npm run build
python scripts/verify_media.py
```

`npm test` uses the project virtual environment when available and runs both pytest and React interaction tests. `verify_media.py` generates an isolated 70-second synthetic recording and tests the real API/worker/media workflow, all caption styles, cancellation, and restart recovery; it is not a long-recording benchmark. It accepts `--ffmpeg` and `--ffprobe` paths.

Run `python scripts/verify_features.py` for the real automatic-caption + embedded-facecam workflow. It generates local speech using Windows SAPI (or takes `--speech-wav`), performs actual offline recognition without manual caption injection, exports all three caption styles, and cleans isolated media. It requires the caption dependencies/model set up by the launcher. The detector/render suites also have small FFmpeg integration tests when binaries are available. Unit tests with mocked inputs are not long-recording performance benchmarks. No test implicitly downloads a model.

## Privacy, security, and YouTube compliance

- Core import-by-path, analysis, manual captions, recognition with cached files, and export run locally. No video/audio is sent to a cloud inference service by those processing paths. The frontend uses local assets/system fonts; no external media request is needed for local projects.
- Installing pip/npm packages, updating software, explicitly downloading a model, and importing YouTube media **do contact external services**. YouTube/its CDN and the model host can see normal request metadata, including your IP. Browser extensions, OS services, or unrelated software are outside the application's privacy guarantee. Do not call a first-time online installation "air-gapped."
- Bind both services to **127.0.0.1**. This is a trusted-user local tool with access to server-local file paths, media, settings, and output folders, **not a hardened authenticated multi-user service**. Do not expose it via `0.0.0.0`, public tunnels, router forwarding, or a shared/untrusted machine. Do not point `--api` at an untrusted server: requests contain local paths/settings and responses may reveal media metadata. Reports themselves contain absolute source paths, settings, and possibly error details; review before sharing.
- Process only files/URLs you own or are authorized to use. Comply with YouTube's Terms of Service, copyright, licenses, privacy, and applicable law. Do not bypass DRM, access controls, paywalls, regional restrictions, or account restrictions. A public URL is not a grant of reuse/download rights. The optional `yt-dlp` dependency and upstream site changes can affect imports; no download or availability guarantee is made.

## Source layout

```text
frontend/src/
  components/   UI primitives, import, player, timeline, captions, export controls
  pages/        Dashboard, Projects, workspace, Highlights, Exports, Settings
  hooks/        Live SSE updates with polling fallback and action handling
  stores/       Zustand server-state and UI stores
  services/     Typed API and streaming upload client
  types/        Shared frontend response types
  styles/       Tailwind theme and local styles
backend/
  main.py       Localhost guards, lifecycle, production frontend serving
  api/          FastAPI routes, range media serving, SSE
  core/         Paths, executable resolution, subprocess-tree ownership
  database/     SQLite WAL metadata, durable jobs, atomic export completion
  models/       Validated settings, import, caption and export schemas
  detection/    Streaming features, sparse samples, cache, ranking, event timeline
  processing/   Portrait encoding, ASS captions, offline speech, VOD downloads
  workers/      Single-heavy-job runner, measured progress, recovery, RSS sampling
  utils/        FFprobe indexing and capability detection
run.dev.cmd     Double-click Windows setup/start/browser launcher
run.dev         Direct Python entry point for the same launcher
scripts/        Dependency bootstrap, startup, tests, benchmarks, model setup, verification
storage/        Projects, disposable cache, MP4 exports, SQLite, rotating logs
```

The required API endpoints are implemented; `/api/events` streams live jobs, `/api/projects/{id}/events` exposes bounded measured signal peaks, and `/api/benchmarks` exposes actual completed run measurements. The event timeline is a lossy activity summary, not an exhaustive gameplay log. Export settings and transcripts are snapshotted when queued, so editing them later does not silently alter a running export.

## Troubleshooting

- **Python/module missing:** activate the correct venv and reinstall `requirements.txt`. Use `python -m pip` rather than an unrelated `pip`. If optional speech wheels fail on a newer interpreter, recreate the venv with Python 3.11/3.12 x64. DLL load failures may need the x64 Visual C++ runtime.
- **FFmpeg/FFprobe missing:** check executable paths and restart the backend after environment changes. A browser restart alone does not update the backend's environment. Codec/filter errors require a compatible FFmpeg build, not an npm package.
- **Captions fail:** provide a valid relative-time transcript, choose standard for segment-only timings, explicitly set up tiny.en in the same environment/cache, or turn captions off. No recognized speech is a valid failure, not permission to invent text.
- **Preview fails but FFmpeg can read the file:** browsers support fewer source codecs than FFmpeg. Try a browser-compatible H.264/AAC source or transcode a separate copy yourself; do not assume analysis failure from a preview codec error.
- **Slow/paused-looking progress:** inspect the current stage/detail, queue, disk, and FFmpeg activity. Audio chunks, long-GOP seeks, optional transcription, warm-cache jumps, and competing work can all change the observed rate. Reduce analysis width/increase sample interval if an acceptable accuracy tradeoff; do not compare changed settings as equivalent runs.
- **Missing source, locked files, or full drive:** restore the referenced path/permissions, close software locking the output, and free space. Do not delete an active job's source, cache, or temporary files to force progress.
