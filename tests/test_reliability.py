"""Reliability regressions using real process trees and isolated SQLite/storage.

No FFmpeg, network, application storage, or shared background worker is used.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import psutil
import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.api import routes
from backend.core import config, process
from backend.database import store
from backend.processing import render, youtube
from backend.utils import media as media_utils
from backend.workers import jobs


def _running(child):
    try:
        return child.is_running() and child.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _assert_exited(child, timeout=5):
    deadline = time.monotonic() + timeout
    while _running(child) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not _running(child), f"Fixture process {child.pid} survived process-owner cleanup"


@pytest.fixture
def process_tree(tmp_path):
    """Use PID-file handshakes, not pipe reads that could hang on a regression."""
    token = f"reliability-{uuid4()}"
    leader_file = tmp_path / "leader.json"
    child_file = tmp_path / "descendant.json"
    owned = {}
    child_code = (
        "import json, os, pathlib, signal, sys, time\n"
        "if os.name != 'nt':\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid()}), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )

    def command(*, exit_leader):
        leader_code = (
            "import json, os, pathlib, subprocess, sys, time\n"
            f"child = subprocess.Popen([sys.executable, '-B', '-c', {child_code!r}, "
            f"{str(child_file)!r}, {token!r}], stdin=subprocess.DEVNULL, "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            f"pathlib.Path({str(leader_file)!r}).write_text("
            "json.dumps({'pid': os.getpid(), 'descendant_pid': child.pid}), encoding='utf-8')\n"
            + ("" if exit_leader else "time.sleep(60)\n")
        )
        # Avoid the extra Windows venv launcher: the fixture needs only stdlib.
        executable = getattr(sys, "_base_executable", None) or sys.executable
        return [executable, "-B", "-c", leader_code, token]

    def read_pids(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def remember(pid):
        if pid in owned:
            return owned[pid]
        try:
            child = psutil.Process(pid)
            if token in child.cmdline():
                owned[pid] = child
                return child
        except psutil.NoSuchProcess:
            pass
        return None

    def observe():
        leader = read_pids(leader_file)
        descendant = read_pids(child_file)
        if leader.get("pid"):
            remember(leader["pid"])
        if leader.get("descendant_pid"):
            remember(leader["descendant_pid"])
        pid = descendant.get("pid")
        if pid and pid == leader.get("descendant_pid"):
            child = remember(pid)
            if child is not None and _running(child):
                return child
        return None

    def wait_ready():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            child = observe()
            if child is not None:
                return child
            time.sleep(0.02)
        pytest.fail("Fixture descendant did not complete its startup handshake")

    try:
        yield SimpleNamespace(command=command, observe=observe, wait_ready=wait_ready)
    finally:
        # Even the old, broken process owner must not leak a fixture descendant.
        for path in (leader_file, child_file):
            for pid in read_pids(path).values():
                remember(pid)
        for child in list(owned.values()):
            try:
                for descendant in child.children(recursive=True):
                    owned.setdefault(descendant.pid, descendant)
            except psutil.NoSuchProcess:
                pass
        for child in reversed(list(owned.values())):
            try:
                if _running(child):
                    child.kill()
            except psutil.NoSuchProcess:
                pass
        for child in owned.values():
            _assert_exited(child)


@pytest.mark.parametrize("cancel", [False, True], ids=["normal-exit", "cancel-after-leader-exit"])
def test_managed_process_owns_descendant_after_leader_exits(process_tree, cancel):
    expected = pytest.raises(process.Cancelled, match="fixture cancellation") if cancel else nullcontext()
    with expected:
        with process.managed_process(process_tree.command(exit_leader=True)) as leader:
            descendant = process_tree.wait_ready()
            assert leader.wait(timeout=10) == 0
            assert _running(descendant), "The fixture must outlive its leader before context exit"
            if cancel:
                raise process.Cancelled("fixture cancellation")
    _assert_exited(descendant)
    assert leader.poll() is not None
    assert leader.stdout.closed


@pytest.mark.parametrize("interruption", ["timeout", "cancel"])
def test_run_process_interrupt_terminates_leader_and_descendant(process_tree, interruption):
    observed = []

    def check_cancelled():
        descendant = process_tree.observe()
        if descendant is not None:
            observed.append(descendant)
            if interruption == "cancel":
                raise process.Cancelled("fixture cancellation")

    command = process_tree.command(exit_leader=False)
    error = subprocess.TimeoutExpired if interruption == "timeout" else process.Cancelled
    with pytest.raises(error) as raised:
        process.run_process(command, ctx=SimpleNamespace(check_cancelled=check_cancelled), timeout=5)
    assert observed, "Interruption must happen after the descendant actually starts"
    _assert_exited(observed[-1])
    if interruption == "timeout":
        assert raised.value.cmd == command
        assert raised.value.timeout == 5


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
    for module in (config, store, routes, jobs, main, render, youtube):
        for name, value in paths.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)
    worker = jobs.JobRunner()
    for module in (jobs, routes, main):
        monkeypatch.setattr(module, "runner", worker)
    store.initialize()
    source = tmp_path / "external recording.mp4"
    source.write_bytes(b"referenced source fixture")
    metadata = {"duration": 120.0, "width": 1920, "height": 1080, "audio_streams": []}
    project = {
        "id": str(uuid4()), "name": "Reliability recording", "source_type": "local",
        "source_path": str(source), "source_url": None, "managed_source": False,
        "status": "ready", "created_at": store.now(), "metadata": metadata, "highlight_count": 0,
    }
    store.insert("projects", project)
    probe = Mock(name="offline_probe", return_value=metadata)
    monkeypatch.setattr(media_utils, "probe", probe)
    environment = SimpleNamespace(
        runner=worker, project=project, source=source, probe=probe,
        storage=storage, cache=paths["CACHE_DIR"], exports=paths["EXPORTS_DIR"],
    )
    try:
        yield environment
    finally:
        worker.close()
        assert worker.thread is None or not worker.thread.is_alive()
        assert source.read_bytes() == b"referenced source fixture"


def _enqueue_export(studio, output_folder=""):
    highlight = {
        "id": str(uuid4()), "project_id": studio.project["id"], "title": "Reliability clip",
        "start": 10.0, "end": 30.0, "duration": 20.0, "transcript": [],
    }
    export_id = str(uuid4())
    payload = {
        "settings": {**store.settings(), "caption_style": "none", "output_folder": str(output_folder)},
        "highlight": highlight, "export_id": export_id,
    }
    job = studio.runner.submit("export", studio.project["id"], payload)
    export = {
        "id": export_id, "project_id": studio.project["id"], "highlight_id": highlight["id"],
        "job_id": job["id"], "status": "queued", "output_path": "", "filename": "",
        "created_at": store.now(), "error": None, "download_url": None,
    }
    store.insert("exports", export)
    return job, export, payload


def _terminal_job(studio, identifier, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = store.get("jobs", identifier)
        if job["status"] not in jobs.ACTIVE:
            return job
        assert studio.runner.thread.is_alive(), f"Worker exited with job still {job['status']}: {job}"
        time.sleep(0.02)
    pytest.fail(f"Job did not finish: {store.get('jobs', identifier)}")


def test_worker_survives_job_context_initialization_failure(studio, monkeypatch):
    first, first_export, _ = _enqueue_export(studio)
    second, second_export, _ = _enqueue_export(studio)
    real_context = jobs.JobContext

    def initialize(worker, job):
        if job["id"] == first["id"]:
            raise OSError("fixture disk full creating work directory")
        return real_context(worker, job)

    def encode(project, highlight, settings, ctx):
        ctx.progress("rendering", highlight["duration"], highlight["duration"])
        target = studio.exports / f"gta-reliability-clip-{ctx.id}.mp4"
        target.write_bytes(b"encoded fixture")
        return {"output_path": str(target), "filename": target.name}

    context = Mock(side_effect=initialize)
    renderer = Mock(side_effect=encode)
    monkeypatch.setattr(jobs, "JobContext", context)
    monkeypatch.setattr(render, "render_clip", renderer)
    studio.runner.start()
    failed = _terminal_job(studio, first["id"])
    completed = _terminal_job(studio, second["id"])
    assert failed["status"] == "failed"
    assert "fixture disk full" in failed["error"]
    assert store.get("exports", first_export["id"])["status"] == "failed"
    assert completed["status"] == "completed", completed
    assert store.get("exports", second_export["id"])["status"] == "completed"
    assert [call.args[1]["id"] for call in context.call_args_list] == [first["id"], second["id"]]
    renderer.assert_called_once()
    assert renderer.call_args.args[3].id == second["id"]
    assert studio.runner.thread.is_alive()
    studio.runner.close()
    assert not (studio.cache / "work" / second["id"]).exists()


@pytest.fixture
def long_queue(studio):
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    template = studio.runner.submit("analyze", studio.project["id"], {})
    active = []
    terminal = []
    # More than 100 newer queued jobs must not hide the oldest running job;
    # terminal history has its own, independent bound.
    with store.connection() as db:
        db.execute("DELETE FROM jobs WHERE id=?", (template["id"],))
        for index in range(235):
            status = "running" if index == 0 else ("queued" if index < 105 else ("completed", "failed", "cancelled")[index % 3])
            timestamp = (base + timedelta(seconds=index)).isoformat()
            job = {**template, "id": str(uuid4()), "status": status, "created_at": timestamp,
                   "updated_at": timestamp, "_fixture_private": True}
            store.insert("jobs", job, db=db)
            (active if status in jobs.ACTIVE else terminal).append(job)
    return SimpleNamespace(active=active, terminal=terminal)


@pytest.mark.parametrize("terminal_limit", [0, 3, 100])
def test_recent_jobs_keeps_every_active_job_and_bounds_terminal_history(long_queue, terminal_limit):
    result = store.recent_jobs(terminal_limit=terminal_limit)
    expected_terminal = long_queue.terminal[-terminal_limit:] if terminal_limit else []
    expected = {item["id"] for item in [*long_queue.active, *expected_terminal]}
    assert {item["id"] for item in result} == expected
    assert len(result) == len(expected)
    assert result == sorted(result, key=lambda item: item["created_at"], reverse=True)


def test_jobs_api_and_events_include_active_jobs_beyond_recent_history(studio, long_queue, monkeypatch):
    monkeypatch.setattr(studio.runner, "start", Mock(name="disabled_api_worker_start"))
    expected = {item["id"] for item in [*long_queue.active, *long_queue.terminal[-100:]]}
    with TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 55000)) as client:
        response = client.get("/api/jobs")
        assert response.status_code == 200, response.text
        result = response.json()
        assert {item["id"] for item in result} == expected
        assert len(result) == len(expected)
        assert not any(key.startswith("_") for item in result for key in item)
        assert result == sorted(result, key=lambda item: item["created_at"], reverse=True)

        async def first_event():
            async def connected():
                return False

            stream = await routes.events(SimpleNamespace(is_disconnected=connected))
            try:
                return await anext(stream.body_iterator)
            finally:
                await stream.body_iterator.aclose()

        # Read exactly one SSE snapshot; a TestClient stream would never finish.
        event = asyncio.run(first_event())
        assert event.startswith("event: update\ndata: ")
        snapshot = json.loads(event.split("data: ", 1)[1])["jobs"]
        assert {item["id"] for item in snapshot} == expected
        assert not any(key.startswith("_") for item in snapshot for key in item)


@pytest.mark.parametrize("custom_output", [False, True], ids=["default-output", "custom-output"])
@pytest.mark.parametrize("completed_row", [False, True], ids=["published-before-db-commit", "completed-export-row"])
def test_restart_recovers_published_export_without_rendering_twice(studio, monkeypatch, custom_output, completed_row):
    output = studio.storage.parent / "custom exports" if custom_output else studio.exports
    output.mkdir(parents=True, exist_ok=True)
    job, export, payload = _enqueue_export(studio, output if custom_output else "")
    filename = f"gta-reliability-clip-{job['id']}.mp4"
    assert render._filename(payload["highlight"], job["id"]) == filename
    final = output / filename
    final.write_bytes(b"atomically published fixture")
    partial = output / f".gta-{job['id']}.part.mp4"
    partial.write_bytes(b"interrupted fixture")
    protected = {
        output / f".gta-{uuid4()}.part.mp4": b"another job's partial",
        output / f".gta-{job['id']}-other.part.mp4": b"similar but not owned",
        output / "user-video.mp4": b"user file",
        studio.exports / f".gta-{job['id']}.part.mp4.backup": b"unrelated backup",
    }
    elsewhere = studio.storage.parent / "new default output"
    elsewhere.mkdir()
    protected[elsewhere / partial.name] = b"same name on another destination"
    for path, content in protected.items():
        path.write_bytes(content)
    store.save_settings({**store.settings(), "output_folder": str(elsewhere)})
    store.update("jobs", job["id"], {"status": "running", "stage": "rendering",
        "total_seconds": 20.0, "processed_seconds": 12.0, "elapsed_seconds": 2.5, "error": "stale job error"})
    patch = {"status": "completed" if completed_row else "running", "error": "stale export error"}
    if completed_row:
        patch.update(output_path=str(final), filename=filename)
    store.update("exports", export["id"], patch)
    renderer = Mock(side_effect=AssertionError("Recovered publication must not be rendered again"))
    monkeypatch.setattr(render, "render_clip", renderer)

    for _ in range(2):
        studio.runner.start()
        recovered = _terminal_job(studio, job["id"])
        studio.runner.close()
        assert recovered["status"] == "completed", recovered
        assert recovered["result"] == {"export_id": export["id"], "output_path": str(final), "filename": filename}
        assert recovered["error"] is None
        assert recovered["processed_seconds"] == recovered["total_seconds"] == 20.0
        assert recovered["progress"] == 1.0
        assert recovered["elapsed_seconds"] == 2.5
        record = store.get("exports", export["id"])
        assert record["status"] == "completed"
        assert record["output_path"] == str(final)
        assert record["filename"] == filename
        assert record["error"] is None
        assert record["download_url"] == f"/api/exports/{export['id']}/file"
        assert store.active_jobs() == []
    renderer.assert_not_called()
    if completed_row:
        studio.probe.assert_not_called()
    else:
        studio.probe.assert_called_once_with(final)
    assert not partial.exists()
    assert final.read_bytes() == b"atomically published fixture"
    for path, content in protected.items():
        assert path.read_bytes() == content
    assert {path.name for path in output.glob("gta-*.mp4")} == {filename}


@pytest.mark.parametrize("empty_final", [False, True], ids=["missing-final", "empty-final"])
def test_restart_removes_exact_partial_and_requeues_unpublished_export(studio, monkeypatch, empty_final):
    job, export, _ = _enqueue_export(studio)
    partial = studio.exports / f".gta-{job['id']}.part.mp4"
    partial.write_bytes(b"unfinished mp4")
    unrelated = studio.exports / f".gta-{uuid4()}.part.mp4"
    unrelated.write_bytes(b"another export's unfinished mp4")
    final = studio.exports / f"gta-reliability-clip-{job['id']}.mp4"
    if empty_final:
        final.touch()
    store.update("jobs", job["id"], {"status": "running", "progress": 0.5, "processed_seconds": 10.0,
        "total_seconds": 20.0, "elapsed_seconds": 4.0})
    store.update("exports", export["id"], {"status": "running"})
    loop = Mock(name="held_worker_loop")
    monkeypatch.setattr(studio.runner, "_loop", loop)
    studio.runner.start()
    studio.runner.thread.join(timeout=5)
    assert not studio.runner.thread.is_alive()
    loop.assert_called_once_with()
    recovered = store.get("jobs", job["id"])
    assert recovered["status"] == recovered["stage"] == "queued"
    assert recovered["progress"] is None
    assert recovered["processed_seconds"] == recovered["total_seconds"] == 0.0
    assert recovered["elapsed_seconds"] == 0.0
    assert store.queued_job()[0]["id"] == job["id"]
    assert store.get("exports", export["id"])["status"] != "completed"
    assert not partial.exists()
    assert unrelated.read_bytes() == b"another export's unfinished mp4"
    studio.probe.assert_not_called()


def test_complete_export_rolls_back_both_records_if_job_update_fails(studio):
    job, export, _ = _enqueue_export(studio)
    store.update("jobs", job["id"], {"status": "running", "total_seconds": 20.0})
    store.update("exports", export["id"], {"status": "running"})
    before_job = store.get("jobs", job["id"])
    before_export = store.get("exports", export["id"])
    with store.connection() as db:
        db.execute("""
            CREATE TRIGGER reject_completion BEFORE UPDATE OF status ON jobs
            WHEN NEW.status = 'completed'
            BEGIN SELECT RAISE(ABORT, 'fixture completion failure'); END
        """)
    rendered = {"output_path": str(studio.exports / "published.mp4"), "filename": "published.mp4"}
    with pytest.raises(sqlite3.IntegrityError, match="fixture completion failure"):
        store.complete_export(export["id"], job["id"], rendered, 3.0)
    assert store.get("exports", export["id"]) == before_export
    assert store.get("jobs", job["id"]) == before_job
    assert {item["id"] for item in store.active_jobs()} == {job["id"]}
