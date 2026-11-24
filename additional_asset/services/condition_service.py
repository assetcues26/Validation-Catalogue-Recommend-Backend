"""Condition normalization (slim port of main condition_service)."""

from __future__ import annotations

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


def _score_to_hundred(score) -> int | None:
    if score is None:
        return None
    try:
        s = int(round(float(score)))
    except (TypeError, ValueError, OverflowError):
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


def normalize_condition(raw: dict | None, detected_asset: str = "") -> dict | None:
    if not isinstance(raw, dict):
        return None
    cond = dict(raw)
    score_raw = cond.get("condition_score") or cond.get("overall_score")
    overall = _score_to_hundred(score_raw)
    if overall is not None:
        cond["overall_score"] = overall
    grade = resolve_condition_grade(cond.get("grade"), cond.get("condition_score"))
    if grade:
        cond["grade"] = grade

    items = []
    seen: set[tuple] = set()
    for raw_item in cond.get("damage_items") or []:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        key = (item.get("location"), item.get("type"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    cond["damage_items"] = items[:50]
    cond["damage_count"] = len(items)
    # Authoritative from structured items only. Keyword-scanning the summary
    # false-positives on phrases like "no visible damage".
    cond["has_damage"] = bool(items)

    # Minimal public shape for AA
    if "condition_summary" not in cond and cond.get("summary"):
        cond["condition_summary"] = cond.get("summary")
    return cond
