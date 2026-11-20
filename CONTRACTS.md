# Internal integration contracts

New automatic-caption, split-layout and cloud-review fields/endpoints are defined in `FEATURES.md`, which takes precedence over older defaults below.

All times are seconds. Persistent IDs are UUID strings. JSON responses are objects except list endpoints, which return arrays. API errors use FastAPI `{detail: string}`.

## Settings (GET/PUT /api/settings)
`max_clips: 10` (1..50), `sampling_interval: 5` (1,2,5,10), `analysis_width: 320` (160,320,480), `audio_sample_rate: 16000` (fixed), `cpu_workers: 2` (1..2), `memory_mode: 'low'` ('low','balanced'), `clip_duration: 35` (10..90), `output_resolution: '720x1280'` ('720x1280','1080x1920'), `caption_style: 'standard'` ('none','standard','animated'), `ai_refinement: false`, `output_folder: ''` (empty means storage/exports), `analysis_chunk_seconds: 300` (60..600). Low memory clamps effective chunk size to 300s; balanced honors up to600s. CPU workers is an FFmpeg thread limit, not number of simultaneous jobs. Single active heavy job across analysis and export. Target memory is not a hard OS cap.

## Project
`{id, name, source_type: 'local'|'youtube', source_path, source_url: string|null, status: 'ready'|'importing'|'analyzing'|'analyzed'|'failed', created_at: ISO UTC, metadata: {duration,width,height,fps,video_codec,audio_codec,audio_streams: [{index,codec,sample_rate,channels}],file_size}, highlight_count: number}`. Missing metadata may be `{}` while downloading. Paths should not be used directly in browser; use /api/projects/{id}/media and /thumbnail.

## Highlight
`{id,project_id,title,start,end,duration,category,score,confidence,audio_score,visual_score,scene_score,explanation,selected: true, transcript: [{start,end,text,words:[{start,end,word}]}], thumbnail_url: '/api/highlights/{id}/thumbnail'}`. Scores are 0..100 except confidence 0..1. Transcript timestamps are RELATIVE to clip start. It may be [] and must never be fabricated. Categories are heuristic, not proven semantic detection.

## Job
`{id,project_id: string|null,kind: 'analyze'|'export'|'youtube',status: 'queued'|'running'|'completed'|'cancelled'|'failed',stage,processed_seconds,total_seconds,elapsed_seconds,speed_ratio: number|null,eta_seconds: number|null,progress: number|null,detail,error: string|null,created_at,updated_at,result: object|null}`. `progress` is current stage fraction 0..1, not fabricated overall progress. `GET /api/projects/{id}/progress` returns latest job or null. `GET /api/jobs` returns all recent jobs. `/api/events` is SSE; event `update`, data `{jobs: Job[]}` once per second. Native EventSource. Jobs with interrupted active state requeue on startup; detection cache chunks support recovery. Resuming resets current-run stage timing (not cumulative speed). Cancel via POST /api/jobs/{id}/cancel.

## Endpoints
GET /api/health => `{status, version, capabilities: {ffmpeg,ffprobe,yt_dlp,faster_whisper,physical_cores,logical_cores,memory_gb},storage: {free_bytes,used_bytes}, benchmark: {runs,median_speed_ratio}|null}`.
GET /api/projects => Project[]
POST /api/projects/local JSON `{path: absolute local path}` OR multipart file upload on same endpoint. => Project (201). Local-path imports reference (do NOT copy) source. Windows path is on server machine, which is localhost.
POST /api/projects/youtube `{url}` => `{project: Project, job: Job}` (202).
GET /api/projects/{id} => Project
POST /api/projects/{id}/analyze `{force?:boolean}` => Job (202).
GET /api/projects/{id}/highlights => Highlight[]
PATCH /api/highlights/{id} `{selected?:boolean,title?:string,transcript?: segments}` => Highlight (captions editor)
GET /api/highlights => Highlight[] (all)
POST /api/highlights/{id}/export `{caption_style?:'none'|'standard'|'animated'}` => Job
POST /api/exports/batch `{highlight_ids: string[],caption_style?:...}` => Job[] (one serialized export job per clip)
GET /api/exports => Export[]: `{id,project_id,highlight_id,job_id,status,output_path,filename,created_at,error,download_url}`; download via /api/exports/{id}/file
DELETE /api/projects/{id} => 204 (409 if queued/running work; user must cancel first; source reference is NEVER deleted).
GET /api/projects/{id}/events => `{complete,events:[{id,time,start,end,category,score,audio_score,visual_score,scene_score}],detail,...}` bounded last-completed measured timeline; not exhaustive semantic events.
GET /api/benchmarks => stored actual analysis run measurements array.
GET /api/jobs returns ALL active jobs plus at most100 recent terminal jobs, never hiding running work behind a long queue.
POST /api/cache/cleanup => `{removed_files,freed_bytes}` removes orphan temp files/cache only for inactive projects.
PUT /api/settings => Settings. GET/PUT validation errors 422.

## Backend processing agent interfaces
`backend.detection.pipeline.analyze(project: dict, settings: dict, ctx) -> list[dict]` returns highlight-shaped dictionaries (IDs may be assigned by API worker). `project['metadata']` is indexed. project['source_path'] points at original video. Use project id for cache keys. Returned transcript may be empty. Do not write SQLite directly. ctx.cache_dir is project-specific Path. ctx.work_dir is per-job temporary Path. ctx.check_cancelled() raises core.process.Cancelled. ctx.progress(stage: str, processed_seconds: float, total_seconds: float, detail: str='') persists updates; stage name remains stable across a pass. ctx can be duck typed for tests. Thumbnails can be generated best effort in pipeline to ctx.cache_dir / 'thumbnails' / '{highlight_id}.jpg'. Pipeline must have deterministic cache key for content metadata and settings, atomic chunk caches, bounded memory, sparse seek-based video sampling (not full frame decode), no mandatory AI, cache-aware progress. AI refine can import processing.transcription.refine if enabled, nonfatal if missing.

`backend.processing.render.render_clip(project: dict, highlight: dict, settings: dict, ctx) -> dict` returns `{output_path,filename}`. Output path is chosen under settings output_folder or config.EXPORTS_DIR. Ensure random unique filenames; temp output then atomic rename, cancelled/failed temp removed. ctx same as above. Must use transcript to generate real captions; if captions requested but no transcript use optional local transcription if installed model cached, otherwise fail with actionable message (never invented text). Frontend can supply user transcript via PATCH. `backend.processing.transcription.refine(project, highlights, settings, ctx) -> list[dict]` optionally transcribes shortlisted clips only; no implicit network/model downloads. Keep tiny.en CPU INT8, optional package.

## Shared modules supplied by coordinator
`backend.core.config`: ROOT, STORAGE_DIR, PROJECTS_DIR, CACHE_DIR, EXPORTS_DIR (Path); get_binary(name) resolves env FFMPEG_PATH/FFPROBE_PATH or shutil.which and raises RuntimeError if missing. `backend.core.process`: Cancelled exception; `run_process(command: list[str], ctx=None, timeout=60) -> subprocess.CompletedProcess[str]` cancellation-aware monitored process (capture bounded output); for streaming use `managed_process(command, ctx=None)` context manager returns subprocess.Popen with binary stdout and stderr spooled to disk, terminating process group on exit/error. May use own subprocess implementation if necessary, but cancel and cleanup are required.

Frontend uses relative /api via Vite proxy 127.0.0.1:8000; root npm run dev starts backend+frontend, frontend 127.0.0.1:5173. Production frontend built to frontend/dist, FastAPI serves it if present. No mock data. Polling fallback allowed; display disconnected/error/loading/empty states truthfully. No external media requests needed.
