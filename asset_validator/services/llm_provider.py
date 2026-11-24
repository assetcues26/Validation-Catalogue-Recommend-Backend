"""Unified LLM provider — routes to Gemini or GPT based on GEMINI_USE flag.

This is the single integration point that all pipeline consumers import.
Gemini-specific code (gemini_service.py) and GPT-specific code (gpt_service.py)
are NEVER modified — this module handles dispatch only.
"""

import logging

from asset_validator.core.config import use_gemini


def call_llm_with_retry(
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
    """Dispatch LLM call to the active provider (Gemini or GPT).

    Return contract is identical for both providers:
      - Success: dict with parsed JSON fields
      - Failure: dict with {"error": "..."}

    ``retry_prompt_suffix`` (Gemini only) is added to the prompt for retries that follow a
    reply which was not valid JSON — see call_gemini_with_retry. The GPT path ignores it.
    """
    if use_gemini():
        from asset_validator.services.gemini_service import call_gemini_with_retry
        return call_gemini_with_retry(
            model, prompt, images, max_tokens, retries, response_mime_type, usage_label,
            retry_prompt_suffix=retry_prompt_suffix,
            model_fallback=model_fallback,
        )
    else:
        from asset_validator.services.gpt_service import call_gpt_with_retry
        return call_gpt_with_retry(
            model, prompt, images, max_tokens, retries, response_mime_type, usage_label,
        )


def get_shared_models():
    """Return (vision_model, text_model) for the active provider."""
    if use_gemini():
        from asset_validator.services.gemini_service import get_shared_models as _gsm
        return _gsm()
    else:
        from asset_validator.services.gpt_service import get_shared_models as _gsm
        return _gsm()


def warmup_models() -> None:
    """Warm up the active provider's models."""
    if use_gemini():
        from asset_validator.services.gemini_service import warmup_models as _wm
        _wm()
    else:
        from asset_validator.services.gpt_service import warmup_models as _wm
        _wm()


def is_models_warmed() -> bool:
    """Check if the active provider's models are warmed."""
    if use_gemini():
        from asset_validator.services.gemini_service import is_models_warmed as _im
        return _im()
    else:
        from asset_validator.services.gpt_service import is_models_warmed as _im
        return _im()


def get_api_usage_tracker() -> dict:
    """Return the usage tracker for the active provider."""
    if use_gemini():
        from asset_validator.services.gemini_service import api_usage_tracker
        return api_usage_tracker
    else:
        from asset_validator.services.gpt_service import api_usage_tracker
        return api_usage_tracker


def get_active_provider_name() -> str:
    """Return 'gemini' or 'gpt' for logging and response headers."""
    return "gemini" if use_gemini() else "gpt"
