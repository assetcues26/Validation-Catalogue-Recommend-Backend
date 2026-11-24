"""Slim port of main's filter_pinecone_candidates_by_vision (legacy product-type families).

Local text/regex only — no Gemini call. Drops candidate rows whose product type clearly
conflicts with vision text. If every row would be dropped, returns the original list
(same fail-safe as main).
"""

from __future__ import annotations

import re
from typing import Any

_MOBILE_NOT_A_PHONE = (
    r"(?:crane|rack|racking|trolley|stand|van|shelving|shelf|shelves|storage|tower|generator"
    r"|toilet|cabinet|pedestal|table|compactor|scaffold|scaffolding|platform|ladder|light"
    r"|lighting|pump|cart|workstation|x ?ray|xray|medical|clinic|dispensary|canteen|kitchen"
    r"|substation|dg|compressor|welding|screen|partition|container|office|cabin|crusher|plant"
    r"|bin|barricade|chair|desk|fan|cooler|projector|podium|whiteboard|board|bed|stretcher)"
)

# Legacy families from main classification_match (_PRODUCT_CATEGORY_PATTERNS) plus `ac`
# so HVAC/split-AC vision text participates in type conflicts (main v2 family).
_PRODUCT_CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
    "tv": re.compile(r"\b(?:tv|television|led tv|smart tv|flat screen tv)s?\b"),
    "monitor": re.compile(
        r"\b(?:monitor|computer monitor|desktop monitor|lcd monitor|led monitor|display monitor)s?\b"
    ),
    "laptop": re.compile(
        r"\b(?:laptop|notebook|thinkpad|macbook|chromebook|latitude|pavilion|elitebook|probook|"
        r"ideapad|vivobook|victus|omen|inspiron|vostro|xps|envy|spectre|zenbook|aspire|swift|"
        r"nitro|rog|tuf|predator)s?\b"
    ),
    "printer": re.compile(r"\b(?:printer|mfp|multifunction)s?\b"),
    "chair": re.compile(
        r"\b(?:chair|armchair|office chair|task chair|cafeteria chair|plastic chair|stool)s?(?:\b|\d)"
    ),
    "table": re.compile(
        r"\b(?:table|desk|workstation top|counter|worktop|writing table|conference table"
        r"|dining table|reception table|side table|coffee table|meeting table)s?\b"
    ),
    "cabinet": re.compile(
        r"\b(?:cabinet|almirah|cupboard|wardrobe|locker|storage unit|filing cabinet"
        r"|book ?case|book ?shelf|shelving|rack unit)s?\b"
    ),
    "sofa": re.compile(
        r"\b(?:sofa|couch|settee|recliner|lounge seat|lounge chair|armchair|arm chair"
        r"|easy chair|club chair|accent chair|sofa chair|wing chair)s?\b"
    ),
    "fire_extinguisher": re.compile(
        r"\b(?:fire extinguisher|fire extinguishers|abc extinguisher|co2 extinguisher)s?\b"
    ),
    "phone": re.compile(
        r"\b(?:phone|smartphone|mobile(?!\s+" + _MOBILE_NOT_A_PHONE + r")|mobile phone|cellphone"
        r"|iphone|oneplus|nord|galaxy|pixel|redmi)s?\b"
    ),
    "mouse": re.compile(r"\b(?:mouse|optical mouse|wireless mouse)s?\b"),
    "ac": re.compile(
        r"\b(?:air conditioners?|air conditioning|air condition|"
        r"(?:split|window|cassette|ductable|tower) acs?|split units?|aircon|acs?)\b"
    ),
}

_LAPTOP_ONLY_WORDS = re.compile(
    r"\b(?:laptop|notebook|thinkpad|macbook|chromebook|latitude|elitebook|probook|ideapad"
    r"|vivobook|spectre|zenbook)s?\b"
)
_NOT_A_LAPTOP_CONTEXT = re.compile(
    r"\b(?:desktop(?!\s+(?:monitor|screen|display))|cpu|tower|sff|small form factor"
    r"|all in one|all-in-one|aio|mini pc|micro tower|car|cars|hatchback|sedan|suv|vehicle"
    r"|maruti|suzuki|dzire)\b"
)


def normalize_classification_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().casefold()
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def join_vision_texts(*parts: Any) -> str:
    """Join vision fields the same way main uses '; ' separators."""
    return "; ".join(text for text in (str(part or "").strip() for part in parts) if text)


def _detect_product_categories(text: str, *, form_factor_context: bool = True) -> set[str]:
    norm = normalize_classification_value(text)
    if not norm:
        return set()
    found: set[str] = set()
    for category, pattern in _PRODUCT_CATEGORY_PATTERNS.items():
        if pattern.search(norm):
            found.add(category)
    if re.search(r"\btv\b", norm):
        found.add("tv")
    for token in norm.split():
        if re.fullmatch(r"tv\w*", token) or token in ("tv", "tvs"):
            found.add("tv")
    if re.search(r"\blatitude\b", norm):
        found.add("laptop")
    if (
        form_factor_context
        and "laptop" in found
        and not _LAPTOP_ONLY_WORDS.search(norm)
        and _NOT_A_LAPTOP_CONTEXT.search(norm)
    ):
        found.discard("laptop")
    return found


def _families_conflict(name_cats: set[str], detected_cats: set[str]) -> bool:
    """Same incompatible pairs as main, plus AC ↔ other-type (AA HVAC coverage)."""
    if not name_cats or not detected_cats:
        return False
    if name_cats & detected_cats:
        return False
    incompatible = (
        ("tv" in name_cats and "monitor" in detected_cats)
        or ("monitor" in name_cats and "tv" in detected_cats)
        or ("laptop" in name_cats and "tv" in detected_cats)
        or ("tv" in name_cats and "laptop" in detected_cats)
        or ("laptop" in name_cats and "monitor" in detected_cats and "laptop" not in detected_cats)
        or ("printer" in name_cats and "monitor" in detected_cats)
        or ("chair" in name_cats and detected_cats - {"chair"})
        or ("ac" in name_cats and detected_cats - {"ac"})
    )
    return bool(incompatible)


def _product_category_conflict(vision_or_name_text: str, row_or_other_text: str) -> bool:
    # Call order matches main filter: conflict(detected_text, row_text).
    return _families_conflict(
        _detect_product_categories(vision_or_name_text),
        _detect_product_categories(row_or_other_text),
    )


def filter_pinecone_candidates_by_vision(
    candidates: list[dict],
    detected_text: str,
    label_text: str | None = None,
) -> list[dict]:
    """Drop rows whose product type clearly conflicts with vision.

    When the client supplies CategoryId it scopes Pinecone; when absent, search is
    customer-only (+ optional AssetClassId). Row type is still judged from
    subcategory / makemodel / model only — same as main when
    CATEGORY_ALWAYS_TRUSTED_ENABLED is on.

    `label_text` is accepted for call-site parity with main; the AA slim port uses the
    legacy (v1) conflict path, which does not need the v2 label rescue branch.
    """
    del label_text  # parity with main signature; unused in AA v1 path
    if not candidates or not detected_text:
        return candidates
    keys = ("subcategory", "makemodel", "model")
    kept: list[dict] = []
    for candidate in candidates:
        row_text = join_vision_texts(*(candidate.get(key) for key in keys))
        if _product_category_conflict(detected_text, row_text):
            continue
        kept.append(candidate)
    return kept if kept else candidates
