"""HTTP payload safeguards for Validation and Catalogue Recommend.

Limits are defense-in-depth against oversized / malformed requests.
They do not change Stage A/B/C business logic once a valid request is accepted.
"""

from __future__ import annotations

import os
from typing import Any


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


# --- Limits (overridable via env; defaults sized for mobile photos + headroom) ---
MAX_REQUEST_BODY_BYTES = _int_env("MAX_REQUEST_BODY_BYTES", 25 * 1024 * 1024)  # 25 MB
MAX_IMAGE_BYTES = _int_env("MAX_IMAGE_BYTES", 12 * 1024 * 1024)  # 12 MB per image
MAX_ASSET_NAME_CHARS = _int_env("MAX_ASSET_NAME_CHARS", 500)
MAX_DESCRIPTION_CHARS = _int_env("MAX_DESCRIPTION_CHARS", 2000)
MAX_TAG_NUMBER_CHARS = _int_env("MAX_TAG_NUMBER_CHARS", 128)
MIN_IMAGE_BYTES = _int_env("MIN_IMAGE_BYTES", 32)


def sanitize_text(value: Any, *, field: str, max_chars: int) -> str | None:
    """Strip, drop NULs, enforce max length. Empty → None."""
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    if not text:
        return None
    if len(text) > max_chars:
        raise ValueError(
            f"{field} exceeds maximum length of {max_chars} characters "
            f"(got {len(text)})"
        )
    return text


def require_content_length_ok(content_length: str | None) -> None:
    """Reject when Content-Length is present and over the body budget."""
    if content_length is None or content_length == "":
        return
    try:
        size = int(str(content_length).strip())
    except ValueError:
        raise ValueError("Invalid Content-Length header") from None
    if size < 0:
        raise ValueError("Invalid Content-Length header")
    if size > MAX_REQUEST_BODY_BYTES:
        raise ValueError(
            f"Request body too large: Content-Length {size} exceeds "
            f"limit of {MAX_REQUEST_BODY_BYTES} bytes"
        )


def estimate_b64_decoded_size(b64_text: str) -> int:
    """Approximate decoded byte length from a base64 string (ignores whitespace)."""
    compact = "".join(b64_text.split())
    padding = compact.count("=")
    return max((len(compact) * 3) // 4 - padding, 0)


def decode_and_validate_image_bytes(
    raw: bytes,
    *,
    field: str,
    require_image_magic: bool = True,
) -> bytes:
    """Enforce empty/min/max size and optional image magic-byte check."""
    if raw is None:
        raise ValueError(f"Missing required field: {field}")
    data = bytes(raw)
    if len(data) < MIN_IMAGE_BYTES:
        raise ValueError(f"{field} is empty or too small to be a valid image")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(
            f"{field} exceeds maximum size of {MAX_IMAGE_BYTES} bytes "
            f"(got {len(data)})"
        )
    if require_image_magic and not looks_like_image(data):
        raise ValueError(
            f"{field} does not look like a supported image "
            "(JPEG, PNG, GIF, or WEBP)"
        )
    return data


def looks_like_image(data: bytes) -> bool:
    """Cheap magic-byte sniff — not a full image parser."""
    if len(data) < 12:
        return False
    if data.startswith(b"\xff\xd8\xff"):
        return True  # JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True  # PNG
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True  # GIF
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True  # WEBP
    # Some clients send BMP
    if data.startswith(b"BM"):
        return True
    return False


def validate_positive_id(value: int | None, *, field: str, required: bool = False) -> int | None:
    if value is None:
        if required:
            raise ValueError(f"Missing or invalid required field: {field}")
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"Invalid {field}: must be a positive integer")
    return value
