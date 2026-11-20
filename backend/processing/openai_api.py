"""Small Responses API payloads shared by titles and editorial review."""
from __future__ import annotations

import json
from .cloud import CloudError

DEFAULT_MODEL = "gpt-6-luna"


def payload(model: str, instructions: str, inputs: dict, schema: dict, settings: dict, frames=()) -> dict:
    content = [{"type": "input_text", "text": json.dumps(inputs, ensure_ascii=False, allow_nan=False, separators=(",", ":"))}]
    for frame in frames:
        content.append({"type": "input_text", "text": f"Sample at clip second {frame['clip_seconds']:.3f}"})
        content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + frame["data"], "detail": "low"})
    result = {"model": model, "instructions": instructions, "input": [{"role": "user", "content": content}],
              "store": False, "reasoning": {"effort": "none"},
              "text": {"format": {"type": "json_schema", "name": "clip_result", "strict": True, "schema": schema}}}
    if settings.get("openai_auth_mode", "api_key") == "chatgpt":
        result["stream"] = True
    else:
        result["max_output_tokens"] = int(settings.get("ai_max_output_tokens", 512))
    return result


def response_text(response: dict) -> str:
    if response.get("status") != "completed" or not isinstance(response.get("output"), list):
        raise CloudError("GPT response did not complete. Retry explicitly; no automatic paid fallback was used.")
    parts = []
    for item in response["output"]:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict) or content.get("type") != "output_text" or not isinstance(content.get("text"), str):
                raise CloudError("GPT returned a refusal or unsupported output. Review the clip and retry explicitly.")
            parts.append(content["text"])
    text = "".join(parts)
    if not text or len(text.encode("utf-8")) > 16384:
        raise CloudError("GPT returned empty or oversized output. Reduce the request and retry.")
    return text


def plan_token(model: str, ctx=None) -> str:
    """Never substitute an API key or a more expensive model for plan access."""
    from backend.core import chatgpt_auth
    try:
        current = chatgpt_auth.status()
        account = current.get("active_account_id")
        if not current.get("connected") or not current.get("plan_enabled") or not account:
            raise CloudError("Connect an eligible ChatGPT account and allow plan usage in Settings first.")
        previous = getattr(ctx, "_chatgpt_account_id", None)
        if previous is not None and previous != account:
            raise CloudError("The active ChatGPT account changed. Start a new AI job to use the selected account.")
        catalog = getattr(ctx, "_chatgpt_models", None)
        if not isinstance(catalog, list):
            catalog = [item["id"] for item in chatgpt_auth.list_models()]
            if ctx is not None:
                ctx._chatgpt_account_id = account
                ctx._chatgpt_models = catalog
        if model not in catalog:
            raise CloudError("This model is not available on the selected ChatGPT account. Choose a listed model; no automatic upgrade will be made.")
        return chatgpt_auth.get_access_token()
    except chatgpt_auth.AuthError as exc:
        raise CloudError(str(exc)) from None
