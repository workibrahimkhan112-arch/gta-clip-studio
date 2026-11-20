"""API/SQLite integration tests with no media binaries, downloads, or shared worker."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api import routes
from backend.core import config
from backend.database import store
from backend.processing import youtube
from backend.utils import capabilities as capability_utils
from backend.utils import media as media_utils
from backend.workers import jobs


MEDIA_BYTES = bytes(range(64))
VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
PROBE_METADATA = {
    "format": {"duration": "120.0"},
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920,
         "height": 1080, "avg_frame_rate": "30000/1001"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac",
         "sample_rate": "48000", "channels": 2},
    ],
}


def local_client(**kwargs):
    return TestClient(
        main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 55000), **kwargs,
    )


def get_json(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


def import_recording(client, source):
    response = client.post("/api/projects/local", json={"path": str(source)})
    assert response.status_code == 201, response.text
    project = response.json()
    # These legacy API tests exercise export/job semantics, not crop selection.
    saved = client.patch(f"/api/projects/{project['id']}/layout", json={"mode": "blurred"})
    assert saved.status_code == 200, saved.text
    return saved.json()


def highlight_data(project_id, *, start=40.0, score=85.0):
    identifier = str(uuid4())
    return {
        "id": identifier, "project_id": project_id, "created_at": store.now(),
        "title": "Measured activity", "start": start, "end": start + 20.0,
        "duration": 20.0, "category": "mixed_activity", "score": score,
        "confidence": 0.8, "audio_score": 80.0, "visual_score": 75.0,
        "scene_score": 20.0, "explanation": "Fixture detection result",
        "selected": True, "transcript": [],
        "thumbnail_url": f"/api/highlights/{identifier}/thumbnail",
    }


def seed_highlight(project_id, **kwargs):
    # There is no create-highlight API; detection normally supplies these rows.
    highlight = highlight_data(project_id, **kwargs)
    store.insert("highlights", highlight)
    return highlight


def transcript():
    return [{
        "start": 0.25, "end": 2.0, "text": "Actual player dialogue!",
        "words": [
            {"start": 0.25, "end": 0.75, "word": "Actual"},
            {"start": 0.8, "end": 1.2, "word": "player"},
            {"start": 1.3, "end": 2.0, "word": "dialogue!"},
        ],
    }]


def job_payload(identifier):
    with store.connection() as db:
        row = db.execute("SELECT payload FROM jobs WHERE id=?", (identifier,)).fetchone()
    assert row is not None
    return json.loads(row["payload"])


def run_one_queued_job(worker, monkeypatch):
    """Run the real claim/execute/commit path synchronously, then exit the loop."""
    assert store.queued_job() is not None
    execute = worker._execute

    def execute_once(job, payload):
        try:
            execute(job, payload)
        finally:
            worker.stopping.set()

    with monkeypatch.context() as controlled:
        controlled.setattr(worker, "_execute", execute_once)
        worker.stopping.clear()
        try:
            worker._loop()
        finally:
            worker.stopping.clear()


@pytest.fixture
def studio(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    paths = {
        "STORAGE_DIR": storage,
        "PROJECTS_DIR": storage / "projects",
        "CACHE_DIR": storage / "cache",
        "EXPORTS_DIR": storage / "exports",
        "DATABASE_PATH": storage / "studio.sqlite3",
    }
    # Patch imported aliases as well as config, including lifespan's log path.
    for module in (config, store, routes, jobs, main, youtube):
        for name, value in paths.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)

    # Fixture behavior must not depend on free space on the host's temp drive.
    disk_usage = routes.shutil.disk_usage(storage.parent)
    monkeypatch.setattr(routes.shutil, "disk_usage", lambda path: type(disk_usage)(100 * 1024**3, 20 * 1024**3, 80 * 1024**3))

    worker = jobs.JobRunner()
    monkeypatch.setattr(worker, "start", Mock(name="disabled_worker_start"))
    for module in (jobs, routes, main):
        monkeypatch.setattr(module, "runner", worker)

    def binary(name):
        assert name in {"ffmpeg", "ffprobe"}
        return f"stub-{name}"

    for module in (config, routes, media_utils, capability_utils, youtube):
        monkeypatch.setattr(module, "get_binary", binary)
    probe_process = Mock(return_value=subprocess.CompletedProcess(
        ["stub-ffprobe"], 0, json.dumps(PROBE_METADATA), "",
    ))
    monkeypatch.setattr(media_utils, "run_process", probe_process)

    source = tmp_path / "external recordings" / "GTA session é.mp4"
    source.parent.mkdir()
    source.write_bytes(MEDIA_BYTES)
    environment = SimpleNamespace(
        storage=storage, projects=paths["PROJECTS_DIR"], cache=paths["CACHE_DIR"],
        exports=paths["EXPORTS_DIR"], source=source, runner=worker, probe=probe_process,
    )
    try:
        yield environment
    finally:
        # Finish every controlled worker before monkeypatch restores DB globals.
        worker.close()
        assert worker.thread is None or not worker.thread.is_alive()


@pytest.fixture
def client(studio):
    with local_client() as client:
        yield client


@pytest.fixture
def project(client, studio):
    return import_recording(client, studio.source)


@pytest.fixture
def highlight(project):
    return seed_highlight(project["id"])


def test_health_and_empty_collections(client, studio):
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    health = response.json()
    assert health["status"] == "ok"
    assert health["version"] == config.VERSION
    assert health["benchmark"] is None
    assert health["storage"]["used_bytes"] == 0
    assert health["storage"]["free_bytes"] > 0
    assert health["capabilities"]["ffmpeg"] is True
    assert health["capabilities"]["ffprobe"] is True
    assert health["capabilities"]["logical_cores"] >= 1
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-frame-options"] == "DENY"
    for endpoint in ("projects", "highlights", "exports", "jobs", "benchmarks"):
        assert get_json(client, f"/api/{endpoint}") == []
    defaults = get_json(client, "/api/settings")
    assert defaults["max_clips"] == 10
    assert defaults["caption_style"] == "standard"
    assert defaults["ai_refinement"] is False
    assert client.get("/api/not-an-endpoint").status_code == 404
    assert store.DATABASE_PATH == studio.storage / "studio.sqlite3"
    assert store.DATABASE_PATH.is_file()
    studio.runner.start.assert_called_once_with()
    assert studio.runner.thread is None
    studio.probe.assert_not_called()


@pytest.mark.parametrize("invalid", [
    pytest.param({"max_clips": 0}, id="max-clips-low"),
    pytest.param({"max_clips": 51}, id="max-clips-high"),
    pytest.param({"sampling_interval": 3}, id="sampling-interval"),
    pytest.param({"analysis_width": 640}, id="analysis-width"),
    pytest.param({"audio_sample_rate": 44100}, id="audio-sample-rate"),
    pytest.param({"cpu_workers": 3}, id="cpu-bound"),
    pytest.param({"memory_mode": "unbounded"}, id="memory-mode"),
    pytest.param({"clip_duration": 9}, id="clip-duration-low"),
    pytest.param({"clip_duration": 91}, id="clip-duration-high"),
    pytest.param({"output_resolution": "1920x1080"}, id="resolution"),
    pytest.param({"caption_style": "invented"}, id="caption-style"),
    pytest.param({"analysis_chunk_seconds": 59}, id="chunk-low"),
    pytest.param({"analysis_chunk_seconds": 601}, id="chunk-high"),
    pytest.param({"output_folder": "relative/exports"}, id="relative-output"),
    pytest.param({"unexpected": True}, id="unknown-field"),
])
def test_invalid_settings_do_not_change_persisted_settings(client, invalid):
    previous = get_json(client, "/api/settings")
    response = client.put("/api/settings", json={**previous, **invalid})
    assert response.status_code == 422, response.text
    assert get_json(client, "/api/settings") == previous
    assert store.settings() == previous


def test_settings_create_output_directory_and_survive_initialize(client, studio):
    folder = studio.source.parent / "custom exports"
    settings = {
        **get_json(client, "/api/settings"), "max_clips": 50, "sampling_interval": 1,
        "analysis_width": 480, "cpu_workers": 1, "memory_mode": "balanced",
        "clip_duration": 90, "output_resolution": "1080x1920",
        "caption_style": "animated", "ai_refinement": True,
        "analysis_chunk_seconds": 600, "output_folder": str(folder),
    }
    response = client.put("/api/settings", json=settings)
    assert response.status_code == 200, response.text
    assert response.json() == settings
    assert folder.is_dir()
    store.initialize()
    assert get_json(client, "/api/settings") == settings


def test_output_folder_that_is_a_file_is_rejected_without_saving(client, studio):
    previous = get_json(client, "/api/settings")
    response = client.put("/api/settings", json={**previous, "output_folder": str(studio.source)})
    assert response.status_code == 422, response.text
    assert "Output folder" in response.json()["detail"]
    assert studio.source.read_bytes() == MEDIA_BYTES
    assert get_json(client, "/api/settings") == previous


def test_local_path_import_probes_real_metadata_without_copying(client, studio, project):
    assert project["source_path"] == str(studio.source.resolve())
    assert project["name"] == studio.source.stem
    assert project["source_type"] == "local"
    assert project["source_url"] is None
    assert project["managed_source"] is False
    assert project["status"] == "ready"
    assert project["metadata"]["duration"] == 120.0
    assert project["metadata"]["fps"] == pytest.approx(30000 / 1001)
    assert project["metadata"]["audio_streams"] == [
        {"index": 1, "codec": "aac", "sample_rate": 48000, "channels": 2},
    ]
    assert project["metadata"]["file_size"] == len(MEDIA_BYTES)
    assert get_json(client, f"/api/projects/{project['id']}") == project
    assert get_json(client, f"/api/projects/{project['id']}/progress") is None
    assert get_json(client, "/api/projects") == [project]
    assert get_json(client, "/api/health")["storage"]["used_bytes"] == 0
    assert list(studio.projects.iterdir()) == []
    assert not studio.source.is_relative_to(studio.storage)
    studio.probe.assert_called_once()
    command = studio.probe.call_args.args[0]
    assert command[0] == "stub-ffprobe"
    assert command[-1] == str(studio.source.resolve())
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    assert studio.probe.call_args.kwargs == {"ctx": None, "timeout": 60}


@pytest.mark.parametrize("kind", ["relative", "missing", "unsupported", "empty"])
def test_invalid_local_sources_leave_no_project(client, studio, kind):
    if kind == "relative":
        source = Path("relative.mp4")
    else:
        source = studio.source.with_name({
            "missing": "missing.mp4", "unsupported": "recording.txt", "empty": "empty.mp4",
        }[kind])
        if kind != "missing":
            source.write_bytes(b"not a video" if kind == "unsupported" else b"")
    response = client.post("/api/projects/local", json={"path": str(source)})
    assert response.status_code == 422, response.text
    assert get_json(client, "/api/projects") == []
    assert list(studio.projects.iterdir()) == []
    studio.probe.assert_not_called()


@pytest.mark.parametrize("failure", ["decoder-error", "no-video", "nonfinite-duration"])
def test_bad_probe_results_do_not_create_projects(client, studio, failure):
    raw = copy.deepcopy(PROBE_METADATA)
    code, error = 0, ""
    if failure == "decoder-error":
        code, error = 1, "Invalid container"
    elif failure == "no-video":
        raw["streams"] = raw["streams"][1:]
    else:
        raw["format"]["duration"] = "NaN"
    studio.probe.return_value = subprocess.CompletedProcess([], code, json.dumps(raw), error)
    response = client.post("/api/projects/local", json={"path": str(studio.source)})
    assert response.status_code == 422, response.text
    assert get_json(client, "/api/projects") == []
    assert studio.source.read_bytes() == MEDIA_BYTES


def test_uploaded_source_is_managed_and_filename_cannot_escape_storage(client, studio):
    response = client.post("/api/projects/local", files={
        "file": ("../../recording.MP4", MEDIA_BYTES, "video/mp4"),
    })
    assert response.status_code == 201, response.text
    project = response.json()
    source = Path(project["source_path"])
    assert project["managed_source"] is True
    assert project["name"] == "recording"
    assert source.parent == studio.projects / project["id"]
    assert source.name == "source.mp4"
    assert source.read_bytes() == MEDIA_BYTES
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert not source.exists()
    assert studio.source.read_bytes() == MEDIA_BYTES


def test_failed_upload_removes_only_incomplete_managed_files(client, studio):
    studio.probe.return_value = subprocess.CompletedProcess([], 1, "", "Invalid container")
    response = client.post("/api/projects/local", files={
        "file": ("bad.mp4", MEDIA_BYTES, "video/mp4"),
    })
    assert response.status_code == 422, response.text
    assert get_json(client, "/api/projects") == []
    assert list(studio.projects.iterdir()) == []
    assert studio.source.read_bytes() == MEDIA_BYTES


def test_local_import_reports_missing_binary_without_writing(client, studio, monkeypatch):
    def missing_binary(name):
        raise RuntimeError(f"{name} is not installed")

    monkeypatch.setattr(routes, "get_binary", missing_binary)
    response = client.post("/api/projects/local", json={"path": str(studio.source)})
    assert response.status_code == 503, response.text
    assert "ffmpeg is not installed" in response.json()["detail"]
    assert get_json(client, "/api/projects") == []
    studio.probe.assert_not_called()


def test_media_full_response_and_missing_source(client, studio, project):
    url = f"/api/projects/{project['id']}/media"
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.content == MEDIA_BYTES
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["accept-ranges"] == "bytes"
    assert int(response.headers["content-length"]) == len(MEDIA_BYTES)
    studio.source.unlink()
    assert client.get(url).status_code == 404
    assert client.get("/api/projects/missing/media").status_code == 404


@pytest.mark.parametrize(("requested", "expected", "content_range"), [
    ("bytes=0-7", MEDIA_BYTES[:8], "bytes 0-7/64"),
    ("bytes=16-", MEDIA_BYTES[16:], "bytes 16-63/64"),
    ("bytes=-5", MEDIA_BYTES[-5:], "bytes 59-63/64"),
    ("bytes=60-999", MEDIA_BYTES[60:], "bytes 60-63/64"),
])
def test_media_byte_ranges(client, project, requested, expected, content_range):
    response = client.get(f"/api/projects/{project['id']}/media", headers={"Range": requested})
    assert response.status_code == 206, response.text
    assert response.content == expected
    assert response.headers["content-range"] == content_range
    assert int(response.headers["content-length"]) == len(expected)


def test_unsatisfiable_media_range_is_416(client, project):
    response = client.get(f"/api/projects/{project['id']}/media", headers={"Range": "bytes=64-"})
    assert response.status_code == 416, response.text
    assert response.headers["content-range"] == "bytes */64"


def test_event_timeline_returns_only_real_completed_cache(client, studio, project):
    url = f"/api/projects/{project['id']}/events"
    assert get_json(client, url)["complete"] is False
    directory = studio.cache / project["id"]
    directory.mkdir(parents=True)
    document = {"project_id": project["id"], "complete": True, "detail": "Measured peaks", "events": [{"time": 12, "score": 70}]}
    (directory / "events.json").write_text(json.dumps(document), encoding="utf-8")
    assert get_json(client, url) == document
    (directory / "events.json").write_text("corrupt", encoding="utf-8")
    assert client.get(url).status_code == 409
    assert client.get("/api/projects/missing/events").status_code == 404


def test_upload_rejects_low_disk_space_without_creating_project(client, studio, monkeypatch):
    usage = routes.shutil.disk_usage(studio.storage)
    monkeypatch.setattr(routes.shutil, "disk_usage", lambda path: type(usage)(1024**3, 1000 * 1024**2, 24 * 1024**2))
    response = client.post("/api/projects/local", files={"file": ("video.mp4", MEDIA_BYTES, "video/mp4")})
    assert response.status_code == 507
    assert get_json(client, "/api/projects") == []
    assert list(studio.projects.iterdir()) == []


def test_patch_transcript_persists_relative_words_and_can_clear(client, highlight):
    url = f"/api/highlights/{highlight['id']}"
    patch = {"selected": False, "title": "Edited clip é", "transcript": transcript()}
    response = client.patch(url, json=patch)
    assert response.status_code == 200, response.text
    assert response.json() == {**highlight, **patch, "caption_status": "ready", "caption_error": None, "ai_review": None, "ai_error": None, "ai_titles": [], "title_error": None}
    assert store.get("highlights", highlight["id"]) == response.json()
    assert get_json(client, f"/api/projects/{highlight['project_id']}/highlights") == [response.json()]
    unchanged = client.patch(url, json={"title": None, "transcript": None})
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json() == response.json()
    cleared = client.patch(url, json={"transcript": []})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["transcript"] == []
    assert cleared.json()["start"] == 40.0
    assert client.patch("/api/highlights/missing", json={"selected": True}).status_code == 404


@pytest.mark.parametrize("patch", [
    pytest.param({"title": ""}, id="empty-title"),
    pytest.param({"title": "x" * 161}, id="long-title"),
    pytest.param({"start": 0}, id="immutable-clip-bounds"),
    pytest.param({"transcript": [{"start": -1, "end": 1, "text": "bad"}]}, id="negative-time"),
    pytest.param({"transcript": [{"start": 2, "end": 1, "text": "bad"}]}, id="reversed-segment"),
    pytest.param({"transcript": [{"start": 1, "end": 1, "text": "bad"}]}, id="empty-interval"),
    pytest.param({"transcript": [{"start": 0, "end": 21, "text": "bad"}]}, id="past-clip-end"),
    pytest.param({"transcript": [
        {"start": 5, "end": 6, "text": "later"}, {"start": 1, "end": 2, "text": "earlier"},
    ]}, id="unordered-segments"),
    pytest.param({"transcript": [{"start": "NaN", "end": 2, "text": "bad"}]}, id="nonfinite-start"),
    pytest.param({"transcript": [{"start": 0, "end": "Infinity", "text": "bad"}]}, id="nonfinite-end"),
    pytest.param({"transcript": [{"start": 0, "end": 1, "text": ""}]}, id="empty-text"),
    pytest.param({"transcript": [{"start": 1, "end": 3, "text": "bad", "words": [
        {"start": 0, "end": 2, "word": "outside"},
    ]}]}, id="word-before-segment"),
    pytest.param({"transcript": [{"start": 1, "end": 3, "text": "bad", "words": [
        {"start": 2, "end": 4, "word": "outside"},
    ]}]}, id="word-after-segment"),
    pytest.param({"transcript": [{"start": 0, "end": 3, "text": "bad", "words": [
        {"start": 2, "end": 1, "word": "backwards"},
    ]}]}, id="reversed-word"),
    pytest.param({"transcript": [{"start": 0, "end": 3, "text": "bad", "words": [
        {"start": 2, "end": 3, "word": "later"}, {"start": 0, "end": 1, "word": "earlier"},
    ]}]}, id="unordered-words"),
])
def test_invalid_highlight_patches_leave_original_unchanged(client, highlight, patch):
    response = client.patch(f"/api/highlights/{highlight['id']}", json=patch)
    assert response.status_code == 422, response.text
    assert store.get("highlights", highlight["id"]) == highlight


def test_export_enqueue_snapshots_settings_and_caption_then_cancel_cascades(client, highlight):
    url = f"/api/highlights/{highlight['id']}/export"
    response = client.post(url, json={"caption_style": "animated"})
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["kind"] == "export"
    assert job["project_id"] == highlight["project_id"]
    assert job["status"] == job["stage"] == "queued"
    assert job["progress"] is None
    assert not any(key.startswith("_") for key in job)
    assert get_json(client, f"/api/jobs/{job['id']}") == job
    assert get_json(client, "/api/jobs") == [job]
    payload = job_payload(job["id"])
    assert payload["highlight"] == highlight
    assert payload["settings"]["caption_style"] == "animated"
    assert get_json(client, "/api/settings")["caption_style"] == "standard"
    exports = get_json(client, "/api/exports")
    assert len(exports) == 1
    export = exports[0]
    assert export["id"] == payload["export_id"]
    assert export["job_id"] == job["id"]
    assert export["highlight_id"] == highlight["id"]
    assert export["caption_style"] == "animated"
    assert export["status"] == "queued"
    assert export["download_url"] is None
    assert client.get(f"/api/exports/{export['id']}/file").status_code == 404
    assert client.post(url).status_code == 409
    assert len(get_json(client, "/api/jobs")) == 1

    assert client.patch(f"/api/highlights/{highlight['id']}", json={"title": "New title"}).status_code == 200
    settings = get_json(client, "/api/settings")
    assert client.put("/api/settings", json={**settings, "caption_style": "none"}).status_code == 200
    assert job_payload(job["id"]) == payload
    cancelled = client.post(f"/api/jobs/{job['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert get_json(client, "/api/exports")[0]["status"] == "cancelled"
    again = client.post(f"/api/jobs/{job['id']}/cancel")
    assert again.status_code == 200, again.text
    assert again.json() == cancelled.json()
    assert client.post(url).status_code == 202
    assert client.post("/api/jobs/missing/cancel").status_code == 404
    assert client.get("/api/jobs/missing").status_code == 404


def test_running_export_cancel_is_cooperative_and_cleans_work_files(client, studio, highlight):
    response = client.post(f"/api/highlights/{highlight['id']}/export")
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    store.update("jobs", identifier, {
        "status": "running", "stage": "rendering", "_run_started_at": store.now(),
    })
    cancelled = client.post(f"/api/jobs/{identifier}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "running"
    assert "Cancellation requested" in cancelled.json()["detail"]
    assert identifier in studio.runner.cancelled
    # The real executor observes cancellation before it could launch FFmpeg.
    studio.runner._execute(store.get("jobs", identifier), job_payload(identifier))
    assert get_json(client, f"/api/jobs/{identifier}")["status"] == "cancelled"
    assert get_json(client, "/api/exports")[0]["status"] == "cancelled"
    assert identifier not in studio.runner.cancelled
    assert not (studio.cache / "work" / identifier).exists()
    assert studio.source.read_bytes() == MEDIA_BYTES


def test_batch_export_deduplicates_and_enqueues_each_clip(client, project, highlight):
    second = seed_highlight(project["id"], start=70.0, score=70.0)
    response = client.post("/api/exports/batch", json={
        "highlight_ids": [highlight["id"], second["id"], highlight["id"]], "caption_style": "none",
    })
    assert response.status_code == 202, response.text
    submitted = response.json()
    assert len(submitted) == 2
    assert len({job["id"] for job in submitted}) == 2
    assert all(job["status"] == "queued" and job["kind"] == "export" for job in submitted)
    assert [job_payload(job["id"])["highlight"]["id"] for job in submitted] == [highlight["id"], second["id"]]
    assert all(job_payload(job["id"])["settings"]["caption_style"] == "none" for job in submitted)
    assert len(get_json(client, "/api/exports")) == 2
    assert get_json(client, f"/api/projects/{project['id']}/progress")["id"] in {job["id"] for job in submitted}


@pytest.mark.parametrize("conflict", ["missing-highlight", "pending-export", "active-analysis"])
def test_batch_preflight_never_enqueues_a_partial_batch(client, studio, project, highlight, conflict):
    other_source = studio.source.with_name("other.mp4")
    other_source.write_bytes(MEDIA_BYTES)
    other_project = import_recording(client, other_source)
    other = seed_highlight(other_project["id"])
    blocked_id = other["id"]
    expected_status = 409
    if conflict == "missing-highlight":
        blocked_id, expected_status = str(uuid4()), 404
    elif conflict == "pending-export":
        assert client.post(f"/api/highlights/{blocked_id}/export").status_code == 202
    else:
        assert client.post(f"/api/projects/{other_project['id']}/analyze").status_code == 202
    previous_jobs = get_json(client, "/api/jobs")
    previous_exports = get_json(client, "/api/exports")
    response = client.post("/api/exports/batch", json={"highlight_ids": [highlight["id"], blocked_id]})
    assert response.status_code == expected_status, response.text
    assert get_json(client, "/api/jobs") == previous_jobs
    assert get_json(client, "/api/exports") == previous_exports


@pytest.mark.parametrize("body", [
    {"highlight_ids": []}, {"highlight_ids": ["missing"] * 51},
    {"highlight_ids": ["missing"], "caption_style": "invalid"},
])
def test_batch_request_validation_does_not_enqueue(client, body):
    response = client.post("/api/exports/batch", json=body)
    assert response.status_code == 422, response.text
    assert get_json(client, "/api/jobs") == []
    assert get_json(client, "/api/exports") == []


def test_active_analysis_blocks_duplicate_analysis_and_single_export(client, project, highlight):
    url = f"/api/projects/{project['id']}/analyze"
    submitted = client.post(url, json={"force": True})
    assert submitted.status_code == 202, submitted.text
    assert job_payload(submitted.json()["id"])["force"] is True
    assert client.post(url).status_code == 409
    assert client.post(f"/api/highlights/{highlight['id']}/export").status_code == 409
    assert len(get_json(client, "/api/jobs")) == 1
    assert get_json(client, "/api/exports") == []


def test_analysis_results_and_settings_survive_sqlite_reinit_and_new_lifespan(studio, monkeypatch):
    from backend.detection import pipeline

    with local_client() as client:
        settings = {**get_json(client, "/api/settings"), "max_clips": 7, "caption_style": "none"}
        assert client.put("/api/settings", json=settings).status_code == 200
        project = import_recording(client, studio.source)
        detected = highlight_data(project["id"])

        def analyze(actual_project, actual_settings, ctx):
            assert actual_project["id"] == project["id"]
            assert actual_settings == settings
            ctx.progress("scan", 120, 120, "Controlled detector completed")
            return [copy.deepcopy(detected)]

        monkeypatch.setattr(pipeline, "analyze", analyze)
        response = client.post(f"/api/projects/{project['id']}/analyze")
        assert response.status_code == 202, response.text
        identifier = response.json()["id"]
        run_one_queued_job(studio.runner, monkeypatch)
        completed = get_json(client, f"/api/jobs/{identifier}")
        assert completed["status"] == "completed", completed
        assert completed["result"]["highlight_count"] == 1
        assert completed["result"]["benchmark"]["cache_mode"] == "cold"
        assert completed["result"]["benchmark"]["wall_seconds"] > 0
        assert get_json(client, f"/api/projects/{project['id']}")["status"] == "analyzed"
        assert get_json(client, "/api/highlights") == [detected]
        patch = {"selected": False, "title": "Durable caption edit", "transcript": transcript()}
        assert client.patch(f"/api/highlights/{detected['id']}", json=patch).status_code == 200
        exported = client.post(f"/api/highlights/{detected['id']}/export")
        assert exported.status_code == 202, exported.text
        assert client.post(f"/api/jobs/{exported.json()['id']}/cancel").status_code == 200
        endpoints = [
            "/api/settings", "/api/projects", "/api/highlights", "/api/jobs",
            "/api/exports", "/api/benchmarks", f"/api/projects/{project['id']}/highlights",
        ]
        expected = {path: get_json(client, path) for path in endpoints}
        assert len(expected["/api/benchmarks"]) == 1
        store.initialize()
        assert {path: get_json(client, path) for path in endpoints} == expected

    with local_client() as restarted:
        assert {path: get_json(restarted, path) for path in endpoints} == expected
        health = get_json(restarted, "/api/health")
        assert health["benchmark"]["runs"] == 1
        assert health["benchmark"]["median_speed_ratio"] == completed["speed_ratio"]
        assert get_json(restarted, f"/api/jobs/{identifier}") == completed
    assert studio.runner.thread is None


def test_startup_requeues_interrupted_jobs_preserving_payload_and_results(studio, monkeypatch):
    with local_client() as client:
        project = import_recording(client, studio.source)
        highlight = seed_highlight(project["id"])
        response = client.post(f"/api/projects/{project['id']}/analyze")
        assert response.status_code == 202, response.text
        terminal_id = response.json()["id"]
        response = client.post(f"/api/jobs/{terminal_id}/cancel")
        assert response.status_code == 200, response.text
        terminal = response.json()
        response = client.post(f"/api/projects/{project['id']}/analyze", json={"force": True})
        assert response.status_code == 202, response.text
        running_id = response.json()["id"]
        store.update("jobs", running_id, {
            "status": "running", "stage": "scan", "progress": 0.5,
            "processed_seconds": 60.0, "total_seconds": 120.0, "elapsed_seconds": 20.0,
            "speed_ratio": 3.0, "eta_seconds": 20.0,
            "_run_started_at": "2000-01-01T00:00:00+00:00",
            "_stage_started_at": "2000-01-01T00:00:00+00:00",
        })
        other_source = studio.source.with_name("queued.mp4")
        other_source.write_bytes(MEDIA_BYTES)
        other = import_recording(client, other_source)
        response = client.post(f"/api/projects/{other['id']}/analyze")
        assert response.status_code == 202, response.text
        queued_id = response.json()["id"]
        payloads = {identifier: job_payload(identifier) for identifier in (running_id, queued_id)}
        cache = studio.cache / project["id"] / "audio-preserved.json"
        cache.parent.mkdir(parents=True)
        cache.write_text('{"completed_chunk":true}', encoding="utf-8")

    # Exercise real recovery, but the new thread's target cannot consume the DB.
    loop = Mock(name="non_consuming_worker_loop")
    monkeypatch.setattr(studio.runner, "_loop", loop)
    monkeypatch.setattr(studio.runner, "start", lambda: jobs.JobRunner.start(studio.runner))
    with local_client() as restarted:
        studio.runner.thread.join(timeout=2)
        assert not studio.runner.thread.is_alive()
        loop.assert_called_once_with()
        for identifier in (running_id, queued_id):
            recovered = get_json(restarted, f"/api/jobs/{identifier}")
            assert recovered["status"] == recovered["stage"] == "queued"
            assert recovered["progress"] is None
            assert recovered["processed_seconds"] == recovered["total_seconds"] == 0.0
            assert recovered["elapsed_seconds"] == 0.0
            assert recovered["speed_ratio"] is None
            assert recovered["eta_seconds"] is None
            assert "Recovered after interruption" in recovered["detail"]
            assert not any(key.startswith("_") for key in recovered)
            assert job_payload(identifier) == payloads[identifier]
        assert get_json(restarted, f"/api/jobs/{terminal_id}") == terminal
        assert get_json(restarted, f"/api/projects/{project['id']}/highlights") == [highlight]
        assert cache.read_text(encoding="utf-8") == '{"completed_chunk":true}'
        assert studio.runner.cancelled == set()


@pytest.mark.parametrize("status", ["queued", "running"])
def test_delete_requires_inactive_jobs_and_never_removes_external_source(client, studio, project, highlight, status):
    owned = studio.projects / project["id"]
    cached = studio.cache / project["id"]
    for directory in (owned, cached):
        directory.mkdir(parents=True)
        (directory / "owned.tmp").write_bytes(b"managed data")
    response = client.post(f"/api/projects/{project['id']}/analyze")
    assert response.status_code == 202, response.text
    identifier = response.json()["id"]
    if status == "running":
        store.update("jobs", identifier, {"status": "running", "stage": "scan"})
    url = f"/api/projects/{project['id']}"
    blocked = client.delete(url)
    assert blocked.status_code == 409, blocked.text
    assert owned.is_dir() and cached.is_dir()
    assert studio.source.read_bytes() == MEDIA_BYTES
    assert store.get("highlights", highlight["id"]) == highlight
    cancelled = client.post(f"/api/jobs/{identifier}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    if status == "running":
        assert client.delete(url).status_code == 409
        studio.runner._execute(store.get("jobs", identifier), job_payload(identifier))
    deleted = client.delete(url)
    assert deleted.status_code == 204, deleted.text
    assert deleted.content == b""
    assert studio.source.read_bytes() == MEDIA_BYTES
    assert not owned.exists()
    assert not cached.exists()
    assert client.get(url).status_code == 404
    assert client.delete(url).status_code == 404
    assert store.get("highlights", highlight["id"]) is None
    assert store.get("jobs", identifier) is None
    assert get_json(client, "/api/projects") == []


def test_selected_caption_job_only_queues_requested_highlights(client, project, highlight):
    other = seed_highlight(project["id"], start=70.0)
    url = f"/api/projects/{project['id']}/captions"
    invalid = client.post(url, json={"highlight_ids": ["missing"]})
    assert invalid.status_code == 404
    response = client.post(url, json={"highlight_ids": [highlight["id"]]})
    assert response.status_code == 202, response.text
    assert job_payload(response.json()["id"])["highlight_ids"] == [highlight["id"]]
    assert store.get("highlights", highlight["id"])["caption_status"] == "pending"
    assert store.get("highlights", other["id"]) == other


def test_selected_ai_review_only_queues_requested_highlights(client, project, highlight, monkeypatch):
    seed_highlight(project["id"], start=70.0)
    monkeypatch.setattr(routes, "require_cloud", lambda settings, provider: None)
    settings = store.settings()
    settings["ai_scoring_provider"] = "gemini"
    store.save_settings(settings)
    url = f"/api/projects/{project['id']}/score"
    assert client.post(url, json={"highlight_ids": ["missing"]}).status_code == 404
    response = client.post(url, json={"highlight_ids": [highlight["id"]]})
    assert response.status_code == 202, response.text
    assert job_payload(response.json()["id"])["highlight_ids"] == [highlight["id"]]


def test_delete_highlight_preserves_source_and_other_highlights(client, studio, project, highlight):
    other = seed_highlight(project["id"], start=70.0)
    thumbnail = studio.cache / project["id"] / "thumbnails" / f"{highlight['id']}.jpg"
    thumbnail.parent.mkdir(parents=True)
    thumbnail.write_bytes(b"thumbnail")
    url = f"/api/highlights/{highlight['id']}"
    response = client.delete(url)
    assert response.status_code == 204, response.text
    assert store.get("highlights", highlight["id"]) is None
    assert store.get("highlights", other["id"]) == other
    assert not thumbnail.exists()
    assert studio.source.read_bytes() == MEDIA_BYTES
    assert client.delete(url).status_code == 404


def test_delete_export_removes_only_its_video(client, studio, project, highlight):
    studio.exports.mkdir(parents=True, exist_ok=True)
    filename = "gta-clip-job123.mp4"
    output = studio.exports / filename
    output.write_bytes(b"rendered video")
    other = studio.exports / "unrelated.mp4"
    other.write_bytes(b"keep")
    identifier = str(uuid4())
    store.insert("exports", {
        "id": identifier, "project_id": project["id"], "highlight_id": highlight["id"],
        "job_id": "job123", "status": "completed", "output_path": str(output),
        "filename": filename, "created_at": store.now(), "error": None,
        "download_url": f"/api/exports/{identifier}/file",
    })
    response = client.delete(f"/api/exports/{identifier}")
    assert response.status_code == 204, response.text
    assert not output.exists()
    assert other.read_bytes() == b"keep"
    assert studio.source.read_bytes() == MEDIA_BYTES
    assert store.get("highlights", highlight["id"]) == highlight
    assert store.get("exports", identifier) is None


@pytest.mark.parametrize("headers", [
    pytest.param({"Host": "attacker.example"}, id="external-host"),
    pytest.param({"Host": "127.0.0.1.attacker.example"}, id="host-suffix"),
    pytest.param({"Host": "127.0.0.1@attacker.example"}, id="external-host-userinfo"),
    pytest.param({"Host": "attacker.example@127.0.0.1"}, id="invalid-host-userinfo"),
    pytest.param({"Origin": "https://attacker.example"}, id="external-origin"),
    pytest.param({"Origin": "http://localhost.attacker.example"}, id="origin-suffix"),
    pytest.param({"Origin": "http://attacker.example@127.0.0.1"}, id="origin-userinfo"),
    pytest.param({"Origin": "https://127.0.0.1"}, id="non-http-origin"),
    pytest.param({"Origin": "null"}, id="opaque-origin"),
    pytest.param({"Origin": "http://[::1"}, id="malformed-origin"),
    pytest.param({"Sec-Fetch-Site": "cross-site"}, id="cross-site-fetch"),
])
def test_untrusted_host_and_origin_cannot_import(client, studio, headers):
    response = client.post("/api/projects/local", json={"path": str(studio.source)}, headers=headers)
    assert response.status_code == 403, response.text
    assert get_json(client, "/api/projects") == []
    studio.probe.assert_not_called()


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8000", "http://localhost:5173", "http://[::1]:5173"])
def test_loopback_development_origins_are_allowed(client, origin):
    assert client.get("/api/health", headers={"Origin": origin}).status_code == 200


def test_non_loopback_peer_is_rejected_even_with_local_host(studio):
    with TestClient(main.app, base_url="http://127.0.0.1", client=("192.0.2.1", 55000)) as remote:
        response = remote.get("/api/health")
        assert response.status_code == 403, response.text


@pytest.mark.parametrize("url", [
    CANONICAL_URL,
    f"https://youtu.be/{VIDEO_ID}?t=10#fragment",
    f"https://m.youtube.com/watch?v={VIDEO_ID}&list=ignored",
    f"https://www.youtube.com/live/{VIDEO_ID}",
    f"https://youtube.com/shorts/{VIDEO_ID}",
    f"https://www.youtube.com/embed/{VIDEO_ID}",
])
def test_youtube_import_only_enqueues_a_canonical_single_video(client, studio, url):
    response = client.post("/api/projects/youtube", json={"url": url})
    assert response.status_code == 202, response.text
    project, job = response.json()["project"], response.json()["job"]
    assert project["source_url"] == CANONICAL_URL
    assert project["source_type"] == "youtube"
    assert project["managed_source"] is True
    assert project["source_path"] == ""
    assert project["status"] == "importing"
    assert job["kind"] == "youtube"
    assert job["status"] == "queued"
    assert job["project_id"] == project["id"]
    assert job_payload(job["id"]) == {"settings": get_json(client, "/api/settings")}
    assert get_json(client, "/api/projects") == [project]
    assert client.get(f"/api/projects/{project['id']}/media").status_code == 404
    assert client.post(f"/api/projects/{project['id']}/analyze").status_code == 409
    assert client.delete(f"/api/projects/{project['id']}").status_code == 409
    studio.probe.assert_not_called()
    assert studio.runner.thread is None


@pytest.mark.parametrize("url", [
    f"http://www.youtube.com/watch?v={VIDEO_ID}",
    f"https://127.0.0.1/watch?v={VIDEO_ID}",
    f"https://youtube.com.attacker.example/watch?v={VIDEO_ID}",
    f"https://www.youtube.com@attacker.example/watch?v={VIDEO_ID}",
    f"https://attacker.example@www.youtube.com/watch?v={VIDEO_ID}",
    f"https://user:password@www.youtube.com/watch?v={VIDEO_ID}",
    f"https://www.youtube.com:8443/watch?v={VIDEO_ID}",
    f"https://www.youtube.com:bad/watch?v={VIDEO_ID}",
    "https://www.youtube.com/playlist?list=PL123",
    "https://www.youtube.com/@channel",
    "https://youtu.be/not-an-id",
    f"file:///recording.mp4?v={VIDEO_ID}",
    "--exec=malicious-command",
])
def test_unsafe_youtube_urls_are_rejected_before_any_work(client, studio, url):
    response = client.post("/api/projects/youtube", json={"url": url})
    assert response.status_code == 422, response.text
    assert get_json(client, "/api/projects") == []
    assert get_json(client, "/api/jobs") == []
    assert list(studio.projects.iterdir()) == []
    studio.probe.assert_not_called()
