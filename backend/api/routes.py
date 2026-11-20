from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import mimetypes

import shutil
import statistics
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from backend.core.config import CACHE_DIR, PROJECTS_DIR, STORAGE_DIR, VIDEO_EXTENSIONS, VERSION, get_binary
from backend.core.process import run_process
from backend.database import store
from backend.models.schemas import (AccountAction, AnalyzeRequest, BatchExport, EnhanceRequest, ExportRequest, HighlightPatch, IntegrationKey, LayoutConfig, LocalImport, Settings, YouTubeImport)
from backend.utils.capabilities import capabilities
from backend.utils.media import probe, validate_source
from backend.workers.jobs import ACTIVE, public_job, runner

router = APIRouter(prefix="/api")
log = logging.getLogger(__name__)


def required(table: str, identifier: str) -> dict:
    item = store.get(table, identifier)
    if item is None:
        raise HTTPException(404, f"{table.rstrip('s').capitalize()} not found")
    return item


def needs_media():
    try:
        get_binary("ffmpeg")
        get_binary("ffprobe")
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


def project_response(item):
    item = dict(item)
    item["highlight_count"] = len(store.list_items("highlights", item["id"]))
    return item


@router.get("/health")
def health():
    measurements = store.list_items("benchmarks", limit=100)
    speeds = [m["speed_ratio"] for m in measurements if m.get("speed_ratio") is not None and m.get("cache_mode") == "cold"]
    # Tracked managed files only: do not recursively walk multi-terabyte sources.
    used = sum(int(p.get("metadata", {}).get("file_size", 0)) for p in store.list_items("projects") if p.get("managed_source"))
    for item in store.list_items("exports"):
        try:
            path = Path(item.get("output_path") or "")
            if path.is_file():
                used += path.stat().st_size
        except OSError:
            pass
    return {"status": "ok", "version": VERSION, "capabilities": capabilities(),
            "storage": {"free_bytes": shutil.disk_usage(STORAGE_DIR).free, "used_bytes": used},
            "benchmark": {"runs": len(measurements), "median_speed_ratio": statistics.median(speeds) if speeds else None} if measurements else None}


@router.get("/projects")
def projects():
    return [project_response(item) for item in store.list_items("projects")]


def create_project(path: Path, source_type="local", managed=False, identifier=None, source_url=None, ctx=None):
    metadata = probe(path, ctx)
    item = {"id": identifier or str(uuid.uuid4()), "name": path.stem, "source_type": source_type,
            "source_path": str(path), "source_url": source_url, "managed_source": managed,
            "status": "ready", "created_at": store.now(), "metadata": metadata, "highlight_count": 0,
            "layout": LayoutConfig(mode=store.settings()["default_layout"]).model_dump()}
    store.insert("projects", item)
    return item


async def bounded_json(request: Request, max_bytes=32768):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > max_bytes:
            raise HTTPException(413, "Import request is too large")
    try:
        return json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, "A valid JSON request is required") from exc


@router.post("/projects/local", status_code=201)
async def import_local(request: Request):
    needs_media()
    directory = None
    try:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            # The recommended path import below avoids any copy. Browser uploads
            # spool to disk in Starlette instead of keeping a recording in RAM.
            free = shutil.disk_usage(PROJECTS_DIR).free
            length = int(request.headers.get("content-length", "0"))
            if length and length > free - 512 * 1024**2:
                raise HTTPException(507, "Not enough storage for this upload; use a local-path import")
            async with request.form(max_files=1, max_fields=2) as form:
                upload = form.get("file")
                if not isinstance(upload, UploadFile):
                    raise HTTPException(422, "Choose a video file")
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in VIDEO_EXTENSIONS:
                    raise HTTPException(422, "Supported files: MP4, MKV, MOV, AVI, WebM")
                identifier = str(uuid.uuid4())
                directory = PROJECTS_DIR / identifier
                directory.mkdir(parents=True)
                target = directory / f"source{suffix}"
                written = 0
                # Leave disk headroom for SQLite, caches and muxing.
                budget = max(0, shutil.disk_usage(PROJECTS_DIR).free - 512 * 1024**2)
                with target.open("wb") as output:
                    while chunk := await upload.read(1024 * 1024):
                        written += len(chunk)
                        if written > budget:
                            raise HTTPException(507, "Upload exceeds available disk space")
                        await asyncio.to_thread(output.write, chunk)
                item = await asyncio.to_thread(create_project, validate_source(str(target.resolve())), "local", True, identifier)
                name = Path((upload.filename or "video").replace("\\", "/")).stem
                return store.update("projects", item["id"], {"name": name[:200]})
        data = LocalImport.model_validate(await bounded_json(request))
        path = validate_source(data.path)
        return await asyncio.to_thread(create_project, path)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_context=False)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        # Delete only an incomplete managed upload, never a user-owned source.
        if directory and not store.get("projects", directory.name):
            shutil.rmtree(directory, ignore_errors=True)


@router.post("/projects/youtube", status_code=202)
def import_youtube(body: YouTubeImport):
    from backend.processing.youtube import validate_url
    needs_media()
    if importlib.util.find_spec("yt_dlp") is None:
        raise HTTPException(503, "Install yt-dlp: python -m pip install yt-dlp")
    try:
        url = validate_url(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    item = {"id": str(uuid.uuid4()), "name": f"YouTube VOD · {url.split('v=')[-1]}", "source_type": "youtube",
            "source_path": "", "source_url": url, "managed_source": True, "status": "importing",
            "created_at": store.now(), "metadata": {}, "highlight_count": 0,
            "layout": LayoutConfig(mode=store.settings()["default_layout"]).model_dump()}
    with runner.lock:
        store.insert("projects", item)
        job = runner.submit("youtube", item["id"], {"settings": store.settings()})
    return {"project": item, "job": job}


@router.get("/integrations")
def integrations_status(response: Response):
    from backend.core.integrations import public_integrations
    response.headers["Cache-Control"] = "no-store"
    return public_integrations()


@router.put("/integrations/{provider}")
async def configure_integration(provider: str, request: Request, response: Response):
    from backend.core.integrations import set_api_key
    if provider not in {"gemini", "groq", "openai"}:
        raise HTTPException(422, "Supported providers: gemini, groq and openai")
    try:
        body = IntegrationKey.model_validate(await bounded_json(request, max_bytes=8192))
    except ValidationError:
        # Validation errors normally include input. Never echo a secret key.
        raise HTTPException(422, "Provide an API key string of at most 4096 characters; empty clears the local key") from None
    try:
        result = await asyncio.to_thread(set_api_key, provider, body.api_key.get_secret_value())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    response.headers["Cache-Control"] = "no-store"
    return result


def auth_action(name, *args):
    from backend.core import chatgpt_auth
    try:
        return getattr(chatgpt_auth, name)(*args)
    except chatgpt_auth.AuthError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("/chatgpt")
def chatgpt_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return auth_action("status")


@router.post("/chatgpt/connect")
def chatgpt_connect(response: Response, body: AccountAction | None = None):
    response.headers["Cache-Control"] = "no-store"
    return auth_action("start_login", body.account_id if body else None)


@router.post("/chatgpt/select")
def chatgpt_select(body: AccountAction, response: Response):
    if not body.account_id:
        raise HTTPException(422, "Choose an account")
    response.headers["Cache-Control"] = "no-store"
    return auth_action("select_account", body.account_id)


@router.post("/chatgpt/disconnect")
def chatgpt_disconnect(response: Response, body: AccountAction | None = None):
    response.headers["Cache-Control"] = "no-store"
    return auth_action("disconnect", body.account_id if body else None)


@router.post("/chatgpt/cancel")
def chatgpt_cancel(response: Response):
    response.headers["Cache-Control"] = "no-store"
    auth_action("close")
    return auth_action("status")


@router.get("/chatgpt/models")
def chatgpt_models(response: Response):
    response.headers["Cache-Control"] = "no-store"
    return auth_action("list_models")


@router.get("/usage")
def ai_usage(response: Response):
    from backend.core import usage
    response.headers["Cache-Control"] = "no-store"
    return usage.summary()


@router.post("/highlights/{identifier}/titles", status_code=202)
def title_suggestions(identifier: str, body: AnalyzeRequest | None = None):
    with runner.lock:
        highlight = required("highlights", identifier)
        if store.active_jobs(highlight["project_id"]):
            raise HTTPException(409, "Finish current project work before generating titles")
        if not highlight.get("transcript"):
            raise HTTPException(409, "Generate subtitles first so titles can use this clip's actual dialogue")
        settings = store.settings()
        provider = settings.get("ai_scoring_provider", "none")
        if provider == "none":
            raise HTTPException(409, "Choose an AI provider in Settings first")
        require_cloud(settings, provider)
        return runner.submit("titles", highlight["project_id"], {"settings": settings, "highlight_id": identifier, "force": bool(body and body.force)})


@router.patch("/projects/{identifier}/layout")
def save_layout(identifier: str, body: LayoutConfig):
    with runner.lock:
        required("projects", identifier)
        # Queued exports have their own project snapshot, so editing here is safe.
        return store.update("projects", identifier, {"layout": body.model_dump()})


def enqueue_captions(identifier: str, highlight_ids=None, force=False):
    project = required("projects", identifier)
    if not project.get("source_path"):
        raise HTTPException(409, "Finish importing this project before generating captions")
    if store.active_jobs(identifier):
        raise HTTPException(409, "Finish or cancel current project work before generating captions")
    items = store.list_items("highlights", identifier)
    if highlight_ids is not None:
        items = [item for item in items if item["id"] in highlight_ids]
    if not items:
        raise HTTPException(409, "Analyze this project to find highlights first")
    settings = store.settings()
    if settings.get("caption_provider") == "groq":
        require_cloud(settings, "groq")
    job = runner.submit("captions", identifier, {"settings": settings, "highlight_ids": [item["id"] for item in items], "force": force})
    for item in items:
        if force or not item.get("transcript"):
            store.update("highlights", item["id"], {"caption_status": "pending", "caption_error": None})
    return job


def require_cloud(settings, provider):
    from backend.core.integrations import get_api_key
    if not settings.get("cloud_consent"):
        raise HTTPException(409, "Cloud uploads are disabled. Enable consent in Settings or choose local processing.")
    if provider == "openai" and settings.get("openai_auth_mode") == "chatgpt":
        from backend.core import chatgpt_auth
        status = chatgpt_auth.status()
        if not status["connected"] or not status["plan_enabled"]:
            raise HTTPException(409, "Connect an eligible ChatGPT account and allow plan usage in Settings first")
    elif not get_api_key(provider):
        raise HTTPException(409, f"Configure the {provider} API key in Settings first")


@router.post("/highlights/{identifier}/captions", status_code=202)
def highlight_captions(identifier: str, body: AnalyzeRequest | None = None):
    needs_media()
    with runner.lock:
        highlight = required("highlights", identifier)
        return enqueue_captions(highlight["project_id"], [identifier], bool(body and body.force))


@router.post("/projects/{identifier}/captions", status_code=202)
def project_caption_job(identifier: str, body: EnhanceRequest | None = None):
    needs_media()
    with runner.lock:
        ids = body.highlight_ids if body else None
        if ids is not None:
            available = {item["id"] for item in store.list_items("highlights", identifier)}
            if any(item not in available for item in ids):
                raise HTTPException(404, "A selected highlight was not found in this project")
        return enqueue_captions(identifier, highlight_ids=ids, force=bool(body and body.force))


@router.post("/projects/{identifier}/score", status_code=202)
def score_project(identifier: str, body: EnhanceRequest | None = None):
    needs_media()
    with runner.lock:
        required("projects", identifier)
        if store.active_jobs(identifier):
            raise HTTPException(409, "Finish current project work before requesting AI review")
        available = {item["id"] for item in store.list_items("highlights", identifier)}
        if not available:
            raise HTTPException(409, "Analyze this project first")
        ids = body.highlight_ids if body else None
        if ids is not None and any(item not in available for item in ids):
            raise HTTPException(404, "A selected highlight was not found in this project")
        settings = store.settings()
        provider = settings.get("ai_scoring_provider", "none")
        if provider == "none":
            raise HTTPException(409, "Choose an AI review provider in Settings first")
        require_cloud(settings, provider)
        return runner.submit("score", identifier, {"settings": settings, "force": bool(body and body.force), "highlight_ids": ids})


@router.get("/projects/{identifier}")
def project(identifier: str):
    return project_response(required("projects", identifier))


@router.post("/projects/{identifier}/analyze", status_code=202)
def start_analysis(identifier: str, body: AnalyzeRequest | None = None):
    needs_media()
    with runner.lock:
        item = required("projects", identifier)
        if store.active_jobs(identifier):
            raise HTTPException(409, "This project has queued or running work. Finish or cancel it first.")
        if not item.get("source_path"):
            raise HTTPException(409, "Import has not completed; retry the YouTube import or use a local file")
        if item["status"] == "analyzed" and not (body and body.force):
            # Explicitly rerun through the cache to honor current ranking settings;
            # no synthetic completed job is returned.
            pass
        return runner.submit("analyze", identifier, {"settings": store.settings(), "force": bool(body and body.force), "auto_enhance": body.auto_enhance if body else True})


@router.get("/projects/{identifier}/progress")
def progress(identifier: str):
    required("projects", identifier)
    active = store.active_jobs(identifier)
    if active:
        active.sort(key=lambda item: (item["status"] != "running", item["created_at"]))
        return public_job(active[0])
    items = store.list_items("jobs", identifier, limit=1)
    return public_job(items[0]) if items else None


@router.get("/projects/{identifier}/events")
def detected_events(identifier: str):
    required("projects", identifier)
    path = CACHE_DIR / identifier / "events.json"
    if not path.is_file():
        return {"complete": False, "events": [], "detail": "No completed signal timeline is cached"}
    try:
        if path.stat().st_size > 2 * 1024**2:
            raise ValueError("Timeline exceeds cache limit")
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("project_id") != identifier:
            raise ValueError("Timeline does not match project")
        return document
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(409, "Timeline cache is unavailable; reanalyze to regenerate it") from exc


@router.get("/projects/{identifier}/highlights")
def project_highlights(identifier: str):
    required("projects", identifier)
    return sorted(store.list_items("highlights", identifier), key=lambda item: item["score"], reverse=True)


@router.get("/highlights")
def all_highlights():
    return store.list_items("highlights")


@router.patch("/highlights/{identifier}")
def patch_highlight(identifier: str, body: HighlightPatch):
    with runner.lock:
        highlight = required("highlights", identifier)
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if "subtitle_style" in body.model_fields_set and body.subtitle_style is None:
            patch["subtitle_style"] = None
        segments = patch.get("transcript", [])
        if any(segment["end"] > highlight["duration"] + 0.01 for segment in segments):
            raise HTTPException(422, "Caption timestamps must fit inside the clip and be relative to clip start")
        if any(b["start"] < a["start"] for a, b in zip(segments, segments[1:])):
            raise HTTPException(422, "Caption segments must be in chronological order")
        if "transcript" in patch:
            patch.update(caption_status="ready" if patch["transcript"] else "pending", caption_error=None)
            if patch["transcript"] != highlight.get("transcript", []):
                patch.update(ai_review=None, ai_error=None, ai_titles=[], title_error=None)
        return store.update("highlights", identifier, patch)


@router.delete("/highlights/{identifier}", status_code=204)
def delete_highlight(identifier: str):
    with runner.lock:
        highlight = required("highlights", identifier)
        if store.active_jobs(highlight["project_id"]):
            raise HTTPException(409, "Finish or cancel current project jobs before deleting a highlight")
        store.delete_highlight(identifier)
        (CACHE_DIR / highlight["project_id"] / "thumbnails" / f"{identifier}.jpg").unlink(missing_ok=True)
    return Response(status_code=204)


def export_layout(project):
    from backend.processing.layout import validate_layout
    try:
        validate_layout(project.get("layout"))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def enqueue_export(highlight: dict, style: str | None):
    project = required("projects", highlight["project_id"])
    export_layout(project)
    active = store.active_jobs(project["id"])
    if any(job["kind"] != "export" for job in active):
        raise HTTPException(409, "Finish or cancel this project's analysis, caption or AI work before exporting")
    for item in store.list_items("exports", project["id"]):
        if item["highlight_id"] == highlight["id"] and item["status"] in ACTIVE:
            raise HTTPException(409, "This clip already has a queued or running export")
    settings = store.settings()
    if style:
        settings["caption_style"] = style
    identifier = str(uuid.uuid4())
    # Caller holds runner lock so worker cannot claim before export row exists.
    job = runner.submit("export", project["id"], {"settings": settings, "highlight": highlight, "export_id": identifier, "project_snapshot": project})
    store.insert("exports", {"id": identifier, "project_id": project["id"], "highlight_id": highlight["id"],
        "job_id": job["id"], "status": "queued", "output_path": "", "filename": "",
        "created_at": store.now(), "error": None, "download_url": None, "title": highlight["title"],
        "caption_style": settings["caption_style"], "layout_mode": (project.get("layout") or {}).get("mode", "blurred")})
    return job


@router.post("/highlights/{identifier}/export", status_code=202)
def export_one(identifier: str, body: ExportRequest | None = None):
    needs_media()
    with runner.lock:
        return enqueue_export(required("highlights", identifier), body.caption_style if body else None)


@router.post("/exports/batch", status_code=202)
def export_batch(body: BatchExport):
    needs_media()
    with runner.lock:
        ids = list(dict.fromkeys(body.highlight_ids))
        highlights = [required("highlights", identifier) for identifier in ids]
        # Preflight everything so invalid batches don't enqueue a partial batch.
        for item in highlights:
            export_layout(required("projects", item["project_id"]))
            if any(j["kind"] != "export" for j in store.active_jobs(item["project_id"])):
                raise HTTPException(409, "A selected project is still importing/analyzing")
            if any(e["highlight_id"] == item["id"] and e["status"] in ACTIVE for e in store.list_items("exports", item["project_id"])):
                raise HTTPException(409, "A selected clip already has a pending export")
        return [enqueue_export(item, body.caption_style) for item in highlights]


@router.get("/exports")
def exports():
    return store.list_items("exports")


@router.get("/exports/{identifier}/file")
def export_file(identifier: str):
    item = required("exports", identifier)
    path = Path(item.get("output_path") or "")
    if item["status"] != "completed" or not path.is_file():
        raise HTTPException(404, "Export file is not ready or has been moved")
    return FileResponse(path, filename=item["filename"], media_type="video/mp4")


@router.delete("/exports/{identifier}", status_code=204)
def delete_export(identifier: str):
    with runner.lock:
        item = required("exports", identifier)
        if item["status"] in ACTIVE:
            raise HTTPException(409, "Cancel this export job before deleting its video")
        path = Path(item.get("output_path") or "")
        filename = item.get("filename") or ""
        job_id = item.get("job_id") or ""
        if filename and path.is_file():
            if path.name != filename or not filename.startswith("gta-") or not filename.endswith(f"-{job_id}.mp4"):
                raise HTTPException(409, "Export file ownership could not be verified")
            try:
                path.unlink()
            except OSError as exc:
                raise HTTPException(409, f"Could not delete export file: {exc}") from exc
        store.delete_export(identifier)
    return Response(status_code=204)


@router.get("/projects/{identifier}/media")
def media(identifier: str):
    item = required("projects", identifier)
    path = Path(item.get("source_path") or "")
    if not path.is_file():
        raise HTTPException(404, "Source video is missing or not yet downloaded")
    # Starlette FileResponse provides HTTP Range/206 for seekable long sources.
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")


@router.get("/highlights/{identifier}/thumbnail")
def highlight_thumbnail(identifier: str):
    item = required("highlights", identifier)
    path = CACHE_DIR / item["project_id"] / "thumbnails" / f"{item['id']}.jpg"
    if not path.is_file():
        raise HTTPException(404, "Thumbnail not available; reanalyze to regenerate")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/projects/{identifier}/thumbnail")
def project_thumbnail(identifier: str):
    item = required("projects", identifier)
    target = CACHE_DIR / identifier / "cover.jpg"
    if not target.is_file():
        source = Path(item.get("source_path") or "")
        if not source.is_file():
            raise HTTPException(404, "Preview not available")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"cover-{uuid.uuid4().hex}.jpg")
        try:
            result = run_process([get_binary("ffmpeg"), "-v", "error", "-nostdin", "-y", "-threads", "1",
                "-ss", str(min(5, float(item.get("metadata", {}).get("duration", 0)) / 3)), "-i", str(source),
                "-map", "0:V:0", "-frames:v", "1", "-vf", "scale=320:-2", "-an", "-threads", "1", str(temporary)], timeout=30)
            if result.returncode or not temporary.is_file():
                raise HTTPException(404, "Source thumbnail could not be decoded")
            temporary.replace(target)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        finally:
            temporary.unlink(missing_ok=True)
    return FileResponse(target, media_type="image/jpeg")


@router.delete("/projects/{identifier}", status_code=204)
def delete_project(identifier: str):
    with runner.lock:
        required("projects", identifier)
        if store.active_jobs(identifier):
            raise HTTPException(409, "Cancel all project jobs before deleting this project")
        store.delete_project(identifier)
        # Only application-owned folders, never a referenced source/output path.
        shutil.rmtree(PROJECTS_DIR / identifier, ignore_errors=True)
        shutil.rmtree(CACHE_DIR / identifier, ignore_errors=True)
    return Response(status_code=204)


@router.get("/jobs")
def jobs():
    return [public_job(item) for item in store.recent_jobs()]


@router.get("/jobs/{identifier}")
def job(identifier: str):
    return public_job(required("jobs", identifier))


@router.post("/jobs/{identifier}/cancel")
def cancel(identifier: str):
    try:
        return runner.cancel(identifier)
    except KeyError as exc:
        raise HTTPException(404, "Job not found") from exc


@router.get("/settings")
def settings():
    return store.settings()


@router.put("/settings")
def save_settings(body: Settings):
    if body.output_folder:
        try:
            Path(body.output_folder).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(422, f"Output folder is not writable: {exc}") from exc
    return store.save_settings(body.model_dump())


@router.get("/benchmarks")
def benchmarks():
    return store.list_items("benchmarks", limit=100)


@router.post("/cache/cleanup")
def cleanup():
    removed, freed = 0, 0
    with runner.lock:
        active = {job["project_id"] for job in store.active_jobs()}
        known = {p["id"] for p in store.list_items("projects")}
        for directory in CACHE_DIR.iterdir():
            if not directory.is_dir() or directory.name in active:
                continue
            if directory.name == "work" and active:
                continue
            # Explicit cleanup clears recoverable features for inactive projects,
            # but keeps covers/thumbnails referenced by surviving highlights.
            for path in directory.rglob("*"):
                if path.is_file() and (directory.name not in known or ("thumbnails" not in path.parts and path.name != "cover.jpg")):
                    try:
                        size = path.stat().st_size
                        path.unlink()
                        removed += 1
                        freed += size
                    except OSError:
                        log.warning("Cannot remove cache file %s", path)
    return {"removed_files": removed, "freed_bytes": freed}


@router.get("/events")
async def events(request: Request):
    async def stream():
        while not await request.is_disconnected():
            items = await asyncio.to_thread(jobs)
            yield f"event: update\ndata: {json.dumps({'jobs': items}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
