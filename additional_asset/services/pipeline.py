"""Additional Asset end-to-end pipeline."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from additional_asset.core import config
from additional_asset.core.models import AdditionalAssetRequest, empty_response
from additional_asset.prompts.builders import (
    build_pinecone_selection_prompt,
    build_unified_phase1_content_route_prompt,
    build_unified_phase1_prompt,
    compute_unified_max_tokens,
)
from additional_asset.services.barcode_decode import decode_codes_from_image
from additional_asset.services.condition_service import normalize_condition
from additional_asset.services.gemini_client import call_llm_with_retry, get_shared_models
from additional_asset.services.image_service import bytes_to_b64, prepare_image_bytes
from additional_asset.services.pinecone_service import (
    candidate_for_pick,
    ids_from_candidate,
    makemodel_is_blank,
    query_pinecone_masterdata,
)
from additional_asset.services.tag_merge import merge_tag_fields, order_decoded_prefer_tag_like
from additional_asset.services.view_classifier import ViewClassification, pick_asset_and_tag_slots
from additional_asset.utils.helpers import truncate_for_phase2
from additional_asset.utils.logging_utils import StepLogger
from additional_asset.utils.token_usage import (
    bind_request_usage,
    clear_request_usage,
    get_request_usage,
    start_request_usage,
)
from additional_asset.utils.vision_type_filter import (
    filter_pinecone_candidates_by_vision,
    join_vision_texts,
)


def _llm_usage_payload() -> dict[str, Any] | None:
    token_usage = get_request_usage()
    if not token_usage:
        return None
    totals = token_usage.totals()
    return {
        "prompt_tokens": totals["prompt_tokens"],
        "completion_tokens": totals["completion_tokens"],
        "total_tokens": totals["total_tokens"],
        "llm_calls": totals["llm_calls"],
        "cached_tokens": totals["cached_tokens"],
        "uncached_prompt_tokens": totals["uncached_prompt_tokens"],
        "cache_hit_percent": totals["cache_hit_percent"],
        "by_phase": token_usage.by_phase(),
    }


def _apply_masterdata_join(
    response: dict[str, Any],
    req: AdditionalAssetRequest,
    ids: dict[str, Any] | None,
) -> None:
    """Merge Pinecone row ids/names with optional user-provided CategoryId / AssetClassId.

    - User-provided IDs are always echoed.
    - Missing optional IDs (and all names / sub / makemodel) come from the best-fit row.
    - When there is no pick (`ids` is None or empty-null set from ids_from_candidate(None)),
      recommended fields stay null; only user-echoed IDs remain.
    """
    ids = ids or ids_from_candidate(None)

    # Names + sub/makemodel always follow the joined row (null when no pick).
    response["assetclassname"] = ids.get("assetclassname")
    response["categoryname"] = ids.get("categoryname")
    response["subcategoryid"] = ids.get("subcategoryid")
    response["subcategoryname"] = ids.get("subcategoryname")
    response["makemodelid"] = ids.get("makemodelid")
    response["makemodel"] = ids.get("makemodel")

    # IDs: echo request when provided; otherwise take from best-fit row.
    if req.assetclassid is not None:
        response["assetclassid"] = req.assetclassid
    else:
        response["assetclassid"] = ids.get("assetclassid")

    if req.categoryid is not None:
        response["categoryid"] = req.categoryid
    else:
        response["categoryid"] = ids.get("categoryid")


def run_additional_asset(req: AdditionalAssetRequest, request_id: str) -> dict[str, Any]:
    logger = StepLogger(request_id)
    start_request_usage()
    t_all = time.perf_counter()
    timings: dict[str, float] = {}
    response = empty_response(
        request_id,
        customerid=req.customerid,
        categoryid=req.categoryid,
        assetclassid=req.assetclassid,
    )
    response["timings_ms"] = timings
    try:
        return _run_additional_asset_inner(req, request_id, logger, timings, t_all, response)
    finally:
        response["timings_ms"] = timings
        usage = _llm_usage_payload()
        response["llm_usage"] = usage
        if usage:
            logger.step("llm_usage", **usage)
        logger.close()
        clear_request_usage()


def _run_additional_asset_inner(
    req: AdditionalAssetRequest,
    request_id: str,
    logger: StepLogger,
    timings: dict[str, float],
    t_all: float,
    response: dict[str, Any],
) -> dict[str, Any]:
    logger.step(
        "entry",
        source=req.source,
        customerid=req.customerid,
        categoryid=req.categoryid,
        assetclassid=req.assetclassid,
        asset_bytes=len(req.asset_image_bytes),
        barcode_bytes=len(req.barcode_image_bytes),
        pixel_gate=config.blank_image_gate_enabled(),
        pinecone=config.include_pinecone_lookup(),
        phase1_content_route=config.phase1_content_route_enabled(),
        blind_view=config.blind_view_enabled(),
    )

    vision_model, text_model = get_shared_models()
    # AA uses same Flash Lite id for both fields by configuration.
    model = vision_model or text_model

    # --- Image prepare ---
    t0 = time.perf_counter()
    try:
        asset_jpeg, asset_meta = prepare_image_bytes(
            req.asset_image_bytes,
            config.asset_image_max_edge(),
            config.image_jpeg_quality(),
            request_id,
            "asset",
        )
        barcode_jpeg, barcode_meta = prepare_image_bytes(
            req.barcode_image_bytes,
            config.barcode_image_max_edge(),
            config.image_jpeg_quality(),
            request_id,
            "barcode",
        )
    except Exception as exc:
        logger.step("preprocess", error=str(exc))
        response["error"] = f"IMAGE_PREPROCESS_FAILED: {exc}"
        timings["total"] = round((time.perf_counter() - t_all) * 1000, 2)
        return response
    preprocess_ms = round((time.perf_counter() - t0) * 1000, 2)
    timings["preprocess"] = preprocess_ms
    logger.step(
        "preprocess",
        latency_ms=preprocess_ms,
        asset=asset_meta,
        barcode=barcode_meta,
    )

    asset_b64 = bytes_to_b64(asset_jpeg)
    barcode_b64 = bytes_to_b64(barcode_jpeg)
    # Keep originals for zxing (better decode range)
    asset_orig_b64 = bytes_to_b64(req.asset_image_bytes)
    barcode_orig_b64 = bytes_to_b64(req.barcode_image_bytes)

    content_route = config.phase1_content_route_enabled()

    if content_route:
        # --- Content-route: skip blind view; zxing (barcode only) ∥ Phase 1 ---
        logger.step(
            "blind_view",
            skipped=True,
            reason="PHASE1_CONTENT_ROUTE_ENABLED",
        )
        timings["blind_view"] = 0.0
        if asset_meta.get("unusable"):
            logger.step("pixel_gate", asset_unusable=asset_meta.get("unusable"))
        logger.step(
            "route",
            mode="content_route",
            asset_slot="upload_asset",
            tag_slot="upload_barcode",
            swapped=False,
        )

        prompt = build_unified_phase1_content_route_prompt()
        images = [
            {"mime_type": "image/jpeg", "data": asset_b64},
            {"mime_type": "image/jpeg", "data": barcode_b64},
        ]
        usage = get_request_usage()

        def _zxing_barcode_only() -> tuple[list[dict[str, Any]], float]:
            t_z = time.perf_counter()
            codes = order_decoded_prefer_tag_like(
                decode_codes_from_image(
                    barcode_orig_b64,
                    user_tagnumber=None,
                    request_id=request_id,
                    label="barcode",
                )
            )
            return codes, round((time.perf_counter() - t_z) * 1000, 2)

        def _phase1_content_route() -> tuple[dict[str, Any], float]:
            bind_request_usage(usage)
            t_p = time.perf_counter()
            result = call_llm_with_retry(
                model,
                prompt,
                images=images,
                max_tokens=compute_unified_max_tokens(),
                response_mime_type="application/json",
                usage_label="unified_phase1",
            )
            return result, round((time.perf_counter() - t_p) * 1000, 2)

        t_wall = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_zxing = pool.submit(_zxing_barcode_only)
            fut_phase1 = pool.submit(_phase1_content_route)
            decoded, zxing_ms = fut_zxing.result()
            phase1, phase1_ms = fut_phase1.result()
        wall_ms = round((time.perf_counter() - t_wall) * 1000, 2)

        timings["zxing"] = zxing_ms
        timings["unified_phase1"] = phase1_ms
        timings["zxing_phase1_wall"] = wall_ms
        logger.step(
            "zxing",
            latency_ms=zxing_ms,
            mode="barcode_slot_only",
            parallel_with="unified_phase1",
            wall_ms=wall_ms,
            codes=[
                {
                    "text": c.get("text") or c.get("value"),
                    "format": c.get("format"),
                    "image": c.get("image"),
                    "type": c.get("type"),
                }
                for c in (decoded or [])
            ],
        )
    else:
        # --- Legacy path ---
        views: dict[str, str] = {}
        if config.blind_view_enabled():
            t0 = time.perf_counter()
            blind = ViewClassification(
                model,
                {"asset": asset_b64, "barcode": barcode_b64},
                request_id=request_id,
            )
            views = blind.result(timeout=config.gemini_call_timeout_sec())
            asset_slot, tag_slot, swapped = pick_asset_and_tag_slots(views)
            blind_ms = round((time.perf_counter() - t0) * 1000, 2)
            timings["blind_view"] = blind_ms
            logger.step(
                "blind_view",
                latency_ms=blind_ms,
                views=views,
                asset_slot=asset_slot,
                tag_slot=tag_slot,
                swapped=swapped,
            )
        else:
            # Blind view off: trust upload slot order (no Gemini classify / swap).
            asset_slot, tag_slot, swapped = "asset", "barcode", False
            timings["blind_view"] = 0.0
            logger.step(
                "blind_view",
                skipped=True,
                reason="BLIND_VIEW_ENABLED=false",
                asset_slot=asset_slot,
                tag_slot=tag_slot,
                swapped=swapped,
            )

        slot_b64 = {"asset": asset_b64, "barcode": barcode_b64}
        slot_orig = {"asset": asset_orig_b64, "barcode": barcode_orig_b64}

        routed_asset_b64 = slot_b64.get(asset_slot) or asset_b64
        routed_tag_b64 = slot_b64.get(tag_slot) or barcode_b64
        routed_tag_orig = slot_orig.get(tag_slot) or barcode_orig_b64

        routed_asset_meta = asset_meta if asset_slot == "asset" else barcode_meta
        if routed_asset_meta.get("unusable"):
            logger.step("pixel_gate", asset_unusable=routed_asset_meta.get("unusable"))

        logger.step(
            "route",
            mode="legacy_slot_order" if not views else "legacy_blind_view",
            asset_slot=asset_slot,
            tag_slot=tag_slot,
            swapped=swapped,
        )

        t0 = time.perf_counter()
        decoded = decode_codes_from_image(
            routed_tag_orig, user_tagnumber=None, request_id=request_id, label="tag"
        )
        if not decoded:
            decoded = decode_codes_from_image(
                slot_orig.get(asset_slot) or asset_orig_b64,
                user_tagnumber=None,
                request_id=request_id,
                label="asset_slot_backup",
            )
        zxing_ms = round((time.perf_counter() - t0) * 1000, 2)
        timings["zxing"] = zxing_ms
        logger.step(
            "zxing",
            latency_ms=zxing_ms,
            codes=[
                {"text": c.get("text") or c.get("value"), "format": c.get("format")}
                for c in (decoded or [])
            ],
        )

        t0 = time.perf_counter()
        prompt = build_unified_phase1_prompt()
        if views:
            prompt += (
                "\n\nSLOT NOTE AFTER BLIND VIEW: "
                f"Image 1 is the routed asset photo (upload slot '{asset_slot}', view={views.get(asset_slot)}). "
                f"Image 2 is the routed barcode/tag photo (upload slot '{tag_slot}', view={views.get(tag_slot)}). "
                "Prefer Image 1 for asset analysis/condition and Image 2 for tag/barcode fields.\n"
            )
        else:
            # Blind view skipped — same Phase 1 prompt MD; slot note uses upload order only.
            prompt += (
                "\n\nSLOT NOTE (upload order; blind view skipped): "
                "Image 1 is the asset upload slot. Image 2 is the barcode/tag upload slot. "
                "Prefer Image 1 for asset analysis/condition and Image 2 for tag/barcode fields.\n"
            )
        images = [
            {"mime_type": "image/jpeg", "data": routed_asset_b64},
            {"mime_type": "image/jpeg", "data": routed_tag_b64},
        ]
        phase1 = call_llm_with_retry(
            model,
            prompt,
            images=images,
            max_tokens=compute_unified_max_tokens(),
            response_mime_type="application/json",
            usage_label="unified_phase1",
        )
        phase1_ms = round((time.perf_counter() - t0) * 1000, 2)
        timings["unified_phase1"] = phase1_ms
    if phase1.get("error"):
        logger.step("unified_phase1", latency_ms=phase1_ms, error=phase1.get("error"))
        response["error"] = f"PHASE1_FAILED: {phase1.get('error')}"
        timings["total"] = round((time.perf_counter() - t_all) * 1000, 2)
        # Still return echo ids
        return response

    response["imageAnalysis"] = phase1.get("imageAnalysis")
    response["detectedAsset"] = phase1.get("detectedAsset")
    response["detected_description"] = phase1.get("detected_description")
    response["condition"] = normalize_condition(
        phase1.get("condition") if isinstance(phase1.get("condition"), dict) else None,
        str(phase1.get("detectedAsset") or ""),
    )

    tag_fields = merge_tag_fields(phase1, decoded)
    response.update(tag_fields)

    logger.step(
        "unified_phase1",
        latency_ms=phase1_ms,
        mode=(
            "content_route"
            if content_route
            else ("legacy_slot_order" if not config.blind_view_enabled() else "legacy_blind_view")
        ),
        detectedAsset=response.get("detectedAsset"),
        detected_description=response.get("detected_description"),
        imageAnalysis_preview=truncate_for_phase2(str(response.get("imageAnalysis") or ""), 200),
        condition_grade=(response.get("condition") or {}).get("grade")
        if isinstance(response.get("condition"), dict)
        else None,
        tag=tag_fields,
    )

    # --- Pinecone ---
    if not config.include_pinecone_lookup():
        logger.step("pinecone_query", skipped=True, reason="INCLUDE_PINECONE_LOOKUP=false")
    else:
        analysis = truncate_for_phase2(
            str(response.get("imageAnalysis") or ""),
            config.phase2_image_analysis_max_chars(),
        )
        search_parts = [
            str(response.get("detectedAsset") or "").strip(),
            str(response.get("detected_description") or "").strip(),
            analysis,
        ]
        search_text = " ".join(p for p in search_parts if p)
        logger.step(
            "pinecone_query_prep",
            search_text_preview=search_text[:300],
            search_text_len=len(search_text),
            filters={
                "customerid": req.customerid,
                "categoryid": req.categoryid,
                "assetclassid": req.assetclassid,
            },
            top_k=config.PINECONE_TOP_K,
        )
        candidates, embed_ms, query_ms = query_pinecone_masterdata(
            search_text,
            req.customerid,
            req.categoryid,
            asset_class_id=req.assetclassid,
            request_id=request_id,
        )
        pinecone_total_ms = round(embed_ms + query_ms, 2)
        timings["pinecone_embed"] = round(embed_ms, 2)
        timings["pinecone_query"] = round(query_ms, 2)
        timings["pinecone_lookup"] = pinecone_total_ms

        detected_asset = str(response.get("detectedAsset") or "").strip()
        detected_description = str(response.get("detected_description") or "").strip()
        vision_text = join_vision_texts(detected_asset, detected_description, analysis)
        pre_filter_hits = len(candidates)
        candidates = filter_pinecone_candidates_by_vision(
            candidates,
            vision_text,
            label_text=detected_asset or None,
        )
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
        }
        if config.log_candidates():
            log_detail["candidates"] = cand_summary
        logger.step("pinecone_query", **log_detail)

        if not candidates:
            logger.step("pinecone_select", result="null", reason="no_hits")
        else:
            t0 = time.perf_counter()
            selection_prompt = build_pinecone_selection_prompt(
                detected_asset=detected_asset,
                detected_description=detected_description,
                candidate_summary=json.dumps(cand_summary, separators=(",", ":")),
            )
            selection = call_llm_with_retry(
                text_model or model,
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
            else:
                blank_mm = makemodel_is_blank(selection.get("recommendedmakemodel"))
                # Hierarchy: null subcategory → null both; blank/unjoinable makemodel → keep sub
                if not str(selection.get("recommendedsubcategory") or "").strip():
                    pick = None
                    ids = ids_from_candidate(None)
                else:
                    pick = candidate_for_pick(candidates, selection)
                    if pick is None and not blank_mm:
                        # Claimed makemodel not in filtered list — keep subcategory, drop mm
                        pick = candidate_for_pick(
                            candidates,
                            {**selection, "recommendedmakemodel": None},
                        )
                        blank_mm = True
                    ids = ids_from_candidate(pick, clear_makemodel=blank_mm)
                _apply_masterdata_join(response, req, ids)
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
                )

    # Always echo required customer; optional IDs already set by join or empty_response
    response["customerid"] = req.customerid
    if req.categoryid is not None:
        response["categoryid"] = req.categoryid
    if req.assetclassid is not None:
        response["assetclassid"] = req.assetclassid
    response["llm_provider"] = "gemini"

    total_ms = round((time.perf_counter() - t_all) * 1000, 2)
    timings["total"] = total_ms
    logger.step(
        "exit",
        latency_ms=total_ms,
        timings_ms=timings,
        fields_present={
            k: response.get(k) is not None
            for k in (
                "imageAnalysis",
                "detectedAsset",
                "detected_description",
                "condition",
                "detectedtagnumber",
                "decodedtagnumber",
                "assetclassname",
                "categoryname",
                "subcategoryname",
                "makemodel",
            )
        },
    )
    return response
