"""Local AI request ledger; estimates are not invoices or provider balances.

The coordinator initializes this table after store.initialize(). No provider,
credential, settings, or database module is imported until a connection is used.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from uuid import uuid4


# USD snapshot, 2026-11-20. A missing cached rate means no advertised discount.
_PRICING = {
    ("openai", "gpt-6-luna"): ("0.10", "0.01", "0.50", None),
    ("gemini", "gemini-3.5-flash-lite"): ("0.30", "0.03", "2.50", None),
    ("groq", "openai/gpt-oss-20b"): ("0.075", None, "0.30", None),
    ("groq", "openai/gpt-oss-120b"): ("0.15", None, "0.60", None),
    ("groq", "whisper-large-v3-turbo"): (None, None, None, "0.04"),
}
_RATE_KEYS = ("input_per_million", "cached_input_per_million", "output_per_million", "audio_per_hour")
_METRICS = ("input_tokens", "output_tokens", "cached_input_tokens", "audio_seconds")
_MAX_BODY_BYTES = 4 * 1024 * 1024
_IMAGE_TOKENS = 8192
_MAX_REPORTED_NUMBER = 2**53 - 1
_RESERVATION_PRECISION = Decimal("0.000000000001")
_FINISH_STATUSES = {"completed", "failed", "cancelled", "interrupted", "incomplete", "uncertain"}
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_NOTE = (
    "USD estimates use the 2026-11-20 API pricing snapshot, not invoices, account balances, "
    "or remaining provider quota. Known costs are partial totals when unknown_usage_requests is nonzero. "
    "Missing usage or discounted-pricing details remain unknown; pending/uncertain API requests keep "
    "their conservative reservations, including after restart. Reported costs can exceed a reservation; "
    "this guard is not a provider billing guarantee. Unpriced opt-in models have no enforceable USD cap. "
    "ChatGPT plan usage has no applicable USD estimate or reservation, uses only the local request cap, "
    "and remains subject to provider plan limits: https://chatgpt.com/settings/usage. "
    "Cache hits are local events with zero HTTP requests and zero cost."
)


class BudgetError(RuntimeError):
    """A cloud request cannot safely be admitted under the current settings."""


def _connection():
    from backend.database.store import connection

    return connection()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def initialize() -> None:
    """Create only the ledger in the currently configured store database."""
    with _connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS ai_usage (
            id TEXT PRIMARY KEY, day TEXT NOT NULL, created_at TEXT NOT NULL, data TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS ai_usage_day ON ai_usage(day,created_at)")


def _identity(provider, model, operation, auth_mode) -> None:
    if not isinstance(provider, str) or provider not in {"gemini", "groq", "openai"}:
        raise ValueError("Unsupported AI usage provider")
    if not isinstance(model, str) or not _MODEL.fullmatch(model) or ".." in model:
        raise ValueError("AI usage requires a model identifier, not a URL or path")
    if not isinstance(operation, str) or operation not in {"review", "titles", "captions"}:
        raise ValueError("Unsupported AI usage operation")
    if (not isinstance(auth_mode, str) or auth_mode not in {"api_key", "chatgpt"}
            or (auth_mode == "chatgpt" and provider != "openai")):
        raise ValueError("ChatGPT plan authentication is supported only for OpenAI")


def _context_ids(ctx) -> dict:
    result = {}
    for source, target in (("id", "job_id"), ("project_id", "project_id")):
        value = getattr(ctx, source, None)
        if isinstance(value, str) and _IDENTIFIER.fullmatch(value):
            result[target] = value
    return result


def _check_context(ctx) -> None:
    for name in ("check_cancelled", "check_cloud_consent"):
        check = getattr(ctx, name, None)
        if callable(check):
            check()


def _settings(settings) -> tuple[int, Decimal, int, bool]:
    # Accept the application's settings dict or its Pydantic settings object.
    def get(name, default):
        return settings.get(name, default) if isinstance(settings, Mapping) else getattr(settings, name, default)

    limit = get("ai_daily_request_limit", 20)
    output = get("ai_max_output_tokens", 512)
    budget = get("ai_daily_budget_usd", 0.10)
    allow_unpriced = get("ai_allow_unpriced_models", False)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise BudgetError("Set the daily AI request limit to an integer from 1 to 500 in Settings.")
    if type(output) is not int or not 128 <= output <= 2048:
        raise BudgetError("Set the AI output token cap from 128 to 2048 in Settings.")
    if type(budget) not in (int, float, Decimal):
        raise BudgetError("Set a finite daily AI budget from $0 to $100 in Settings.")
    if ((isinstance(budget, Decimal) and not budget.is_finite())
            or (isinstance(budget, float) and not math.isfinite(budget)) or not 0 <= budget <= 100):
        raise BudgetError("Set a finite daily AI budget from $0 to $100 in Settings.")
    budget = Decimal(str(budget))
    if type(allow_unpriced) is not bool:
        raise BudgetError("Unpriced AI models require explicit opt-in in Settings.")
    return limit, budget, output, allow_unpriced


def _payload_tokens(payload, output_cap: int, *, plan: bool) -> int:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise BudgetError("Provide a bounded JSON request body to reserve AI usage.")
    try:
        # Enforce the wire-size bound before excluding image bytes from token estimates.
        size = len(_encode(payload).encode("utf-8"))
        if size > _MAX_BODY_BYTES:
            raise BudgetError("AI request exceeds 4 MiB. Reduce the transcript or sampled frames.")
        if not plan:
            limits = [payload[key] for key in ("max_output_tokens", "max_tokens", "max_completion_tokens") if key in payload]
            config = payload.get("generationConfig", payload.get("generation_config", {}))
            if isinstance(config, dict):
                limits.extend(config[key] for key in ("maxOutputTokens", "max_output_tokens") if key in config)
            if any(type(value) is not int or not 0 < value <= output_cap for value in limits):
                raise BudgetError("The request output limit exceeds the AI token cap in Settings.")

        images = 0

        def without_image_data(value):
            nonlocal images
            if isinstance(value, list):
                return [without_image_data(item) for item in value]
            if not isinstance(value, dict):
                if value is None or type(value) in (str, int, float, bool):
                    return value
                raise ValueError("Request bodies must contain only JSON values")
            if any(not isinstance(key, str) for key in value):
                raise ValueError("Request body keys must be strings")
            if value.get("type") in ("input_image", "image_url", "image"):
                images += 1
                return {key: "" if key in {"image_url", "url", "data", "source"} else without_image_data(item)
                        for key, item in value.items()}
            # Gemini's image part has no type discriminator.
            result = {}
            for key, item in value.items():
                if key in {"inlineData", "inline_data", "fileData", "file_data"} and isinstance(item, dict):
                    mime = item.get("mimeType", item.get("mime_type", ""))
                    if isinstance(mime, str) and mime.startswith("image/"):
                        images += 1
                        result[key] = without_image_data({
                            name: "" if name in {"data", "fileUri", "file_uri"} else part
                            for name, part in item.items()
                        })
                        continue
                result[key] = without_image_data(item)
            return result

        text_bytes = len(_encode(without_image_data(payload)).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise BudgetError("AI request must be finite, bounded JSON. Reduce the transcript or sampled frames.") from None
    # One token per UTF-8 byte deliberately overestimates ordinary text.
    return text_bytes + images * _IMAGE_TOKENS


def _seconds(value) -> float | None:
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and 0 <= number <= _MAX_REPORTED_NUMBER else None


def _reservation(rates, input_tokens: int, output_cap: int, audio_seconds) -> Decimal:
    if rates is None:
        return Decimal(0)
    if rates[3] is not None:
        seconds = _seconds(audio_seconds)
        if seconds is None:
            raise BudgetError("A known, finite audio duration is required before reserving cloud captions.")
        # Round a recurring per-second rate upward, never below the cost being held.
        return (Decimal(str(max(seconds, 10))) * Decimal(rates[3]) / 3600).quantize(
            _RESERVATION_PRECISION, rounding=ROUND_CEILING
        )
    return (Decimal(input_tokens) * Decimal(rates[0]) + Decimal(output_cap) * Decimal(rates[2])) / 1_000_000


def _day_entries(db, day: str) -> list[dict]:
    return [json.loads(row["data"]) for row in db.execute(
        "SELECT data FROM ai_usage WHERE day=? ORDER BY created_at DESC,id DESC", (day,)
    )]


def _amount(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def _estimated_amount(entry: dict) -> Decimal:
    # Keep exact settlement arithmetic for admission; JSON floats are for display.
    return _amount(entry.get("estimated_cost_usd_exact", entry["estimated_cost_usd"]))


def _insert(db, entry: dict) -> None:
    db.execute("INSERT INTO ai_usage (id,day,created_at,data) VALUES (?,?,?,?)",
               (entry["id"], entry["day"], entry["created_at"], _encode(entry)))


def reserve(provider: str, model: str, operation: str, settings, ctx=None, *, auth_mode="api_key", payload=None,
            audio_seconds=None) -> str:
    """Atomically count one imminent POST and hold its conservative API cost.

    Call immediately before each actual attempt. A reservation is never refunded
    from the request count, even if the request fails or the process restarts.
    """
    _identity(provider, model, operation, auth_mode)
    _check_context(ctx)
    limit, budget, output_cap, allow_unpriced = _settings(settings)
    plan = auth_mode == "chatgpt"
    rates = _PRICING.get((provider, model))
    if not plan and budget == 0:
        raise BudgetError("Cloud API requests are disabled by the $0 daily AI budget. Increase it in Settings.")
    if not plan and rates is None and not allow_unpriced:
        raise BudgetError("This model has no local API price. Choose a priced model or explicitly allow unpriced models "
                          "in Settings; their USD cap cannot be enforced.")
    input_tokens = _payload_tokens(payload, output_cap, plan=plan)
    held = Decimal(0) if plan else _reservation(rates, input_tokens, output_cap, audio_seconds)
    with _connection() as db:
        db.execute("BEGIN IMMEDIATE")
        # Compute UTC day only after the lock: a wait can cross midnight.
        moment = _now()
        day = moment.date().isoformat()
        entries = _day_entries(db, day)
        requests = [entry for entry in entries if entry["event"] == "request"]
        if len(requests) >= limit:
            raise BudgetError("Daily AI request limit reached. Wait until the next UTC day or raise the limit in Settings.")
        committed = sum((_estimated_amount(entry) + _amount(entry["reserved_cost_usd"])
                         for entry in requests if entry["auth_mode"] == "api_key"), Decimal(0))
        if not plan and rates is not None and committed + held > budget:
            raise BudgetError("Daily AI USD budget cannot cover this conservative reservation. Reduce the request/output "
                              "size, increase the budget in Settings, or wait until the next UTC day. "
                              "Pending or failed requests without usage still hold their reservations.")
        _check_context(ctx)
        identifier = uuid4().hex
        entry = {"id": identifier, "day": day, "created_at": moment.isoformat(), "event": "request",
                 "provider": provider, "model": model, "operation": operation, "auth_mode": auth_mode,
                 "status": "pending", "error": None, "rates": rates, "usage_complete": False,
                 "estimated_cost_usd": None, "reserved_cost_usd": float(held),
                 **dict.fromkeys(_METRICS), **_context_ids(ctx)}
        _insert(db, entry)
    return identifier


def _tokens(value) -> int | None:
    # Do not turn null, booleans, fractions, or numeric strings into zero/tokens.
    return value if type(value) is int and 0 <= value <= _MAX_REPORTED_NUMBER else None


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _reported(entry: dict, response) -> dict:
    result: dict[str, int | float | None] = dict.fromkeys(_METRICS)
    response = _object(response)
    # Incomplete/failed terminal events can report billable usage too.
    if response.get("type") in ("response.completed", "response.incomplete", "response.failed"):
        response = _object(response.get("response"))
    rates = entry["rates"]
    if rates is not None and rates[3] is not None:
        result["audio_seconds"] = _seconds(response.get("duration"))
        if result["audio_seconds"] is None:
            result["audio_seconds"] = _seconds(_object(response.get("usage")).get("seconds"))
        return result
    if entry["provider"] == "gemini":
        usage = _object(response.get("usageMetadata"))
        prompt = _tokens(usage.get("promptTokenCount"))
        candidates = _tokens(usage.get("candidatesTokenCount"))
        thoughts = _tokens(usage.get("thoughtsTokenCount"))
        total = _tokens(usage.get("totalTokenCount"))
        output = candidates + thoughts if candidates is not None and thoughts is not None else None
        # Without thoughtsTokenCount, total can establish hidden output usage.
        if prompt is not None and total is not None:
            derived = total - prompt
            if derived < 0 or (candidates is not None and derived < candidates):
                output = None
            elif output is None or output == derived:
                output = derived
            else:
                output = None
        result.update(input_tokens=prompt, output_tokens=output,
                      cached_input_tokens=_tokens(usage.get("cachedContentTokenCount")))
    else:
        usage = _object(response.get("usage"))
        if entry["provider"] == "openai":
            input_key, output_key, details_key = "input_tokens", "output_tokens", "input_tokens_details"
        else:
            input_key, output_key, details_key = "prompt_tokens", "completion_tokens", "prompt_tokens_details"
        result.update(input_tokens=_tokens(usage.get(input_key)), output_tokens=_tokens(usage.get(output_key)),
                      cached_input_tokens=_tokens(_object(usage.get(details_key)).get("cached_tokens")))
        # OpenAI output_tokens already includes reasoning; never add it again.
    if (result["cached_input_tokens"] is not None and result["input_tokens"] is not None
            and result["cached_input_tokens"] > result["input_tokens"]):
        result["cached_input_tokens"] = None
    return result


def _settlement(entry: dict) -> tuple[bool, Decimal | None]:
    rates = entry["rates"]
    if rates is not None and rates[3] is not None:
        seconds = entry["audio_seconds"]
        if seconds is None:
            return False, None
        return True, Decimal(str(max(seconds, 10))) * Decimal(rates[3]) / 3600
    prompt, output, cached = (entry[key] for key in _METRICS[:3])
    if prompt is None or output is None:
        return False, None
    if entry["auth_mode"] == "chatgpt":
        return True, None
    if rates is None:
        return True, None
    if rates[1] is not None and cached is None:
        return False, None
    discounted = cached if rates[1] is not None else 0
    cost = (Decimal(prompt - discounted) * Decimal(rates[0])
            + Decimal(discounted) * Decimal(rates[1] or rates[0]) + Decimal(output) * Decimal(rates[2])) / 1_000_000
    return True, cost


def finish(reservation_id: str, response: dict | None = None, *, status="completed", error: str | None = None) -> None:
    """Settle reported usage, or retain the hold when billing is uncertain.

    Raw errors and provider bodies are deliberately not stored. Repeated calls
    without new usage cannot erase previously reported usage or release a hold.
    """
    if not isinstance(status, str) or status not in _FINISH_STATUSES:
        raise ValueError("Unsupported AI usage completion status")
    if not isinstance(reservation_id, str):
        raise ValueError("AI usage reservation ID must be a string")
    with _connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT data FROM ai_usage WHERE id=?", (reservation_id,)).fetchone()
        if row is None:
            raise KeyError("AI usage reservation was not found")
        entry = json.loads(row["data"])
        if entry["event"] != "request":
            raise ValueError("A cache event is not an AI request reservation")
        metrics = _reported(entry, response)
        # A subsequent empty failure callback must not destroy a settled record.
        if not entry["usage_complete"]:
            entry.update({key: value for key, value in metrics.items() if value is not None})
            if (entry["cached_input_tokens"] is not None and entry["input_tokens"] is not None
                    and entry["cached_input_tokens"] > entry["input_tokens"]):
                entry["cached_input_tokens"] = None
            complete, cost = _settlement(entry)
            entry["usage_complete"] = complete
            entry["estimated_cost_usd"] = float(cost) if cost is not None else None
            entry["estimated_cost_usd_exact"] = str(cost) if cost is not None else None
            if complete and (cost is not None or entry["auth_mode"] == "chatgpt"):
                entry["reserved_cost_usd"] = 0.0
        entry["status"] = status
        entry["error"] = ("Provider request did not complete; billing may still apply."
                          if error is not None or status != "completed" else None)
        db.execute("UPDATE ai_usage SET data=? WHERE id=?", (_encode(entry), reservation_id))


def record_cache_hit(provider: str, model: str, operation: str, ctx=None, *, auth_mode="api_key") -> None:
    """Record a local event without spending a request or requiring cloud consent."""
    _identity(provider, model, operation, auth_mode)
    moment = _now()
    entry = {"id": uuid4().hex, "day": moment.date().isoformat(), "created_at": moment.isoformat(),
             "event": "cache_hit", "provider": provider, "model": model, "operation": operation,
             "auth_mode": auth_mode, "status": "cache_hit", "error": None,
             "estimated_cost_usd": 0.0, "reserved_cost_usd": 0.0, "usage_complete": True,
             **dict.fromkeys(_METRICS), **_context_ids(ctx)}
    with _connection() as db:
        _insert(db, entry)


def _unknown(entry: dict) -> bool:
    return entry["event"] == "request" and (not entry["usage_complete"] or (
        entry["auth_mode"] == "api_key" and entry["estimated_cost_usd"] is None
    ))


def _totals(entries: list[dict]) -> dict:
    requests = [entry for entry in entries if entry["event"] == "request"]
    costs = [_estimated_amount(entry) for entry in requests if entry["estimated_cost_usd"] is not None]
    return {"requests": len(requests), "cache_hits": len(entries) - len(requests),
            "estimated_cost_usd": float(sum(costs, Decimal(0))) if costs else (
                None if requests else 0.0),
            "reserved_cost_usd": float(sum((_amount(entry["reserved_cost_usd"]) for entry in requests), Decimal(0))),
            "unknown_usage_requests": sum(_unknown(entry) for entry in requests)}


def summary() -> dict:
    """Return today's UTC totals, per-operation/model rows and 20 safe recent events.

    Token/duration totals stay null if any request in that group is unknown.
    Known API dollar costs may be partial; unknown counts remain visible.
    """
    day = _now().date().isoformat()
    with _connection() as db:
        entries = _day_entries(db, day)
    groups = {}
    for entry in entries:
        key = tuple(entry[name] for name in ("provider", "auth_mode", "model", "operation"))
        groups.setdefault(key, []).append(entry)
    rows = []
    for key, events in sorted(groups.items()):
        requests = [entry for entry in events if entry["event"] == "request"]
        row: dict = dict(zip(("provider", "auth_mode", "model", "operation"), key))
        row.update(_totals(events))
        for metric in _METRICS:
            values = [entry[metric] for entry in requests]
            row[metric] = sum(values) if values and all(value is not None for value in values) else None
        rows.append(row)
    totals = _totals(entries)
    totals["request_count"] = totals.pop("requests")
    public_keys = ("id", "day", "created_at", "event", "provider", "auth_mode", "model", "operation",
                   "status", "error", "job_id", "project_id", *_METRICS, "estimated_cost_usd", "reserved_cost_usd")
    recent = [{**{key: entry[key] for key in public_keys if key in entry},
               "unknown_usage_requests": int(_unknown(entry))} for entry in entries[:20]]
    pricing = [{"provider": provider, "model": model,
                **{key: float(value) if value is not None else None for key, value in zip(_RATE_KEYS, rates)}}
               for (provider, model), rates in _PRICING.items()]
    return {"day": day, **totals, "rows": rows, "recent": recent, "pricing": pricing, "note": _NOTE}
