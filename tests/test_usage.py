"""Isolated, small SQLite ledger tests. No providers, servers, models or network.

The parent coordinator runs this module serially; importing it starts no workers.
"""
from __future__ import annotations

import builtins
import importlib
import importlib.util
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.core import usage
from backend.database import store


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "usage.sqlite3"
    monkeypatch.setattr(store, "DATABASE_PATH", path)
    monkeypatch.setattr(usage, "_now", lambda: datetime(2026, 11, 20, 12, tzinfo=timezone.utc))
    usage.initialize()
    return path


def settings(**patch):
    return {"ai_daily_request_limit": 20, "ai_daily_budget_usd": 1.0,
            "ai_max_output_tokens": 512, "ai_allow_unpriced_models": False, **patch}


def reserve(*, provider="openai", model="gpt-6-luna", operation="review", config=None, **kwargs):
    return usage.reserve(provider, model, operation, settings() if config is None else config, **kwargs)


def openai_response(prompt=100, output=20, cached=0):
    return {"usage": {"input_tokens": prompt, "output_tokens": output,
                      "input_tokens_details": {"cached_tokens": cached}}}


def luna_hold(payload, output=512):
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return (Decimal(size) * Decimal("0.10") + Decimal(output) * Decimal("0.50")) / 1_000_000


def test_empty_summary_and_own_table_only():
    usage.initialize()
    with store.connection() as db:
        tables = {row["name"] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"ai_usage"}
    result = usage.summary()
    assert result["day"] == "2026-11-20"
    assert result["request_count"] == result["cache_hits"] == result["unknown_usage_requests"] == 0
    assert result["estimated_cost_usd"] == result["reserved_cost_usd"] == 0
    assert result["rows"] == result["recent"] == []
    assert "not invoices" in result["note"]
    prices = {(row["provider"], row["model"]): row for row in result["pricing"]}
    assert len(prices) == 5
    assert prices["openai", "gpt-6-luna"]["cached_input_per_million"] == 0.01
    assert prices["gemini", "gemini-3.5-flash-lite"]["output_per_million"] == 2.5
    assert prices["groq", "whisper-large-v3-turbo"]["audio_per_hour"] == 0.04


def test_connection_is_resolved_lazily_for_each_database(tmp_path, monkeypatch, ledger):
    reserve()
    second = tmp_path / "fresh.sqlite3"
    monkeypatch.setattr(store, "DATABASE_PATH", second)
    usage.initialize()
    assert usage.summary()["request_count"] == 0
    reserve()
    assert usage.summary()["request_count"] == 1
    monkeypatch.setattr(store, "DATABASE_PATH", ledger)
    assert usage.summary()["request_count"] == 1


@pytest.mark.parametrize("cap", ["requests", "usd"])
def test_concurrent_reservations_cannot_exceed_cap(cap):
    payload = {"input": "x" * 1000}
    hold = luna_hold(payload, output=128)
    config = settings(ai_max_output_tokens=128, ai_daily_request_limit=3 if cap == "requests" else 20,
                      ai_daily_budget_usd=float(hold * 3) if cap == "usd" else 1)
    barrier = threading.Barrier(8, timeout=10)

    def attempt(_):
        barrier.wait()
        try:
            return reserve(config=config, payload=payload)
        except usage.BudgetError:
            return None

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = [identifier for identifier in executor.map(attempt, range(8)) if identifier is not None]
    assert len(ids) == len(set(ids)) == 3
    result = usage.summary()
    assert result["request_count"] == result["unknown_usage_requests"] == 3
    assert result["reserved_cost_usd"] == pytest.approx(float(hold * 3))
    assert result["estimated_cost_usd"] is None


@pytest.mark.parametrize("allow_unpriced", [False, True])
def test_zero_budget_disables_api_even_with_unpriced_opt_in(allow_unpriced):
    with pytest.raises(usage.BudgetError, match=r"\$0"):
        reserve(model="custom-model" if allow_unpriced else "gpt-6-luna",
                config=settings(ai_daily_budget_usd=0, ai_allow_unpriced_models=allow_unpriced))
    assert usage.summary()["request_count"] == 0


def test_plan_uses_request_cap_only_even_with_zero_budget():
    config = settings(ai_daily_budget_usd=0, ai_daily_request_limit=1)
    identifier = reserve(model="account-catalog-model", config=config, auth_mode="chatgpt",
                         payload={"stream": True, "input": "Plan request"})
    usage.finish(identifier, {"usage": {"input_tokens": 30, "output_tokens": 10}})
    with pytest.raises(usage.BudgetError, match="request limit"):
        reserve(config=config, auth_mode="chatgpt")
    result = usage.summary()
    assert result["request_count"] == 1
    assert result["estimated_cost_usd"] is None
    assert result["reserved_cost_usd"] == result["unknown_usage_requests"] == 0
    assert result["rows"][0]["input_tokens"] == 30
    assert result["rows"][0]["cached_input_tokens"] is None
    assert result["rows"][0]["estimated_cost_usd"] is None
    assert "https://chatgpt.com/settings/usage" in result["note"]


def test_unpriced_api_models_require_explicit_opt_in_and_remain_unknown_cost():
    with pytest.raises(usage.BudgetError, match="USD cap cannot be enforced"):
        reserve(model="custom-model")
    identifier = reserve(model="custom-model", config=settings(ai_allow_unpriced_models=True))
    usage.finish(identifier, openai_response())
    result = usage.summary()
    assert result["request_count"] == 1
    assert result["estimated_cost_usd"] is None
    assert result["reserved_cost_usd"] == 0
    assert result["unknown_usage_requests"] == 1
    assert result["rows"][0]["input_tokens"] == 100
    assert "Unpriced opt-in models have no enforceable USD cap" in result["note"]


def test_defaults_and_settings_object():
    identifier = reserve(config={})
    usage.finish(identifier, openai_response())
    identifier = reserve(config=SimpleNamespace(ai_daily_request_limit=2))
    usage.finish(identifier, openai_response())
    with pytest.raises(usage.BudgetError, match="request limit"):
        reserve(config={"ai_daily_request_limit": 2})


@pytest.mark.parametrize("patch", [
    {"ai_daily_request_limit": 0}, {"ai_daily_request_limit": True},
    {"ai_daily_request_limit": 501}, {"ai_daily_budget_usd": -1},
    {"ai_daily_budget_usd": float("nan")}, {"ai_daily_budget_usd": float("inf")},
    {"ai_daily_budget_usd": True}, {"ai_daily_budget_usd": "0.1"},
    {"ai_max_output_tokens": 0}, {"ai_max_output_tokens": True},
    {"ai_allow_unpriced_models": "yes"}, {"ai_daily_request_limit": 1.5},
    {"ai_daily_budget_usd": 101}, {"ai_daily_budget_usd": 10**5000},
    {"ai_daily_budget_usd": Decimal("sNaN")}, {"ai_daily_budget_usd": Decimal("Infinity")},
    {"ai_max_output_tokens": 2049}, {"ai_max_output_tokens": "512"},
])
def test_invalid_settings_fail_closed(patch):
    with pytest.raises(usage.BudgetError):
        reserve(config=settings(**patch))
    assert usage.summary()["request_count"] == 0


@pytest.mark.parametrize("provider,model,response,expected_input,expected_output,expected_cached,cost", [
    ("openai", "gpt-6-luna",
     {"usage": {"input_tokens": 1_000_000, "input_tokens_details": {"cached_tokens": 200_000},
                "output_tokens": 100_000, "output_tokens_details": {"reasoning_tokens": 80_000}}},
     1_000_000, 100_000, 200_000, 0.132),
    ("gemini", "gemini-3.5-flash-lite",
     {"usageMetadata": {"promptTokenCount": 1_000_000, "candidatesTokenCount": 100_000,
                        "thoughtsTokenCount": 200_000, "cachedContentTokenCount": 200_000,
                        "totalTokenCount": 1_300_000}},
     1_000_000, 300_000, 200_000, 0.996),
    ("groq", "openai/gpt-oss-20b",
     {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 100_000,
                "prompt_tokens_details": {"cached_tokens": 200_000}}},
     1_000_000, 100_000, 200_000, 0.105),
    ("groq", "openai/gpt-oss-120b",
     {"usage": {"prompt_tokens": 1_000_000, "completion_tokens": 100_000}},
     1_000_000, 100_000, None, 0.21),
])
def test_provider_prices_cached_input_and_hidden_reasoning(
        provider, model, response, expected_input, expected_output, expected_cached, cost):
    identifier = reserve(provider=provider, model=model)
    usage.finish(identifier, response)
    result = usage.summary()
    row = result["rows"][0]
    assert row["input_tokens"] == expected_input
    assert row["output_tokens"] == expected_output
    assert row["cached_input_tokens"] == expected_cached
    assert row["audio_seconds"] is None
    assert row["estimated_cost_usd"] == pytest.approx(cost)
    assert result["estimated_cost_usd"] == pytest.approx(cost)
    assert result["reserved_cost_usd"] == result["unknown_usage_requests"] == 0


@pytest.mark.parametrize("event,status", [
    ("response.completed", "completed"), ("response.incomplete", "incomplete"), ("response.failed", "failed"),
])
def test_terminal_responses_sse_event_usage_is_supported(event, status):
    identifier = reserve()
    usage.finish(identifier, {"type": event, "response": openai_response()}, status=status)
    assert usage.summary()["rows"][0]["output_tokens"] == 20
    assert usage.summary()["unknown_usage_requests"] == 0


@pytest.mark.parametrize("metadata,expected", [
    ({"promptTokenCount": 100, "candidatesTokenCount": 20, "totalTokenCount": 150}, 50),
    ({"promptTokenCount": 100, "totalTokenCount": 150}, 50),
    ({"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 0}, 20),
    ({"promptTokenCount": 100, "candidatesTokenCount": 20}, None),
    ({"promptTokenCount": 100, "candidatesTokenCount": 20, "totalTokenCount": 110}, None),
    ({"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 10,
      "totalTokenCount": 150}, None),
])
def test_gemini_missing_or_inconsistent_thoughts_are_not_assumed_zero(metadata, expected):
    identifier = reserve(provider="gemini", model="gemini-3.5-flash-lite")
    usage.finish(identifier, {"usageMetadata": {**metadata, "cachedContentTokenCount": 0}})
    result = usage.summary()
    assert result["rows"][0]["output_tokens"] == expected
    assert result["unknown_usage_requests"] == int(expected is None)
    assert (result["reserved_cost_usd"] > 0) == (expected is None)


@pytest.mark.parametrize("response", [None, {}, [], "not a response object", {"usage": {}}, {"usage": None},
    {"usage": {"input_tokens": 0}}, {"usage": {"output_tokens": 0}},
    {"usage": {"input_tokens": 10, "output_tokens": 10}},
    openai_response(prompt=True), openai_response(prompt=-1), openai_response(prompt="100"),
    openai_response(output=1.5), openai_response(cached=101), openai_response(cached=None),
    openai_response(prompt=2**53), openai_response(output=float("nan")),
    openai_response(output=float("inf")), openai_response(cached=True),
])
def test_missing_or_invalid_usage_is_unknown_and_keeps_hold(response):
    identifier = reserve()
    before = usage.summary()["reserved_cost_usd"]
    usage.finish(identifier, response)
    result = usage.summary()
    assert result["estimated_cost_usd"] is None
    assert result["reserved_cost_usd"] == before
    assert result["unknown_usage_requests"] == 1


def test_explicit_zero_usage_is_known_and_releases_usd_but_not_request_count():
    identifier = reserve(config=settings(ai_daily_request_limit=1))
    usage.finish(identifier, openai_response(prompt=0, output=0, cached=0))
    result = usage.summary()
    assert result["request_count"] == 1
    assert result["estimated_cost_usd"] == result["reserved_cost_usd"] == 0
    assert result["unknown_usage_requests"] == 0
    assert result["rows"][0]["input_tokens"] == result["rows"][0]["output_tokens"] == 0
    with pytest.raises(usage.BudgetError, match="request limit"):
        reserve(config=settings(ai_daily_request_limit=1))


@pytest.mark.parametrize("seconds,response,billed_seconds", [
    (1, {"duration": 1.0}, 10), (60, {"duration": 60.0}, 60),
    (30, {"usage": {"seconds": 30.0}}, 30),
])
def test_audio_uses_reported_seconds_and_ten_second_minimum(seconds, response, billed_seconds):
    identifier = reserve(provider="groq", model="whisper-large-v3-turbo", operation="captions", audio_seconds=seconds)
    assert usage.summary()["reserved_cost_usd"] == pytest.approx(max(seconds, 10) * 0.04 / 3600)
    usage.finish(identifier, response)
    row = usage.summary()["rows"][0]
    assert row["audio_seconds"] == seconds
    assert row["input_tokens"] is row["output_tokens"] is row["cached_input_tokens"] is None
    assert row["estimated_cost_usd"] == pytest.approx(billed_seconds * 0.04 / 3600)
    assert row["reserved_cost_usd"] == row["unknown_usage_requests"] == 0


@pytest.mark.parametrize("seconds", [
    None, -1, True, float("inf"), float("nan"), "60", 2**53, pytest.param(10**5000, id="huge-integer"),
])
def test_audio_duration_must_be_known_before_reservation(seconds):
    with pytest.raises(usage.BudgetError, match="audio duration"):
        reserve(provider="groq", model="whisper-large-v3-turbo", operation="captions", audio_seconds=seconds)
    assert usage.summary()["request_count"] == 0


def test_audio_input_duration_does_not_invent_provider_reported_usage():
    identifier = reserve(provider="groq", model="whisper-large-v3-turbo", operation="captions", audio_seconds=60)
    held = usage.summary()["reserved_cost_usd"]
    usage.finish(identifier, {"text": "private transcript", "duration": None}, status="failed")
    result = usage.summary()
    assert result["estimated_cost_usd"] is None
    assert result["reserved_cost_usd"] == held
    assert result["rows"][0]["audio_seconds"] is None
    assert result["unknown_usage_requests"] == 1


def test_cache_hits_are_local_zero_cost_and_do_not_spend_request_cap():
    context = SimpleNamespace(id="job-1", project_id="project-1", check_cloud_consent=Mock(side_effect=RuntimeError))
    for operation in ("review", "titles", "captions"):
        usage.record_cache_hit("openai", "gpt-6-luna", operation, context)
    context.check_cloud_consent.assert_not_called()
    result = usage.summary()
    assert result["request_count"] == 0
    assert result["cache_hits"] == 3
    assert result["estimated_cost_usd"] == result["reserved_cost_usd"] == 0
    assert {row["operation"] for row in result["rows"]} == {"review", "titles", "captions"}
    assert result["recent"][0]["job_id"] == "job-1"
    assert result["recent"][0]["project_id"] == "project-1"
    reserve(config=settings(ai_daily_request_limit=1))
    result = usage.summary()
    assert result["request_count"] == 1 and result["cache_hits"] == 3
    assert result["estimated_cost_usd"] is None


@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted", "incomplete", "uncertain", "completed"])
def test_unknown_completion_and_pending_reservations_survive_restart(status, monkeypatch):
    payload = {"input": "x" * 100}
    hold = luna_hold(payload)
    config = settings(ai_daily_budget_usd=float(hold * 2))
    first = reserve(config=config, payload=payload)
    reserve(config=config, payload=payload)  # Simulate a process dying before finish().
    usage.finish(first, status=status, error="private error detail")
    importlib.reload(usage)
    monkeypatch.setattr(usage, "_now", lambda: datetime(2026, 11, 20, 12, tzinfo=timezone.utc))
    usage.initialize()
    result = usage.summary()
    assert result["request_count"] == result["unknown_usage_requests"] == 2
    assert result["reserved_cost_usd"] == pytest.approx(float(hold * 2))
    assert {row["status"] for row in result["recent"]} == {"pending", status}
    with pytest.raises(usage.BudgetError, match="conservative reservation"):
        reserve(config=config, payload=payload)


def test_failed_request_with_reported_usage_is_still_billed_and_finish_is_idempotent():
    identifier = reserve()
    usage.finish(identifier, openai_response(), status="failed", error="do not persist")
    first = usage.summary()
    usage.finish(identifier, status="failed")
    usage.finish(identifier, openai_response(prompt=0, output=0, cached=0))
    result = usage.summary()
    assert result["request_count"] == 1
    assert result["estimated_cost_usd"] == first["estimated_cost_usd"] == pytest.approx(0.00002)
    assert result["reserved_cost_usd"] == result["unknown_usage_requests"] == 0


def test_reported_cost_plus_outstanding_reservations_enforce_daily_budget():
    config = settings(ai_daily_budget_usd=0.001)
    first = reserve(config=config)
    usage.finish(first, openai_response(prompt=9000, output=0, cached=0))
    assert usage.summary()["estimated_cost_usd"] == pytest.approx(0.0009)
    with pytest.raises(usage.BudgetError, match="USD budget"):
        reserve(config=config)


def test_partial_dollar_totals_expose_unknown_count_and_unknown_token_totals():
    measured = reserve()
    usage.finish(measured, openai_response())
    reserve()
    result = usage.summary()
    assert result["estimated_cost_usd"] == pytest.approx(0.00002)
    assert result["unknown_usage_requests"] == 1
    assert result["rows"][0]["input_tokens"] is None
    assert result["rows"][0]["output_tokens"] is None
    assert result["rows"][0]["requests"] == 2


def test_utc_rollover_resets_caps_but_old_finish_stays_on_original_day(monkeypatch):
    config = settings(ai_daily_request_limit=1)
    yesterday = reserve(config=config)
    tomorrow = datetime(2026, 11, 20, 12, tzinfo=timezone.utc) + timedelta(days=1)
    monkeypatch.setattr(usage, "_now", lambda: tomorrow)
    assert usage.summary()["request_count"] == 0
    reserve(config=config)
    usage.finish(yesterday, openai_response())
    result = usage.summary()
    assert result["day"] == "2026-11-21"
    assert result["request_count"] == result["unknown_usage_requests"] == 1
    assert result["estimated_cost_usd"] is None


@pytest.mark.parametrize("payload", [
    {"input": "text", "max_output_tokens": 513}, {"max_tokens": True},
    {"generationConfig": {"maxOutputTokens": 1024}},
    {"input": "x" * (4 * 1024 * 1024)}, {"input": float("nan")},
    {"input": float("inf")}, {"input": ("not", "a JSON array")}, {1: "not a JSON key"},
    {"input": "unpaired surrogate \ud800"}, {"max_output_tokens": -1},
    {"generationConfig": {"maxOutputTokens": 512.5}},
])
def test_oversized_or_unbounded_payload_is_not_reserved(payload):
    with pytest.raises(usage.BudgetError):
        reserve(payload=payload)
    assert usage.summary()["request_count"] == 0


def test_reservation_uses_utf8_bytes_not_characters_or_cached_input_prices():
    payload = {"input": "Café 🎬"}
    reserve(payload=payload)
    assert usage.summary()["reserved_cost_usd"] == pytest.approx(float(luna_hold(payload)))


@pytest.mark.parametrize("part", [
    {"type": "input_image", "image_url": "data:image/jpeg;base64,IMAGE"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,IMAGE"}},
    {"inlineData": {"mimeType": "image/jpeg", "data": "IMAGE"}},
])
def test_image_bytes_are_excluded_but_each_image_holds_8192_tokens(part):
    small = {"input": [part, part]}
    large = json.loads(json.dumps(small).replace("IMAGE", "a" * 100_000))
    reserve(payload=small)
    first = usage.summary()["reserved_cost_usd"]
    reserve(payload=large)
    assert usage.summary()["reserved_cost_usd"] == pytest.approx(first * 2)
    assert first >= (2 * 8192 * 0.10 + 512 * 0.50) / 1_000_000


def test_context_consent_and_cancellation_are_checked_before_admission():
    context = SimpleNamespace(check_cancelled=Mock(), check_cloud_consent=Mock(side_effect=RuntimeError("consent withdrawn")))
    with pytest.raises(RuntimeError, match="consent withdrawn"):
        reserve(ctx=context)
    context.check_cancelled.assert_called_once()
    assert usage.summary()["request_count"] == 0


def test_storage_and_public_summary_never_contain_prompts_responses_paths_or_secrets(ledger):
    secret = "test-only-private-provider-secret"
    transcript = "private transcript must not enter the usage database"
    path = "C:/private/source/video.mp4"
    context = SimpleNamespace(id="job-safe", project_id=path, api_key=secret, source_path=path)
    identifier = reserve(payload={"input": transcript, "Authorization": secret, "source": path}, ctx=context,
                         config=settings(api_key=secret))
    usage.finish(identifier, {**openai_response(), "output_text": transcript, "api_key": secret},
                 status="failed", error=f"Bearer {secret} {path} {transcript}")
    with sqlite3.connect(ledger) as db:
        stored = "\n".join(row[0] for row in db.execute("SELECT data FROM ai_usage"))
    public = json.dumps(usage.summary())
    for value in (secret, transcript, path, "Authorization", "output_text", "source_path"):
        assert value not in stored
        assert value not in public
    assert usage.summary()["recent"][0]["job_id"] == "job-safe"
    assert "project_id" not in usage.summary()["recent"][0]
    assert "rates" not in usage.summary()["recent"][0]


@pytest.mark.parametrize("patch", [
    {"provider": "other"}, {"operation": "model-listing"}, {"model": "https://example.test/model?key=secret"},
    {"provider": "groq", "model": "openai/gpt-oss-20b", "auth_mode": "chatgpt"},
    {"provider": []}, {"model": None}, {"operation": {}}, {"auth_mode": []},
    {"model": "../secret"}, {"model": "C:\\private\\video.mp4"},
])
def test_invalid_identity_never_creates_usage(patch):
    with pytest.raises(ValueError):
        reserve(**patch)
    assert usage.summary()["request_count"] == 0


def test_unknown_reservation_cannot_create_or_change_usage():
    with pytest.raises(KeyError, match="not found"):
        usage.finish("missing")
    assert usage.summary()["request_count"] == 0


def test_import_does_not_resolve_database_or_create_an_import_cycle(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith("backend.database"):
            raise AssertionError("The usage module imported the store eagerly")
        return original_import(name, *args, **kwargs)

    spec = importlib.util.spec_from_file_location("usage_import_probe", usage.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    spec.loader.exec_module(module)
    assert callable(module.reserve)


def test_all_public_operations_use_the_current_store_connection_alias(monkeypatch):
    replacement = Mock(side_effect=store.connection)
    monkeypatch.setattr(store, "connection", replacement)
    usage.initialize()
    identifier = reserve()
    usage.finish(identifier, openai_response())
    usage.record_cache_hit("openai", "gpt-6-luna", "review")
    assert usage.summary()["request_count"] == 1
    assert replacement.call_count == 5


def test_default_request_limit_is_twenty_even_after_known_free_completions():
    for _ in range(20):
        identifier = reserve(config={})
        usage.finish(identifier, openai_response(prompt=0, output=0, cached=0))
    with pytest.raises(usage.BudgetError, match="request limit"):
        reserve(config={})
    assert usage.summary()["request_count"] == 20


def test_default_dollar_budget_is_ten_cents_and_failed_admission_has_no_row():
    payload = {"input": "x" * 1_000_000}
    assert luna_hold(payload) > Decimal("0.10")
    with pytest.raises(usage.BudgetError, match="USD budget"):
        reserve(config={}, payload=payload)
    assert usage.summary()["request_count"] == 0
    reserve(config={}, payload={})
    assert usage.summary()["reserved_cost_usd"] == pytest.approx(float(luna_hold({})))


@pytest.mark.parametrize("first_mode", ["chatgpt", "api_key"])
def test_api_and_plan_calls_share_one_daily_request_cap(first_mode):
    config = settings(ai_daily_request_limit=1)
    reserve(config=config, auth_mode=first_mode)
    other = "api_key" if first_mode == "chatgpt" else "chatgpt"
    with pytest.raises(usage.BudgetError, match="request limit"):
        reserve(config=config, auth_mode=other)
    assert usage.summary()["request_count"] == 1


def test_requests_across_providers_and_operations_share_one_dollar_budget():
    config = settings(ai_daily_budget_usd=0.001)
    identifier = reserve(config=config)
    usage.finish(identifier, openai_response(prompt=9000, output=0, cached=0))
    with pytest.raises(usage.BudgetError, match="USD budget"):
        reserve(provider="groq", model="openai/gpt-oss-20b", operation="titles", config=config)
    assert usage.summary()["request_count"] == 1


def test_plan_unknown_usage_never_holds_dollars_or_invents_token_totals():
    identifier = reserve(config=settings(ai_daily_budget_usd=0), auth_mode="chatgpt")
    usage.finish(identifier, status="uncertain")
    result = usage.summary()
    assert result["estimated_cost_usd"] is None
    assert result["reserved_cost_usd"] == 0
    assert result["unknown_usage_requests"] == 1
    assert all(result["rows"][0][metric] is None for metric in
               ("input_tokens", "output_tokens", "cached_input_tokens", "audio_seconds"))


def test_cache_events_do_not_fill_unknown_metrics_or_dilute_reported_usage():
    identifier = reserve(config=settings(ai_daily_request_limit=1))
    usage.finish(identifier, openai_response(prompt=40, output=15, cached=10))
    for _ in range(3):
        usage.record_cache_hit("openai", "gpt-6-luna", "review")
    result = usage.summary()
    row = result["rows"][0]
    assert row["requests"] == 1 and row["cache_hits"] == 3
    assert (row["input_tokens"], row["output_tokens"], row["cached_input_tokens"]) == (40, 15, 10)
    assert row["estimated_cost_usd"] == pytest.approx(0.0000106)
    cached = [event for event in result["recent"] if event["event"] == "cache_hit"]
    assert all(event["estimated_cost_usd"] == event["reserved_cost_usd"] == 0 for event in cached)
    assert all(event["input_tokens"] is None for event in cached)


def test_all_cached_prompt_tokens_are_discounted_not_falsely_free():
    identifier = reserve()
    usage.finish(identifier, openai_response(prompt=100, output=20, cached=100))
    assert usage.summary()["estimated_cost_usd"] == pytest.approx(0.000011)


def test_audio_settlement_uses_actual_reported_duration_not_reserved_duration():
    identifier = reserve(provider="groq", model="whisper-large-v3-turbo", operation="captions", audio_seconds=60)
    usage.finish(identifier, {"duration": 12.34567, "text": "do not store"})
    row = usage.summary()["rows"][0]
    assert row["audio_seconds"] == 12.34567
    assert row["estimated_cost_usd"] == pytest.approx(12.34567 * 0.04 / 3600)
    assert row["reserved_cost_usd"] == row["unknown_usage_requests"] == 0


@pytest.mark.parametrize("duration", [-1, True, float("nan"), float("inf"), "60", 2**53])
def test_invalid_audio_response_duration_keeps_the_reservation(duration):
    identifier = reserve(provider="groq", model="whisper-large-v3-turbo", operation="captions", audio_seconds=60)
    held = usage.summary()["reserved_cost_usd"]
    usage.finish(identifier, {"duration": duration})
    result = usage.summary()
    assert result["reserved_cost_usd"] == held
    assert result["estimated_cost_usd"] is None
    assert result["rows"][0]["audio_seconds"] is None


def test_recurring_audio_reservation_rounds_up_instead_of_under_reserving():
    seconds = Decimal("12.34567")
    exact = seconds * Decimal("0.04") / 3600
    reserve(provider="groq", model="whisper-large-v3-turbo", operation="captions", audio_seconds=float(seconds))
    held = Decimal(str(usage.summary()["reserved_cost_usd"]))
    assert exact <= held < exact + Decimal("0.000000000001")


def test_budget_admission_uses_exact_audio_cost_at_a_sub_float_boundary():
    exact = Decimal("12.34567") * Decimal("0.04") / 3600
    config = settings(ai_daily_budget_usd=exact + luna_hold({}) - Decimal("1e-26"))
    identifier = reserve(provider="groq", model="whisper-large-v3-turbo", operation="captions",
                         audio_seconds=12.34567, config=config)
    usage.finish(identifier, {"duration": 12.34567})
    with pytest.raises(usage.BudgetError, match="USD budget"):
        reserve(config=config)
    assert "estimated_cost_usd_exact" not in usage.summary()["recent"][0]


def test_settlement_uses_the_price_snapshot_saved_with_the_reservation(monkeypatch):
    identifier = reserve()
    monkeypatch.setitem(usage._PRICING, ("openai", "gpt-6-luna"), ("10", "1", "50", None))
    usage.finish(identifier, openai_response())
    assert usage.summary()["estimated_cost_usd"] == pytest.approx(0.00002)


def test_later_reported_usage_can_resolve_an_uncertain_request():
    identifier = reserve()
    usage.finish(identifier, {"usage": {"input_tokens": 100}}, status="uncertain")
    assert usage.summary()["unknown_usage_requests"] == 1
    assert usage.summary()["rows"][0]["input_tokens"] == 100
    usage.finish(identifier, openai_response())
    result = usage.summary()
    assert result["unknown_usage_requests"] == result["reserved_cost_usd"] == 0
    assert result["request_count"] == 1


def test_consent_withdrawn_during_admission_rolls_back_the_transaction():
    context = SimpleNamespace(check_cancelled=Mock(), check_cloud_consent=Mock(
        side_effect=[None, RuntimeError("consent withdrawn while waiting")]))
    with pytest.raises(RuntimeError, match="consent withdrawn"):
        reserve(ctx=context)
    assert context.check_cloud_consent.call_count == 2
    assert usage.summary()["request_count"] == 0
    reserve(config=settings(ai_daily_request_limit=1))
    assert usage.summary()["request_count"] == 1


def test_recent_events_are_bounded_and_ordered_without_losing_daily_totals(monkeypatch):
    base = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    moments = iter(base + timedelta(seconds=index) for index in range(26))
    monkeypatch.setattr(usage, "_now", lambda: next(moments))
    for index in range(25):
        usage.record_cache_hit("openai", "gpt-6-luna", "titles", SimpleNamespace(id=f"job-{index}"))
    result = usage.summary()
    assert result["cache_hits"] == 25
    assert len(result["recent"]) == 20
    assert [row["job_id"] for row in result["recent"]] == [f"job-{index}" for index in range(24, 4, -1)]


@pytest.mark.parametrize("patch", [{"status": []}, {"status": "refunded"}])
def test_invalid_finish_status_leaves_the_reservation_unchanged(patch):
    identifier = reserve()
    before = usage.summary()
    with pytest.raises(ValueError):
        usage.finish(identifier, openai_response(), **patch)
    assert usage.summary() == before


def test_cache_event_cannot_be_settled_as_a_request():
    usage.record_cache_hit("openai", "gpt-6-luna", "review", auth_mode="chatgpt")
    result = usage.summary()
    identifier = result["recent"][0]["id"]
    with pytest.raises(ValueError, match="cache event"):
        usage.finish(identifier, openai_response())
    assert usage.summary() == result
