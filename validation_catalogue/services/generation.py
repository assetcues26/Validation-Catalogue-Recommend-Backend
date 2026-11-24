"""Stage A — Generation.

Reuses Additional Asset FastAPI phase-1 flow (image prep, blind-view/content-route,
zxing, unified Phase 1, tag merge, condition) without Pinecone.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from additional_asset.core import config
from additional_asset.prompts.builders import (
    build_unified_phase1_content_route_prompt,
    build_unified_phase1_prompt,
    compute_unified_max_tokens,
)
from additional_asset.services.barcode_decode import decode_codes_from_image
from additional_asset.services.condition_service import normalize_condition
from additional_asset.services.gemini_client import call_llm_with_retry, get_shared_models
from additional_asset.services.image_service import bytes_to_b64, prepare_image_bytes
from additional_asset.services.tag_merge import merge_tag_fields, order_decoded_prefer_tag_like
from additional_asset.services.view_classifier import ViewClassification, pick_asset_and_tag_slots
from additional_asset.utils.helpers import truncate_for_phase2
from additional_asset.utils.logging_utils import StepLogger
from additional_asset.utils.token_usage import bind_request_usage, get_request_usage


@dataclass
class GenerationResult:
    ok: bool
    phase1: dict[str, Any] = field(default_factory=dict)
    decoded: list[dict[str, Any]] = field(default_factory=list)
    asset_b64: str = ""
    barcode_b64: str = ""
    asset_orig_b64: str = ""
    barcode_orig_b64: str = ""
    vision_model: Any = None
    text_model: Any = None
    error: str | None = None


def run_generation_phase(
    *,
    asset_image_bytes: bytes,
    barcode_image_bytes: bytes,
    request_id: str,
    logger: StepLogger,
    timings: dict[str, float],
    response: dict[str, Any],
) -> GenerationResult:
    """Run Additional Asset Stage A through unified Phase 1 + tag/condition mapping."""
    logger.step(
        "generation_entry",
        asset_bytes=len(asset_image_bytes),
        barcode_bytes=len(barcode_image_bytes),
        pixel_gate=config.blank_image_gate_enabled(),
        phase1_content_route=config.phase1_content_route_enabled(),
        blind_view=config.blind_view_enabled(),
    )

    vision_model, text_model = get_shared_models()
    model = vision_model or text_model

    t0 = time.perf_counter()
    try:
        asset_jpeg, asset_meta = prepare_image_bytes(
            asset_image_bytes,
            config.asset_image_max_edge(),
            config.image_jpeg_quality(),
            request_id,
            "asset",
        )
        barcode_jpeg, barcode_meta = prepare_image_bytes(
            barcode_image_bytes,
            config.barcode_image_max_edge(),
            config.image_jpeg_quality(),
            request_id,
            "barcode",
        )
    except Exception as exc:
        logger.step("preprocess", error=str(exc))
        return GenerationResult(ok=False, error=f"IMAGE_PREPROCESS_FAILED: {exc}")

    preprocess_ms = round((time.perf_counter() - t0) * 1000, 2)
    timings["preprocess"] = preprocess_ms
    logger.step("preprocess", latency_ms=preprocess_ms, asset=asset_meta, barcode=barcode_meta)

    asset_b64 = bytes_to_b64(asset_jpeg)
    barcode_b64 = bytes_to_b64(barcode_jpeg)
    asset_orig_b64 = bytes_to_b64(asset_image_bytes)
    barcode_orig_b64 = bytes_to_b64(barcode_image_bytes)

    content_route = config.phase1_content_route_enabled()
    phase1: dict[str, Any]
    decoded: list[dict[str, Any]]
    phase1_ms: float

    if content_route:
        logger.step("blind_view", skipped=True, reason="PHASE1_CONTENT_ROUTE_ENABLED")
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
        return GenerationResult(
            ok=False,
            phase1=phase1,
            decoded=decoded or [],
            asset_b64=asset_b64,
            barcode_b64=barcode_b64,
            asset_orig_b64=asset_orig_b64,
            barcode_orig_b64=barcode_orig_b64,
            vision_model=vision_model,
            text_model=text_model,
            error=f"PHASE1_FAILED: {phase1.get('error')}",
        )

    response["imageAnalysis"] = phase1.get("imageAnalysis")
    response["detectedAsset"] = phase1.get("detectedAsset")
    response["detected_description"] = phase1.get("detected_description")
    response["condition"] = normalize_condition(
        phase1.get("condition") if isinstance(phase1.get("condition"), dict) else None,
        str(phase1.get("detectedAsset") or ""),
    )
    # Extra Phase 1 fields useful for tag match / response completeness
    for key in (
        "barcodeposition",
        "barcodereadability",
        "tag_number_status",
        "detected_codes",
        "detectedtagnumbers",
    ):
        if key in phase1 and response.get(key) is None:
            response[key] = phase1.get(key)

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

    return GenerationResult(
        ok=True,
        phase1=phase1,
        decoded=decoded or [],
        asset_b64=asset_b64,
        barcode_b64=barcode_b64,
        asset_orig_b64=asset_orig_b64,
        barcode_orig_b64=barcode_orig_b64,
        vision_model=vision_model,
        text_model=text_model,
    )
