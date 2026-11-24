"""Gemini API client with cost protection and retry logic."""

import base64
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

from google import genai
from google.genai import types

from asset_validator.core.config import (
    API_LIMITS,
    JSON_FALLBACK_ENABLED,
    JSON_FALLBACK_MODEL,
    JSON_FALLBACK_TIME_BUDGET_SEC,
    JSON_RECOVERY_LABELS,
    JSON_RETRY_NOTE_ENABLED,
    TEXT_CALL_USAGE_LABELS,
    effective_gemini_call_timeout_sec,
    MAX_JSON_LENGTH,
    MAX_PROMPT_LENGTH,
    clamp_output_tokens,
    text_model_name,
)
from asset_validator.core.constants import DANGEROUS_RESPONSE_KEYS
from asset_validator.prompts.loader import load_prompt
from asset_validator.utils.token_usage import extract_gemini_usage, record_usage

try:
    import psutil
    MEMORY_MONITORING = True
except ImportError:
    MEMORY_MONITORING = False

api_usage_tracker = {
    "daily_calls": 0,
    "hourly_calls": 0,
    "minute_calls": 0,
    "last_reset_day": None,
    "last_reset_hour": None,
    "last_reset_minute": None,
    "lock": threading.Lock(),
}

circuit_breaker = {
    "failure_count": 0,
    "last_failure_time": 0,
    "is_open": False,
    "lock": threading.Lock(),
}

_client = None
_model_name = None
_model_lock = threading.Lock()
_configured = False


def _ensure_configured() -> None:
    global _configured, _client, _model_name
    if not _configured:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set.")
        model_name = os.getenv("GEMINI_MODEL_NAME")
        if not model_name:
            raise ValueError("GEMINI_MODEL_NAME environment variable not set.")
        _client = genai.Client(api_key=api_key)
        _model_name = model_name
        _configured = True


def get_genai_client():
    _ensure_configured()
    return _client


def check_api_limits() -> None:
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

        if api_usage_tracker["daily_calls"] >= API_LIMITS["MAX_CALLS_PER_DAY"]:
            raise Exception("DAILY API LIMIT EXCEEDED - COST PROTECTION ACTIVATED")
        if api_usage_tracker["hourly_calls"] >= API_LIMITS["MAX_CALLS_PER_HOUR"]:
            raise Exception("HOURLY API LIMIT EXCEEDED - COST PROTECTION ACTIVATED")
        if api_usage_tracker["minute_calls"] >= API_LIMITS["MAX_CALLS_PER_MINUTE"]:
            raise Exception("MINUTE API LIMIT EXCEEDED - COST PROTECTION ACTIVATED")


def record_api_call() -> None:
    with api_usage_tracker["lock"]:
        api_usage_tracker["daily_calls"] += 1
        api_usage_tracker["hourly_calls"] += 1
        api_usage_tracker["minute_calls"] += 1


def check_circuit_breaker() -> None:
    with circuit_breaker["lock"]:
        current_time = time.time()
        if (
            circuit_breaker["is_open"]
            and current_time - circuit_breaker["last_failure_time"]
            > API_LIMITS["CIRCUIT_BREAKER_TIMEOUT"]
        ):
            circuit_breaker["is_open"] = False
            circuit_breaker["failure_count"] = 0
        if circuit_breaker["is_open"]:
            raise Exception("CIRCUIT BREAKER OPEN - API calls suspended")


def record_api_failure() -> None:
    with circuit_breaker["lock"]:
        circuit_breaker["failure_count"] += 1
        circuit_breaker["last_failure_time"] = time.time()
        if circuit_breaker["failure_count"] >= API_LIMITS["CIRCUIT_BREAKER_THRESHOLD"]:
            circuit_breaker["is_open"] = True


def record_api_success() -> None:
    with circuit_breaker["lock"]:
        circuit_breaker["failure_count"] = 0


def get_shared_models():
    """Return (model_name, model_name) for vision and text calls."""
    _ensure_configured()
    with _model_lock:
        return _model_name, _model_name


def is_models_warmed() -> bool:
    return _client is not None


def warmup_models() -> None:
    try:
        get_shared_models()
        logging.info("Model warmup complete")
    except Exception as e:
        logging.warning(f"Model warmup failed: {e}")


def _build_contents(prompt: str, images=None):
    if not images:
        return prompt
    parts: list = [prompt]
    for img in images:
        data = img["data"]
        if isinstance(data, str):
            data = base64.b64decode(data)
        parts.append(types.Part.from_bytes(data=data, mime_type=img["mime_type"]))
    return parts


def _build_generate_config(
    max_tokens: int,
    response_mime_type: str | None,
) -> types.GenerateContentConfig:
    config_kwargs: dict = {
        "temperature": 0,
        "max_output_tokens": max_tokens,
    }
    if response_mime_type == "application/json":
        config_kwargs["response_mime_type"] = "application/json"
    timeout_sec = effective_gemini_call_timeout_sec()
    if timeout_sec > 0:
        config_kwargs["http_options"] = types.HttpOptions(
            timeout=int(timeout_sec * 1000),
        )
    return types.GenerateContentConfig(**config_kwargs)


def call_gemini_with_retry(
    model,
    prompt: str,
    images=None,
    max_tokens: int | None = None,
    retries: int | None = None,
    response_mime_type: str | None = None,
    usage_label: str | None = None,
    retry_prompt_suffix: str | None = None,
    model_fallback: bool = True,
) -> dict:
    """Call Gemini, retrying failures.

    ``retry_prompt_suffix`` is appended to the prompt for the attempts that follow a reply
    which was not valid JSON. Calls run at temperature 0, so resending the identical prompt
    returns the identical broken reply; the suffix lets the retry ask differently. Transport
    errors and timeouts still retry with the prompt unchanged.

    For a call whose broken reply makes a check incomplete (``usage_label`` in
    JSON_RECOVERY_LABELS, docs §2.6): without a suffix of its own, the retry carries
    prompts/md/json_reply_retry.md; and if EVERY attempt on this model broke, one last attempt
    goes to JSON_FALLBACK_MODEL with the original prompt — unless ``model_fallback`` is False
    (a reply that carries a price) or JSON_FALLBACK_TIME_BUDGET_SEC has passed. Any other call
    retries exactly as before.
    """
    if retries is None:
        retries = API_LIMITS["MAX_RETRIES"]
    if max_tokens is None:
        max_tokens = clamp_output_tokens()
    else:
        max_tokens = clamp_output_tokens(max_tokens)

    _ensure_configured()
    model_name = model if isinstance(model, str) else _model_name
    # Hybrid routing: text-only phase-2 calls (no tag OCR) run on the cheaper text model
    # when GEMINI_TEXT_MODEL_NAME is set; the vision/OCR call stays on GEMINI_MODEL_NAME.
    if usage_label in TEXT_CALL_USAGE_LABELS:
        _tm = text_model_name()
        if _tm and _tm != model_name:
            model_name = _tm
    generation_config = _build_generate_config(max_tokens, response_mime_type)

    try:
        check_api_limits()
        check_circuit_breaker()
    except Exception as safety_error:
        return {"error": f"API_LIMIT_PROTECTION: {safety_error}"}

    if len(prompt) > MAX_PROMPT_LENGTH:
        return {"error": "PROMPT_TOO_LONG"}

    last_error = None
    raw_text = ""
    attempt_prompt = prompt
    recoverable = usage_label in JSON_RECOVERY_LABELS
    generic_note_due = retry_prompt_suffix is None and recoverable and JSON_RETRY_NOTE_ENABLED
    attempt_model = model_name
    total_attempts = retries
    fell_back = False
    json_breaks = 0
    primary_error = None
    started = time.monotonic()

    attempt = 0
    while attempt < total_attempts:
        try:
            record_api_call()
            contents = _build_contents(attempt_prompt, images)
            response = _client.models.generate_content(
                model=attempt_model,
                contents=contents,
                config=generation_config,
            )

            if usage_label:
                record_usage(
                    usage_label,
                    attempt_model,
                    extract_gemini_usage(response),
                    has_images=bool(images),
                )

            # An empty reply (output blocked as RECITATION or SAFETY) is a broken reply too: it
            # gets the same retry and fallback instead of failing on None (docs §2.6).
            raw_text = (response.text or "").strip()
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

            json_start = raw_text.find("{")
            if json_start == -1:
                raise json.JSONDecodeError("No JSON object found", raw_text, 0)
            raw_text = raw_text[json_start:]

            if len(raw_text) > MAX_JSON_LENGTH:
                raw_text = raw_text[:MAX_JSON_LENGTH]

            brace_count = 0
            json_end = -1
            for i, char in enumerate(raw_text):
                if char == "{":
                    brace_count += 1
                elif char == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i + 1
                        break
            if json_end != -1:
                raw_text = raw_text[:json_end]

            parsed = json.loads(raw_text.strip())
            if not isinstance(parsed, dict):
                raise ValueError(f"Expected JSON object, got {type(parsed).__name__}")

            for key in DANGEROUS_RESPONSE_KEYS.intersection(parsed.keys()):
                del parsed[key]

            record_api_success()
            if fell_back:
                logging.warning(
                    f"[{usage_label}] reply recovered on {attempt_model} after {retries} "
                    f"replies from {model_name} that were not valid JSON"
                )
            return parsed

        except json.JSONDecodeError as e:
            last_error = f"Invalid JSON: {e}"
            if fell_back:
                last_error = f"{primary_error}; fallback {attempt_model}: {last_error}"
            else:
                json_breaks += 1
            if generic_note_due and retry_prompt_suffix is None:
                # Read only when a retry is due, and never let a missing file break a call.
                try:
                    retry_prompt_suffix = load_prompt("json_reply_retry.md")
                except Exception as note_error:  # pragma: no cover - deployment fault
                    logging.error(f"[{usage_label}] retry note unavailable: {note_error}")
                    retry_prompt_suffix = ""
            if "recommendedsubcategory" in raw_text and "recommendedmakemodel" in raw_text:
                try:
                    fixed = raw_text
                    if fixed.count('"') % 2 == 1:
                        fixed += '"'
                    if not fixed.strip().endswith("}"):
                        fixed += " }"
                    parsed = json.loads(fixed)
                    record_api_success()
                    return parsed
                except json.JSONDecodeError:
                    pass
            if (
                retry_prompt_suffix
                and attempt < retries - 1
                and len(prompt) + len(retry_prompt_suffix) <= MAX_PROMPT_LENGTH
            ):
                if attempt_prompt is prompt:
                    logging.warning(
                        f"[{usage_label}] reply was not valid JSON ({str(e)[:80]}); "
                        "retrying with the corrective note"
                    )
                attempt_prompt = prompt + retry_prompt_suffix
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
            elif (
                not fell_back
                and recoverable
                and model_fallback
                and JSON_FALLBACK_ENABLED
                and JSON_FALLBACK_MODEL
                and JSON_FALLBACK_MODEL != model_name
                and json_breaks == retries
                and time.monotonic() - started <= JSON_FALLBACK_TIME_BUDGET_SEC
            ):
                # Every reply from this model broke (usually a repetition loop cut at the cap):
                # one last attempt on another model, with the original prompt (docs §2.6).
                logging.warning(
                    f"[{usage_label}] {retries} replies from {model_name} were not valid JSON "
                    f"({str(e)[:80]}); one last attempt on {JSON_FALLBACK_MODEL}"
                )
                fell_back = True
                primary_error = last_error
                attempt_model = JSON_FALLBACK_MODEL
                attempt_prompt = prompt
                total_attempts += 1
        except Exception as e:
            last_error = str(e)
            if fell_back:
                last_error = f"{primary_error}; fallback {attempt_model}: {last_error}"
            err_lower = last_error.lower()
            is_capacity = (
                "503" in last_error
                or "unavailable" in err_lower
                or "high demand" in err_lower
                or "429" in err_lower
                or "rate limit" in err_lower
                or "rate_limit" in err_lower
                or "quota" in err_lower
                or "resourceexhausted" in err_lower
            )
            logging.error(
                "[%s] gemini fail attempt=%s model=%s capacity=%s err=%s",
                usage_label or "llm",
                attempt + 1,
                attempt_model,
                is_capacity,
                last_error[:500],
            )
            if attempt < retries - 1:
                wait_time = 1.0 * (attempt + 1)
                if is_capacity:
                    match = re.search(r"try again in ([0-9.]+)s", last_error, re.IGNORECASE)
                    if match:
                        try:
                            wait_time = max(float(match.group(1)) + 0.5, wait_time)
                        except ValueError:
                            wait_time = max(3.0 * (2 ** attempt), 4.0)
                    else:
                        # 503 high-demand: longer backoff (major latency source).
                        wait_time = max(3.0 * (2 ** attempt), 4.0)
                time.sleep(min(wait_time, 20.0))
        attempt += 1

    record_api_failure()
    logging.error(
        "[%s] gemini FAILED after %s attempts: %s",
        usage_label or "llm",
        retries,
        (last_error or "unknown")[:500],
    )
    return {"error": f"API_FAILURE: Gemini failed after {retries} attempts: {last_error}"}
