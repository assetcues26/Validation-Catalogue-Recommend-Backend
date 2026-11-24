"""Validation and Catalogue Recommend — end-to-end orchestrator.

Stage A  Generation          → Additional Asset FastAPI (through unified Phase 1)
Stage B  Matching (optional) → validation-ai-backend name + tag stacks
Stage C  Catalogue / Pinecone → Additional Asset FastAPI (+ identity when name match Y)
"""

from __future__ import annotations

import time
from typing import Any

from additional_asset.utils.logging_utils import StepLogger
from additional_asset.utils import token_usage as aa_token_usage
from asset_validator.utils import token_usage as val_token_usage
from validation_catalogue.core.models import ValidationCatalogueRequest, empty_response
from validation_catalogue.services.catalogue import run_catalogue_phase
from validation_catalogue.services.generation import run_generation_phase
from validation_catalogue.services.matching import run_matching_phase


def _merge_phase_buckets(
    left: dict[str, dict[str, int]], right: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    merged: dict[str, dict[str, int]] = {k: dict(v) for k, v in left.items()}
    for phase, bucket in right.items():
        dest = merged.setdefault(
            phase,
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "calls": 0,
                "cached_tokens": 0,
            },
        )
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "calls", "cached_tokens"):
            dest[key] = int(dest.get(key) or 0) + int(bucket.get(key) or 0)
    return merged


def _llm_usage_payload() -> dict[str, Any] | None:
    """Merge Additional Asset + validation token trackers into one llm_usage block."""
    aa = aa_token_usage.get_request_usage()
    val = val_token_usage.get_request_usage()
    if not aa and not val:
        return None

    aa_totals = aa.totals() if aa else None
    val_totals = val.totals() if val else None
    aa_phases = aa.by_phase() if aa else {}
    val_phases = val.by_phase() if val else {}

    prompt = int((aa_totals or {}).get("prompt_tokens") or 0) + int(
        (val_totals or {}).get("prompt_tokens") or 0
    )
    cached = int((aa_totals or {}).get("cached_tokens") or 0) + int(
        (val_totals or {}).get("cached_tokens") or 0
    )
    completion = int((aa_totals or {}).get("completion_tokens") or 0) + int(
        (val_totals or {}).get("completion_tokens") or 0
    )
    total = int((aa_totals or {}).get("total_tokens") or 0) + int(
        (val_totals or {}).get("total_tokens") or 0
    )
    calls = int((aa_totals or {}).get("llm_calls") or 0) + int(
        (val_totals or {}).get("llm_calls") or 0
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "llm_calls": calls,
        "cached_tokens": cached,
        "uncached_prompt_tokens": max(prompt - cached, 0),
        "cache_hit_percent": round(cached / prompt * 100, 1) if prompt else 0.0,
        "by_phase": _merge_phase_buckets(aa_phases, val_phases),
    }


def run_validation_catalogue(
    req: ValidationCatalogueRequest, request_id: str
) -> dict[str, Any]:
    logger = StepLogger(request_id, logger_name="validation_catalogue")
    # Stage A/C (AA) and Stage B (validation) use separate thread-local collectors.
    aa_token_usage.start_request_usage()
    val_token_usage.start_request_usage()
    t_all = time.perf_counter()
    timings: dict[str, float] = {}

    user_name = req.user_asset_name()
    user_desc = req.user_description()
    user_tag = req.user_tag_number()

    response = empty_response(
        request_id,
        customerid=req.customerid,
        categoryid=req.categoryid,
        assetclassid=req.assetclassid,
        user_asset_name=user_name or None,
        user_description=user_desc or None,
        user_tag_number=user_tag or None,
    )
    response["timings_ms"] = timings

    try:
        return _run_inner(
            req,
            request_id,
            logger,
            timings,
            t_all,
            response,
            user_name,
            user_desc,
            user_tag,
        )
    finally:
        response["timings_ms"] = timings
        usage = _llm_usage_payload()
        response["llm_usage"] = usage
        if usage:
            logger.step("llm_usage", **usage)
        logger.step(
            "debug_log_file",
            path=logger.debug_file_path,
            steps_recorded=len(logger.steps),
        )
        logger.close()
        aa_token_usage.clear_request_usage()
        val_token_usage.clear_request_usage()


def _run_inner(
    req: ValidationCatalogueRequest,
    request_id: str,
    logger: StepLogger,
    timings: dict[str, float],
    t_all: float,
    response: dict[str, Any],
    user_name: str,
    user_desc: str,
    user_tag: str,
) -> dict[str, Any]:
    run_name = req.has_name_or_description()
    run_tag = req.has_tag_number()

    logger.step(
        "entry",
        source=req.source,
        customerid=req.customerid,
        categoryid=req.categoryid,
        assetclassid=req.assetclassid,
        asset_bytes=len(req.asset_image_bytes or b""),
        barcode_bytes=len(req.barcode_image_bytes or b""),
        has_user_asset_name=bool(user_name),
        has_user_description=bool(user_desc),
        has_user_tag=bool(user_tag),
        user_asset_name_preview=(user_name[:80] if user_name else None),
        user_description_preview=(user_desc[:120] if user_desc else None),
        user_tag_preview=(user_tag[:64] if user_tag else None),
        run_name_match=run_name,
        run_tag_match=run_tag,
        debug_log_file=logger.debug_file_path,
    )

    # --- Stage A: Generation (Additional Asset) ---
    logger.step("stage_a_generation_start")
    gen = run_generation_phase(
        asset_image_bytes=req.asset_image_bytes,
        barcode_image_bytes=req.barcode_image_bytes,
        request_id=request_id,
        logger=logger,
        timings=timings,
        response=response,
    )
    if not gen.ok:
        response["error"] = gen.error
        timings["total"] = round((time.perf_counter() - t_all) * 1000, 2)
        logger.step(
            "stage_a_generation_end",
            ok=False,
            error=gen.error,
            total_ms=timings["total"],
        )
        logger.step("exit", total_ms=timings["total"], error=gen.error, early_exit=True)
        return response
    logger.step(
        "stage_a_generation_end",
        ok=True,
        detectedAsset=response.get("detectedAsset"),
        detected_description=response.get("detected_description"),
        decoded_count=len(gen.decoded or []),
        tagnumbersource=response.get("tagnumbersource"),
    )

    # --- Stage B: Matching (validation) — optional ---
    logger.step(
        "stage_b_matching_start",
        run_name=run_name,
        run_tag=run_tag,
    )
    run_matching_phase(
        response=response,
        phase1=gen.phase1,
        decoded=gen.decoded,
        user_asset_name=user_name,
        user_description=user_desc,
        user_tag_number=user_tag,
        run_name=run_name,
        run_tag=run_tag,
        asset_b64=gen.asset_b64,
        barcode_b64=gen.barcode_b64,
        asset_orig_b64=gen.asset_orig_b64,
        barcode_orig_b64=gen.barcode_orig_b64,
        customerid=req.customerid,
        categoryid=req.categoryid,
        assetclassid=req.assetclassid,
        request_id=request_id,
        logger=logger,
        timings=timings,
    )
    logger.step(
        "stage_b_matching_end",
        matching_ran=response.get("matching_ran"),
        namedescriptionmatch=response.get("namedescriptionmatch"),
        namedescriptionmatchpercent=response.get("namedescriptionmatchpercent"),
        detectedtagnumbermatch=response.get("detectedtagnumbermatch"),
        detectedtagnumbermatchpercent=response.get("detectedtagnumbermatchpercent"),
    )

    # Echo IDs (user-provided always win when set)
    response["customerid"] = req.customerid
    if req.categoryid is not None:
        response["categoryid"] = req.categoryid
    if req.assetclassid is not None:
        response["assetclassid"] = req.assetclassid

    # --- Stage C: Catalogue / Pinecone (Additional Asset + identity gate) ---
    logger.step(
        "stage_c_catalogue_start",
        namedescriptionmatch=response.get("namedescriptionmatch"),
        use_user_identity=str(response.get("namedescriptionmatch") or "").upper() == "Y",
    )
    run_catalogue_phase(
        response=response,
        customerid=req.customerid,
        categoryid=req.categoryid,
        assetclassid=req.assetclassid,
        user_asset_name=user_name,
        user_description=user_desc,
        text_model=gen.text_model,
        vision_model=gen.vision_model,
        request_id=request_id,
        logger=logger,
        timings=timings,
    )
    logger.step(
        "stage_c_catalogue_end",
        pinecone_mode=response.get("pinecone_mode"),
        categoryname=response.get("categoryname"),
        subcategoryname=response.get("subcategoryname"),
        makemodel=response.get("makemodel"),
        recommendedcategory=response.get("recommendedcategory"),
        recommendedsubcategory=response.get("recommendedsubcategory"),
        recommendedmakemodel=response.get("recommendedmakemodel"),
    )

    timings["total"] = round((time.perf_counter() - t_all) * 1000, 2)
    logger.step(
        "exit",
        total_ms=timings["total"],
        timings_ms=timings,
        namedescriptionmatch=response.get("namedescriptionmatch"),
        detectedtagnumbermatch=response.get("detectedtagnumbermatch"),
        pinecone_mode=response.get("pinecone_mode"),
        detectedAsset=response.get("detectedAsset"),
        error=response.get("error"),
        debug_log_file=logger.debug_file_path,
    )
    return response
