"""Stage C — Pinecone / catalogue recommend.

Reuses Additional Asset FastAPI Pinecone path. When namedescriptionmatch == Y,
builds identity-assisted search text and skips vision type filter.
"""

from __future__ import annotations

import json
import time
from typing import Any

from additional_asset.core import config
from additional_asset.core.models import AdditionalAssetRequest
from additional_asset.prompts.builders import build_pinecone_selection_prompt
from additional_asset.services.gemini_client import call_llm_with_retry
from additional_asset.services.pinecone_service import (
    candidate_for_pick,
    ids_from_candidate,
    makemodel_is_blank,
    query_pinecone_masterdata,
)
from additional_asset.services.pipeline import _apply_masterdata_join
from additional_asset.utils.helpers import truncate_for_phase2
from additional_asset.utils.logging_utils import StepLogger
from additional_asset.utils.vision_type_filter import (
    filter_pinecone_candidates_by_vision,
    join_vision_texts,
)


def build_pinecone_search_text(
    *,
    response: dict[str, Any],
    user_asset_name: str,
    user_description: str,
) -> tuple[str, str, bool]:
    """Return (search_text, pinecone_mode, use_user_identity).

    Y-path identity fill-in (frozen contract):
      - description only → user Description + LLM detectedAsset + truncated analysis
      - asset name only  → user AssetName + LLM detected_description + truncated analysis
      - both             → user AssetName + user Description + truncated analysis
    N / skipped → AA vision path: detectedAsset + detected_description + truncated analysis
    """
    analysis = truncate_for_phase2(
        str(response.get("imageAnalysis") or ""),
        config.phase2_image_analysis_max_chars(),
    )
    name_match = str(response.get("namedescriptionmatch") or "").upper()
    use_identity = name_match == "Y"

    if use_identity:
        has_name = bool(user_asset_name.strip())
        has_desc = bool(user_description.strip())
        if has_name and has_desc:
            parts = [user_asset_name.strip(), user_description.strip(), analysis]
            mode = "identity_both_user"
        elif has_desc and not has_name:
            parts = [
                user_description.strip(),
                str(response.get("detectedAsset") or "").strip(),
                analysis,
            ]
            mode = "identity_user_desc_llm_name"
        else:
            # name only (or Y with empty both — defensive)
            parts = [
                user_asset_name.strip(),
                str(response.get("detected_description") or "").strip(),
                analysis,
            ]
            mode = "identity_user_name_llm_desc"
        search_text = " ".join(p for p in parts if p)
        return search_text, mode, True

    # AA default (N or matching skipped)
    parts = [
        str(response.get("detectedAsset") or "").strip(),
        str(response.get("detected_description") or "").strip(),
        analysis,
    ]
    search_text = " ".join(p for p in parts if p)
    mode = "aa_vision" if name_match == "N" else "aa_vision_skip_match"
    return search_text, mode, False


def run_catalogue_phase(
    *,
    response: dict[str, Any],
    customerid: int,
    categoryid: int | None,
    assetclassid: int | None,
    user_asset_name: str,
    user_description: str,
    text_model: Any,
    vision_model: Any,
    request_id: str,
    logger: StepLogger,
    timings: dict[str, float],
) -> None:
    """Additional Asset Pinecone query → selection → masterdata join."""
    if not config.include_pinecone_lookup():
        response["pinecone_mode"] = "disabled"
        logger.step("pinecone_query", skipped=True, reason="INCLUDE_PINECONE_LOOKUP=false")
        return

    search_text, mode, use_user_identity = build_pinecone_search_text(
        response=response,
        user_asset_name=user_asset_name,
        user_description=user_description,
    )
    response["pinecone_mode"] = mode

    aa_req = AdditionalAssetRequest(
        asset_image_bytes=b"",
        barcode_image_bytes=b"",
        customerid=customerid,
        categoryid=categoryid,
        assetclassid=assetclassid,
    )

    logger.step(
        "pinecone_query_prep",
        search_text_preview=search_text[:300],
        search_text_len=len(search_text),
        pinecone_mode=mode,
        use_user_identity=use_user_identity,
        namedescriptionmatch=response.get("namedescriptionmatch"),
        identity_inputs={
            "user_asset_name": bool(str(user_asset_name or "").strip()),
            "user_description": bool(str(user_description or "").strip()),
            "detectedAsset": bool(str(response.get("detectedAsset") or "").strip()),
            "detected_description": bool(str(response.get("detected_description") or "").strip()),
        },
        filters={
            "customerid": customerid,
            "categoryid": categoryid,
            "assetclassid": assetclassid,
        },
        top_k=config.PINECONE_TOP_K,
    )

    candidates, embed_ms, query_ms = query_pinecone_masterdata(
        search_text,
        customerid,
        categoryid,
        asset_class_id=assetclassid,
        request_id=request_id,
    )
    pinecone_total_ms = round(embed_ms + query_ms, 2)
    timings["pinecone_embed"] = round(embed_ms, 2)
    timings["pinecone_query"] = round(query_ms, 2)
    timings["pinecone_lookup"] = pinecone_total_ms

    analysis = truncate_for_phase2(
        str(response.get("imageAnalysis") or ""),
        config.phase2_image_analysis_max_chars(),
    )
    detected_asset = str(response.get("detectedAsset") or "").strip()
    detected_description = str(response.get("detected_description") or "").strip()
    pre_filter_hits = len(candidates)

    if not use_user_identity:
        vision_text = join_vision_texts(detected_asset, detected_description, analysis)
        candidates = filter_pinecone_candidates_by_vision(
            candidates,
            vision_text,
            label_text=detected_asset or None,
        )
    else:
        logger.step("vision_type_filter", skipped=True, reason="use_user_identity")

    cand_summary = [
        {
            "category": c.get("category") or c.get("categoryname"),
            "subcategory": c.get("subcategory"),
            "makemodel": c.get("makemodel"),
            "model": c.get("model"),
            "score": round(float(c.get("_score") or 0), 4),
        }
        for c in candidates
    ]
    log_detail: dict[str, Any] = {
        "latency_ms": pinecone_total_ms,
        "embed_ms": round(embed_ms, 2),
        "query_ms": round(query_ms, 2),
        "hits": pre_filter_hits,
        "hits_after_type_filter": len(candidates),
        "pinecone_mode": mode,
    }
    if config.log_candidates():
        log_detail["candidates"] = cand_summary
    logger.step("pinecone_query", **log_detail)

    if not candidates:
        logger.step("pinecone_select", result="null", reason="no_hits")
        return

    t0 = time.perf_counter()
    selection_prompt = build_pinecone_selection_prompt(
        detected_asset=detected_asset,
        detected_description=detected_description,
        candidate_summary=json.dumps(cand_summary, separators=(",", ":")),
    )
    selection = call_llm_with_retry(
        text_model or vision_model,
        selection_prompt,
        images=None,
        max_tokens=350,
        response_mime_type="application/json",
        usage_label="pinecone_selection",
    )
    sel_ms = round((time.perf_counter() - t0) * 1000, 2)
    timings["pinecone_select"] = sel_ms

    if selection.get("error"):
        logger.step("pinecone_select", latency_ms=sel_ms, error=selection.get("error"))
        return

    response["recommendation_confidence"] = selection.get("recommendation_confidence")
    response["selection_reasoning"] = selection.get("selection_reasoning")
    response["recommendedcategory"] = selection.get("recommendedcategory")
    response["recommendedsubcategory"] = selection.get("recommendedsubcategory")
    response["recommendedmakemodel"] = selection.get("recommendedmakemodel")
    response["recommendedmodel"] = selection.get("recommendedmodel")

    blank_mm = makemodel_is_blank(selection.get("recommendedmakemodel"))
    if not str(selection.get("recommendedsubcategory") or "").strip():
        pick = None
        ids = ids_from_candidate(None)
    else:
        pick = candidate_for_pick(candidates, selection)
        if pick is None and not blank_mm:
            pick = candidate_for_pick(
                candidates,
                {**selection, "recommendedmakemodel": None},
            )
            blank_mm = True
        ids = ids_from_candidate(pick, clear_makemodel=blank_mm)

    _apply_masterdata_join(response, aa_req, ids)
    logger.step(
        "pinecone_select",
        latency_ms=sel_ms,
        selection={
            "recommendedcategory": selection.get("recommendedcategory"),
            "recommendedsubcategory": selection.get("recommendedsubcategory"),
            "recommendedmakemodel": selection.get("recommendedmakemodel"),
        },
        joined_ids=ids,
        pick_found=bool(pick),
        makemodel_cleared=blank_mm,
        pinecone_mode=mode,
    )
