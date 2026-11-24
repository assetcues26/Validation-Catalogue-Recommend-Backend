"""OpenAI GPT API client with cost protection and retry logic.

Mirror of gemini_service.py — same rate-limiting, circuit-breaker, and JSON
parsing patterns so that the pipeline layer can switch providers transparently.
"""

import base64
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

from asset_validator.core.config import (
    API_LIMITS,
    effective_gpt_call_timeout_sec,
    GPT_IMAGE_DETAIL,
    GPT_MAX_RETRIES,
    GPT_MIN_CALL_INTERVAL_SEC,
    GPT_POST_VISION_COOLDOWN_SEC,
    GPT_RATE_LIMIT_MAX_WAIT_SEC,
    GPT_TPM_COOLDOWN_SEC,
    MAX_JSON_LENGTH,
    MAX_PROMPT_LENGTH,
    clamp_gpt_output_tokens,
)
from asset_validator.core.constants import DANGEROUS_RESPONSE_KEYS
from asset_validator.utils.token_usage import extract_gpt_usage, record_usage

# ---------------------------------------------------------------------------
# Shared rate-limiting and circuit-breaker state (same structure as Gemini)
# ---------------------------------------------------------------------------

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
_gpt_call_lock = threading.Lock()
_last_gpt_call_finished = 0.0


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _ensure_configured() -> None:
    global _configured, _client, _model_name
    if not _configured:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set.")
        model_name = os.getenv("GPT_MODEL_NAME")
        if not model_name:
            raise ValueError("GPT_MODEL_NAME environment variable not set.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is required when GEMINI_USE=false. "
                "Install it with: pip install openai>=1.30.0"
            ) from exc

        timeout_sec = effective_gpt_call_timeout_sec()
        # App-level retry handles 429/backoff; disable SDK retries to avoid doubling calls.
        _client = OpenAI(
            api_key=api_key,
            timeout=timeout_sec if timeout_sec > 0 else None,
            max_retries=0,
        )
        _model_name = model_name
        _configured = True


# ---------------------------------------------------------------------------
# Rate limiting (identical algorithm to gemini_service)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Circuit breaker (identical algorithm to gemini_service)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Model helpers
# ---------------------------------------------------------------------------

def get_shared_models():
    """Return (model_name, model_name) for vision and text calls — same contract as Gemini."""
    _ensure_configured()
    with _model_lock:
        return _model_name, _model_name


def is_models_warmed() -> bool:
    return _client is not None


def warmup_models() -> None:
    try:
        get_shared_models()
        logging.info("GPT model warmup complete")
    except Exception as e:
        logging.warning(f"GPT model warmup failed: {e}")


def _acquire_gpt_call_slot() -> None:
    """Serialize GPT calls and enforce a minimum gap to reduce OpenAI 429 bursts."""
    global _last_gpt_call_finished
    _gpt_call_lock.acquire()
    if GPT_MIN_CALL_INTERVAL_SEC > 0:
        elapsed = time.time() - _last_gpt_call_finished
        if elapsed < GPT_MIN_CALL_INTERVAL_SEC:
            time.sleep(GPT_MIN_CALL_INTERVAL_SEC - elapsed)


def _release_gpt_call_slot() -> None:
    global _last_gpt_call_finished
    _last_gpt_call_finished = time.time()
    _gpt_call_lock.release()


def _rate_limit_wait_seconds(error_message: str, attempt: int) -> float:
    """Compute backoff for OpenAI 429 / quota errors."""
    wait_time = 1.0 * (attempt + 1)
    err_lower = error_message.lower()
    if (
        "429" not in err_lower
        and "rate limit" not in err_lower
        and "rate_limit" not in err_lower
        and "quota" not in err_lower
    ):
        return wait_time

    match = re.search(r"try again in ([0-9.]+)s", error_message, re.IGNORECASE)
    if match:
        try:
            return max(float(match.group(1)) + 0.5, wait_time)
        except ValueError:
            pass

    # TPM bursts need a full minute window to recover — short retries keep failing.
    if "tpm" in err_lower or "tokens per min" in err_lower:
        elapsed_in_minute = time.time() % 60.0
        return max(GPT_TPM_COOLDOWN_SEC - elapsed_in_minute, 10.0)

    return max(3.0 * (2 ** attempt), 4.0)


# ---------------------------------------------------------------------------
# Message building (GPT multimodal format)
# ---------------------------------------------------------------------------

def _build_gpt_messages(prompt: str, images=None):
    """Build OpenAI chat messages with optional vision image parts."""
    content: list = [{"type": "text", "text": prompt}]
    if images:
        for img in images:
            data = img["data"]
            if isinstance(data, bytes):
                data = base64.b64encode(data).decode("utf-8")
            # Ensure data is pure base64 (no prefix)
            if isinstance(data, str) and not data.startswith("data:"):
                data_url = f"data:{img['mime_type']};base64,{data}"
            else:
                data_url = data
            detail = GPT_IMAGE_DETAIL if GPT_IMAGE_DETAIL in ("auto", "low", "high") else "auto"
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": data_url,
                    "detail": detail,
                },
            })
    return [{"role": "user", "content": content}]


# ---------------------------------------------------------------------------
# Core API call with retry
# ---------------------------------------------------------------------------

def call_gpt_with_retry(
    model,
    prompt: str,
    images=None,
    max_tokens: int | None = None,
    retries: int | None = None,
    response_mime_type: str | None = None,
    usage_label: str | None = None,
) -> dict:
    """Call OpenAI GPT with retry/circuit-breaker — same return contract as call_gemini_with_retry."""
    if retries is None:
        retries = GPT_MAX_RETRIES
    if max_tokens is None:
        max_tokens = clamp_gpt_output_tokens()
    else:
        max_tokens = clamp_gpt_output_tokens(max_tokens)

    _ensure_configured()
    model_name = model if isinstance(model, str) else _model_name
    messages = _build_gpt_messages(prompt, images)

    # Build request kwargs
    is_reasoning_model = str(model_name).lower().startswith(("o1", "o3", "gpt-5"))
    token_param = "max_completion_tokens" if is_reasoning_model else "max_tokens"
    effective_tokens = max(max_tokens, 2500) if (is_reasoning_model and max_tokens is not None) else max_tokens

    request_kwargs: dict = {
        "model": model_name,
        "messages": messages,
        token_param: effective_tokens,
    }
    if not is_reasoning_model:
        request_kwargs["temperature"] = 0

    # Use JSON mode when caller requests JSON
    if response_mime_type == "application/json":
        request_kwargs["response_format"] = {"type": "json_object"}

    try:
        check_api_limits()
        check_circuit_breaker()
    except Exception as safety_error:
        return {"error": f"API_LIMIT_PROTECTION: {safety_error}"}

    if len(prompt) > MAX_PROMPT_LENGTH:
        return {"error": "PROMPT_TOO_LONG"}

    last_error = None
    raw_text = ""

    for attempt in range(retries):
        _acquire_gpt_call_slot()
        try:
            record_api_call()
            kw = dict(request_kwargs)
            for _fix_try in range(3):
                try:
                    response = _client.chat.completions.create(**kw)
                    break
                except Exception as api_err:
                    err_msg = str(api_err)
                    adjusted = False
                    if "temperature" in err_msg and "temperature" in kw:
                        kw.pop("temperature")
                        adjusted = True
                    if "max_tokens" in err_msg and "max_tokens" in kw:
                        val = kw.pop("max_tokens")
                        kw["max_completion_tokens"] = val
                        adjusted = True
                    if "response_format" in err_msg and "response_format" in kw:
                        kw.pop("response_format")
                        adjusted = True
                    if not adjusted:
                        raise

            choice = response.choices[0]
            if usage_label:
                record_usage(
                    usage_label,
                    model_name,
                    extract_gpt_usage(response),
                    has_images=bool(images),
                )
            raw_text = (choice.message.content or "").strip()

            if choice.finish_reason == "length" or not raw_text:
                if token_param in request_kwargs and isinstance(request_kwargs[token_param], int):
                    request_kwargs[token_param] = min(request_kwargs[token_param] * 2, 16000)

            # Strip markdown fences (same logic as gemini_service)
            if raw_text.startswith("```json"):
                raw_text = raw_text[7:]
            elif raw_text.startswith("```"):
                raw_text = raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3]
            raw_text = raw_text.strip()

            # Find first JSON object
            json_start = raw_text.find("{")
            if json_start == -1:
                raise json.JSONDecodeError("No JSON object found", raw_text, 0)
            raw_text = raw_text[json_start:]

            if len(raw_text) > MAX_JSON_LENGTH:
                raw_text = raw_text[:MAX_JSON_LENGTH]

            # Brace-counting to find complete JSON object
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
            if images and GPT_POST_VISION_COOLDOWN_SEC > 0:
                time.sleep(GPT_POST_VISION_COOLDOWN_SEC)
            return parsed

        except json.JSONDecodeError as e:
            last_error = f"Invalid JSON: {e}"
            # Same partial-recovery logic as gemini_service
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
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
        except Exception as e:
            last_error = str(e)
            if attempt < retries - 1:
                wait_time = min(
                    _rate_limit_wait_seconds(last_error, attempt),
                    GPT_RATE_LIMIT_MAX_WAIT_SEC,
                )
                logging.warning(
                    "GPT call failed (attempt %s/%s), retrying in %.1fs: %s",
                    attempt + 1,
                    retries,
                    wait_time,
                    last_error[:160],
                )
                time.sleep(wait_time)
        finally:
            _release_gpt_call_slot()

    record_api_failure()
    return {"error": f"API_FAILURE: GPT failed after {retries} attempts: {last_error}"}
