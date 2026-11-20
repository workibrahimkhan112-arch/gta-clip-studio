"""Durable, serialized heavy-work queue with cooperative process cancellation."""
from __future__ import annotations


import logging
import math
import shutil

import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import overload

import psutil
from backend.core.config import CACHE_DIR, EXPORTS_DIR
from backend.core.process import Cancelled
from backend.database import store

log = logging.getLogger(__name__)
ACTIVE = {"queued", "running"}


def _age(timestamp: str | None) -> float:
    if not timestamp:
        return 0.0
    try:
        return max(0.0, (datetime.fromisoformat(store.now()) - datetime.fromisoformat(timestamp)).total_seconds())
    except (ValueError, TypeError):
        return 0.0


@overload
def public_job(job: None) -> None: ...


@overload
def public_job(job: dict) -> dict: ...


def public_job(job: dict | None) -> dict | None:
    if job is None:
        return None
    result = {key: value for key, value in job.items() if not key.startswith("_")}
    if job["status"] == "running":
        result["elapsed_seconds"] = _age(job.get("_run_started_at"))
        seconds = _age(job.get("_stage_started_at"))
        covered = float(job.get("processed_seconds", 0))
        total = float(job.get("total_seconds", 0))
        # Stage-local, measured throughput, not a guessed weighted overall ETA.
        speed = covered / seconds if seconds > 0.1 and covered > 0 and job["stage"] in {"scan", "rendering", "transcription"} else None
        result["speed_ratio"] = speed
        result["eta_seconds"] = max(0, total - covered) / speed if speed and total else None
    return result


class JobContext:
    def __init__(self, runner: "JobRunner", job: dict):
        self.runner = runner
        self.id = job["id"]
        self.project_id = job.get("project_id")
        self.cache_dir = CACHE_DIR / str(job["project_id"] or self.id)
        self.work_dir = CACHE_DIR / "work" / self.id
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.last_stage = ""
        self.last_update = 0.0

    def check_cancelled(self) -> None:
        if self.runner.stopping.is_set() or self.id in self.runner.cancelled:
            raise Cancelled("Job cancelled; committed analysis chunks are preserved")

    def cloud_policy(self, snapshot: dict) -> dict:
        current = store.settings()
        # Render/provider choices stay snapshotted, but a lowered spending limit
        # must stop remaining calls in an already queued/running job.
        return {**snapshot, **{key: current[key] for key in ("ai_daily_request_limit", "ai_daily_budget_usd", "ai_max_output_tokens", "ai_allow_unpriced_models")}}

    def check_cloud_consent(self) -> None:
        # Queued render preferences are snapshots; permission to send media is
        # live and may be withdrawn after queuing or during a request.
        if not store.settings().get("cloud_consent"):
            from backend.processing.cloud import CloudError
            raise CloudError("Cloud consent was withdrawn. No further media will be uploaded.")

    def progress(self, stage: str, processed_seconds: float, total_seconds: float, detail: str = "") -> None:
        self.check_cancelled()
        clock = time.monotonic()
        total = max(0.0, float(total_seconds))
        processed = max(0.0, float(processed_seconds))
        if not math.isfinite(total) or not math.isfinite(processed):
            raise ValueError("Progress must be finite")
        processed = min(total, processed) if total > 0 else 0.0
        changed = stage != self.last_stage
        if not changed and clock - self.last_update < 0.35 and (total == 0 or processed < total):
            return
        with self.runner.lock:
            self.check_cancelled()
            patch = {"stage": stage, "processed_seconds": processed, "total_seconds": total,
                     "progress": processed / total if total > 0 else None, "detail": detail}
            if changed:
                patch["_stage_started_at"] = store.now()
            store.update("jobs", self.id, patch)
        self.last_stage, self.last_update = stage, clock


class MemorySampler:
    def __init__(self):
        self.process = psutil.Process()
        self.baseline = self.rss()
        self.peak = self.baseline
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="rss-sampler", daemon=True)

    def rss(self) -> int:
        total = 0
        try:
            processes = [self.process, *self.process.children(recursive=True)]
        except psutil.Error:
            processes = [self.process]
        for process in processes:
            try:
                total += process.memory_info().rss
            except psutil.Error:
                pass
        return total

    def _run(self):
        while not self.stop_event.wait(0.5):
            self.peak = max(self.peak, self.rss())

    def start(self):
        self.thread.start()

    def stop(self):
        self.peak = max(self.peak, self.rss())
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=2)


class JobRunner:
    def __init__(self):
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.cancelled: set[str] = set()
        self.thread = None

    def start(self):
        self.stopping.clear()
        self.cancelled.clear()
        # Reconcile job-owned render paths before restarting interrupted work.
        # Completed file publication can precede the final SQLite transaction.
        for job in store.active_jobs():
            if job["kind"] == "export" and self._recover_export(job):
                continue
            store.update("jobs", job["id"], {"status": "queued", "stage": "queued", "progress": None,
                "processed_seconds": 0.0, "total_seconds": 0.0, "elapsed_seconds": 0.0,
                "speed_ratio": None, "eta_seconds": None, "detail": "Recovered after interruption; completed analysis chunks will be reused"})
        self.thread = threading.Thread(target=self._loop, name="studio-worker", daemon=True)
        self.thread.start()

    def _recover_export(self, job: dict) -> bool:
        try:
            payload = store.job_payload(job["id"])
            export = store.get("exports", payload.get("export_id", ""))
            if not export:
                return False
            directory = Path(payload.get("settings", {}).get("output_folder") or EXPORTS_DIR).expanduser().resolve()
            # Exact ownership, not a wildcard that could delete unrelated files.
            partial = directory / f".gta-{job['id']}.part.mp4"
            partial.unlink(missing_ok=True)
            if export.get("status") == "completed" and Path(export.get("output_path") or "").is_file():
                rendered = {"output_path": export["output_path"], "filename": export["filename"]}
            else:
                from backend.processing.render import _filename
                path = directory / _filename(payload["highlight"], job["id"])
                if not path.is_file() or not path.stat().st_size:
                    return False
                # A deterministic published path is created only by atomic
                # rename, after encoding and all cancellation-aware work.
                from backend.utils.media import probe
                probe(path)
                rendered = {"output_path": str(path), "filename": path.name}
            store.complete_export(export["id"], job["id"], rendered, float(job.get("elapsed_seconds") or 0))
            return True
        except (OSError, RuntimeError, ValueError, KeyError):
            log.exception("Could not reconcile export %s; retrying through the worker", job["id"])
            return False

    def close(self):
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=20)
        if self.thread and self.thread.is_alive():
            log.warning("Worker still stopping; optional inference cancels at the next segment boundary")

    def submit(self, kind: str, project_id: str, payload: dict) -> dict:
        timestamp = store.now()
        job = {"id": str(uuid.uuid4()), "project_id": project_id, "kind": kind, "status": "queued",
               "stage": "queued", "processed_seconds": 0.0, "total_seconds": 0.0,
               "elapsed_seconds": 0.0, "speed_ratio": None, "eta_seconds": None, "progress": None,
               "detail": "Waiting for the local processing worker", "error": None,
               "created_at": timestamp, "updated_at": timestamp, "result": None}
        with self.lock:
            store.insert("jobs", job, payload)
        self.wake.set()
        return public_job(job)

    def cancel(self, identifier: str) -> dict:
        with self.lock:
            job = store.get("jobs", identifier)
            if job is None:
                raise KeyError(identifier)
            if job["status"] not in ACTIVE:
                return public_job(job)
            self.cancelled.add(identifier)
            if job["status"] == "queued":
                job = store.update("jobs", identifier, {"status": "cancelled", "detail": "Cancelled before processing"})
                self._status_side_effects(job, "cancelled", None)
                self.cancelled.discard(identifier)
            else:
                job = store.update("jobs", identifier, {"detail": "Cancellation requested; stopping the current subprocess"})
        self.wake.set()
        return public_job(job)

    def _loop(self):
        while not self.stopping.is_set():
            job = None
            payload = {}
            with self.lock:
                queued = store.queued_job()
                if queued:
                    job, payload = queued
                    job = store.update("jobs", job["id"], {"status": "running", "stage": "starting",
                        "_run_started_at": store.now(), "_stage_started_at": store.now(),
                        "detail": "Starting local worker", "error": None})
            if job is None:
                self.wake.wait(0.75)
                self.wake.clear()
                continue
            self._execute(job, payload)

    def _enqueue_enhancements(self, project, settings):
        highlights = store.list_items("highlights", project["id"])
        if not highlights:
            return
        active_kinds = {item["kind"] for item in store.active_jobs(project["id"])}
        if settings.get("caption_style", "standard") != "none" and "captions" not in active_kinds:
            ids = [item["id"] for item in highlights if item.get("selected", True) and not item.get("transcript")]
            if ids:
                for identifier in ids:
                    store.update("highlights", identifier, {"caption_status": "pending", "caption_error": None})
                self.submit("captions", project["id"], {"settings": settings, "highlight_ids": ids, "force": False})
        provider = settings.get("ai_scoring_provider", "none")
        if provider != "none" and settings.get("auto_ai_review", False) and settings.get("cloud_consent") and "score" not in active_kinds:
            from backend.core.integrations import get_api_key
            try:
                if provider == "openai" and settings.get("openai_auth_mode") == "chatgpt":
                    from backend.core import chatgpt_auth
                    configured = chatgpt_auth.status()["plan_enabled"]
                else:
                    configured = bool(get_api_key(provider))
            except RuntimeError:
                configured = False
            if configured:
                self.submit("score", project["id"], {"settings": settings, "force": False})
            else:
                for item in highlights:
                    store.update("highlights", item["id"], {"ai_error": "AI review skipped: configure the provider API key in Settings."})

    def _status_side_effects(self, job, status, error):
        project = store.get("projects", job["project_id"])
        if project and job["kind"] in {"analyze", "youtube"}:
            existing = store.list_items("highlights", project["id"])
            project_status = "analyzed" if existing else ("failed" if job["kind"] == "youtube" or status == "failed" else "ready")
            store.update("projects", project["id"], {"status": project_status, "error": error})
        if job["kind"] == "titles" and status in {"failed", "cancelled"}:
            payload = store.job_payload(job["id"])
            identifier = payload.get("highlight_id")
            if identifier and store.get("highlights", identifier):
                store.update("highlights", identifier, {"title_error": error or "Title generation cancelled; previous suggestions are unchanged"})
        if job["kind"] == "captions" and status in {"failed", "cancelled"}:
            for item in store.list_items("highlights", job["project_id"]):
                if item.get("caption_status") == "pending":
                    store.update("highlights", item["id"], {"caption_status": "failed", "caption_error": error or "Caption generation cancelled; retry or choose No captions"})
        if job["kind"] == "export":
            for item in store.list_items("exports", job["project_id"]):
                if item["job_id"] == job["id"]:
                    store.update("exports", item["id"], {"status": status, "error": error})

    def _execute(self, job: dict, payload: dict):
        ctx = None
        sampler = None
        started = time.monotonic()
        try:
            # Resource setup can fail on full/locked volumes. It must be inside
            # the failure boundary or one bad job would kill the only worker.
            ctx = JobContext(self, job)
            sampler = MemorySampler()
            sampler.start()
            ctx.check_cancelled()
            project = store.get("projects", job["project_id"])
            if project is None:
                raise ValueError("Project no longer exists")
            result: dict = {}
            settings = dict(payload.get("settings") or store.settings())
            if not store.settings().get("cloud_consent"):
                settings["cloud_consent"] = False
            if job["kind"] == "analyze":
                from backend.detection.pipeline import analyze
                from backend.utils.media import probe, validate_source
                source = validate_source(project["source_path"])
                metadata = probe(source, ctx)
                store.update("projects", project["id"], {"metadata": metadata, "status": "analyzing", "error": None})
                project["metadata"] = metadata
                # Force bypasses the existing result, not reusable feature chunks.
                cache_mode = "warm" if any(ctx.cache_dir.glob("**/audio-*.json")) or any(ctx.cache_dir.glob("**/video-*.json")) else "cold"
                highlights = analyze(project, settings, ctx)
                with self.lock:
                    ctx.check_cancelled()
                    store.replace_highlights(project["id"], highlights)
                    store.update("projects", project["id"], {"status": "analyzed", "highlight_count": len(highlights)})
                wall = time.monotonic() - started
                sampler.stop()
                measurement = {"id": str(uuid.uuid4()), "project_id": project["id"], "job_id": job["id"],
                    "created_at": store.now(), "video_duration_seconds": metadata["duration"], "wall_seconds": wall,
                    "speed_ratio": metadata["duration"] / wall if wall else None, "peak_rss_bytes": sampler.peak,
                    "peak_additional_rss_bytes": max(0, sampler.peak - sampler.baseline),
                    "memory_target_bytes": int(1.5 * 1024**3), "cache_mode": cache_mode,
                    "settings": settings, "highlight_count": len(highlights)}
                store.insert("benchmarks", measurement)
                result = {"highlight_count": len(highlights), "benchmark": measurement}
            elif job["kind"] == "export":
                from backend.processing.render import render_clip
                highlight = dict(payload["highlight"])
                project = payload.get("project_snapshot") or project
                if settings.get("caption_style", "standard") != "none" and not highlight.get("transcript"):
                    from backend.processing.captioning import caption_highlight
                    highlight = caption_highlight(project, highlight, settings, ctx)
                    with self.lock:
                        latest_highlight = store.get("highlights", highlight["id"])
                        if latest_highlight is not None and not latest_highlight.get("transcript"):
                            store.update("highlights", highlight["id"], {"transcript": highlight["transcript"], "caption_status": "ready", "caption_error": None})
                export_id = payload["export_id"]
                store.update("exports", export_id, {"status": "running", "error": None})
                rendered = render_clip(project, highlight, settings, ctx)
                # Once a valid file is atomically published it is complete, even
                # if cancel arrives just after the final FFmpeg boundary.
                with self.lock:
                    store.complete_export(export_id, job["id"], rendered, time.monotonic() - started)
                return
            elif job["kind"] in {"captions", "score"}:
                requested_ids = payload.get("highlight_ids")
                ids = set(requested_ids) if requested_ids is not None else None
                highlights = [item for item in store.list_items("highlights", project["id"]) if ids is None or item["id"] in ids]
                highlights.sort(key=lambda item: item["score"], reverse=True)
                if not highlights:
                    raise ValueError("No highlights remain; analyze this project first")
                if job["kind"] == "captions":
                    from backend.processing.captioning import generate_captions
                    original = {item["id"]: item.get("transcript", []) for item in highlights}
                    def commit_caption(updated):
                        with self.lock:
                            ctx.check_cancelled()
                            latest = store.get("highlights", updated["id"])
                            if latest is not None and latest.get("transcript", []) == original[updated["id"]]:
                                patch = {key: updated.get(key) for key in ("transcript", "caption_status", "caption_error")}
                                if updated.get("transcript") != latest.get("transcript"):
                                    patch.update(ai_review=None, ai_error=None, ai_titles=[], title_error=None)
                                store.update("highlights", updated["id"], patch)
                    updated = generate_captions(project, highlights, settings, ctx, force=payload.get("force", False), on_result=commit_caption)
                    ready = sum(item.get("caption_status") == "ready" for item in updated)
                    result = {"ready": ready, "failed": len(updated) - ready}
                    if not ready:
                        raise RuntimeError(updated[0].get("caption_error") or "No captions could be generated; choose No captions or retry model setup")
                else:
                    from backend.processing.editorial import score_highlights
                    updated = score_highlights(project, highlights, settings, ctx, force=payload.get("force", False))
                    reviewed = 0
                    errors = []
                    with self.lock:
                        ctx.check_cancelled()
                        for item in updated:
                            if item.get("ai_review") is not None or item.get("ai_error") is not None:
                                latest = store.get("highlights", item["id"])
                                if latest is not None and latest.get("transcript", []) == item.get("transcript", []):
                                    store.update("highlights", item["id"], {"ai_review": item.get("ai_review"), "ai_error": item.get("ai_error")})
                            if item.get("ai_review"):
                                reviewed += 1
                            if item.get("ai_error"):
                                errors.append(item["ai_error"])
                    result = {"reviewed": reviewed, "errors": errors}
                    if not reviewed and errors:
                        raise RuntimeError(errors[0])
            elif job["kind"] == "titles":
                from backend.processing.titles import suggest_titles
                highlight = store.get("highlights", payload["highlight_id"])
                if highlight is None:
                    raise ValueError("Highlight no longer exists")
                suggestions = suggest_titles(project, highlight, settings, ctx, force=payload.get("force", False))
                with self.lock:
                    ctx.check_cancelled()
                    latest = store.get("highlights", highlight["id"])
                    if latest is not None and latest.get("transcript", []) == highlight.get("transcript", []):
                        store.update("highlights", highlight["id"], {"ai_titles": suggestions, "title_error": None})
                result = {"highlight_id": highlight["id"], "suggestions": len(suggestions)}
            elif job["kind"] == "youtube":
                from backend.processing.youtube import download
                result = download(project, settings, ctx)
                store.update("projects", project["id"], {**result, "status": "ready", "error": None})
            else:
                raise ValueError("Unknown processing job kind")
            with self.lock:
                latest = store.get("jobs", job["id"])
                if latest is None:
                    raise RuntimeError("Active job was unexpectedly removed")
                values = public_job(latest)
                patch = {"status": "completed", "elapsed_seconds": time.monotonic() - started,
                         "eta_seconds": 0.0, "detail": "Completed", "result": result,
                         "speed_ratio": values["speed_ratio"]}
                if job["kind"] == "analyze":
                    patch["speed_ratio"] = result["benchmark"]["speed_ratio"]
                store.update("jobs", job["id"], patch)
                if job["kind"] == "analyze" and payload.get("auto_enhance", False):
                    self._enqueue_enhancements(project, settings)
        except Cancelled:
            with self.lock:
                interrupted = self.stopping.is_set() and job["id"] not in self.cancelled
                status = "queued" if interrupted else "cancelled"
                store.update("jobs", job["id"], {"status": status, "elapsed_seconds": time.monotonic() - started,
                    "eta_seconds": None, "detail": "Paused for shutdown; resumes on startup" if interrupted else "Cancelled; completed analysis chunks preserved"})
                self._status_side_effects(job, "queued" if interrupted else "cancelled", None)
        except Exception as exc:
            log.exception("Job %s (%s) failed", job["id"], job["kind"])
            with self.lock:
                store.update("jobs", job["id"], {"status": "failed", "error": str(exc)[:6000],
                    "elapsed_seconds": time.monotonic() - started, "eta_seconds": None, "detail": "Failed; see error for recovery instructions"})
                self._status_side_effects(job, "failed", str(exc)[:6000])
        finally:
            if sampler is not None:
                sampler.stop()
            self.cancelled.discard(job["id"])
            if ctx is not None:
                shutil.rmtree(ctx.work_dir, ignore_errors=True)


runner = JobRunner()
