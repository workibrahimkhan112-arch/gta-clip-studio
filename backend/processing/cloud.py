"""Small, fixed-endpoint cloud transport. Callers must obtain explicit consent.

Keys are read only immediately before a request. Provider bodies, request URLs,
headers and exception text are never included in public errors. There are no
retries: a disconnected billable POST may already have reached the provider.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib
import io
import json
import logging
import sqlite3
import re
from collections.abc import Mapping
from typing import Any

import httpx

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_AUDIO_BYTES = 3_000_000
REQUEST_SECONDS = 30.0


class CloudError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def get_api_key(provider: str) -> str | None:
    # Import lazily: offline processing does not depend on credential storage.
    try:
        lookup = importlib.import_module("backend.core.integrations").get_api_key
        key = lookup(provider)
    except Exception:  # noqa: BLE001 - credential failures must never expose secret exception text
        raise CloudError("Cloud credentials are unavailable. Configure the provider key in Settings.") from None
    if key is None or key == "":
        return None
    if not isinstance(key, str) or not key.isascii() or any(ord(c) < 33 or ord(c) == 127 for c in key):
        raise CloudError("The provider key is invalid. Replace it in Settings.")
    return key


def _check(ctx) -> None:
    if ctx is not None:
        ctx.check_cancelled()
        consent_check = getattr(ctx, "check_cloud_consent", None)
        if callable(consent_check):
            consent_check()


def _validate_url(provider: str, url: str) -> None:
    allowed = False
    if provider == "gemini" and isinstance(url, str):
        allowed = re.fullmatch(
            r"https://generativelanguage\.googleapis\.com/v1beta/models/"
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}:generateContent", url,
        ) is not None
    elif provider == "openai":
        allowed = url == "https://api.openai.com/v1/responses"
    elif provider == "groq":
        allowed = url in (
            "https://api.groq.com/openai/v1/chat/completions",
            "https://api.groq.com/openai/v1/audio/transcriptions",
        )
    if not allowed:
        raise CloudError("Unsupported cloud endpoint. Use the application's official provider integration.")


def _multipart(files, data) -> tuple[dict, dict]:
    """Snapshot a single bounded audio file; do not give httpx an unbounded stream."""
    if not isinstance(files, Mapping) or set(files) != {"file"}:
        raise CloudError("Cloud captions require one audio file of at most 3 MB.")
    item = files["file"]
    content: Any
    if isinstance(item, tuple) and 2 <= len(item) <= 3:
        filename, content = item[:2]
        content_type = item[2] if len(item) == 3 else "audio/wav"
    else:
        filename, content, content_type = "clip.wav", item, "audio/wav"
    if not isinstance(content, bytes):
        if not hasattr(content, "read"):
            raise CloudError("Cloud captions require readable audio of at most 3 MB.")
        content = content.read(MAX_AUDIO_BYTES + 1)
    if not isinstance(content, bytes) or not content or len(content) > MAX_AUDIO_BYTES:
        raise CloudError("Cloud caption audio must be nonempty and at most 3 MB. Select a shorter clip.")
    # Only an audio extension is useful to the API, not the user's source name.
    suffix = str(filename).rsplit(".", 1)[-1].lower()
    if suffix not in {"wav", "mp3", "mp4", "m4a", "ogg", "flac", "webm"}:
        raise CloudError("Cloud caption audio has an unsupported format.")
    if not isinstance(content_type, str) or not re.fullmatch(r"(?:audio|video)/[a-zA-Z0-9.+-]+", content_type):
        raise CloudError("Cloud caption audio has an invalid content type.")
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, (str, int, float, bool)) for k, v in data.items()):
        raise CloudError("Cloud caption form fields are invalid.")
    form = {k: str(v) for k, v in data.items()}
    if len(json.dumps(form).encode("utf-8")) > 32 * 1024:
        raise CloudError("Cloud caption form fields are too large.")
    return {"file": ("clip." + suffix, io.BytesIO(content), content_type)}, form


def _reject_constant(_value):
    raise ValueError("Non-finite JSON number")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property")
        result[key] = value
    return result


def load_json(raw):
    """Strict JSON, shared with editorial output/cache validation."""
    return json.loads(raw, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def _settle(identifier, response, *, status="completed"):
    from backend.core import usage
    try:
        usage.finish(identifier, response, status=status)
    except (OSError, sqlite3.Error, ValueError, KeyError):
        logging.getLogger(__name__).warning("AI usage could not be settled; its conservative reservation remains held")


async def _openai_events(response, ctx) -> dict:
    pending = bytearray()
    data_lines = []
    total = 0
    output_bytes = 0
    async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
        _check(ctx)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise CloudError("GPT stream exceeded its response limit. No automatic retry was made.")
        pending.extend(chunk)
        while b"\n" in pending:
            line, _, rest = pending.partition(b"\n")
            pending = bytearray(rest)
            line = line.rstrip(b"\r")
            if len(line) > 128 * 1024:
                raise CloudError("GPT returned an oversized stream event.")
            if line.startswith(b"data:"):
                data_lines.append(line[5:].lstrip())
            elif not line and data_lines:
                raw = b"\n".join(data_lines)
                data_lines = []
                if raw == b"[DONE]":
                    continue
                event = load_json(raw)
                if not isinstance(event, dict):
                    raise CloudError("GPT returned an invalid stream event.")
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    output_bytes += len(str(event.get("delta", "")).encode("utf-8"))
                    if output_bytes > 8192:
                        raise CloudError("GPT output exceeded the app's short-response limit.")
                elif kind == "response.completed":
                    result = event.get("response")
                    if not isinstance(result, dict) or result.get("status") != "completed":
                        raise CloudError("GPT stream ended without completed inference.")
                    return result
                elif kind in {"response.failed", "error", "response.incomplete"}:
                    terminal = event.get("response")
                    error = (terminal.get("error") if isinstance(terminal, dict) else None) or event.get("error") or {}
                    code = error.get("code") if isinstance(error, dict) else None
                    if code in {"subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable", "rate_limit_exceeded", "insufficient_quota"}:
                        raise CloudError("GPT usage limit reached. Check ChatGPT Settings → Usage or your API quota; no paid fallback was used.", 429)
                    raise CloudError("GPT inference failed or was incomplete. Retry explicitly after checking the selected model and account.")
        if len(pending) > 128 * 1024 or sum(map(len, data_lines)) > 128 * 1024:
            raise CloudError("GPT returned an oversized stream event.")
    raise CloudError("GPT stream was interrupted before response.completed. Billing may still apply; no automatic retry was made.")


async def _exchange(url: str, headers: dict, options: dict, ctx) -> dict:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False,
        trust_env=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
    ) as client:
        _check(ctx)
        async with client.stream("POST", url, headers=headers, **options) as response:
            _check(ctx)
            status = response.status_code
            if not 200 <= status < 300:
                if status in (401, 403):
                    message = "Cloud authentication/permission failed. Check the provider API key and model access in Settings."
                elif status == 429:
                    message = "Cloud quota/rate limit reached. Wait or check the provider's quota before retrying."
                elif status >= 500:
                    message = "The cloud provider is unavailable. Retry later; local detection results are preserved."
                else:
                    message = "The cloud request was rejected. Check the configured model and provider availability."
                raise CloudError(message, status)
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise CloudError("The cloud provider returned an unsupported compressed response.")
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    oversized = int(length) < 0 or int(length) > MAX_RESPONSE_BYTES
                except ValueError:
                    raise CloudError("The cloud provider returned an invalid response length.") from None
                if oversized:
                    raise CloudError("The cloud response exceeded the 2 MiB limit.")
            if url == "https://api.openai.com/v1/responses" and load_json(options.get("content", b"{}" )).get("stream") is True:
                return await _openai_events(response, ctx)
            raw = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                _check(ctx)
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise CloudError("The cloud response exceeded the 2 MiB limit.")
                raw.extend(chunk)
            _check(ctx)
            try:
                result = load_json(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise CloudError("The cloud provider returned invalid JSON. Retry or choose another model.") from None
            if not isinstance(result, dict):
                raise CloudError("The cloud provider returned an invalid response object.")
            return result


async def _request(url: str, headers: dict, options: dict, ctx) -> dict:
    # Poll cancellation while connecting, uploading AND waiting for the first
    # response byte. Cancelling the task closes its socket; no detached thread
    # can continue uploading after the job has been cancelled.
    task = asyncio.create_task(_exchange(url, headers, options, ctx))
    deadline = asyncio.get_running_loop().time() + REQUEST_SECONDS
    try:
        while True:
            _check(ctx)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise CloudError("Cloud request timed out after at most 30 seconds. Retry later.")
            done, _pending = await asyncio.wait({task}, timeout=min(0.1, remaining))
            _check(ctx)
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        # A response/close failure racing with cancellation must not replace
        # the caller's Cancelled exception (or the wall-clock deadline error).
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def post_json(provider: str, url: str, *, payload: dict | None = None, files=None,
              data: dict | None = None, ctx=None, settings: dict | None = None,
              operation="review", audio_seconds=None) -> dict:
    """POST JSON or Groq caption multipart; synchronous worker entry point.

    Consent belongs to the calling scoring/caption operation (the transport has
    no settings). No key is read, nor any connection opened, for invalid input.
    """
    _check(ctx)
    _validate_url(provider, url)
    reservation = None
    response_received = False
    from backend.core import usage
    from backend.models.schemas import Settings
    policy = dict(settings or Settings().model_dump())
    live_policy = getattr(ctx, "cloud_policy", None)

    if callable(live_policy):
        current = live_policy(policy)
        if not isinstance(current, dict):
            raise CloudError("AI spending policy is unavailable. No request was sent.")
        policy = current
    try:
        if files is not None:
            if provider != "groq" or not url.endswith("/audio/transcriptions") or payload is not None:
                raise CloudError("Multipart uploads are supported only for Groq captions.")
            audio, form = _multipart(files, data)
            options = {"files": audio, "data": form}
        else:
            if not isinstance(payload, dict) or data is not None:
                raise CloudError("The cloud request requires a JSON object.")
            raw = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
            if len(raw) > MAX_REQUEST_BYTES:
                raise CloudError("The cloud request is too large. Reduce the transcript or sampled frames.")
            options = {"content": raw}
        _check(ctx)
        auth_mode = policy.get("openai_auth_mode", "api_key") if provider == "openai" else "api_key"
        if auth_mode == "chatgpt":
            from .openai_api import plan_token
            if not payload or payload.get("store") is not False or payload.get("stream") is not True:
                raise CloudError("ChatGPT plan requests require private streaming Responses mode.")
            forbidden = {"background", "conversation", "max_output_tokens", "max_tool_calls", "metadata", "moderation", "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier", "temperature", "top_logprobs", "top_p", "truncation", "user", "previous_response_id"}
            if forbidden.intersection(payload):
                raise CloudError("This request contains fields unsupported by ChatGPT plan usage.")
            key = plan_token(payload.get("model", ""), ctx)
        else:
            key = get_api_key(provider)
        if not key:
            raise CloudError("No cloud API key configured. Add the provider key in Settings.")
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if files is None:
            headers["Content-Type"] = "application/json"
        headers["x-goog-api-key" if provider == "gemini" else "Authorization"] = key if provider == "gemini" else "Bearer " + key
        model = (payload or data or {}).get("model", "")
        if provider == "gemini":
            model = url.split("/models/", 1)[1].split(":", 1)[0]
        try:
            reservation = usage.reserve(provider, model, operation, policy, ctx, auth_mode=auth_mode,
                                        payload=payload or {}, audio_seconds=audio_seconds)
        except sqlite3.Error:
            raise CloudError("AI usage storage is unavailable. No request was sent; check local storage and retry.") from None
        result = asyncio.run(_request(url, headers, options, ctx))
        response_received = True
        if reservation is not None:
            _settle(reservation, result)
        return result
    except usage.BudgetError as exc:
        raise CloudError(str(exc), 429) from None
    except CloudError:
        raise
    except httpx.TimeoutException:
        raise CloudError("Cloud connection timed out. Check your connection and retry later.") from None
    except httpx.HTTPError:
        raise CloudError("Cloud connection failed. Check your network and retry later; local results are preserved.") from None
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        raise CloudError("The cloud request or response could not be processed. Check provider settings and retry.") from None
    finally:
        if reservation is not None and not response_received:
            # Settled usage is idempotent. An interrupted POST may still bill;
            # never refund its reservation merely because no response arrived.
            _settle(reservation, None, status="uncertain")
