"""Offline API/worker coverage for default captions, split layouts and AI keys."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_api import (client, studio, project, highlight, get_json, seed_highlight,
                      job_payload, run_one_queued_job, transcript)
from backend.core import integrations
from backend.core.process import Cancelled
from backend.database import store
from backend.detection import pipeline
from backend.models.schemas import Settings
from backend.processing import captioning, cloud, editorial, transcription


@pytest.fixture(autouse=True)
def isolate_keys(studio, monkeypatch):
    monkeypatch.setattr(integrations, "STORAGE_DIR", studio.storage)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(cloud, "post_json", Mock(side_effect=AssertionError("Unexpected real cloud request")))
    monkeypatch.setattr(editorial, "post_json", Mock(side_effect=AssertionError("Unexpected real cloud request")))


def test_new_defaults_migrate_old_settings_without_overriding_optout(client):
    defaults = Settings().model_dump()
    assert defaults["caption_style"] == "standard"
    assert defaults["caption_provider"] == "local"
    assert defaults["default_layout"] == "split"
    assert defaults["ai_scoring_provider"] == "none" and defaults["cloud_consent"] is False
    with store.connection() as db:
        db.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps({"caption_style": "none", "max_clips": 7}),))
    loaded = get_json(client, "/api/settings")
    assert loaded["caption_style"] == "none" and loaded["max_clips"] == 7
    assert loaded["caption_provider"] == "local" and loaded["cloud_consent"] is False


def test_new_import_requires_selected_facecam_before_split_export(client, studio):
    response = client.post("/api/projects/local", json={"path": str(studio.source)})
    assert response.status_code == 201, response.text
    project = response.json()
    assert project["layout"]["mode"] == "split" and project["layout"]["facecam"] is None
    clip = seed_highlight(project["id"])
    rejected = client.post(f"/api/highlights/{clip['id']}/export", json={"caption_style": "none"})
    assert rejected.status_code == 409
    assert "camera crop" in rejected.json()["detail"]
    layout = {"mode": "split", "facecam": {"x": .7, "y": .05, "width": .25, "height": .3}, "split_ratio": .5}
    saved = client.patch(f"/api/projects/{project['id']}/layout", json=layout)
    assert saved.status_code == 200, saved.text
    queued = client.post(f"/api/highlights/{clip['id']}/export", json={"caption_style": "none"})
    assert queued.status_code == 202, queued.text
    snapshot = job_payload(queued.json()["id"])["project_snapshot"]
    assert snapshot["layout"]["facecam"] == layout["facecam"]
    client.patch(f"/api/projects/{project['id']}/layout", json={"mode": "blurred"})
    assert job_payload(queued.json()["id"])["project_snapshot"] == snapshot


@pytest.mark.parametrize("layout", [
    {"mode": "split", "facecam": {"x": .9, "y": 0, "width": .2, "height": .2}},
    {"mode": "split", "split_ratio": .9},
    {"mode": "split", "facecam": {"x": 0, "y": 0, "width": 0, "height": .2}},
    {"mode": "unknown"},
])
def test_invalid_layout_not_saved(client, project, layout):
    response = client.patch(f"/api/projects/{project['id']}/layout", json=layout)
    assert response.status_code == 422
    assert get_json(client, f"/api/projects/{project['id']}")["layout"] == project["layout"]


def test_analysis_persists_results_then_automatically_generates_captions(client, studio, highlight, monkeypatch):
    monkeypatch.setattr(pipeline, "analyze", lambda *args: [dict(highlight)])
    response = client.post(f"/api/projects/{highlight['project_id']}/analyze", json={"force": True})
    assert response.status_code == 202, response.text
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", response.json()["id"])["status"] == "completed"
    assert store.get("highlights", highlight["id"])["caption_status"] == "pending"
    queued, payload = store.queued_job()
    assert queued["kind"] == "captions"
    assert payload["settings"]["caption_provider"] == "local"
    load = Mock(return_value=object())
    speech = Mock(return_value=transcript())
    monkeypatch.setattr(captioning, "_load_model", load)
    monkeypatch.setattr(captioning, "transcribe_clip", speech)
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", queued["id"])["status"] == "completed"
    saved = store.get("highlights", highlight["id"])
    assert saved["transcript"] == transcript() and saved["caption_status"] == "ready"
    assert saved["score"] == highlight["score"]
    load.assert_called_once()
    assert len(list((studio.cache / highlight["project_id"] / "captions").glob("*.json"))) == 1


def test_no_caption_setting_disables_automatic_transcription(client, studio, highlight, monkeypatch):
    store.save_settings({**store.settings(), "caption_style": "none"})
    monkeypatch.setattr(pipeline, "analyze", lambda *args: [dict(highlight)])
    response = client.post(f"/api/projects/{highlight['project_id']}/analyze")
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", response.json()["id"])["status"] == "completed"
    assert store.queued_job() is None


def test_captions_preserve_partial_success_and_report_no_speech(client, studio, highlight, monkeypatch):
    other = seed_highlight(highlight["project_id"], start=70)
    monkeypatch.setattr(captioning, "_load_model", Mock(return_value=object()))
    monkeypatch.setattr(captioning, "transcribe_clip", Mock(side_effect=[transcript(), transcription.TranscriptionUnavailable("No speech transcript produced")]))
    response = client.post(f"/api/projects/{highlight['project_id']}/captions")
    assert response.status_code == 202
    run_one_queued_job(studio.runner, monkeypatch)
    job = store.get("jobs", response.json()["id"])
    assert job["status"] == "completed" and job["result"] == {"ready": 1, "failed": 1}
    results = [store.get("highlights", identifier) for identifier in (highlight["id"], other["id"])]
    assert sum(item["caption_status"] == "ready" for item in results) == 1
    failed = next(item for item in results if item["caption_status"] != "ready")
    assert failed["caption_status"] == "no_speech" and "No speech" in failed["caption_error"]


def test_cancelled_caption_job_keeps_prior_transcript_and_cache(client, studio, highlight, monkeypatch):
    seed_highlight(highlight["project_id"], start=70)
    monkeypatch.setattr(captioning, "_load_model", Mock(return_value=object()))
    monkeypatch.setattr(captioning, "transcribe_clip", Mock(side_effect=[transcript(), Cancelled("cancel fixture")]))
    response = client.post(f"/api/projects/{highlight['project_id']}/captions")
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", response.json()["id"])["status"] == "cancelled"
    items = store.list_items("highlights", highlight["project_id"])
    assert sum(bool(item["transcript"]) for item in items) == 1
    assert len(list((studio.cache / highlight["project_id"] / "captions").glob("*.json"))) == 1
    assert not any(item.get("caption_status") == "pending" for item in items)


def test_existing_manual_caption_is_not_replaced_without_force(client, studio, highlight, monkeypatch):
    store.update("highlights", highlight["id"], {"transcript": transcript()})
    recognizer = Mock(side_effect=AssertionError("Do not overwrite manual text"))
    monkeypatch.setattr(captioning, "transcribe_clip", recognizer)
    response = client.post(f"/api/highlights/{highlight['id']}/captions", json={"force": False})
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", response.json()["id"])["status"] == "completed"
    assert store.get("highlights", highlight["id"])["transcript"] == transcript()
    recognizer.assert_not_called()


def test_caption_cache_avoids_second_recognition(client, studio, highlight, monkeypatch):
    ctx = SimpleNamespace(cache_dir=studio.cache / highlight["project_id"], work_dir=studio.cache / "work", check_cancelled=lambda: None, progress=lambda *args: None)
    recognize = Mock(return_value=transcript())
    monkeypatch.setattr(captioning, "transcribe_clip", recognize)
    project = store.get("projects", highlight["project_id"])
    one = captioning.caption_highlight(project, highlight, store.settings(), ctx)
    two = captioning.caption_highlight(project, highlight, store.settings(), ctx)
    assert one["transcript"] == two["transcript"] == transcript()
    recognize.assert_called_once()


def test_groq_captions_require_consent_before_audio_or_network(client, studio, highlight, monkeypatch):
    settings = {**store.settings(), "caption_provider": "groq", "cloud_consent": False}
    store.save_settings(settings)
    response = client.post(f"/api/highlights/{highlight['id']}/captions")
    assert response.status_code == 409
    extract = Mock(side_effect=AssertionError("No extraction before consent"))
    monkeypatch.setattr(captioning, "run_process", extract)
    with pytest.raises(transcription.TranscriptionUnavailable, match="consent"):
        captioning.transcribe_groq({}, {}, settings, SimpleNamespace())
    extract.assert_not_called()
    cloud.post_json.assert_not_called()


def test_keys_are_write_only_private_and_environment_wins(client, studio, monkeypatch):
    fake = "fixture-key-not-real-123"
    response = client.put("/api/integrations/gemini", json={"api_key": fake})
    assert response.status_code == 200, response.text
    assert fake not in response.text
    assert response.json()["gemini"] == {"configured": True, "source": "local"}
    assert integrations.get_api_key("gemini") == fake
    path = studio.storage / "private/provider-keys.bin"
    if os.name == "nt":
        assert fake.encode() not in path.read_bytes()
    else:
        assert path.stat().st_mode & 0o077 == 0
    monkeypatch.setenv("GEMINI_API_KEY", "environment-fixture")
    assert get_json(client, "/api/integrations")["gemini"]["source"] == "environment"
    assert integrations.get_api_key("gemini") == "environment-fixture"
    assert client.put("/api/integrations/gemini", json={"api_key": ""}).status_code == 200
    monkeypatch.delenv("GEMINI_API_KEY")
    assert get_json(client, "/api/integrations")["gemini"]["configured"] is False
    for endpoint in ("settings", "jobs", "projects", "exports"):
        assert fake not in client.get(f"/api/{endpoint}").text


def test_bad_key_validation_never_echoes_key(client):
    fake = "fixture-secret-" * 500
    response = client.put("/api/integrations/groq", json={"api_key": fake})
    assert response.status_code == 422
    assert "fixture-secret" not in response.text


def test_ai_review_endpoint_preserves_detection_score_and_never_snapshots_keys(client, studio, highlight, monkeypatch):
    assert client.post(f"/api/projects/{highlight['project_id']}/score").status_code == 409
    store.save_settings({**store.settings(), "ai_scoring_provider": "groq", "cloud_consent": True})
    assert client.post(f"/api/projects/{highlight['project_id']}/score").status_code == 409
    integrations.set_api_key("groq", "fake-groq-fixture-key")
    review = {"provider": "groq", "model": "fixture", "score": 77, "rubric": {key: 77 for key in ("hook", "payoff", "reaction", "clarity", "novelty")}, "explanation": "Fixture editorial opinion", "evidence": "transcript_only", "created_at": store.now(), "cache_hit": False}
    monkeypatch.setattr(editorial, "score_highlights", lambda project, highlights, settings, ctx, force=False: [{**item, "ai_review": review, "ai_error": None} for item in highlights])
    response = client.post(f"/api/projects/{highlight['project_id']}/score", json={"force": True})
    assert response.status_code == 202, response.text
    assert "fake-groq" not in json.dumps(job_payload(response.json()["id"]))
    run_one_queued_job(studio.runner, monkeypatch)
    saved = store.get("highlights", highlight["id"])
    assert saved["score"] == highlight["score"] and saved["ai_review"] == review
    assert store.get("jobs", response.json()["id"])["status"] == "completed"
    unchanged = client.patch(f"/api/highlights/{highlight['id']}", json={"title": "Export title", "transcript": []})
    assert unchanged.json()["ai_review"] == review
    patched = client.patch(f"/api/highlights/{highlight['id']}", json={"transcript": transcript()})
    assert patched.json()["ai_review"] is None


def test_withdrawing_consent_blocks_already_queued_ai_job(client, studio, highlight, monkeypatch):
    store.save_settings({**store.settings(), "ai_scoring_provider": "groq", "cloud_consent": True})
    integrations.set_api_key("groq", "fixture-only-key")
    queued = client.post(f"/api/projects/{highlight['project_id']}/score")
    assert queued.status_code == 202
    store.save_settings({**store.settings(), "cloud_consent": False})
    run_one_queued_job(studio.runner, monkeypatch)
    saved = store.get("highlights", highlight["id"])
    assert saved["score"] == highlight["score"] and saved.get("ai_error")
    assert store.get("jobs", queued.json()["id"])["status"] == "failed"
    editorial.post_json.assert_not_called()


def test_auto_caption_job_runs_before_optional_review(client, studio, highlight, monkeypatch):
    store.save_settings({**store.settings(), "ai_scoring_provider": "groq", "cloud_consent": True, "auto_ai_review": True})
    integrations.set_api_key("groq", "fixture-key")
    monkeypatch.setattr(pipeline, "analyze", lambda *args: [dict(highlight)])
    monkeypatch.setattr(captioning, "_load_model", Mock(return_value=object()))
    monkeypatch.setattr(captioning, "transcribe_clip", Mock(return_value=transcript()))
    captured = []
    def review(project, highlights, settings, ctx, force=False):
        captured.append(highlights[0]["transcript"])
        return highlights
    monkeypatch.setattr(editorial, "score_highlights", review)
    assert client.post(f"/api/projects/{highlight['project_id']}/analyze").status_code == 202
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.queued_job()[0]["kind"] == "captions"
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.queued_job()[0]["kind"] == "score"
    run_one_queued_job(studio.runner, monkeypatch)
    assert captured == [transcript()]


def test_groq_captions_use_actual_returned_words_only(client, studio, highlight, monkeypatch):
    project = store.get("projects", highlight["project_id"])
    ctx = SimpleNamespace(cache_dir=studio.cache, work_dir=studio.cache / "groq-work", check_cancelled=lambda: None, progress=lambda *args: None)
    def extract(command, **kwargs):
        import wave
        with wave.open(str(command[-1]), "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x00" * 64000)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(captioning, "run_process", extract)
    post = Mock(return_value={"text": "Actual words", "words": [{"start": .2, "end": .7, "word": "Actual"}, {"start": .8, "end": 1.5, "word": "words"}]})
    monkeypatch.setattr(cloud, "post_json", post)
    result = captioning.transcribe_groq(project, highlight, {**store.settings(), "caption_provider": "groq", "cloud_consent": True}, ctx)
    assert result == [{"start": .2, "end": 1.5, "text": "Actual words", "words": [{"start": .2, "end": .7, "word": "Actual"}, {"start": .8, "end": 1.5, "word": "words"}]}]
    assert post.call_args.kwargs["data"]["timestamp_granularities[]"] == "word"
    assert post.call_args.kwargs["audio_seconds"] == 2.0
    assert list(ctx.work_dir.iterdir()) == []


def test_project_model_cache_can_be_loaded_without_huggingface_import(tmp_path, monkeypatch):
    monkeypatch.setattr(transcription, "MODEL_DIR", tmp_path)
    monkeypatch.delenv("GTA_WHISPER_MODEL_PATH", raising=False)
    assert not transcription.model_is_cached()
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        (tmp_path / filename).write_bytes(b"fixture")
    assert transcription.model_is_cached()
    assert transcription._cached_model_path() == str(tmp_path.resolve())
