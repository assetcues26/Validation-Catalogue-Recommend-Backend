"""Merge zxing decode with LLM OCR (AA generation path — no declared tag)."""

from __future__ import annotations

import re
from typing import Any

_DATE_STAMP_RE = re.compile(
    r"^\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4}$|^\d{1,2}[-/]\d{1,2}[-/]\d{2,4}$"
)


def _is_tag_like(value: str | None) -> bool:
    if not value:
        return False
    text = str(value).strip()
    if not text or text.upper() == "UNREADABLE":
        return False
    if _DATE_STAMP_RE.match(text):
        return False
    if text.lower().startswith("http"):
        return False
    return True


def order_decoded_prefer_tag_like(decoded_codes: list[dict] | None) -> list[dict]:
    """Prefer client asset-tag style payloads over manufacturer serial / DataMatrix blobs."""

    def _rank(entry: dict) -> tuple:
        val = str((entry or {}).get("text") or (entry or {}).get("value") or "").strip()
        typ = str((entry or {}).get("type") or "").strip().lower()
        fmt = str((entry or {}).get("format") or "").upper()
        img = str((entry or {}).get("image") or "").lower()
        score = 0
        if typ == "asset_tag":
            score += 1000
        if val.isdigit() and 4 <= len(val) <= 20:
            score += 500
        if "QR" in fmt:
            score += 100
        if img in ("barcode", "tag"):
            score += 50
        if len(val) > 20 or "," in val or "#" in val:
            score -= 200
        # Higher score first; stable by value length then value.
        return (-score, len(val), val)

    return sorted(list(decoded_codes or []), key=_rank)


def merge_tag_fields(
    llm_out: dict[str, Any],
    decoded_codes: list[dict] | None,
) -> dict[str, Any]:
    """Prefer zxing when available; else LLM OCR."""
    ordered = order_decoded_prefer_tag_like(decoded_codes)
    decoded_vals: list[str] = []
    for entry in ordered:
        val = str((entry or {}).get("text") or (entry or {}).get("value") or "").strip()
        if _is_tag_like(val):
            decoded_vals.append(val)

    decodedtagnumber = decoded_vals[0] if decoded_vals else None

    llm_tag = llm_out.get("detectedtagnumber")
    if isinstance(llm_tag, str):
        llm_tag = llm_tag.strip() or None
    else:
        llm_tag = None
    if llm_tag and str(llm_tag).upper() == "UNREADABLE":
        llm_tag = None
    if llm_tag and _DATE_STAMP_RE.match(str(llm_tag)):
        llm_tag = None

    if decodedtagnumber:
        return {
            "decodedtagnumber": decodedtagnumber,
            "detectedtagnumber": llm_tag or decodedtagnumber,
            "tagnumbersource": "decoded",
        }
    if llm_tag:
        return {
            "decodedtagnumber": None,
            "detectedtagnumber": llm_tag,
            "tagnumbersource": "vision",
        }
    return {
        "decodedtagnumber": None,
        "detectedtagnumber": None,
        "tagnumbersource": "NA",
    }
