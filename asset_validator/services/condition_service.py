"""Condition normalization and repair policy (adapted from reference condition_mapper)."""

from __future__ import annotations

import re

_DAMAGE_KEYWORDS = re.compile(
    r"\b(dent|scratch|crack|chip|stain|rust|corrosion|damage|broken|missing|"
    r"deform|bent|tear|hole|scuff)\b",
    re.IGNORECASE,
)

_GRADE_ALIASES = {
    "excellent": "Excellent",
    "good": "Good",
    "fair": "Fair",
    "average": "Fair",
    "moderate": "Fair",
    "poor": "Poor",
    "bad": "Bad",
    "damaged": "Poor",
    "critical": "Poor",
}

_VEHICLE_TERMS = ("vehicle", "car", "truck", "van", "suv", "fleet", "sedan", "hatchback")
_PHONE_LAPTOP_TERMS = ("phone", "smartphone", "laptop", "notebook", "tablet", "macbook", "iphone")
_SCREEN_TERMS = ("screen", "display", "lcd", "glass", "monitor panel")
_VEHICLE_LOCATIONS = ("bumper", "fender", "panel", "door", "hood", "roof", "quarter", "grille")


def _asset_class(detected_asset: str) -> str:
    blob = (detected_asset or "").lower()
    if any(t in blob for t in _VEHICLE_TERMS):
        return "vehicle"
    if any(t in blob for t in _PHONE_LAPTOP_TERMS):
        return "phone_laptop"
    if any(t in blob for t in ("hvac", "ac", "cooler", "split")):
        return "hvac"
    if any(t in blob for t in ("furniture", "chair", "desk", "cabinet")):
        return "furniture"
    if any(t in blob for t in ("industrial", "generator", "machine", "compressor")):
        return "industrial"
    return "other"


def _score_to_hundred(score) -> int | None:
    if score is None:
        return None
    try:
        s = int(round(float(score)))
    except (TypeError, ValueError, OverflowError):  # "1e400" is infinity: no score, not a crash
        return None
    if 1 <= s <= 10:
        s *= 10
    return max(0, min(100, s))


def resolve_condition_grade(grade: str | None, score: int | float | None = None) -> str | None:
    if grade:
        key = str(grade).strip().lower()
        if key in _GRADE_ALIASES:
            return _GRADE_ALIASES[key]
        if key not in ("unknown", "n/a", "na", ""):
            return str(grade).strip().title()

    normalized = _score_to_hundred(score)
    if normalized is None:
        return None
    if normalized >= 85:
        return "Excellent"
    if normalized >= 70:
        return "Good"
    if normalized >= 50:
        return "Fair"
    return "Poor"


def _apply_repair_policy_to_item(item: dict, asset_class: str) -> None:
    location = (item.get("location") or "").lower()
    dtype = (item.get("type") or "").lower()
    detail = (item.get("detail") or "").lower()
    combined = f"{location} {dtype} {detail}"

    if asset_class == "vehicle":
        if any(loc in combined for loc in _VEHICLE_LOCATIONS):
            item.setdefault("acceptable_wear", True)
            item.setdefault("repair_needed", "cosmetic_only")
            item.setdefault("repair_urgency", "none")
            if item.get("severity") == "minor":
                item["severity"] = "minor"

    if asset_class == "phone_laptop":
        if any(term in combined for term in _SCREEN_TERMS):
            item["repair_needed"] = "required"
            item["repair_urgency"] = "immediate"
            item["acceptable_wear"] = False
            item["affects_function"] = True


def _merge_damage_items(items: list) -> list[dict]:
    seen: set[tuple[str | None, str | None]] = set()
    result: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = (item.get("location"), item.get("type"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:50]


def _count_severity(items: list[dict]) -> dict[str, int]:
    counts = {"minor": 0, "moderate": 0, "severe": 0}
    for item in items:
        sev = str(item.get("severity") or "").lower()
        if sev in counts:
            counts[sev] += 1
    return counts


_UNREADABLE_LABELS = frozenset({
    "unreadable", "blurry", "blank", "not visible", "unavailable",
    "no image", "black", "dark", "overexposed",
})


def _is_image_unavailable(image_readability: str | None) -> bool:
    """Return True when the asset image is blank, blurry, or the asset is not visible."""
    if not image_readability:
        return False
    # "E" is the schema's not-readable code and the value the pipeline actually passes.
    # Match it exactly — as a substring it would hit almost any ordinary word.
    if str(image_readability).strip().lower() == "e":
        return True
    normalized = str(image_readability).strip().lower()
    return any(label in normalized for label in _UNREADABLE_LABELS)


def _build_enriched_summary(cond: dict) -> str:
    """Build a comprehensive summary from all condition sub-fields."""
    field_map = [
        ("Cosmetic Condition", "cosmetic_condition"),
        ("Structural Condition", "structural_condition"),
        ("Functional Status", "functional_status"),
        ("Cleanliness", "cleanliness"),
        ("Wear Level", "wear_level"),
        ("Usability", "usability"),
        ("Repair Recommendation", "repair_recommendation"),
        ("Estimated Remaining Life", "estimated_remaining_life"),
    ]
    parts = []
    for label, key in field_map:
        val = cond.get(key)
        if val and isinstance(val, str) and val.strip():
            parts.append(f"{label}: {val.strip()}")
    return ". ".join(parts) + "." if parts else ""


def damage_needs_review(cond: dict) -> bool:
    if cond.get("damage_items"):
        return False
    summary = cond.get("condition_summary") or cond.get("summary") or ""
    cosmetic = cond.get("cosmetic_condition") or ""
    return bool(_DAMAGE_KEYWORDS.search(f"{summary} {cosmetic}"))


def normalize_condition(
    raw: dict | None,
    detected_asset: str = "",
    image_readability: str | None = None,
) -> dict | None:
    """Normalize LLM condition output; apply repair policy; build enriched summary.

    When the asset image is blank/blurry/unreadable the summary is set to an
    unavailable message and all other normalization still runs.
    """
    if not isinstance(raw, dict):
        return None

    cond = dict(raw)
    asset_class = _asset_class(detected_asset)

    score_raw = cond.get("condition_score") or cond.get("overall_score")
    overall = _score_to_hundred(score_raw)
    if overall is not None:
        cond["overall_score"] = overall

    grade = resolve_condition_grade(cond.get("grade"), cond.get("condition_score"))
    if grade:
        cond["grade"] = grade

    items = _merge_damage_items(cond.get("damage_items") or [])
    for item in items:
        _apply_repair_policy_to_item(item, asset_class)
        if item.get("seen_in_image") is None:
            item["seen_in_image"] = 2
    cond["damage_items"] = items

    if items:
        cond["has_damage"] = True
        cond["damage_count"] = len(items)
        cond["damage_by_severity"] = _count_severity(items)
    else:
        cond.setdefault("has_damage", False)
        cond.setdefault("damage_count", 0)
        cond.setdefault("damage_by_severity", {"minor": 0, "moderate": 0, "severe": 0})
        cond.setdefault("damage_items", [])

    if damage_needs_review(cond):
        cond["needs_review"] = True

    # --- Build enriched summary (server-side) ---
    if _is_image_unavailable(image_readability):
        cond["summary"] = "Asset image is unavailable and asset is not visible."
    else:
        enriched = _build_enriched_summary(cond)
        if enriched:
            cond["summary"] = enriched

    _apply_condition_detail_level(cond)
    return cond


# Detail fields the consumer does not display. In minimal mode the model is never asked
# for them, so they are filled here to keep the response SHAPE identical — strings null,
# collections empty, counters zero — and nothing downstream sees a missing key.
_CONDITION_NULL_STRING_FIELDS = (
    "cosmetic_condition",
    "structural_condition",
    "functional_status",
    "cleanliness",
    "wear_level",
    "usability",
    "repair_recommendation",
    "estimated_remaining_life",
)
_CONDITION_EMPTY_LIST_FIELDS = ("missing_parts", "functional_issues", "positive_aspects")


def _apply_condition_detail_level(cond: dict) -> None:
    """In minimal mode, null the undisplayed detail fields but keep every key present."""
    from asset_validator.core.config import condition_detail_minimal

    if not condition_detail_minimal():
        return

    for key in _CONDITION_NULL_STRING_FIELDS:
        cond[key] = None
    for key in _CONDITION_EMPTY_LIST_FIELDS:
        # Empty list, not null — consumers iterate these without a null guard.
        cond[key] = []
    cond["damage_items"] = []
    cond["has_damage"] = False
    cond["damage_count"] = 0
    cond["damage_by_severity"] = {"minor": 0, "moderate": 0, "severe": 0}
    # `summary` normally concatenates the fields just nulled, so mirror the single line
    # the reviewer actually sees rather than leaving an empty string behind.
    if not _is_image_unavailable_summary(cond):
        cond["summary"] = cond.get("condition_summary") or None


def _is_image_unavailable_summary(cond: dict) -> bool:
    """True when summary already carries the image-unavailable message."""
    return str(cond.get("summary") or "").startswith("Asset image is unavailable")
