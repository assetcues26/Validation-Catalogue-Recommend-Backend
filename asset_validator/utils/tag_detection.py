"""Multi-code tag detection and asset-tag-only resolution (adapted from reference)."""

from __future__ import annotations

import logging


def normalize_detected_codes(raw_value):
    """Coerce model detected_codes output into a deduped list of {type, value} dicts."""
    if raw_value is None:
        return []

    if isinstance(raw_value, str):
        raw_value = [raw_value]

    if not isinstance(raw_value, list):
        return []

    codes = []
    seen: set[str] = set()
    for item in raw_value:
        if isinstance(item, dict):
            val = item.get("value")
            code_type = item.get("type") or "code"
            if val is not None:
                val = str(val).strip()
        elif item is not None:
            val = str(item).strip()
            code_type = "code"
        else:
            continue

        if not val:
            continue
        if val in seen:
            continue
        seen.add(val)
        codes.append({"type": str(code_type).lower(), "value": val})

    return codes


def _format_code_type_label(code_type: str) -> str:
    labels = {
        "asset_tag": "AssetTag",
        "barcode": "Barcode",
        "serial": "Serial",
        "qr": "QR",
        "printed_number": "PrintedNumber",
        "code": "Code",
    }
    return labels.get(str(code_type).lower(), str(code_type).replace("_", "").title())


def is_readable_code(value) -> bool:
    return bool(value) and str(value).strip().upper() != "UNREADABLE"


def _score_primary_asset_tag(value: str) -> int:
    """Score how likely a value is the primary client numeric asset tag (higher = better)."""
    val = str(value).strip()
    score = 0
    if val.isdigit():
        score += 100
        length = len(val)
        if 6 <= length <= 14:
            score += 20
        if val.startswith("1000"):
            score += 10
        if length < 6:
            score -= 30
    else:
        score -= 80
        upper = val.upper()
        if upper.startswith("COMP"):
            score -= 50
        if val.count("-") >= 2:
            score -= 50
    return score


def _pick_best_asset_tag(asset_tag_entries: list[dict]) -> str | None:
    if not asset_tag_entries:
        return None
    if len(asset_tag_entries) == 1:
        return asset_tag_entries[0]["value"]
    return max(
        asset_tag_entries,
        key=lambda entry: (
            _score_primary_asset_tag(entry["value"]),
            len(entry["value"]) if str(entry["value"]).strip().isdigit() else 0,
        ),
    )["value"]


def _asset_tag_entries(codes: list[dict]) -> list[dict]:
    return [
        entry
        for entry in codes
        if entry.get("type") == "asset_tag" and is_readable_code(entry.get("value"))
    ]


def normalize_code_types_for_asset_tags(
    codes: list[dict],
    user_tagnumber: Any,
    request_id: str = "",
) -> list[dict]:
    """
    Promote misclassified codes to asset_tag when they match user TagNumber or
    look like a client numeric asset tag (e.g. 1000000902).
    """
    if not codes:
        return codes

    user_str = str(user_tagnumber).strip() if user_tagnumber else ""
    for entry in codes:
        if entry.get("type") == "asset_tag":
            continue
        val = entry.get("value")
        if not is_readable_code(val):
            continue
        val_str = str(val).strip()
        promote = False
        reason = ""
        if user_str and val_str == user_str:
            promote = True
            reason = "matches input TagNumber"
        elif user_str and (
            val_str.isdigit()
            and val_str.startswith("1000")
            and 6 <= len(val_str) <= 20
        ):
            promote = True
            reason = "numeric client asset-tag pattern"

        if promote:
            logging.info(
                f"[{request_id}] Promoted {entry.get('type')} '{val_str}' "
                f"to asset_tag ({reason})"
            )
            entry["type"] = "asset_tag"

    return codes


def resolve_asset_tag_from_codes(
    detected_codes: list[dict],
    user_tagnumber,
    request_id: str = "",
) -> str | None:
    """
    Pick detectedtagnumber from asset_tag entries only — never serial/barcode/QR.
    Prefer exact match with user input; otherwise return best-scored asset_tag.
    """
    asset_tags = _asset_tag_entries(detected_codes)

    if not asset_tags:
        if detected_codes:
            logging.info(f"[{request_id}] No readable asset_tag among {len(detected_codes)} code(s)")
            return "UNREADABLE"
        logging.info(f"[{request_id}] No detected codes found")
        return None

    if user_tagnumber:
        user_str = str(user_tagnumber).strip()
        for entry in asset_tags:
            if entry["value"] == user_str:
                logging.info(
                    f"[{request_id}] Asset tag resolution: matched input tag '{user_str}' "
                    f"from {len(asset_tags)} asset_tag(s)"
                )
                return entry["value"]

    picked = _pick_best_asset_tag(asset_tags)
    logging.info(
        f"[{request_id}] Asset tag resolution: no input match; "
        f"using scored asset_tag '{picked}' from {len(asset_tags)} candidate(s)"
    )
    return picked


def format_detected_codes_summary(codes: list[dict]) -> str:
    parts = []
    for item in codes:
        if isinstance(item, dict):
            label = _format_code_type_label(item.get("type", "code"))
            parts.append(f"{label}={item.get('value', '')}")
        else:
            parts.append(f"Code={item}")
    return ", ".join(parts)


def enrich_tag_detection_reasoning(
    reasoning,
    codes: list[dict],
    user_tagnumber,
    resolved,
) -> str | None:
    """Prepend multi-code summary to tag_detection_reasoning if the model omitted it."""
    if not codes:
        return reasoning

    summary = format_detected_codes_summary(codes)
    readable_count = sum(
        1 for item in codes if is_readable_code(item.get("value") if isinstance(item, dict) else item)
    )
    if readable_count == 0:
        readable_count = len(codes)

    prefix = f"Found {readable_count} code(s): {summary}."
    if reasoning and "Found " in reasoning and " code" in reasoning:
        return reasoning

    if user_tagnumber and resolved and str(resolved).strip() == str(user_tagnumber).strip():
        prefix += " Matched input tag."
    elif user_tagnumber and resolved:
        prefix += " No input match; using primary asset tag from image."

    if reasoning:
        return f"{prefix} {reasoning}"
    return prefix
