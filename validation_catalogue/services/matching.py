"""Stage B — Name/description match + tag match.

Reuses validation-ai-backend-preprod-updates-oct21 logic (prompts, settlement,
industrial escalation, tag compare / decode override / reread) without rewriting.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from additional_asset.utils.logging_utils import StepLogger
from asset_validator.core.config import (
    BLIND_VIEW_TIMEOUT_SEC,
    DECODED_TAG_RESCUE_ENABLED,
    effective_barcode_retry_on_unreadable,
)
from asset_validator.services.check_runner import run_name_match_phase2
from asset_validator.services.llm_provider import get_shared_models as get_validation_models
from asset_validator.services.pipeline_service import (
    _barcode_position_text,
    _decide_industrial_name,
    _make_brand_conflict_confirmer,
    _needs_industrial_vision_escalation,
    _relabel_confirmed_brand,
    _reread_photo_with_stronger_model,
    _settle_name_check,
    _tag_needs_reread,
)
from asset_validator.services.image_service import format_barcode_position
from asset_validator.services.validation_service import (
    apply_check_failure_defaults,
    apply_tag_detection_to_output,
    retry_barcode_read,
    settle_failed_tag_check_from_decode,
)
from asset_validator.utils.helpers import normalize_user_description
from asset_validator.utils.logging_utils import PhaseTimer
from asset_validator.utils.token_usage import (
    bind_request_usage as bind_val_usage,
    get_request_usage as get_val_usage,
)


def _req_body_for_validation(
    *,
    user_asset_name: str,
    user_description: str,
    user_tag_number: str,
    customerid: int | None,
    categoryid: int | None,
    assetclassid: int | None,
    asset_orig_b64: str,
    barcode_orig_b64: str,
) -> dict[str, Any]:
    """Shape expected by validation industrial / brand helpers."""
    return {
        "assetname": user_asset_name,
        "description": user_description,
        "tagnumber": user_tag_number,
        "customerid": customerid,
        "categoryid": categoryid,
        "assetclassid": assetclassid,
        "assetimage": asset_orig_b64,
        "barcodeimage": barcode_orig_b64,
    }


def run_tag_match(
    *,
    response: dict[str, Any],
    phase1: dict[str, Any],
    decoded: list[dict[str, Any]],
    user_tag_number: str,
    asset_b64: str,
    barcode_b64: str,
    asset_orig_b64: str,
    barcode_orig_b64: str,
    request_id: str,
    logger: StepLogger,
    timings: dict[str, float],
) -> None:
    """Full validation tag settlement on Stage A phase1 + AA decode (one decode)."""
    t0 = time.perf_counter()
    # Worker threads need the request's validation usage collector rebound.
    bind_val_usage(get_val_usage())
    response["tagnumber"] = user_tag_number

    vision_model, _ = get_validation_models()
    llm_out = dict(phase1 or {})
    if response.get("barcodeposition") is not None:
        response["barcodeposition"] = format_barcode_position(response.get("barcodeposition"))

    logger.step(
        "tag_match_start",
        user_tag=user_tag_number,
        decoded_count=len(decoded or []),
        decoded_preview=[
            {"value": c.get("value") or c.get("text"), "type": c.get("type"), "format": c.get("format")}
            for c in (decoded or [])[:8]
        ],
        phase1_detectedtagnumber=llm_out.get("detectedtagnumber"),
        retry_enabled=effective_barcode_retry_on_unreadable(),
    )

    reread_ran = False
    try:
        retry_output = None
        if effective_barcode_retry_on_unreadable() and (barcode_b64 or asset_b64):
            apply_tag_detection_to_output(
                response, llm_out, None, request_id, decoded_codes=decoded
            )
            needs_reread = _tag_needs_reread(response)
            logger.step(
                "tag_match_after_first_pass",
                match=response.get("detectedtagnumbermatch"),
                detected=response.get("detectedtagnumber"),
                source=response.get("tagnumbersource"),
                needs_reread=needs_reread,
            )
            if needs_reread:
                reread_ran = True
                retry_output = retry_barcode_read(
                    vision_model,
                    barcode_b64 or "",
                    request_id,
                    asset_b64=asset_b64,
                    barcode_original_b64=barcode_orig_b64,
                    asset_original_b64=asset_orig_b64,
                    registered=user_tag_number,
                )
                apply_tag_detection_to_output(
                    response,
                    llm_out,
                    retry_output,
                    request_id,
                    decoded_codes=decoded,
                )
                logger.step(
                    "tag_match_after_reread",
                    match=response.get("detectedtagnumbermatch"),
                    detected=response.get("detectedtagnumber"),
                    source=response.get("tagnumbersource"),
                    retry_keys=list((retry_output or {}).keys())[:20],
                )
        else:
            apply_tag_detection_to_output(
                response, llm_out, None, request_id, decoded_codes=decoded
            )
    except Exception as exc:
        logging.exception("[%s] tag match failed", request_id)
        prior = response.get("error")
        apply_check_failure_defaults(response, "tagnumbercheck", str(exc), request_id)
        rescued = False
        if DECODED_TAG_RESCUE_ENABLED and settle_failed_tag_check_from_decode(
            response, decoded, request_id
        ):
            response["error"] = prior
            rescued = True
        logger.step(
            "tag_match_error",
            error=str(exc),
            decoded_rescue=rescued,
            match=response.get("detectedtagnumbermatch"),
        )

    timings["tag_match"] = round((time.perf_counter() - t0) * 1000, 2)
    logger.step(
        "tag_match",
        latency_ms=timings["tag_match"],
        match=response.get("detectedtagnumbermatch"),
        percent=response.get("detectedtagnumbermatchpercent"),
        source=response.get("tagnumbersource"),
        detected=response.get("detectedtagnumber"),
        decodedtagnumber=response.get("decodedtagnumber"),
        barcodereadability=response.get("barcodereadability"),
        reread_ran=reread_ran,
        user_tag=user_tag_number,
    )


def run_name_description_match(
    *,
    response: dict[str, Any],
    phase1: dict[str, Any],
    user_asset_name: str,
    user_description: str,
    asset_b64: str,
    barcode_b64: str,
    asset_orig_b64: str,
    barcode_orig_b64: str,
    customerid: int | None,
    categoryid: int | None,
    assetclassid: int | None,
    request_id: str,
    logger: StepLogger,
    timings: dict[str, float],
) -> None:
    """Full validation name Phase 1.5 + _settle_name_check + industrial escalation."""
    t0 = time.perf_counter()
    # Worker threads need the request's validation usage collector rebound.
    bind_val_usage(get_val_usage())

    vision_model, _ = get_validation_models()
    user_description = normalize_user_description(user_description)
    req_body = _req_body_for_validation(
        user_asset_name=user_asset_name,
        user_description=user_description,
        user_tag_number=str(response.get("user_tag_number") or ""),
        customerid=customerid,
        categoryid=categoryid,
        assetclassid=assetclassid,
        asset_orig_b64=asset_orig_b64,
        barcode_orig_b64=barcode_orig_b64,
    )

    brand_confirm = _make_brand_conflict_confirmer(
        vision_model, asset_b64, barcode_b64, req_body, response, request_id
    )
    timer = PhaseTimer()
    path = response.setdefault("_decision_path", {})

    logger.step(
        "name_match_start",
        user_asset_name=user_asset_name or "(empty)",
        user_description_preview=(user_description[:120] if user_description else "(empty)"),
        detected_asset=str(response.get("detectedAsset") or "").strip()[:80],
        image_analysis_preview=str(response.get("imageAnalysis") or "").strip()[:160],
        barcode_position=_barcode_position_text(response),
        brand_confirm_enabled=brand_confirm is not None,
    )

    name_result = run_name_match_phase2(
        vision_model=vision_model,
        asset_b64=asset_b64,
        barcode_b64=barcode_b64,
        user_asset_name=user_asset_name,
        user_description=user_description,
        detected_asset=str(response.get("detectedAsset") or "").strip(),
        image_analysis=str(response.get("imageAnalysis") or "").strip(),
        barcode_position=_barcode_position_text(response),
        request_id=request_id,
        req_body=req_body,
    )
    timings["name_match_phase2"] = round(name_result.timing_ms or 0, 2)

    if name_result.error:
        apply_check_failure_defaults(
            response, "assetnamedescriptionmatch", name_result.error, request_id
        )
        logger.step(
            "name_match",
            error=name_result.error,
            latency_ms=timings["name_match_phase2"],
        )
        timings["name_match"] = round((time.perf_counter() - t0) * 1000, 2)
        return

    logger.step(
        "name_match_llm",
        latency_ms=timings["name_match_phase2"],
        match=name_result.llm_output.get("namedescriptionmatch"),
        percent=name_result.llm_output.get("namedescriptionmatchpercent"),
        reasoning_preview=str(
            name_result.llm_output.get("namedescriptionreasoning")
            or name_result.llm_output.get("reasoning")
            or ""
        )[:200],
    )

    vision_llm = phase1 if phase1 and not phase1.get("error") else name_result.llm_output
    path["phase1_detected"] = str(response.get("detectedAsset") or "")[:80]
    path["name_ai"] = name_result.llm_output.get("namedescriptionmatch")
    _settle_name_check(
        response,
        name_result.llm_output,
        user_asset_name,
        vision_llm,
        confirm_brand=brand_confirm,
    )
    path["name_after_rules"] = response.get("namedescriptionmatch")
    logger.step(
        "name_match_after_settle",
        name_ai=path.get("name_ai"),
        name_after_rules=path.get("name_after_rules"),
        percent=response.get("namedescriptionmatchpercent"),
        reasoning_preview=str(response.get("namedescriptionreasoning") or "")[:200],
        detectedAsset_after=str(response.get("detectedAsset") or "")[:80],
        placeholder=bool((response.get("_decision_path") or {}).get("placeholder_name")),
    )

    # Industrial decision + stronger-model escalation (validation logic, views empty —
    # AA blind-view is routing-only for this module).
    views: dict = {}
    shadow_confirm, decided = _decide_industrial_name(
        output=response,
        req_body=req_body,
        name_llm_out=name_result.llm_output,
        vision_llm=vision_llm,
        asset_b64=asset_b64,
        barcode_b64=barcode_b64,
        user_asset_name=user_asset_name,
        views=views,
        brand_confirm=brand_confirm,
        request_id=request_id,
        timer=timer,
    )
    logger.step(
        "industrial_decision",
        decided=decided,
        shadow=shadow_confirm is not None,
        path_summary=(response.get("_decision_path") or {}).get("industrial_decision"),
    )
    if shadow_confirm is not None:
        # Shadow mode: do not block; validation finishes shadow before response —
        # for this FastAPI host we skip waiting (mode usually off/on in prod).
        logger.step("industrial_decision_shadow", mode="shadow_skipped_wait")

    escalate = (not decided) and _needs_industrial_vision_escalation(
        response, req_body, views, vision_model
    )
    logger.step("vision_escalation_gate", escalate=escalate, decided=decided)
    if escalate:
        timer.start("vision_escalation_ms")
        rescued = _reread_photo_with_stronger_model(
            output=response,
            req_body=req_body,
            asset_b64=asset_b64,
            barcode_b64=barcode_b64,
            request_id=request_id,
        )
        timer.stop("vision_escalation_ms")
        path["escalated"] = bool(rescued)
        logger.step(
            "vision_escalation",
            latency_ms=timer.phases_ms.get("vision_escalation_ms"),
            rescued=bool(rescued),
            detectedAsset=str(response.get("detectedAsset") or "")[:80],
        )
        if rescued:
            path["escalated_detected"] = str(response.get("detectedAsset") or "")[:80]
            retry_name = run_name_match_phase2(
                vision_model=vision_model,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                user_asset_name=user_asset_name,
                user_description=user_description,
                detected_asset=str(response.get("detectedAsset") or "").strip(),
                image_analysis=str(response.get("imageAnalysis") or "").strip(),
                barcode_position=_barcode_position_text(response),
                request_id=request_id,
                req_body=req_body,
            )
            if not retry_name.error:
                _settle_name_check(
                    response,
                    retry_name.llm_output,
                    user_asset_name,
                    retry_name.llm_output,
                    confirm_brand=brand_confirm,
                )
                path["name_ai_retry"] = retry_name.llm_output.get("namedescriptionmatch")
                path["name_after_rules"] = response.get("namedescriptionmatch")
                logger.step(
                    "name_match_after_escalation_settle",
                    name_ai_retry=path.get("name_ai_retry"),
                    name_after_rules=path.get("name_after_rules"),
                    percent=response.get("namedescriptionmatchpercent"),
                )
            else:
                logger.step("name_match_escalation_retry_error", error=retry_name.error)

    _relabel_confirmed_brand(response, brand_confirm, req_body)

    timings["name_match"] = round((time.perf_counter() - t0) * 1000, 2)
    timings.update({k: v for k, v in (timer.phases_ms or {}).items()})
    logger.step(
        "name_match",
        latency_ms=timings["name_match"],
        match=response.get("namedescriptionmatch"),
        percent=response.get("namedescriptionmatchpercent"),
        reasoning_preview=str(response.get("namedescriptionreasoning") or "")[:200],
        decision_path={
            k: path.get(k)
            for k in ("name_ai", "name_after_rules", "escalated", "name_ai_retry", "placeholder_name")
            if path.get(k) is not None
        },
    )


def run_matching_phase(
    *,
    response: dict[str, Any],
    phase1: dict[str, Any],
    decoded: list[dict[str, Any]],
    user_asset_name: str,
    user_description: str,
    user_tag_number: str,
    run_name: bool,
    run_tag: bool,
    asset_b64: str,
    barcode_b64: str,
    asset_orig_b64: str,
    barcode_orig_b64: str,
    customerid: int | None,
    categoryid: int | None,
    assetclassid: int | None,
    request_id: str,
    logger: StepLogger,
    timings: dict[str, float],
) -> None:
    """Run name and/or tag match; parallel when both enabled."""
    if not run_name and not run_tag:
        logger.step("matching", skipped=True, reason="no_user_name_description_or_tag")
        response["matching_ran"] = {"name_description": False, "tag": False}
        return

    response["matching_ran"] = {"name_description": run_name, "tag": run_tag}

    # Capture validation usage on this thread so parallel workers can bind it.
    val_usage = get_val_usage()

    if run_name and run_tag:
        def _tag_worker() -> None:
            bind_val_usage(val_usage)
            run_tag_match(
                response=response,
                phase1=phase1,
                decoded=decoded,
                user_tag_number=user_tag_number,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                asset_orig_b64=asset_orig_b64,
                barcode_orig_b64=barcode_orig_b64,
                request_id=request_id,
                logger=logger,
                timings=timings,
            )

        def _name_worker() -> None:
            bind_val_usage(val_usage)
            run_name_description_match(
                response=response,
                phase1=phase1,
                user_asset_name=user_asset_name,
                user_description=user_description,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                asset_orig_b64=asset_orig_b64,
                barcode_orig_b64=barcode_orig_b64,
                customerid=customerid,
                categoryid=categoryid,
                assetclassid=assetclassid,
                request_id=request_id,
                logger=logger,
                timings=timings,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_tag = pool.submit(_tag_worker)
            fut_name = pool.submit(_name_worker)
            fut_tag.result()
            fut_name.result()
        logger.step("matching", mode="parallel", name=True, tag=True)
        return

    if run_tag:
        run_tag_match(
            response=response,
            phase1=phase1,
            decoded=decoded,
            user_tag_number=user_tag_number,
            asset_b64=asset_b64,
            barcode_b64=barcode_b64,
            asset_orig_b64=asset_orig_b64,
            barcode_orig_b64=barcode_orig_b64,
            request_id=request_id,
            logger=logger,
            timings=timings,
        )
    if run_name:
        run_name_description_match(
            response=response,
            phase1=phase1,
            user_asset_name=user_asset_name,
            user_description=user_description,
            asset_b64=asset_b64,
            barcode_b64=barcode_b64,
            asset_orig_b64=asset_orig_b64,
            barcode_orig_b64=barcode_orig_b64,
            customerid=customerid,
            categoryid=categoryid,
            assetclassid=assetclassid,
            request_id=request_id,
            logger=logger,
            timings=timings,
        )
    logger.step("matching", mode="sequential", name=run_name, tag=run_tag)
