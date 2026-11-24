"""Dual-lane check execution — barcode image lane + asset image lane, flag-gated prompts."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from asset_validator.core.config import (
    GEMINI_MAX_OUTPUT_TOKENS,
    INSTALLED_PLANT_NAME_RULES_ENABLED,
    TAG_REPLY_RETRY_NOTE_ENABLED,
    use_gemini,
)
from asset_validator.core.validation_plan import ValidationPlan
from asset_validator.core.market import market_for_request
from asset_validator.prompts.cost_date import (
    build_cost_date_prompt,
    load_cost_output_schema,
)
from asset_validator.prompts.loader import load_prompt
from asset_validator.prompts.checks import (
    asset_lane_check_ids,
    build_acquisition_cost_prompt,
    build_asset_lane_prompt,
    build_barcode_lane_prompt,
    build_make_model_phase2_prompt,
    build_name_match_phase2_prompt,
    build_unified_phase1_prompt,
    compute_asset_lane_max_tokens,
    compute_barcode_lane_max_tokens,
    compute_cost_call_max_tokens,
    compute_make_model_max_tokens,
    compute_name_max_tokens,
    compute_unified_phase1_max_tokens,
    requires_asset_lane,
    requires_barcode_lane,
)
from asset_validator.services.image_service import build_llm_image_parts
from asset_validator.services.llm_provider import call_llm_with_retry
from asset_validator.utils.helpers import (
    is_industrial_record,
    is_installed_plant,
    is_structure_or_infrastructure_asset,
    normalize_user_description,
    truncate_for_phase2,
)

COST_CHECK_ID = "acquisitiondatecostmatch"
NAME_CHECK_ID = "assetnamedescriptionmatch"
MAKE_MODEL_CHECK_ID = "assetmakemodelmatch"
TAG_CHECK_ID = "tagnumbercheck"
BARCODE_LANE_ID = "barcode_lane"
ASSET_LANE_ID = "asset_lane"
UNIFIED_PHASE1_LANE_ID = "unified_phase1"
ASSET_VISION_KEY = "asset_lane_vision"


@dataclass
class CheckResult:
    check_id: str
    llm_output: dict
    timing_ms: float
    error: str | None = None


@dataclass
class CheckRunSummary:
    results: dict[str, CheckResult] = field(default_factory=dict)
    phase_timings: dict[str, float] = field(default_factory=dict)


def should_defer_cost_check(plan: ValidationPlan) -> bool:
    """Defer cost until after name/description match when both checks are enabled."""
    return plan.is_enabled(COST_CHECK_ID) and plan.is_enabled(NAME_CHECK_ID)


def images_for_check(
    check_id: str,
    asset_b64: str | None,
    barcode_b64: str | None,
) -> list[dict[str, Any]]:
    """Return both upload-slot images when available (swap-safe); one image if only one provided."""
    del check_id  # all checks receive the same dual-image set
    return build_llm_image_parts(asset_b64, barcode_b64)


def _usage_label_for_lane(lane_id: str) -> str:
    if lane_id == BARCODE_LANE_ID:
        return "barcode_lane"
    if lane_id == ASSET_LANE_ID:
        return "asset_lane"
    if lane_id == UNIFIED_PHASE1_LANE_ID:
        return "unified_phase1"
    return lane_id


# Lanes whose prompt asks for "detected_codes" — the tag-reading calls.
_TAG_READING_LANES = frozenset({BARCODE_LANE_ID, UNIFIED_PHASE1_LANE_ID})


def _tag_reply_retry_note(lane_id: str) -> str | None:
    """Corrective note for the retry of a tag-reading lane whose reply was not valid JSON.

    gemini-2.5-flash-lite transcribes a tag's QR code as an invented URL and repeats one digit
    until the output cap (AssetId 1951716, every run), so the reply never closes. The note is
    sent only on that retry; a first reply that parses never sees it.
    """
    if lane_id not in _TAG_READING_LANES:
        return None
    if not TAG_REPLY_RETRY_NOTE_ENABLED:
        return ""  # the identical retry, as before — never the generic note (docs §2.6)
    return load_prompt("tag_reply_retry.md")


def _lane_may_fall_back(lane_id: str, plan: ValidationPlan) -> bool:
    """A lane whose reply carries the price never falls back to another model (docs §2.6).

    The asset-image lane, alone or inside the unified phase-1 call, prices the asset itself
    whenever the cost check is not deferred to the separate cost call (should_defer_cost_in_plan).
    """
    return lane_id == BARCODE_LANE_ID or COST_CHECK_ID not in asset_lane_check_ids(plan)


def _run_lane_call(
    lane_id: str,
    *,
    vision_model,
    image_b64: str | None,
    prompt: str,
    max_tokens: int,
    check_id_for_images: str,
    asset_b64: str | None,
    barcode_b64: str | None,
    request_id: str,
    model_fallback: bool = True,
) -> CheckResult:
    image_parts = images_for_check(check_id_for_images, asset_b64, barcode_b64)
    if not image_parts or not prompt:
        return CheckResult(
            check_id=lane_id,
            llm_output={},
            timing_ms=0,
            error="No image available for check",
        )

    start = time.perf_counter()
    try:
        llm_output = call_llm_with_retry(
            vision_model,
            prompt,
            images=image_parts,
            max_tokens=max_tokens,
            response_mime_type="application/json",
            usage_label=_usage_label_for_lane(lane_id),
            retry_prompt_suffix=_tag_reply_retry_note(lane_id),
            model_fallback=model_fallback,
        ) or {}
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        if llm_output.get("error"):
            return CheckResult(
                check_id=lane_id,
                llm_output=llm_output,
                timing_ms=elapsed,
                error=str(llm_output["error"]),
            )
        return CheckResult(check_id=lane_id, llm_output=llm_output, timing_ms=elapsed)
    except Exception as exc:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        logging.error(f"[{request_id}] Lane {lane_id} failed: {exc}")
        return CheckResult(
            check_id=lane_id,
            llm_output={"error": str(exc)},
            timing_ms=elapsed,
            error=str(exc),
        )


def _map_lane_results_to_checks(
    summary: CheckRunSummary,
    plan: ValidationPlan,
    barcode_result: CheckResult | None,
    asset_result: CheckResult | None,
) -> None:
    if barcode_result and plan.is_enabled(TAG_CHECK_ID):
        summary.results[TAG_CHECK_ID] = CheckResult(
            check_id=TAG_CHECK_ID,
            llm_output=barcode_result.llm_output,
            timing_ms=barcode_result.timing_ms,
            error=barcode_result.error,
        )
        summary.phase_timings[f"{TAG_CHECK_ID}_ms"] = barcode_result.timing_ms

    if asset_result:
        summary.results[ASSET_VISION_KEY] = CheckResult(
            check_id=ASSET_VISION_KEY,
            llm_output=asset_result.llm_output,
            timing_ms=asset_result.timing_ms,
            error=asset_result.error,
        )
        summary.phase_timings[f"{ASSET_LANE_ID}_ms"] = asset_result.timing_ms
        for check_id in asset_lane_check_ids(plan):
            summary.results[check_id] = CheckResult(
                check_id=check_id,
                llm_output=asset_result.llm_output,
                timing_ms=asset_result.timing_ms,
                error=asset_result.error,
            )
            summary.phase_timings[f"{check_id}_ms"] = asset_result.timing_ms


def run_name_match_phase2(
    *,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    user_asset_name: str,
    user_description: str,
    detected_asset: str,
    image_analysis: str,
    barcode_position: str,
    request_id: str,
    req_body: dict | None = None,
) -> CheckResult:
    """Phase 1.5 name/description match with injected vision context (pre-prd aligned)."""
    image_parts = images_for_check(NAME_CHECK_ID, asset_b64, barcode_b64)
    if not image_parts:
        return CheckResult(
            check_id=NAME_CHECK_ID,
            llm_output={},
            timing_ms=0,
            error="No image available for check",
        )

    structure = is_structure_or_infrastructure_asset(req_body or {})
    # Both tests must hold. The plant terms are matched against the free-text name, where a plant
    # word can name the LOCATION rather than the asset: customer 1299's "AC PROCUREMENT FOR
    # POTLINE 6" is a Mitsubishi tower AC at Rs 45,731, registered under Office Equipment, and it
    # must keep the ordinary rules. Requiring an industrial record drops those 22 and the two other
    # oddly classified records in the logs, and keeps all 528 genuine plant records.
    plant = (
        INSTALLED_PLANT_NAME_RULES_ENABLED
        and is_industrial_record(req_body or {})
        and is_installed_plant(req_body or {})
    )
    prompt = build_name_match_phase2_prompt(
        user_asset_name=user_asset_name,
        user_description=user_description,
        detected_asset=detected_asset,
        image_analysis=image_analysis,
        barcode_position=barcode_position,
        include_structure_rules=structure,
        include_plant_rules=plant,
    )
    max_tokens = compute_name_max_tokens(max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS)

    # Phase 1 already analyzed images — reuse injected context to avoid a second vision call.
    has_vision_context = bool(
        str(detected_asset or "").strip() or str(image_analysis or "").strip()
    )
    llm_images = image_parts if not has_vision_context else None
    mode = "text-only" if has_vision_context else "with-images"
    logging.info(f"[{request_id}] Name match Phase 1.5 ({mode}, injected vision context)")

    start = time.perf_counter()
    try:
        llm_output = call_llm_with_retry(
            vision_model,
            prompt,
            images=llm_images,
            max_tokens=max_tokens,
            response_mime_type="application/json",
            usage_label="name_match_phase2",
        ) or {}
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        if llm_output.get("error"):
            return CheckResult(
                check_id=NAME_CHECK_ID,
                llm_output=llm_output,
                timing_ms=elapsed,
                error=str(llm_output["error"]),
            )
        return CheckResult(check_id=NAME_CHECK_ID, llm_output=llm_output, timing_ms=elapsed)
    except Exception as exc:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        logging.error(f"[{request_id}] Name match Phase 1.5 failed: {exc}")
        return CheckResult(
            check_id=NAME_CHECK_ID,
            llm_output={"error": str(exc)},
            timing_ms=elapsed,
            error=str(exc),
        )


def run_make_model_phase2(
    *,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    user_category: str,
    user_subcategory: str,
    user_makemodel: str,
    user_model: str,
    detected_asset: str,
    image_analysis: str,
    include_user_identity: bool,
    user_asset_name: str = "",
    user_description: str = "",
    request_id: str,
) -> CheckResult:
    """Phase 2 make/model match with injected vision context."""
    image_parts = images_for_check(MAKE_MODEL_CHECK_ID, asset_b64, barcode_b64)
    if not image_parts:
        return CheckResult(
            check_id=MAKE_MODEL_CHECK_ID,
            llm_output={},
            timing_ms=0,
            error="No image available for check",
        )

    prompt = build_make_model_phase2_prompt(
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_makemodel=user_makemodel,
        user_model=user_model,
        detected_asset=detected_asset,
        image_analysis=image_analysis,
        include_user_identity=include_user_identity,
        user_asset_name=user_asset_name,
        user_description=user_description,
    )
    max_tokens = compute_make_model_max_tokens(max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS)

    has_vision_context = bool(
        str(detected_asset or "").strip() or str(image_analysis or "").strip()
    )
    llm_images = None if has_vision_context else image_parts
    from asset_validator.core.config import MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED

    # Which prompt build_make_model_phase2_prompt picks (docs 6.4).
    if not include_user_identity:
        mode = "vision-only"
    elif MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED:
        mode = "confirmed name + photo description"
    else:
        mode = "with-name-context, no photo description"
    if has_vision_context:
        mode += ", text-only"
    logging.info(f"[{request_id}] Make/model Phase 2 ({mode}, injected vision context)")

    start = time.perf_counter()
    try:
        llm_output = call_llm_with_retry(
            vision_model,
            prompt,
            images=llm_images,
            max_tokens=max_tokens,
            response_mime_type="application/json",
            usage_label="make_model_phase2",
        ) or {}
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        if llm_output.get("error"):
            return CheckResult(
                check_id=MAKE_MODEL_CHECK_ID,
                llm_output=llm_output,
                timing_ms=elapsed,
                error=str(llm_output["error"]),
            )
        return CheckResult(
            check_id=MAKE_MODEL_CHECK_ID, llm_output=llm_output, timing_ms=elapsed
        )
    except Exception as exc:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        logging.error(f"[{request_id}] Make/model Phase 2 failed: {exc}")
        return CheckResult(
            check_id=MAKE_MODEL_CHECK_ID,
            llm_output={"error": str(exc)},
            timing_ms=elapsed,
            error=str(exc),
        )


def run_cost_check(
    *,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    req_body: dict[str, Any],
    acquisition_date: date | None,
    request_id: str,
    user_asset_name: str = "",
    user_description: str = "",
    use_user_identity: bool = False,
    detected_asset: str = "",
    image_analysis: str = "",
) -> CheckResult:
    """Run acquisition cost check — both images when available (swap-safe)."""
    market, _ = market_for_request(req_body, request_id)
    image_parts = images_for_check(COST_CHECK_ID, asset_b64, barcode_b64)
    if not image_parts:
        return CheckResult(
            check_id=COST_CHECK_ID,
            llm_output={},
            timing_ms=0,
            error="No image available for check",
        )

    # Confirmed master data carries specs a photo cannot show (dimensions, seat counts).
    declared = {
        "user_category": str(req_body.get("categoryname") or "").strip(),
        "user_subcategory": str(req_body.get("subcategoryname") or "").strip(),
        "user_makemodel": str(
            req_body.get("makemodel") or req_body.get("makemodelname") or ""
        ).strip(),
        "user_model": str(req_body.get("model") or "").strip(),
    }

    has_vision_context = bool(
        str(detected_asset or "").strip() or str(image_analysis or "").strip()
    )
    if has_vision_context:
        llm_images = None
        mode = "text-only-identity" if use_user_identity else "text-only"
        prompt = build_cost_date_prompt(
            detected_asset,
            truncate_for_phase2(image_analysis),
            acquisition_date=acquisition_date,
            use_user_identity=use_user_identity,
            user_asset_name=user_asset_name,
            user_description=user_description,
            market=market,
            **declared,
        ) + load_cost_output_schema(market)
    else:
        llm_images = image_parts
        mode = "identity-assisted" if use_user_identity else "image-only"
        prompt = build_acquisition_cost_prompt(
            acquisition_date=acquisition_date,
            declared_cost=str(req_body.get("cost") or "").strip(),
            user_asset_name=user_asset_name,
            user_description=user_description,
            use_user_identity=use_user_identity,
            market=market,
            **declared,
        )
    max_tokens = compute_cost_call_max_tokens(max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS)
    logging.info(f"[{request_id}] Cost check ({mode})")

    start = time.perf_counter()
    try:
        llm_output = call_llm_with_retry(
            vision_model,
            prompt,
            images=llm_images,
            max_tokens=max_tokens,
            response_mime_type="application/json",
            usage_label="cost_check",
            model_fallback=False,  # a price always comes from the usual model (docs §2.6)
        ) or {}
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        if llm_output.get("error"):
            return CheckResult(
                check_id=COST_CHECK_ID,
                llm_output=llm_output,
                timing_ms=elapsed,
                error=str(llm_output["error"]),
            )
        return CheckResult(check_id=COST_CHECK_ID, llm_output=llm_output, timing_ms=elapsed)
    except Exception as exc:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        logging.error(f"[{request_id}] Cost check failed: {exc}")
        return CheckResult(
            check_id=COST_CHECK_ID,
            llm_output={"error": str(exc)},
            timing_ms=elapsed,
            error=str(exc),
        )


def run_enabled_checks(
    plan: ValidationPlan,
    *,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    req_body: dict[str, Any],
    acquisition_date: date | None,
    request_id: str,
) -> CheckRunSummary:
    """Run barcode lane + asset lane in parallel — prompts include only enabled flags."""
    summary = CheckRunSummary()
    if not plan.enabled:
        return summary

    user_asset_name = str(req_body.get("assetname") or "").strip()
    user_description = normalize_user_description(req_body.get("description"))
    user_category = str(req_body.get("categoryname") or "").strip()
    user_subcategory = str(req_body.get("subcategoryname") or "").strip()
    user_makemodel = str(req_body.get("makemodel") or req_body.get("makemodelname") or "").strip()
    user_model = str(req_body.get("model") or "").strip()
    cap = GEMINI_MAX_OUTPUT_TOKENS

    lane_jobs: list[tuple[str, str, int, str]] = []
    use_unified_phase1 = requires_barcode_lane(plan) and requires_asset_lane(plan)

    if use_unified_phase1:
        lane_jobs.append((
            UNIFIED_PHASE1_LANE_ID,
            build_unified_phase1_prompt(
                plan,
                user_asset_name=user_asset_name,
                user_description=user_description,
                user_category=user_category,
                user_subcategory=user_subcategory,
                user_makemodel=user_makemodel,
                user_model=user_model,
                acquisition_date=acquisition_date,
                declared_cost=str(req_body.get("cost") or "").strip(),
            ),
            compute_unified_phase1_max_tokens(plan, max_output_tokens=cap),
            TAG_CHECK_ID,
        ))
    else:
        if requires_barcode_lane(plan):
            lane_jobs.append((
                BARCODE_LANE_ID,
                build_barcode_lane_prompt(),
                compute_barcode_lane_max_tokens(max_output_tokens=cap),
                TAG_CHECK_ID,
            ))

        if requires_asset_lane(plan):
            lane_jobs.append((
                ASSET_LANE_ID,
                build_asset_lane_prompt(
                    plan,
                    user_asset_name=user_asset_name,
                    user_description=user_description,
                    user_category=user_category,
                    user_subcategory=user_subcategory,
                    user_makemodel=user_makemodel,
                    user_model=user_model,
                    market=market_for_request(req_body, request_id)[0],
                    acquisition_date=acquisition_date,
                    declared_cost=str(req_body.get("cost") or "").strip(),
                ),
                compute_asset_lane_max_tokens(plan, max_output_tokens=cap),
                COST_CHECK_ID,
            ))

    if not lane_jobs:
        return summary

    barcode_result: CheckResult | None = None
    asset_result: CheckResult | None = None

    if len(lane_jobs) == 1:
        lane_id, prompt, max_tokens, image_key = lane_jobs[0]
        result = _run_lane_call(
            lane_id,
            vision_model=vision_model,
            image_b64=barcode_b64 if lane_id == BARCODE_LANE_ID else asset_b64,
            prompt=prompt,
            max_tokens=max_tokens,
            check_id_for_images=image_key,
            asset_b64=asset_b64,
            barcode_b64=barcode_b64,
            request_id=request_id,
            model_fallback=_lane_may_fall_back(lane_id, plan),
        )
        if lane_id == BARCODE_LANE_ID:
            barcode_result = result
        elif lane_id == UNIFIED_PHASE1_LANE_ID:
            barcode_result = CheckResult(
                check_id=BARCODE_LANE_ID,
                llm_output=result.llm_output,
                timing_ms=result.timing_ms,
                error=result.error,
            )
            asset_result = CheckResult(
                check_id=ASSET_LANE_ID,
                llm_output=result.llm_output,
                timing_ms=result.timing_ms,
                error=result.error,
            )
        else:
            asset_result = result
        summary.phase_timings[f"{lane_id}_ms"] = result.timing_ms
    elif not use_gemini():
        for lane_id, prompt, max_tokens, image_key in lane_jobs:
            result = _run_lane_call(
                lane_id,
                vision_model=vision_model,
                image_b64=barcode_b64 if lane_id == BARCODE_LANE_ID else asset_b64,
                prompt=prompt,
                max_tokens=max_tokens,
                check_id_for_images=image_key,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                request_id=request_id,
                model_fallback=_lane_may_fall_back(lane_id, plan),
            )
            summary.phase_timings[f"{lane_id}_ms"] = result.timing_ms
            if lane_id == BARCODE_LANE_ID:
                barcode_result = result
            elif lane_id == UNIFIED_PHASE1_LANE_ID:
                barcode_result = CheckResult(
                    check_id=BARCODE_LANE_ID,
                    llm_output=result.llm_output,
                    timing_ms=result.timing_ms,
                    error=result.error,
                )
                asset_result = CheckResult(
                    check_id=ASSET_LANE_ID,
                    llm_output=result.llm_output,
                    timing_ms=result.timing_ms,
                    error=result.error,
                )
            else:
                asset_result = result
    else:
        with ThreadPoolExecutor(max_workers=len(lane_jobs)) as executor:
            futures = {
                executor.submit(
                    _run_lane_call,
                    lane_id,
                    vision_model=vision_model,
                    image_b64=barcode_b64 if lane_id == BARCODE_LANE_ID else asset_b64,
                    prompt=prompt,
                    max_tokens=max_tokens,
                    check_id_for_images=image_key,
                    asset_b64=asset_b64,
                    barcode_b64=barcode_b64,
                    request_id=request_id,
                    model_fallback=_lane_may_fall_back(lane_id, plan),
                ): lane_id
                for lane_id, prompt, max_tokens, image_key in lane_jobs
            }
            for future in as_completed(futures):
                lane_id = futures[future]
                result = future.result()
                summary.phase_timings[f"{lane_id}_ms"] = result.timing_ms
                if lane_id == BARCODE_LANE_ID:
                    barcode_result = result
                elif lane_id == UNIFIED_PHASE1_LANE_ID:
                    barcode_result = CheckResult(
                        check_id=BARCODE_LANE_ID,
                        llm_output=result.llm_output,
                        timing_ms=result.timing_ms,
                        error=result.error,
                    )
                    asset_result = CheckResult(
                        check_id=ASSET_LANE_ID,
                        llm_output=result.llm_output,
                        timing_ms=result.timing_ms,
                        error=result.error,
                    )
                else:
                    asset_result = result

    _map_lane_results_to_checks(summary, plan, barcode_result, asset_result)
    return summary
