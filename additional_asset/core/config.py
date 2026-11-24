"""Environment configuration for Additional Asset (ported from main patterns)."""

from __future__ import annotations

import os


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


# --- Models (both fields kept; AA uses the same Flash Lite id for both) ---
def gemini_model_name() -> str:
    return os.getenv("GEMINI_MODEL_NAME", "gemini-3.5-flash-lite").strip()


def gemini_text_model_name() -> str:
    return os.getenv("GEMINI_TEXT_MODEL_NAME", "gemini-3.5-flash-lite").strip() or gemini_model_name()


def use_gemini() -> bool:
    return _bool("GEMINI_USE", "true")


def gemini_call_timeout_sec() -> float:
    return _float("GEMINI_CALL_TIMEOUT_SEC", 30.0)


def gemini_max_output_tokens() -> int:
    return _int("GEMINI_MAX_OUTPUT_TOKENS", 2000)


def gemini_default_output_tokens() -> int:
    return _int("GEMINI_DEFAULT_OUTPUT_TOKENS", 500)


def max_retries() -> int:
    return _int("GEMINI_MAX_RETRIES", 3)


# --- Images ---
def asset_image_max_edge() -> int:
    return _int("ASSET_IMAGE_MAX_EDGE", 896)


def barcode_image_max_edge() -> int:
    return _int("BARCODE_IMAGE_MAX_EDGE", 768)


def image_jpeg_quality() -> int:
    return _int("IMAGE_JPEG_QUALITY", 80)


def blank_image_gate_enabled() -> bool:
    """Pixel gate. When false, images go straight to blind view."""
    return _bool("BLANK_IMAGE_GATE_ENABLED", "true")


BLANK_IMAGE_DARK_LEVEL = _int("BLANK_IMAGE_DARK_LEVEL", 16)
BLANK_IMAGE_FLAT_TOLERANCE = _int("BLANK_IMAGE_FLAT_TOLERANCE", 8)
BLANK_IMAGE_DARK_FRACTION = _float("BLANK_IMAGE_DARK_FRACTION", 0.85)
BLANK_IMAGE_FLAT_FRACTION = _float("BLANK_IMAGE_FLAT_FRACTION", 0.95)


# --- Transport ---
def accept_multipart() -> bool:
    return _bool("ADDITIONAL_ASSET_ACCEPT_MULTIPART", "true")


def accept_json() -> bool:
    return _bool("ADDITIONAL_ASSET_ACCEPT_JSON", "true")


# --- Logging ---
def log_candidates() -> bool:
    return _bool("ADDITIONAL_ASSET_LOG_CANDIDATES", "true")


def log_level() -> str:
    return os.getenv("LOG_LEVEL", "INFO").strip().upper()


def debug_log_to_file() -> bool:
    """When true, mirror StepLogger lines to debug/<request_id>.jsonl."""
    return _bool("DEBUG_LOG_TO_FILE", "true")


def debug_log_dir() -> str:
    return os.getenv("DEBUG_LOG_DIR", "debug").strip() or "debug"


# --- Prompts ---
def prompt_hot_reload() -> bool:
    return _bool("PROMPT_HOT_RELOAD", "false")


def prompt_cache_hoist() -> bool:
    return _bool("PROMPT_CACHE_HOIST", "true")


def phase1_content_route_enabled() -> bool:
    """When true: skip blind-view Gemini calls; Phase 1 does content-based dual-image routing.

    Uses phase1_unified_content_route.md + build_unified_phase1_content_route_prompt().
    When false: legacy path (see blind_view_enabled).
    """
    return _bool("PHASE1_CONTENT_ROUTE_ENABLED", "false")


def blind_view_enabled() -> bool:
    """Legacy path only. When false: skip blind-view Gemini calls; use upload slot order.

    Ignored when PHASE1_CONTENT_ROUTE_ENABLED=true (content-route already skips blind view).
    """
    return _bool("BLIND_VIEW_ENABLED", "true")


# --- Condition / phase2 ---
def condition_detail_level() -> str:
    return os.getenv("CONDITION_DETAIL_LEVEL", "minimal").strip().lower() or "minimal"


def phase2_image_analysis_max_chars() -> int:
    return _int("PHASE2_IMAGE_ANALYSIS_MAX_CHARS", 350)


# --- Pinecone ---
def include_pinecone_lookup() -> bool:
    return _bool("INCLUDE_PINECONE_LOOKUP", "true")


PINECONE_TOP_K = _int("PINECONE_TOP_K", 10)
PINECONE_ASSET_CLASS_SCOPE_ENABLED = _bool("PINECONE_ASSET_CLASS_SCOPE_ENABLED", "true")
# Legacy env key retained for settings compatibility. CategoryId is optional;
# when absent, Pinecone searches with customerid (+ optional AssetClassId) only.
PINECONE_CATEGORY_SCOPE_ENABLED = _bool("PINECONE_CATEGORY_SCOPE_ENABLED", "true")


def embedding_model_name() -> str:
    return os.getenv("EMBEDDING_MODEL_NAME", "gemini-embedding-001").strip()


def embedding_dimension() -> int:
    return _int("EMBEDDING_DIMENSION", 768)


def pinecone_index_name() -> str:
    return os.getenv("PINECONE_INDEX_NAME", "").strip()


API_LIMITS = {
    "MAX_CALLS_PER_DAY": _int("GEMINI_MAX_CALLS_PER_DAY", 10000),
    "MAX_CALLS_PER_HOUR": _int("GEMINI_MAX_CALLS_PER_HOUR", 1000),
    "MAX_CALLS_PER_MINUTE": _int("GEMINI_MAX_CALLS_PER_MINUTE", 60),
    "MAX_RETRIES": max_retries(),
    "CIRCUIT_BREAKER_THRESHOLD": 8,
    "CIRCUIT_BREAKER_TIMEOUT": 60,
}

MAX_PROMPT_LENGTH = _int("MAX_PROMPT_LENGTH", 200_000)
MAX_JSON_LENGTH = _int("MAX_JSON_LENGTH", 100_000)
