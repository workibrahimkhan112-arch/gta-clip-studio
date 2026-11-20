"""Budgeted GPT API/plan wiring with real SQLite and fully mocked HTTP."""
from __future__ import annotations

import json
from unittest.mock import Mock

import httpx
import pytest

from test_api import client, studio, project, highlight, transcript, get_json, run_one_queued_job
from backend.core import integrations, chatgpt_auth, oauth_storage, usage
from backend.database import store
from backend.processing import cloud


def completed(value, *, tokens=True):
    result = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(value)}]}]}
    if tokens:
        result["usage"] = {"input_tokens": 1000, "output_tokens": 200, "input_tokens_details": {"cached_tokens": 0}}
    return result


@pytest.fixture(autouse=True)
def isolated(studio, monkeypatch):
    monkeypatch.setattr(integrations, "STORAGE_DIR", studio.storage)
    monkeypatch.setattr(oauth_storage, "STORAGE_DIR", studio.storage)
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    original = httpx.AsyncClient
    state = {"requests": [], "body": completed({"titles": ["That jump almost ended badly", "Somehow we survived", "One jump too far"]}), "sse": False, "truncate": False}
    def handler(request):
        state["requests"].append(request)
        if state["sse"]:
            event = {"type": "response.completed", "response": state["body"]}
            content = 'data: {"type":"response.output_text.delta","delta":"short"}\n\n'
            if not state["truncate"]:
                content += "data: " + json.dumps(event) + "\n\n"
            return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=state["body"])
    monkeypatch.setattr(cloud.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    yield state
    chatgpt_auth.close()


def configure_openai():
    integrations.set_api_key("openai", "fixture-openai-key-not-real")
    store.save_settings({**store.settings(), "ai_scoring_provider": "openai", "cloud_consent": True, "ai_daily_request_limit": 1})


def test_openai_titles_are_cached_budgeted_and_never_auto_applied(client, studio, highlight, monkeypatch, isolated):
    configure_openai()
    store.update("highlights", highlight["id"], {"transcript": transcript()})
    endpoint = f"/api/highlights/{highlight['id']}/titles"
    queued = client.post(endpoint)
    assert queued.status_code == 202, queued.text
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", queued.json()["id"])["status"] == "completed"
    saved = store.get("highlights", highlight["id"])
    assert len(saved["ai_titles"]) == 3 and saved["title"] == highlight["title"]
    request = isolated["requests"][0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert body["model"] == "gpt-6-luna" and body["reasoning"] == {"effort": "none"}
    assert body["max_output_tokens"] == 512 and body["store"] is False
    assert "fixture-openai-key" not in request.content.decode()
    assert "image_url" not in request.content.decode()
    measured = get_json(client, "/api/usage")
    assert measured["request_count"] == 1
    assert measured["rows"][0]["input_tokens"] == 1000
    assert measured["rows"][0]["output_tokens"] == 200
    assert measured["estimated_cost_usd"] == pytest.approx(.0002)
    cached = client.post(endpoint)
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", cached.json()["id"])["status"] == "completed"
    assert len(isolated["requests"]) == 1
    assert get_json(client, "/api/usage")["cache_hits"] == 1
    forced = client.post(endpoint, json={"force": True})
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", forced.json()["id"])["status"] == "failed"
    assert len(isolated["requests"]) == 1
    applied = client.patch(f"/api/highlights/{highlight['id']}", json={"title": saved["ai_titles"][0]})
    assert applied.status_code == 200 and applied.json()["title"] == saved["ai_titles"][0]


def test_openai_review_is_text_first_and_keeps_detection_score(client, studio, highlight, monkeypatch, isolated):
    configure_openai()
    store.update("highlights", highlight["id"], {"transcript": transcript()})
    isolated["body"] = completed({"score": 81, "rubric": {name: 80 for name in ("hook", "payoff", "reaction", "clarity", "novelty")}, "explanation": "Clear dialogue, but no visuals or tone were reviewed."})
    queued = client.post(f"/api/projects/{highlight['project_id']}/score")
    assert queued.status_code == 202, queued.text
    run_one_queued_job(studio.runner, monkeypatch)
    saved = store.get("highlights", highlight["id"])
    assert saved["score"] == highlight["score"]
    assert saved["ai_review"]["score"] == 81
    assert saved["ai_review"]["evidence"] == "transcript_only"
    assert "input_image" not in isolated["requests"][0].content.decode()


def mock_plan(monkeypatch):
    monkeypatch.setattr(chatgpt_auth, "status", lambda: {"connected": True, "plan_enabled": True, "pending": False, "error": None, "active_account_id": "fixture-account", "accounts": [], "expires_at": None})
    monkeypatch.setattr(chatgpt_auth, "list_models", lambda: [{"id": "gpt-6-luna", "name": "GPT-6 Luna"}])
    monkeypatch.setattr(chatgpt_auth, "get_access_token", lambda: "fixture-plan-token-not-real")


def test_plan_stream_waits_for_completed_and_has_no_usd_charge_claim(client, studio, highlight, monkeypatch, isolated):
    mock_plan(monkeypatch)
    store.save_settings({**store.settings(), "ai_scoring_provider": "openai", "openai_auth_mode": "chatgpt", "cloud_consent": True, "ai_daily_budget_usd": 0})
    store.update("highlights", highlight["id"], {"transcript": transcript()})
    isolated["sse"] = True
    queued = client.post(f"/api/highlights/{highlight['id']}/titles")
    assert queued.status_code == 202, queued.text
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", queued.json()["id"])["status"] == "completed"
    body = json.loads(isolated["requests"][0].content)
    assert body["stream"] is True and body["store"] is False
    assert not {"max_output_tokens", "temperature", "metadata", "previous_response_id"}.intersection(body)
    assert isolated["requests"][0].headers["authorization"] == "Bearer fixture-plan-token-not-real"
    summary = get_json(client, "/api/usage")
    assert summary["rows"][0]["auth_mode"] == "chatgpt"
    assert summary["rows"][0]["estimated_cost_usd"] is None
    assert summary["rows"][0]["input_tokens"] == 1000
    assert "fixture-plan-token" not in json.dumps(summary)


def test_interrupted_plan_stream_does_not_make_up_titles_or_retry(client, studio, highlight, monkeypatch, isolated):
    mock_plan(monkeypatch)
    store.save_settings({**store.settings(), "ai_scoring_provider": "openai", "openai_auth_mode": "chatgpt", "cloud_consent": True})
    store.update("highlights", highlight["id"], {"transcript": transcript()})
    isolated.update(sse=True, truncate=True)
    queued = client.post(f"/api/highlights/{highlight['id']}/titles")
    run_one_queued_job(studio.runner, monkeypatch)
    assert store.get("jobs", queued.json()["id"])["status"] == "failed"
    assert not store.get("highlights", highlight["id"]).get("ai_titles")
    assert len(isolated["requests"]) == 1
    assert get_json(client, "/api/usage")["unknown_usage_requests"] == 1


def test_visual_style_save_reset_and_snapshot_do_not_remove_ai_review(client, studio, highlight):
    store.update("highlights", highlight["id"], {"ai_review": {"score": 80}})
    style = {"font_family": "Impact", "font_size": 60, "text_color": "#ff8844", "x": .4, "y": .3, "width": .6}
    saved = client.patch(f"/api/highlights/{highlight['id']}", json={"subtitle_style": style})
    assert saved.status_code == 200, saved.text
    value = saved.json()
    assert value["subtitle_style"]["text_color"] == "#FF8844"
    assert value["ai_review"] == {"score": 80}
    queued = client.post(f"/api/highlights/{highlight['id']}/export", json={"caption_style": "none"})
    assert queued.status_code == 202
    payload = store.job_payload(queued.json()["id"])
    cleared = client.patch(f"/api/highlights/{highlight['id']}", json={"subtitle_style": None})
    assert cleared.status_code == 200 and cleared.json()["subtitle_style"] is None
    assert payload["highlight"]["subtitle_style"] == value["subtitle_style"]


def test_auth_routes_never_return_tokens_and_require_local_origin(client, monkeypatch):
    public = {"connected": False, "plan_enabled": False, "pending": False, "error": None, "accounts": [], "active_account_id": None, "expires_at": None}
    monkeypatch.setattr(chatgpt_auth, "status", lambda: public)
    assert get_json(client, "/api/chatgpt") == public
    response = client.get("/api/chatgpt", headers={"Origin": "https://untrusted.example"})
    assert response.status_code == 403
    start = Mock(return_value={"authorization_url": "https://auth.openai.com/api/accounts/authorize?state=fixture", "expires_at": "future"})
    monkeypatch.setattr(chatgpt_auth, "start_login", start)
    response = client.post("/api/chatgpt/connect", json={})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    start.assert_called_once_with(None)
