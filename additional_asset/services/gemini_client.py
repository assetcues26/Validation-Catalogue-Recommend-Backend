"""Gemini client (simplified port of main gemini_service + llm_provider)."""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any

from google import genai
from google.genai import types

from additional_asset.core import config
from additional_asset.prompts.loader import load_prompt
from additional_asset.utils.token_usage import extract_gemini_usage, record_usage

_client = None
_configured = False
_lock = threading.Lock()

api_usage_tracker = {
    "daily_calls": 0,
    "hourly_calls": 0,
    "minute_calls": 0,
    "last_reset_day": None,
    "last_reset_hour": None,
    "last_reset_minute": None,
    "lock": threading.Lock(),
}


def _ensure_configured() -> None:
    global _configured, _client
    if _configured:
        return
    with _lock:
        if _configured:
            return
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set.")
        _client = genai.Client(api_key=api_key)
        _configured = True


def get_genai_client():
    _ensure_configured()
    return _client


def get_shared_models() -> tuple[str, str]:
    _ensure_configured()
    vision = config.gemini_model_name()
    text = config.gemini_text_model_name()
    return vision, text


def _check_api_limits() -> None:
    with api_usage_tracker["lock"]:
        now = datetime.now()
        if api_usage_tracker["last_reset_day"] is None:
            api_usage_tracker["last_reset_day"] = now.day
            api_usage_tracker["last_reset_hour"] = now.hour
            api_usage_tracker["last_reset_minute"] = now.minute
        if now.day != api_usage_tracker["last_reset_day"]:
            api_usage_tracker["daily_calls"] = 0
            api_usage_tracker["last_reset_day"] = now.day
        if now.hour != api_usage_tracker["last_reset_hour"]:
            api_usage_tracker["hourly_calls"] = 0
            api_usage_tracker["last_reset_hour"] = now.hour
        if now.minute != api_usage_tracker["last_reset_minute"]:
            api_usage_tracker["minute_calls"] = 0
            api_usage_tracker["last_reset_minute"] = now.minute
        limits = config.API_LIMITS
        if api_usage_tracker["daily_calls"] >= limits["MAX_CALLS_PER_DAY"]:
            raise Exception("DAILY API LIMIT EXCEEDED")
        if api_usage_tracker["hourly_calls"] >= limits["MAX_CALLS_PER_HOUR"]:
            raise Exception("HOURLY API LIMIT EXCEEDED")
        if api_usage_tracker["minute_calls"] >= limits["MAX_CALLS_PER_MINUTE"]:
            raise Exception("MINUTE API LIMIT EXCEEDED")


def _record_api_call() -> None:
    with api_usage_tracker["lock"]:
        api_usage_tracker["daily_calls"] += 1
        api_usage_tracker["hourly_calls"] += 1
        api_usage_tracker["minute_calls"] += 1


def _build_contents(prompt: str, images=None):
    if not images:
        return prompt
    parts: list = [prompt]
    for img in images:
        data = img["data"]
        if isinstance(data, str):
            data = base64.b64decode(data)
        parts.append(types.Part.from_bytes(data=data, mime_type=img.get("mime_type") or "image/jpeg"))
    return parts


def _parse_json_object(raw_text: str) -> dict:
    text = (raw_text or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    json_start = text.find("{")
    if json_start == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)
    text = text[json_start:]
    if len(text) > config.MAX_JSON_LENGTH:
        text = text[: config.MAX_JSON_LENGTH]
    brace_count = 0
    json_end = -1
    for i, char in enumerate(text):
        if char == "{":
            brace_count += 1
        elif char == "}":
            brace_count -= 1
            if brace_count == 0:
                json_end = i + 1
                break
    if json_end != -1:
        text = text[:json_end]
    parsed = json.loads(text.strip())
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed


def call_llm_with_retry(
    model: str | None,
    prompt: str,
    images=None,
    max_tokens: int | None = None,
    retries: int | None = None,
    response_mime_type: str | None = "application/json",
    usage_label: str | None = None,
) -> dict[str, Any]:
    """Call Gemini; return parsed JSON dict or {"error": ...}."""
    _ensure_configured()
    model_name = model or config.gemini_model_name()
    if retries is None:
        retries = config.API_LIMITS["MAX_RETRIES"]
    if max_tokens is None:
        max_tokens = config.gemini_default_output_tokens()
    max_tokens = min(max_tokens, config.gemini_max_output_tokens())

    try:
        _check_api_limits()
    except Exception as exc:
        return {"error": f"API_LIMIT_PROTECTION: {exc}"}

    if len(prompt) > config.MAX_PROMPT_LENGTH:
        return {"error": "PROMPT_TOO_LONG"}

    timeout_sec = config.gemini_call_timeout_sec()
    config_kwargs: dict[str, Any] = {
        "temperature": 0,
        "max_output_tokens": max_tokens,
    }
    if response_mime_type == "application/json":
        config_kwargs["response_mime_type"] = "application/json"
    if timeout_sec > 0:
        config_kwargs["http_options"] = types.HttpOptions(timeout=int(timeout_sec * 1000))
    generation_config = types.GenerateContentConfig(**config_kwargs)

    last_error = None
    attempt_prompt = prompt
    for attempt in range(1, retries + 1):
        try:
            _record_api_call()
            contents = _build_contents(attempt_prompt, images)
            response = _client.models.generate_content(
                model=model_name,
                contents=contents,
                config=generation_config,
            )
            if usage_label:
                record_usage(
                    usage_label,
                    model_name,
                    extract_gemini_usage(response),
                    has_images=bool(images),
                )
            raw_text = (response.text or "").strip()
            parsed = _parse_json_object(raw_text)
            logging.info(
                "[%s] gemini ok model=%s attempt=%s",
                usage_label or "llm",
                model_name,
                attempt,
            )
            return parsed
        except Exception as exc:
            last_error = str(exc)
            err_lower = last_error.lower()
            is_capacity = (
                "503" in last_error
                or "unavailable" in err_lower
                or "high demand" in err_lower
                or "429" in last_error
                or "resourceexhausted" in err_lower
            )
            log_fn = logging.error if is_capacity or attempt >= retries else logging.warning
            log_fn(
                "[%s] gemini fail attempt=%s/%s capacity=%s err=%s",
                usage_label or "llm",
                attempt,
                retries,
                is_capacity,
                last_error[:500],
            )
            if attempt >= retries:
                break
            # JSON recovery note on retry
            try:
                note = load_prompt("json_reply_retry.md")
                attempt_prompt = prompt + "\n\n" + note
            except Exception:
                attempt_prompt = prompt
            # Capacity / 503: back off longer — this is what inflates wall latency.
            sleep_s = min(2 ** (attempt - 1), 4)
            if is_capacity:
                sleep_s = min(max(sleep_s, 2.0 * attempt), 12.0)
            time.sleep(sleep_s)

    logging.error(
        "[%s] gemini FAILED after %s attempts: %s",
        usage_label or "llm",
        retries,
        (last_error or "LLM_CALL_FAILED")[:500],
    )
    return {"error": last_error or "LLM_CALL_FAILED"}


def generate_embedding(text: str, request_id: str = "") -> list[float]:
    """Embed search text for Pinecone (same pattern as main)."""
    if not (text or "").strip():
        return []
    try:
        client = get_genai_client()
        model_name = config.embedding_model_name()
        if model_name.startswith("models/"):
            model_name = model_name[len("models/") :]
        start = time.time()
        result = client.models.embed_content(
            model=model_name,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=config.embedding_dimension()),
        )
        elapsed_ms = (time.time() - start) * 1000
        embedding = result.embeddings[0].values
        # Same billable fallback as main pinecone_service.generate_embedding
        billable = getattr(result, "billable_character_count", None)
        if billable is None:
            meta = getattr(result, "metadata", None)
            if meta is not None:
                billable = getattr(meta, "billable_character_count", None)
        if billable is not None:
            usage = {"prompt_tokens": int(billable), "total_tokens": int(billable), "completion_tokens": 0}
        else:
            usage = {"prompt_tokens": len(text), "total_tokens": len(text), "completion_tokens": 0}
        record_usage("embedding", model_name, usage, has_images=False)
        logging.info(
            "[%s] Embedding in %.0fms (dims=%s, text_len=%s)",
            request_id,
            elapsed_ms,
            len(embedding),
            len(text),
        )
        return list(embedding)
    except Exception as exc:
        logging.error("[%s] Embedding generation failed: %s", request_id, exc)
        return []
