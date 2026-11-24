"""Full asset analysis pipeline orchestration."""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

try:
    import azure.functions as func
except ImportError:  # FastAPI host — minimal stub so type hints / error helpers import
    class _HttpResponse:
        def __init__(self, body=None, *, mimetype=None, status_code=200, headers=None):
            self.body = body
            self.mimetype = mimetype
            self.status_code = status_code
            self.headers = headers or {}

    class _FuncModule:
        HttpResponse = _HttpResponse
        HttpRequest = object

    func = _FuncModule()  # type: ignore
from pydantic import ValidationError

from asset_validator.core.config import (
    BLANK_FIELD_CERTAIN_MATCH_FAILS_ENABLED,
    BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED,
    BLIND_VIEW_TIMEOUT_SEC,
    COST_TRUST_DECLARED_WHEN_UNSURE,
    DECISION_PATH_LOG_ENABLED,
    DECODED_TAG_RESCUE_ENABLED,
    MASTER_DATA_PHOTO_FIT_ENABLED,
    EXACT_LIFT_RESPECTS_PHOTO_FIT_ENABLED,
    ESCALATION_VISION_MODEL,
    INDUSTRIAL_VISION_ESCALATION_ENABLED,
    WASTE_BIN_NAME_RESCUE_ENABLED,
    MAKEMODEL_BRAND_CHECK_ENABLED,
    INDUSTRIAL_PHOTO_DECISION_SHADOW_WAIT_SEC,
    INDUSTRIAL_PHOTO_DECISION_TIMEOUT_SEC,
    TAG_MISMATCH_REREAD_ENABLED,
    effective_barcode_retry_on_unreadable,
    GEMINI_MAX_OUTPUT_TOKENS,
    PINECONE_TOP_K,
    active_llm_provider,
    cost_single_pass,
    include_pinecone,
    log_response_body,
    use_gemini,
)
from asset_validator.core.market import market_for_request
from asset_validator.core.models import AssetAnalysisRequest
from asset_validator.prompts.cost_date import build_cost_price_refine_prompt
from asset_validator.prompts.pinecone import build_pinecone_selection_prompt
from asset_validator.prompts.checks import compute_cost_max_tokens
from asset_validator.services.check_runner import (
    ASSET_VISION_KEY,
    CheckResult,
    images_for_check,
    run_cost_check,
    run_enabled_checks,
    run_make_model_phase2,
    run_name_match_phase2,
    should_defer_cost_check,
)
from asset_validator.services.condition_service import normalize_condition
from asset_validator.services.cost_service import (
    CostDateResult,
    apply_cost_category_policy,
    apply_cost_date_from_gemini,
    apply_everyday_cost_band,
    compute_declared_fallback_bounds,
    is_placeholder_cost,
    format_cost_range,
    parse_visible_model_years,
    resolve_cost_category_policy,
    round_cost_fields,
    should_refine_cost,
)
from asset_validator.services.llm_provider import (
    call_llm_with_retry,
    get_api_usage_tracker,
    get_shared_models,
)
from asset_validator.services.image_service import (
    format_barcode_position,
    prepare_images_and_decode_parallel,
    validate_base64_size_first,
)
from asset_validator.services import blank_field_master
from asset_validator.services.pinecone_service import (
    generate_embedding,
    query_pinecone_masterdata,
)
from asset_validator.services.validation_service import (
    ASSET_LANE_CHECKS,
    ASSET_PHOTO_UNUSABLE,
    SUBCATMODEL_REASONING_MAX_WORDS,
    apply_acquisition_cost_input_echo,
    apply_blind_view_policy,
    apply_check_failure_defaults,
    apply_recapture_policy,
    apply_detected_description_policy,
    apply_generation_ambiguity_cap,
    apply_null_subcat_override,
    apply_tag_detection_to_output,
    apply_single_image_policy,
    settle_failed_tag_check_from_decode,
    fail_asset_side,
    build_failure_msg,
    build_initial_output,
    compute_final_verdict,
    reconcile_asset_image_readability,
    enforce_identity_confidence,
    enforce_match_percent_bounds,
    enrich_subcatmodel_reasoning_with_pinecone,
    filter_response_fields,
    finalize_response_output,
    normalize_detected_tag,
    normalize_detected_tags,
    retry_barcode_read,
)
from asset_validator.utils.classification_match import (
    TRUSTED_CATEGORY_PERCENT,
    _brand_label,
    _detect_product_categories,
    _join_texts,
    _strip_it_product_lines,
    category_trusted,
    extract_brand_keys,
    families_disjoint,
    families_ruled_out,
    product_types_scope,
    reads_record_product_types,
    scoped_families,
    strip_brand_words,
    enforce_asset_name_vs_vision,
    enforce_it_identity_vs_photo,
    enforce_placeholder_name,
    enforce_classification_match_percent_bounds,
    enforce_makemodel_brand_vs_vision,
    enforce_makemodel_vs_subcategory_contradiction,
    enforce_user_classification_vs_vision,
    filter_pinecone_candidates_by_vision,
    makemodel_type_unsupported_by_vision,
    recommended_makemodel_unsupported_by_vision,
    row_brand_not_on_asset,
    suggestion_names_another_brand,
    reconcile_classification_matches,
    reconcile_detected_asset_with_confirmed_name,
    reconcile_namedescription_with_vision,
    reconcile_subcatmodel_with_vision,
    rescue_waste_bin_name,
    resolve_user_model_for_subcat_check,
    strengthen_subcatmodel_y_reasoning,
)
from asset_validator.utils.date_utils import format_acquisition_date
from asset_validator.utils.helpers import normalize_user_description, to_int_or_none, truncate_for_phase2
from asset_validator.utils.token_usage import (
    clear_request_usage,
    get_request_usage,
    start_request_usage,
)
from asset_validator.utils.logging_utils import (
    PhaseTimer,
    StructuredLogger,
    get_memory_header,
    log_memory_usage,
)
from asset_validator.utils.response_utils import create_error_response

# Fields used internally or for server-side logic — not exposed in API response.
_INTERNAL_RESPONSE_KEYS = frozenset(
    {
        "missing_image",
        "_asset_photo_kind",
        "_blind_views",
        "_checks_incomplete",
        "_cost_photo_override",
        "_cost_priced_from_photo",
        "_vision_escalated",
        "_decision_path",
        "_makemodel_brand_conflict",
        "_recommendation_withdrawn",
        "_recapture",
        "_recaptured",
        "full_asset_photo_present",
        "visibleModelYearMin",
        "visibleModelYearMax",
        "detectedtagnumbers",
        "_asset_image_detail",
        "_request_id",
        "_barcode_image_present",
        "_asset_image_provided",
        "_barcode_image_provided",
        "decodedtagnumbers",
        "multiplebarcodesdetected",
        "matchedtagnumber",
        "detected_codes",
        "cost_reasoning",
        "release_year_reasoning",
        "acquisition_date_invalid_reason",
        "namedescriptionreasoning",
        "_placeholder_name_reason",
        "inspection_reasoning",
        "valuation_deliberation_notes",
        "age_estimation_rationale",
        "selected_identity_rationale",
        "confidence_asset_name",
        "valuation_confidence",
        "uncertainty_flags",
        "asset_class_detected",
        "visual_tier",
        "tier_evidence",
        "identified_model",
        "predicted_cost",
        "note",
        "tag_number_status",
        "visible_assets",
        "expected_asset_visible",
        "identity_corrected_from",
        "_makemodel_ai_verdict",
        "_makemodel_type_conflict",
        "_blank_field_master",
        "_blank_field_recommendation",
    }
)


# A classification status reported beside a Y sits at the bottom of the "matched" band the
# selection prompt defines (matched 85-100, failed 10-40) unless a stronger figure was
# actually measured.
CLASSIFICATION_MATCHED_FLOOR_PERCENT = 85

# "... Pinecone: master subcategory=Workstation, master makemodel=HAT Workstation With
# Pedestal." — enrich_subcatmodel_reasoning_with_pinecone appends this wherever a
# recommendation exists. It is the recommendation restated in prose, so it goes when the
# recommendation does.
_PINECONE_REASONING_SUFFIX_RE = re.compile(
    r"\s*Pinecone:\s*master[^.]*\.?\s*$", re.IGNORECASE
)


def _normalize_matched_classification(output: dict) -> None:
    """A Y is a clean bill: every status matched, and nothing else recommended.

    The recommended fields of a Y carry the customer's own category, subcategory, make/model and model
    (owner, 2026-11-01): an approved response states the classification it approved instead of nulls.
    A blank input carries master data's best match for it where the photo supports one (owner, 2026-11-02;
    docs 6.5), otherwise null; an N keeps its master-data recommendation. With
    PASS_RECOMMENDS_CUSTOMER_VALUES_ENABLED off the customer's own values are null, as before.

    subcatmodelmatch "Y" tells the customer their classification is right. Anything in the
    same payload proposing a different one contradicts it, and the contradiction had been
    reaching customers in three shapes at once:

        subcatmodelmatch        "Y"
        makemodelmatch          null                      <- neither matched nor failed
        recommendedmakemodel    "HAT Workstation With Pedestal"
        subcatmodelreasoning    "... Pinecone: master makemodel=HAT Workstation With ..."

    Five separate paths can settle on a Y — the exact-master-data lift, the null-field
    override, the make/model withhold, the no-Pinecone branch and the selection call itself
    — and they did not agree on what the rest of the payload should say. Rather than teach
    each one, the invariant is enforced once here, on the way out, after every path has had
    its say and before the verdict is computed from these fields.

    A null status is not neutral: the response carries only "matched" or "failed", so null
    is a third state every caller has to interpret, and beside a Y the only reading is
    "matched". That covers the REQ-9 case where the customer left makemodel blank — there
    was nothing to disagree with, which is a match, not an unknown.

    Only a failed make/model check stops it. "error" is set by ANY check that failed: AssetId
    1951716's tag call broke, and its correct Y then shipped with all four statuses null.
    A Y can only come from a make/model check that ran, so another check's failure says
    nothing against it. (A failed make/model check leaves subcatmodelmatch null anyway.)
    """
    if output.get("subcatmodelmatch") != "Y":
        return
    if "assetmakemodelmatch" in (output.get("_checks_incomplete") or []):
        return

    from asset_validator.core.config import PASS_RECOMMENDS_CUSTOMER_VALUES_ENABLED

    # Kept by _keep_blank_field_recommendation for a field the customer left blank only.
    blank_field = output.get("_blank_field_recommendation") if BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED else None
    for key, field in (("recommendedcategory", "categoryname"), ("recommendedsubcategory", "subcategoryname"),
                       ("recommendedmakemodel", "makemodel"), ("recommendedmodel", "model")):
        value = output.get(field)
        if isinstance(value, str) and value.strip():
            output[key] = value if PASS_RECOMMENDS_CUSTOMER_VALUES_ENABLED else None
        else:
            # AssetId 163497's rule (a Y carries no correction) is relaxed for a blank field only: a suggestion for an
            # empty field corrects nothing the customer entered. It is the row the blank-field lookup found, or null.
            output[key] = blank_field.get(key) if isinstance(blank_field, dict) else None

    for status, percent in (("categorymatch", "categorymatchpercent"),
                            ("subcategorymatch", "subcategorymatchpercent"),
                            ("makemodelmatch", "makemodelmatchpercent"),
                            ("modelmatch", "modelmatchpercent")):
        output[status] = "matched"
        measured = output.get(percent)
        if not isinstance(measured, int) or isinstance(measured, bool)                 or measured < CLASSIFICATION_MATCHED_FLOOR_PERCENT:
            output[percent] = CLASSIFICATION_MATCHED_FLOOR_PERCENT

    reasoning = output.get("subcatmodelreasoning")
    if isinstance(reasoning, str):
        output["subcatmodelreasoning"] = (
            _PINECONE_REASONING_SUFFIX_RE.sub("", reasoning).strip() or None
        )


def _trust_customer_category(output: dict) -> None:
    """The category is the customer's own choice and is never judged (docs 6.6).

    Wherever categorymatch is stated it reads "matched" at 100 — the master-data comparison already
    says so, and so does a Y once _normalize_matched_classification has stated every status — and a
    recommendation names the customer's own category. Enforced once more on the way out, after every
    path has had its say, so that no writer — an extra key in a model's reply included — can leave
    the category "failed" in the payload. A status nothing stated stays null: an N whose lookup
    never ran keeps its four nulls (2.1). Nothing here passes or fails a record; compute_final_verdict
    never counts the category when it is trusted.
    """
    if not category_trusted():
        return
    if output.get("categorymatch") is not None:
        output["categorymatch"] = "matched"
        output["categorymatchpercent"] = TRUSTED_CATEGORY_PERCENT
    category = str(output.get("categoryname") or "").strip()
    if category and any(
        output.get(k) for k in ("recommendedcategory", "recommendedsubcategory",
                                "recommendedmakemodel", "recommendedmodel")
    ):
        output["recommendedcategory"] = category


def _strip_internal_response_fields(output: dict) -> None:
    for key in _INTERNAL_RESPONSE_KEYS:
        output.pop(key, None)


def _view_slots(asset_b64: str | None, barcode_b64: str | None) -> dict[str, str]:
    """Upload slots worth classifying. A barcode slot identical to the asset slot (the
    customer sent one photo twice) is one photo, classified once."""
    slots: dict[str, str] = {}
    if asset_b64:
        slots["asset"] = asset_b64
    if barcode_b64 and barcode_b64 != asset_b64:
        slots["barcode"] = barcode_b64
    return slots


def _start_blind_view(plan, vision_model, slots: dict[str, str], request_id: str):
    """Start name-free photo classification, or return None when it cannot help."""
    from asset_validator.core.config import BLIND_VIEW_ENABLED

    if not BLIND_VIEW_ENABLED or not slots:
        return None
    if not any(plan.is_enabled(c) for c in ASSET_LANE_CHECKS):
        return None     # tag-only request: nothing asks whether the asset is shown
    from asset_validator.services.view_classifier import ViewClassification

    return ViewClassification(vision_model, slots, request_id=request_id)


def _start_recapture_detection(plan, vision_model, slots: dict[str, str], request_id: str):
    """Start the name-free photo-of-a-photo check, or return None when it cannot matter.

    Its verdict needs blind view to say which photo shows the asset, so it only runs when blind
    view does. It runs even with RECAPTURE_ENFORCE off, so detections can be reviewed in the logs.
    """
    from asset_validator.core.config import BLIND_VIEW_ENABLED, RECAPTURE_DETECTION_ENABLED

    if not RECAPTURE_DETECTION_ENABLED or not BLIND_VIEW_ENABLED or not slots:
        return None
    if not any(plan.is_enabled(c) for c in ASSET_LANE_CHECKS):
        return None     # tag-only request: nothing asks about the asset photo
    from asset_validator.services.recapture_detector import RecaptureDetection

    return RecaptureDetection(vision_model, slots, request_id=request_id)


def _asset_photo_b64(
    views: dict[str, str], slots: dict[str, str], asset_b64: str | None
) -> str | None:
    """The upload that actually SHOWS the asset, by content rather than by field.

    The name-free classifier says which slot holds the whole asset; the AssetImage field
    is only the fallback, for when classification did not run or did not find one.
    """
    from asset_validator.services.view_classifier import FULL_ASSET, PARTIAL_ASSET

    for wanted in (FULL_ASSET, PARTIAL_ASSET):
        for slot in ("asset", "barcode"):
            if views.get(slot) == wanted:
                return slots.get(slot)
    return asset_b64


def _build_no_ai_response(
    req_body: dict,
    plan,
    *,
    request_id: str,
    start_time: float,
    timer: PhaseTimer,
    logger: StructuredLogger,
) -> func.HttpResponse:
    """Return echoed response without calling vision/text models."""
    output = build_initial_output(req_body, plan)
    output["finalverdict"] = "PASS"
    output["finalverdict_reason"] = []
    output["msg"] = None
    output = finalize_response_output(output, plan)

    total_time = time.time() - start_time
    timer.phases_ms["total_ms"] = round(total_time * 1000, 2)
    logger.log_request_complete(
        total_time_ms=timer.phases_ms["total_ms"],
        status_code=200,
        api_calls_today=0,
        **timer.to_dict(),
    )
    return func.HttpResponse(
        json.dumps(output),
        mimetype="application/json",
        status_code=200,
        headers={
            "X-Request-ID": request_id,
            "X-Response-Time": f"{total_time:.2f}s",
            "X-Phase-Timing": json.dumps(timer.to_dict()),
            "X-Memory-Usage": get_memory_header(),
            "X-API-Calls-Today": "0",
            "X-API-Calls-Hour": "0",
        },
    )


_VISION_ANALYSIS_MAX_LEN = 500


def _build_search_text(asset_name: str, description: str, detected_asset: str, image_analysis: str = "") -> str:
    parts = [asset_name]
    if description:
        parts.append(description)
    if detected_asset:
        parts.append(detected_asset)
    if image_analysis:
        parts.append(image_analysis)
    return " ".join(p.strip() for p in parts if p and p.strip()).strip()


def _build_pinecone_search_text(
    plan,
    asset_name: str,
    description: str,
    detected_asset: str,
    image_analysis: str = "",
    *,
    use_user_identity: bool | None = None,
) -> str:
    """Build Pinecone embedding text — vision-only unless name/description check passed."""
    analysis = (image_analysis or "")[:_VISION_ANALYSIS_MAX_LEN]
    if use_user_identity is None:
        use_user_identity = plan.is_enabled("assetnamedescriptionmatch")
    if use_user_identity:
        return _build_search_text(asset_name, description, detected_asset, analysis)
    parts = [p for p in (detected_asset, analysis) if p and str(p).strip()]
    return " ".join(str(p).strip() for p in parts).strip()


def start_speculative_embedding(search_text: str, request_id: str) -> dict:
    state = {"embedding": None, "text": search_text.strip(), "done": threading.Event()}

    def _worker():
        if state["text"]:
            state["embedding"] = generate_embedding(state["text"], request_id)
        state["done"].set()

    threading.Thread(target=_worker, daemon=True).start()
    return state


def resolve_pinecone_embedding(
    prefetch_state: dict,
    final_search_text: str,
    request_id: str,
    phase1_state: dict | None = None,
) -> list:
    from asset_validator.core.config import effective_gemini_call_timeout_sec

    timeout = effective_gemini_call_timeout_sec()
    prefetch_state["done"].wait(timeout=timeout)
    if phase1_state:
        phase1_state["done"].wait(timeout=timeout)

    if phase1_state and phase1_state.get("embedding"):
        return phase1_state["embedding"]

    prefetch_text = prefetch_state.get("text", "")
    prefetch_embedding = prefetch_state.get("embedding")
    if not final_search_text:
        return []
    detected_suffix = (
        final_search_text[len(prefetch_text):].strip() if prefetch_text else final_search_text
    )
    if prefetch_embedding and (not detected_suffix or len(detected_suffix) < 10):
        return prefetch_embedding
    logging.info(f"[{request_id}] Re-embedding with detected asset context")
    return generate_embedding(final_search_text, request_id)




def _scope_id(value) -> int | None:
    """A positive id for the master-data scope, read float-safely: helpers.to_int_or_none reads "3.0" as 30 and
    passes 0 through, which would scope the lookup to another asset class or to none (review, 1 Nov 2026)."""
    from asset_validator.services.pinecone_service import _metadata_id

    parsed = _metadata_id(value)
    return parsed if parsed and parsed > 0 else None


def _blank_field_lookup_filter(
    output: dict, req_body: dict, subcategory_null: bool, makemodel_null: bool
) -> dict | None:
    """The filled field's own id, which the lookup for the blank one is narrowed to (owner, 2026-11-02; docs 6.5).

    Only with exactly one field blank, the make/model check having approved the filled one (subcatmodelmatch Y at
    lookup time), and a positive id for it in the request. A record the check rejected is searched across its
    category as before, so its correction can come from outside the doubtful field: AssetId 949007's MONITORS,
    photographed as a laptop, is recommended LAPTOPS.
    """
    if not BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED or subcategory_null == makemodel_null:
        return None
    if output.get("subcatmodelmatch") != "Y":
        return None
    key = "subcategoryid" if makemodel_null else "makemodelid"
    filled_id = _scope_id((req_body or {}).get(key))
    return {key: filled_id} if filled_id else None


def _blank_field_rows_by_name(
    candidates: list, output: dict, req_body: dict, subcategory_null: bool, makemodel_null: bool
) -> list:
    """Without the filled field's id, only rows carrying its exact name can be the blank field's suggestion (6.5).

    The same customer-and-category lookup runs, and rows whose filled field has another name are set aside unless
    none is left. Exact after strip, casefold and whitespace collapse: the fuzzy comparison of 6.6 scores "BATTERIES"
    inside "BATTERIES5000" at 0.92. This path never fails a record: customer 1063 files TABLES and tables apart.
    """
    if not BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED or subcategory_null == makemodel_null or not candidates:
        return candidates
    if output.get("subcatmodelmatch") != "Y":
        return candidates
    body = req_body or {}
    if makemodel_null:
        id_key, name_key, name = "subcategoryid", "subcategory", body.get("subcategoryname")
    else:
        id_key, name_key, name = "makemodelid", "makemodel", body.get("makemodel") or body.get("makemodelname")
    if _scope_id(body.get(id_key)):
        return candidates  # narrowed by the id instead (_blank_field_lookup_filter)
    same = [c for c in candidates if blank_field_master.same_name(c.get(name_key), name)]
    return same or candidates


def _norm_pinecone_value(value) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def _resolve_recommended_model(candidates: list, selection: dict):
    """Validate 4-tuple from one candidate row; else match cat/subcat/makemodel."""
    if not candidates or not selection:
        return None

    cat = selection.get("recommendedcategory")
    sub = selection.get("recommendedsubcategory")
    makemodel = selection.get("recommendedmakemodel")
    model = selection.get("recommendedmodel")

    for candidate in candidates:
        if (
            _norm_pinecone_value(candidate.get("category")) == _norm_pinecone_value(cat)
            and _norm_pinecone_value(candidate.get("subcategory")) == _norm_pinecone_value(sub)
            and _norm_pinecone_value(candidate.get("makemodel")) == _norm_pinecone_value(makemodel)
            and _norm_pinecone_value(candidate.get("model")) == _norm_pinecone_value(model)
        ):
            return candidate.get("model")

    for candidate in candidates:
        if (
            _norm_pinecone_value(candidate.get("category")) == _norm_pinecone_value(cat)
            and _norm_pinecone_value(candidate.get("subcategory")) == _norm_pinecone_value(sub)
            and _norm_pinecone_value(candidate.get("makemodel")) == _norm_pinecone_value(makemodel)
        ):
            return candidate.get("model")

    return None


def _candidate_for_pick(candidates: list, selection: dict) -> dict | None:
    """The returned row the selection picked, by subcategory and make/model (strip + casefold, as
    _resolve_recommended_model matches): the row carries the ids the selection's free-text answer lacks. None when
    it picked nothing, or a row it was not offered."""
    if not candidates or not selection:
        return None
    sub = _norm_pinecone_value(selection.get("recommendedsubcategory"))
    makemodel = _norm_pinecone_value(selection.get("recommendedmakemodel"))
    if not sub and not makemodel:
        return None
    for candidate in candidates:
        if (
            _norm_pinecone_value(candidate.get("subcategory")) == sub
            and _norm_pinecone_value(candidate.get("makemodel")) == makemodel
        ):
            return candidate
    return None


def _merge_detected_description(output: dict, llm_out: dict) -> None:
    """Preserve first non-empty detected_description from a vision check."""
    desc = llm_out.get("detected_description") or llm_out.get("ai_description")
    if desc and isinstance(desc, str) and desc.strip() and not output.get("detected_description"):
        output["detected_description"] = desc.strip()


def _barcode_position_text(output: dict) -> str:
    bp = output.get("barcodeposition")
    if isinstance(bp, dict):
        return str(bp.get("position") or "").strip()
    return str(bp or "").strip()


def _merge_asset_vision_output(output: dict, llm_out: dict) -> None:
    """Merge Phase 1 vision fields from asset lane into output."""
    for key in (
        "imageAnalysis",
        "detectedAsset",
        "imageReadability",
        "visibleModelYearMin",
        "visibleModelYearMax",
        "visible_assets",
        "expected_asset_visible",
    ):
        if key in llm_out:
            output[key] = llm_out[key]
    _merge_detected_description(output, llm_out)


def _sanitize_classification_match(value) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in ("matched", "y", "yes", "pass"):
        return "matched"
    if normalized in ("failed", "n", "no", "fail"):
        return "failed"
    return None


@dataclass
class PineconePhaseResult:
    recommendedcategory: Any = None
    recommendedsubcategory: Any = None
    recommendedmakemodel: Any = None
    recommendedmodel: Any = None
    categorymatch: Any = None
    categorymatchpercent: Any = None
    subcategorymatch: Any = None
    subcategorymatchpercent: Any = None
    makemodelmatch: Any = None
    makemodelmatchpercent: Any = None
    modelmatch: Any = None
    modelmatchpercent: Any = None
    apply_null_override: bool = False
    phase_timings: dict[str, float] = field(default_factory=dict)
    # The user values reconciliation compared against — kept so the exact-match lift
    # tests the same strings, rather than re-reading req_body at a later point.
    user_category: str = ""
    user_subcategory: str = ""
    user_makemodel: str = ""
    # For a blank field (docs 6.5): every row the lookup returned, before the photo filter; whether that was every
    # row the filter allows (fewer than top_k came back); the filled field's id it was narrowed to; and the returned
    # row the selection picked.
    pool: list = field(default_factory=list)
    pool_complete: bool = False
    lookup_filter: dict | None = None
    pick_row: dict | None = None


def _run_pinecone_phase(
    output: dict,
    req_body: dict,
    text_model,
    prefetch_state: dict,
    phase1_embed_state: dict | None,
    timer: PhaseTimer,
    logger: StructuredLogger,
    request_id: str,
    subcategory_null: bool,
    makemodel_null: bool,
    plan,
    *,
    use_user_identity: bool | None = None,
) -> PineconePhaseResult:
    result = PineconePhaseResult()
    p2_user_name = str(req_body.get("assetname") or "").strip()
    p2_user_desc = normalize_user_description(req_body.get("description"))
    p2_detected = output.get("detectedAsset", "") or ""
    p2_analysis = truncate_for_phase2(output.get("imageAnalysis", "") or "")
    if use_user_identity is None:
        use_user_identity = _resolve_use_user_identity(plan, output)
    search_text = _build_pinecone_search_text(
        plan,
        p2_user_name,
        p2_user_desc,
        p2_detected,
        p2_analysis,
        use_user_identity=use_user_identity,
    )

    candidates = []
    if search_text and req_body.get("customerid"):
        prefetched = resolve_pinecone_embedding(
            prefetch_state, search_text, request_id, phase1_embed_state
        )
        # A blank field is looked up inside the field the customer did fill, by its id (docs 6.5).
        result.lookup_filter = _blank_field_lookup_filter(output, req_body, subcategory_null, makemodel_null)
        stats: dict = {}
        candidates, embed_ms, query_ms = query_pinecone_masterdata(
            search_text=search_text,
            customer_id=to_int_or_none(req_body.get("customerid")),
            category_id=to_int_or_none(req_body.get("categoryid")),
            top_k=PINECONE_TOP_K,
            request_id=request_id,
            prefetched_embedding=prefetched,
            # The record's own asset class too, when PINECONE_ASSET_CLASS_SCOPE_ENABLED (docs 6.6);
            # without a category id, its category by name.
            asset_class_id=_scope_id(req_body.get("assetclassid")),
            category_name=str(req_body.get("categoryname") or "").strip() or None,
            filled_filter=result.lookup_filter,
            stats=stats,
        )
        returned = stats.get("returned")
        result.pool_complete = isinstance(returned, int) and returned < PINECONE_TOP_K
        result.phase_timings["pinecone_embed_ms"] = round(embed_ms, 2)
        result.phase_timings["pinecone_query_ms"] = round(query_ms, 2)

    candidates = _blank_field_rows_by_name(candidates, output, req_body, subcategory_null, makemodel_null)
    if not candidates:
        logger.info("No Pinecone candidates found")
        return result
    # Every returned row, before the photo filter below: the blank-field rule weighs all of them (docs 6.5).
    result.pool = list(candidates)

    vision_text = _join_texts(p2_detected, p2_analysis)
    if not use_user_identity:
        candidates = filter_pinecone_candidates_by_vision(candidates, vision_text, label_text=p2_detected)

    candidate_summary = json.dumps(
        [
            {
                "category": c.get("category"),
                "subcategory": c.get("subcategory"),
                "makemodel": c.get("makemodel"),
                "model": c.get("model"),
                "score": round(c.get("_score", 0), 4),
            }
            for c in candidates
        ],
        separators=(",", ":"),
    )
    p2_user_category = str(req_body.get("categoryname") or "").strip()
    p2_user_subcategory = str(req_body.get("subcategoryname") or "").strip()
    p2_user_makemodel = str(
        req_body.get("makemodel") or req_body.get("makemodelname") or ""
    ).strip()
    p2_user_model = str(req_body.get("model") or "").strip()
    result.user_category = p2_user_category
    result.user_subcategory = p2_user_subcategory
    result.user_makemodel = p2_user_makemodel
    selection_prompt = build_pinecone_selection_prompt(
        p2_detected,
        p2_analysis,
        p2_user_name,
        p2_user_desc,
        candidate_summary,
        user_category=p2_user_category,
        user_subcategory=p2_user_subcategory,
        user_makemodel=p2_user_makemodel,
        user_model=p2_user_model,
        include_user_identity=use_user_identity,
    )
    sel_start = time.perf_counter()
    selection_result = call_llm_with_retry(
        text_model,
        selection_prompt,
        images=None,
        max_tokens=350,
        response_mime_type="application/json",
        usage_label="pinecone_selection",
    )
    sel_ms = round((time.perf_counter() - sel_start) * 1000, 2)
    result.phase_timings["pinecone_selection_ms"] = sel_ms
    sel_usage = get_request_usage()
    sel_tokens: dict[str, int] = {}
    if sel_usage and sel_usage.records:
        last = sel_usage.records[-1]
        if last.phase == "pinecone_selection":
            sel_tokens = {
                "prompt_tokens": last.prompt_tokens,
                "completion_tokens": last.completion_tokens,
            }
    logger.log_api_call("pinecone_selection", sel_ms, **sel_tokens)
    logger.info(f"Pinecone LLM selection over {len(candidates)} candidates")

    if selection_result and not selection_result.get("error"):
        result.recommendedcategory = selection_result.get("recommendedcategory")
        result.recommendedsubcategory = selection_result.get("recommendedsubcategory")
        result.recommendedmakemodel = selection_result.get("recommendedmakemodel")
        result.recommendedmodel = _resolve_recommended_model(candidates, selection_result)
        result.pick_row = _candidate_for_pick(result.pool, selection_result)

        llm_cat = _sanitize_classification_match(selection_result.get("categorymatch"))
        llm_sub = _sanitize_classification_match(selection_result.get("subcategorymatch"))
        llm_makemodel = _sanitize_classification_match(selection_result.get("makemodelmatch"))
        llm_model = _sanitize_classification_match(selection_result.get("modelmatch"))

        reconciled = reconcile_classification_matches(
            user_category=p2_user_category,
            user_subcategory=p2_user_subcategory,
            user_makemodel=p2_user_makemodel,
            user_model=p2_user_model,
            recommended_category=result.recommendedcategory,
            recommended_subcategory=result.recommendedsubcategory,
            recommended_model=result.recommendedmodel,
            recommended_makemodel=result.recommendedmakemodel,
            llm_category_match=llm_cat,
            llm_subcategory_match=llm_sub,
            llm_makemodel_match=llm_makemodel,
            llm_model_match=llm_model,
            llm_category_percent=selection_result.get("categorymatchpercent"),
            llm_subcategory_percent=selection_result.get("subcategorymatchpercent"),
            llm_makemodel_percent=selection_result.get("makemodelmatchpercent"),
            llm_model_percent=selection_result.get("modelmatchpercent"),
        )
        result.categorymatch = reconciled["categorymatch"]
        result.categorymatchpercent = reconciled["categorymatchpercent"]
        result.subcategorymatch = reconciled["subcategorymatch"]
        result.subcategorymatchpercent = reconciled["subcategorymatchpercent"]
        result.makemodelmatch = reconciled["makemodelmatch"]
        result.makemodelmatchpercent = reconciled["makemodelmatchpercent"]
        result.modelmatch = reconciled["modelmatch"]
        result.modelmatchpercent = reconciled["modelmatchpercent"]
        # The customer's category is always right (docs 6.6), so a suggestion is made inside it and
        # names it. Relabelled only after the comparison: compared with the row's own category, a row that
        # carries none leaves categorymatch null, as "no comparison ran" requires (review, 1 Nov 2026).
        if p2_user_category and category_trusted() and any(
            str(value or "").strip()
            for value in (result.recommendedcategory, result.recommendedsubcategory,
                          result.recommendedmakemodel, result.recommendedmodel)
        ):
            result.recommendedcategory = p2_user_category

    if (subcategory_null or makemodel_null) and candidates:
        result.apply_null_override = True

    return result


def _log_barcode_unreadable(
    barcode_image_b64: str | None,
    gemini_output: dict,
    request_id: str,
) -> None:
    """Log unreadable barcode; retry LLM disabled (single image upload policy)."""
    if not effective_barcode_retry_on_unreadable() or not barcode_image_b64:
        return
    primary_tag = normalize_detected_tag(gemini_output.get("detectedtagnumber"))
    initial_tags = normalize_detected_tags(gemini_output.get("detectedtagnumbers"))
    if not initial_tags:
        initial_tags = normalize_detected_tags(primary_tag)
    if not initial_tags or primary_tag == "UNREADABLE":
        logging.info(
            f"[{request_id}] Barcode UNREADABLE in Phase 1 — retry skipped (single-image policy)"
        )


def _settle_name_check(
    output: dict, llm_out: dict, user_asset_name: str, vision_llm: dict, confirm_brand=None
) -> None:
    """Write the name verdict and run every server rule that can correct it.

    `confirm_brand` is the full-size brand read the brand rules consult before failing (6.3).
    """
    for key in ("namedescriptionmatch", "namedescriptionmatchpercent", "namedescriptionreasoning"):
        if key in llm_out:
            output[key] = llm_out[key]
    if not output.get("namedescriptionreasoning") and llm_out.get("reasoning"):
        output["namedescriptionreasoning"] = llm_out["reasoning"]
    enforce_asset_name_vs_vision(output, user_asset_name, llm_out, confirm_brand=confirm_brand)
    # Rescue false N: same product family, or the named asset sits elsewhere in frame.
    reconcile_namedescription_with_vision(output, user_asset_name, vision_llm)
    if WASTE_BIN_NAME_RESCUE_ENABLED:
        rescue_waste_bin_name(output, user_asset_name, vision_llm)
    # An IT record must name the brand legible on the device (5.5). After the rescues, so a Y
    # they produced is held to the same bar.
    enforce_it_identity_vs_photo(output, vision_llm, confirm_brand=confirm_brand)
    # A name that says nothing ("Asset 9799", "333") fails whatever the model said (5.7). After every rule that can
    # turn an N into a Y, so none of them can pass it.
    if enforce_placeholder_name(output, user_asset_name):
        output.setdefault("_decision_path", {})["placeholder_name"] = True
    # A confirmed name settles what the asset is. Propagate it before make/model and cost run,
    # so every check describes the same object.
    reconcile_detected_asset_with_confirmed_name(output, user_asset_name, vision_llm)
    enforce_match_percent_bounds(output)


def _photo_shows_the_asset(output: dict, views) -> bool:
    """Whether any upload shows the asset, from the name-free blind view when it answered.

    The blind view runs beside the checks and is applied later (3.1, 3.2); its answer is read
    early here so a blank or tag-only photo never pays for a second photo read. With no answer,
    fall back to phase 1's own readability. `views` may be a callable, so that only a record
    that has passed every other condition ever waits on that job.
    """
    from asset_validator.services.view_classifier import ASSET_PRESENT_VIEWS

    if callable(views):
        views = views()
    if views:
        return any(view in ASSET_PRESENT_VIEWS for view in views.values())
    return str(output.get("imageReadability") or "").upper() != "E"


def _needs_industrial_vision_escalation(output: dict, req_body: dict, views, vision_model) -> bool:
    """Whether to read the photo again with the stronger model and re-run the name check.

    Only for an industrial record (plant & machinery or a civil structure) whose name check
    failed on a photo that does show the asset. Customer 1299's smelter equipment is the case:
    gemini-2.5-flash-lite described "pipes and conduits" or "a white metal enclosure" and
    rejected 13 of 22 records; gemini-3.1-flash-lite named the air slide system and rejected 1.
    """
    from asset_validator.core.config import PLACEHOLDER_NAME_RULE_ENABLED
    from asset_validator.utils.classification_match import (
        _asset_photo_brand_keys,
        extract_brand_keys,
    )
    from asset_validator.utils.helpers import is_industrial_record, is_placeholder_name

    if not INDUSTRIAL_VISION_ESCALATION_ENABLED or not use_gemini():
        return False
    if output.get("namedescriptionmatch") != "N" or output.get("_vision_escalated"):
        return False
    if not ESCALATION_VISION_MODEL or ESCALATION_VISION_MODEL == str(vision_model or ""):
        return False
    if not is_industrial_record(req_body or {}):
        return False
    # A name that says nothing fails by a server rule (5.7) whatever the photo shows. A second read would only pay
    # to rewrite the description that make/model and cost read next.
    if PLACEHOLDER_NAME_RULE_ENABLED and is_placeholder_name(
        (req_body or {}).get("assetname"), (req_body or {}).get("description")
    ):
        return False
    # A legible brand that contradicts the name is not a recognition failure: a better photo
    # read confirms it. Customer 1001 files laptops under "PLANT AND MACHINERY", and
    # "Lenovo Laptop-SATA Dell Latitude 3400" against a Dell photo is a correct rejection —
    # 15 of the 360 candidates in the logs. Skip them rather than pay to re-confirm.
    name_brands = extract_brand_keys(str((req_body or {}).get("assetname") or ""))
    photo_brands = _asset_photo_brand_keys(output, None)
    if name_brands and photo_brands and not (name_brands & photo_brands):
        return False
    return _photo_shows_the_asset(output, views)


def _reread_photo_with_stronger_model(
    *,
    output: dict,
    req_body: dict,
    asset_b64: str | None,
    barcode_b64: str | None,
    request_id: str,
) -> bool:
    """Describe the asset again with ESCALATION_VISION_MODEL; True when the description changed.

    Only the description is replaced — imageReadability keeps the answer the readability chain
    owns (3.x), and the tag, condition and blind-view results stand. make/model and cost run
    after the name check, so they read the new description without another call.
    """
    from asset_validator.core.validation_plan import ValidationPlan
    from asset_validator.prompts.checks import build_asset_lane_prompt, compute_vision_max_tokens

    image_parts = images_for_check(ASSET_VISION_KEY, asset_b64, barcode_b64)
    if not image_parts:
        return False
    prompt = build_asset_lane_prompt(
        ValidationPlan.from_flags({"assetnamedescriptionmatch": True}),
        user_asset_name=str(req_body.get("assetname") or ""),
        user_description=normalize_user_description(req_body.get("description")),
        user_category=str(req_body.get("categoryname") or ""),
        user_subcategory=str(req_body.get("subcategoryname") or ""),
        user_makemodel=str(req_body.get("makemodel") or req_body.get("makemodelname") or ""),
    )
    if not prompt:
        return False
    llm_out = call_llm_with_retry(
        ESCALATION_VISION_MODEL,
        prompt,
        images=image_parts,
        max_tokens=compute_vision_max_tokens(max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS),
        response_mime_type="application/json",
        usage_label="vision_escalation",
    ) or {}
    if llm_out.get("error") or not str(llm_out.get("detectedAsset") or llm_out.get("imageAnalysis") or "").strip():
        logging.warning(
            f"[{request_id}] Second photo read failed: {str(llm_out.get('error'))[:120]}"
        )
        return False
    for key in ("imageAnalysis", "detectedAsset", "visible_assets",
                "visibleModelYearMin", "visibleModelYearMax"):
        if key in llm_out:
            output[key] = llm_out[key]
    _merge_detected_description(output, llm_out)
    output["_vision_escalated"] = True
    logging.info(
        f"[{request_id}] Photo re-read with {ESCALATION_VISION_MODEL}: "
        f"{str(output.get('detectedAsset'))[:60]}"
    )
    return True


def _decision_images(views, asset_b64: str | None, barcode_b64: str | None) -> list[dict[str, str]]:
    """The photo(s) the blind view says show the asset; both uploads when it gave no answer (3.1)."""
    from asset_validator.services.view_classifier import ASSET_PRESENT_VIEWS

    if callable(views):
        views = views()
    slots = {"asset": asset_b64, "barcode": barcode_b64 if barcode_b64 != asset_b64 else None}
    chosen = [b64 for slot, b64 in slots.items() if b64 and (views or {}).get(slot) in ASSET_PRESENT_VIEWS]
    if not chosen:
        chosen = [b64 for b64 in slots.values() if b64]
    return [{"mime_type": "image/jpeg", "data": b64} for b64 in chosen]


def _decision_summary(result: dict, mode: str) -> dict:
    """The industrial name decision as the decision-path log records it."""
    reading = result.get("reading") or {}
    judgement = result.get("judgement") or {}
    summary = {
        "mode": mode, "trigger": result.get("trigger"), "action": result.get("action"),
        "why": result.get("why"), "saw": reading.get("main_object"),
        "specificity": reading.get("specificity"), "heavy": reading.get("fixed_installed_machinery"),
        "relation": judgement.get("relation"), "reason": judgement.get("reason"), "ms": result.get("ms"),
        "error": reading.get("error") or judgement.get("error"),
    }
    return {k: v for k, v in summary.items() if v is not None}


class _ShadowIndustrialDecision:
    """The industrial name decision in shadow mode (5.6). `finish` records what it would have decided,
    judged by the same server rules on a copy of the record as it stood when the decision started, so
    the response itself is never touched."""

    def __init__(self, job, snapshot: dict, name_llm_out: dict, vision_llm: dict,
                 user_asset_name: str, mode: str):
        self._job = job
        self._snapshot = snapshot
        self._name_llm_out = name_llm_out
        self._vision_llm = vision_llm
        self._user_asset_name = user_asset_name
        self._mode = mode

    def finish(self, output: dict, wait_sec: float) -> None:
        from asset_validator.services import industrial_decision

        try:
            result = self._job.result(wait_sec)
            summary = _decision_summary(result, self._mode)
            _apply_industrial_decision(
                self._snapshot, result, self._name_llm_out, self._user_asset_name, self._vision_llm,
                confirm_brand=None,
            )
            summary["would_be"] = self._snapshot.get("namedescriptionmatch")
        except Exception as exc:
            summary = {"mode": self._mode, "error": f"{type(exc).__name__}: {exc}"[:160]}
        output.setdefault("_decision_path", {})["industrial_decision"] = summary


def _apply_industrial_decision(
    output: dict, result: dict, name_llm_out: dict, user_asset_name: str, vision_llm: dict, *,
    confirm_brand,
) -> None:
    """Write the decision into the record.

    A pass goes through _settle_name_check like any Y, so the brand rules still apply. A fail is written
    directly: the rescue rules in _settle_name_check read phase 1's description, which on a wrong record
    repeats the record and would rescue it.
    """
    from asset_validator.services import industrial_decision

    action = result.get("action")
    if action == industrial_decision.ACTION_PASS and output.get("namedescriptionmatch") != "Y":
        passed = industrial_decision.passed_name_output(name_llm_out, result)
        if industrial_decision.is_part_pass(result):
            # A motor, panel or connected machine carries its own maker's badge, not the named asset's, so the
            # name's brand rule (6.0) would fail the component photo the owner accepts. The confirmed name still
            # reaches make/model and cost (6.4).
            for key in ("namedescriptionmatch", "namedescriptionmatchpercent", "namedescriptionreasoning"):
                output[key] = passed[key]
            reconcile_detected_asset_with_confirmed_name(output, user_asset_name, vision_llm)
            enforce_match_percent_bounds(output)
        else:
            _settle_name_check(output, passed, user_asset_name, vision_llm, confirm_brand=confirm_brand)
    elif action == industrial_decision.ACTION_FAIL:
        industrial_decision.apply_fail(output, result)


def _decide_industrial_name(
    *,
    output: dict,
    req_body: dict,
    name_llm_out: dict,
    vision_llm: dict,
    asset_b64: str | None,
    barcode_b64: str | None,
    user_asset_name: str,
    views,
    brand_confirm,
    request_id: str,
    timer: PhaseTimer,
) -> tuple[_ShadowIndustrialDecision | None, bool]:
    """The name decision for an industrial record (5.6, industrial_decision.py).

    Returns (shadow job, decided). "on": the decision runs now, and `decided` is True when it passed or
    failed the name, so the caller skips the stronger-model re-read of 5.3. "shadow": it starts in the background on a copy of the record and
    is returned for the caller to finish; nothing in the response changes. Any failure keeps the first
    verdict.
    """
    from asset_validator.services import industrial_decision

    path = output.setdefault("_decision_path", {})
    try:
        mode = industrial_decision.mode_for(req_body)
        if mode == industrial_decision.MODE_OFF:
            return None, False
        first_reason = str(
            name_llm_out.get("namedescriptionreasoning") or name_llm_out.get("reasoning") or ""
        )
        trigger, skipped = industrial_decision.trigger_for(
            output, req_body, name_llm_out.get("namedescriptionmatch"), first_reason,
            lambda: _photo_shows_the_asset(output, views),
        )
        if not trigger:
            if skipped not in ("not_industrial", "check_error"):
                path["industrial_decision"] = {"mode": mode, "skipped": skipped}
            return None, False
        kwargs = dict(
            images=_decision_images(views, asset_b64, barcode_b64), req_body=req_body,
            trigger=trigger, first_reason=first_reason, request_id=request_id,
        )
        if mode == industrial_decision.MODE_SHADOW:
            return _ShadowIndustrialDecision(
                industrial_decision.BackgroundDecision(**kwargs), copy.deepcopy(output), name_llm_out,
                vision_llm, user_asset_name, mode,
            ), False
        timer.start("industrial_decision_ms")
        try:
            # Run with a deadline: the reading and the judge each retry on their own, and a slow model must
            # not hold the request (INDUSTRIAL_PHOTO_DECISION_TIMEOUT_SEC).
            result = industrial_decision.BackgroundDecision(**kwargs).result(INDUSTRIAL_PHOTO_DECISION_TIMEOUT_SEC)
        finally:
            timer.stop("industrial_decision_ms")
        summary = _decision_summary(result, mode)
        _apply_industrial_decision(
            output, result, name_llm_out, user_asset_name, vision_llm, confirm_brand=brand_confirm,
        )
        summary["after"] = output.get("namedescriptionmatch")
        path["industrial_decision"] = summary
        # A pass or a fail settles the name, and so does a timeout: the 5.3 re-read would run on the same slow
        # model. Any other kept verdict (an error, the tag rule for anything that is not heavy machinery)
        # leaves the record to the re-read of 5.3, as before.
        return None, result.get("action") in (
            industrial_decision.ACTION_PASS, industrial_decision.ACTION_FAIL,
        ) or result.get("why") == "timeout"
    except Exception as exc:
        logging.error(f"[{request_id}] Industrial name decision failed: {exc}")
        path["industrial_decision"] = {"error": f"{type(exc).__name__}: {exc}"[:160]}
        return None, False


def _log_decision_path(logger: StructuredLogger, output: dict, req_body: dict) -> None:
    """One "Decision path" line per request: how its verdicts were reached (docs 2.9).

    Logging only, from the request thread (lines from worker threads never reach App Insights).
    Never raises.
    """
    if not DECISION_PATH_LOG_ENABLED:
        return
    from asset_validator.utils.helpers import is_industrial_record

    try:
        logger.info(
            "Decision path",
            assetid=req_body.get("assetid"),
            customerid=req_body.get("customerid"),
            industrial=is_industrial_record(req_body),
            name=output.get("namedescriptionmatch"),
            subcatmodel=output.get("subcatmodelmatch"),
            verdict=output.get("finalverdict"),
            reasons=output.get("finalverdict_reason"),
            **(output.get("_decision_path") or {}),
        )
    except Exception as exc:
        logging.warning(f"Decision path log failed: {exc}")


def _tag_needs_reread(output: dict) -> bool:
    """Whether the first tag read earns the focused re-read.

    A decoded match already settled the tag — re-reading the photo with the model cannot
    improve on a checksum-verified value. Otherwise an unreadable tag is re-read, and so
    (TAG_MISMATCH_REREAD_ENABLED) is a read that does not match the registered tag: small
    sideways digits get misread, not only missed (AssetId 1986988, 1001101602 read as
    100101002; AssetId 2911075's 2000021001849 lost to a printed equipment code).
    """
    if output.get("tagnumbersource") == "decoded":
        return False
    detected = output.get("detectedtagnumber")
    if not detected or str(detected).upper() == "UNREADABLE":
        return True
    return (
        TAG_MISMATCH_REREAD_ENABLED
        and output.get("detectedtagnumbermatch") == "N"
        and bool(str(output.get("tagnumber") or "").strip())
    )


def _clear_pinecone_recommendations(output: dict) -> None:
    """Null recommended master-data fields when subcatmodelmatch passed (no lookup needed)."""
    for key in (
        "recommendedcategory",
        "recommendedsubcategory",
        "recommendedmakemodel",
        "recommendedmodel",
        "categorymatch",
        "categorymatchpercent",
        "subcategorymatch",
        "subcategorymatchpercent",
        "makemodelmatch",
        "makemodelmatchpercent",
        "modelmatch",
        "modelmatchpercent",
    ):
        output[key] = None


def _should_run_pinecone(
    include_pc: bool,
    output: dict,
    subcategory_null: bool,
    makemodel_null: bool,
    plan=None,
) -> bool:
    """Run Pinecone only on mismatch or when user subcategory/makemodel is null."""
    del plan  # kept for call-site compatibility
    if not include_pc or output.get("error"):
        return False
    if subcategory_null or makemodel_null:
        return True
    return output.get("subcatmodelmatch", "N") == "N"


def _parse_visible_year_from_output(output: dict) -> tuple[int | None, int | None]:
    return parse_visible_model_years(output)


def _apply_cost_result_to_output(output: dict, cost_result: CostDateResult, acquisition_date: date | None) -> None:
    # Preserve the currency resolved at build_initial_output (from customerid) when the
    # cost path did not set one (skip / no-image fallback), so it never resets to INR.
    output["currency"] = cost_result.currency or output.get("currency") or "INR"
    output["cost_lower_bound"] = cost_result.cost_lower_bound
    output["cost_upper_bound"] = cost_result.cost_upper_bound
    output["cost_acceptance_lower"] = cost_result.cost_acceptance_lower
    output["cost_acceptance_upper"] = cost_result.cost_acceptance_upper
    output["cost_range"] = cost_result.cost_range
    output["cost_check"] = cost_result.cost_check
    output["model_release_year"] = cost_result.model_release_year
    output["model_end_year"] = cost_result.model_end_year
    output["acquisition_date_valid"] = cost_result.acquisition_date_valid
    # Internal — folded into public `reasoning` at finalize, then stripped.
    if cost_result.cost_reasoning:
        output["cost_reasoning"] = cost_result.cost_reasoning
    if cost_result.release_year_reasoning:
        output["release_year_reasoning"] = cost_result.release_year_reasoning
    if cost_result.acquisition_date_invalid_reason:
        output["acquisition_date_invalid_reason"] = cost_result.acquisition_date_invalid_reason
    if acquisition_date:
        output["acquisitiondate"] = format_acquisition_date(acquisition_date)


def _apply_cost_from_phase1(
    output: dict,
    gemini_output: dict | None,
    *,
    user_cost: float | None,
    acquisition_date: date | None,
    has_asset_image: bool,
    request_id: str,
    user_category: str = "",
    user_subcategory: str = "",
    user_asset_name: str = "",
    priced_natively: bool = False,
    user_description: str = "",
    user_makemodel: str = "",
    user_model: str = "",
) -> None:
    """Apply server-side cost/date logic from merged Phase 1 JSON (no extra LLM call)."""
    if user_cost is None and acquisition_date is None:
        return

    if not has_asset_image or not gemini_output or gemini_output.get("error"):
        # The valuation call did not produce a usable answer. That says nothing about the
        # declared cost, so do not convert an internal failure into a customer-facing one.
        cost_result = CostDateResult()
        if user_cost is not None:
            cost_result.cost_check = "PASS" if COST_TRUST_DECLARED_WHEN_UNSURE else "FAIL"
            if is_placeholder_cost(user_cost):     # zero or a placeholder fails in every path (7.8)
                cost_result.cost_check = "FAIL"
            # Show a band around the declared cost rather than a row of nulls.
            accept_min, accept_max = compute_declared_fallback_bounds(user_cost)
            cost_result.cost_acceptance_lower = accept_min
            cost_result.cost_acceptance_upper = accept_max
            cost_result.cost_range = format_cost_range(accept_min, accept_max)
            cost_result.cost_bounds_source = "declared"
        if acquisition_date is not None:
            cost_result.acquisition_date_valid = (
                acquisition_date <= date.today()
                if COST_TRUST_DECLARED_WHEN_UNSURE
                else False
            )
        _apply_cost_result_to_output(output, cost_result, acquisition_date)
        return

    vis_min, vis_max = _parse_visible_year_from_output(output)
    cost_result = apply_cost_date_from_gemini(
        gemini_output,
        detected_asset=output.get("detectedAsset") or "",
        image_analysis=output.get("imageAnalysis") or "",
        user_cost=user_cost,
        acquisition_date=acquisition_date,
        request_id=request_id,
        visible_model_year_min=vis_min,
        visible_model_year_max=vis_max,
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_asset_name=user_asset_name,
        currency=output.get("currency") or "INR",
        priced_natively=priced_natively,
        user_description=user_description,
        user_makemodel=user_makemodel,
        user_model=user_model,
    )
    _apply_cost_result_to_output(output, cost_result, acquisition_date)


def _merge_cost_vision_context(output: dict, llm_out: dict) -> None:
    """Prefer richer vision context from name/make checks for cost enrichment."""
    for key in ("detectedAsset", "imageAnalysis", "detected_description", "visibleModelYearMin", "visibleModelYearMax"):
        if not output.get(key) and llm_out.get(key):
            output[key] = llm_out[key]


def _resolve_use_user_identity(plan, output: dict) -> bool:
    """True when name/description check passed (Y). Used for cost, make/model, and Pinecone."""
    if not plan.is_enabled("assetnamedescriptionmatch"):
        return False
    return output.get("namedescriptionmatch") == "Y"


def _refine_cost_pass(
    *,
    output: dict,
    pass1: dict,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    acquisition_date: date | None,
    request_id: str,
    user_asset_name: str = "",
    user_description: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
    use_user_identity: bool = False,
    market=None,
    retries: int | None = None,
    usage_label: str = "cost_refine",
) -> dict | None:
    """Second vision call to correct weak tier or generic model pricing.

    Also the second valuation of the everyday cost band (docs 7.1), with one attempt and its own usage
    label; the defaults keep the refine call exactly as it was.
    """
    vis_min, vis_max = _parse_visible_year_from_output(output)
    detected = str(
        output.get("detectedAsset") or pass1.get("detectedAsset") or ""
    ).strip()
    analysis = truncate_for_phase2(
        str(output.get("imageAnalysis") or pass1.get("imageAnalysis") or "").strip()
    )
    if not detected and not analysis:
        return None

    prompt = build_cost_price_refine_prompt(
        detected_asset=detected,
        image_analysis=analysis,
        pass1_identified_model=str(pass1.get("identified_model") or ""),
        pass1_visual_tier=str(pass1.get("visual_tier") or ""),
        pass1_predicted_cost=pass1.get("predicted_cost"),
        pass1_confidence=pass1.get("valuation_confidence"),
        acquisition_date=acquisition_date,
        visible_model_year_min=vis_min,
        visible_model_year_max=vis_max,
        user_asset_name=user_asset_name,
        user_description=user_description,
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_makemodel=user_makemodel,
        user_model=user_model,
        use_user_identity=use_user_identity,
        market=market,
    )
    image_parts = images_for_check(
        "acquisitiondatecostmatch", asset_b64, barcode_b64
    )
    has_vision_context = bool(detected or analysis)
    refine_images = None if has_vision_context else image_parts
    if not refine_images and not has_vision_context:
        return None

    max_tokens = compute_cost_max_tokens(max_output_tokens=GEMINI_MAX_OUTPUT_TOKENS)
    mode = "identity-assisted" if use_user_identity else "image-only"
    if has_vision_context:
        mode = "text-only"
    logging.info(f"[{request_id}] Cost refine pass ({mode} tier correction)")
    return call_llm_with_retry(
        vision_model,
        prompt,
        images=refine_images,
        max_tokens=max_tokens,
        retries=retries,
        response_mime_type="application/json",
        usage_label=usage_label,
    )


def _resolve_cost_gemini_output(
    *,
    output: dict,
    pass1: dict,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    acquisition_date: date | None,
    has_asset_image: bool,
    request_id: str,
    user_asset_name: str = "",
    user_description: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
    use_user_identity: bool = False,
    req_body: dict | None = None,
) -> dict:
    """Run optional refine when pass 1 tier/model is weak."""
    gemini_for_cost = pass1
    _merge_cost_vision_context(output, pass1)
    # Native pricing only when the request named its own currency; the legacy
    # customer-currency map keeps the India-prompt-then-convert behaviour.
    _cost_market, _priced_natively = market_for_request(req_body or {}, request_id)
    _cost_market = _cost_market if _priced_natively else None

    if (
        not cost_single_pass()
        and has_asset_image
        and vision_model
        and not pass1.get("error")
        and should_refine_cost(pass1)
    ):
        try:
            refined = _refine_cost_pass(
                output=output,
                pass1=pass1,
                vision_model=vision_model,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                acquisition_date=acquisition_date,
                request_id=request_id,
                user_asset_name=user_asset_name,
                user_description=user_description,
                user_category=user_category,
                user_subcategory=user_subcategory,
                user_makemodel=user_makemodel,
                user_model=user_model,
                use_user_identity=use_user_identity,
                market=_cost_market,
            )
            if refined and not refined.get("error"):
                gemini_for_cost = refined
                _merge_cost_vision_context(output, refined)
        except Exception as exc:
            logging.error(f"[{request_id}] Cost refine failed: {exc}")

    return gemini_for_cost


def _apply_everyday_cost_band(
    *,
    output: dict,
    valuation: dict | None,
    req_body: dict,
    cost_check_enabled: bool,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    acquisition_date: date | None,
    user_cost: float | None,
    has_asset_image: bool,
    request_id: str,
    timer: PhaseTimer,
    user_asset_name: str,
    user_description: str,
    user_category: str,
    user_subcategory: str,
    user_makemodel: str,
    user_model: str,
    use_user_identity: bool,
) -> None:
    """The everyday cost band (docs 7.1), on the valuation today's band was built from.

    Its second valuation is the refine pass, asked only when today's band accepts the declared cost and the
    everyday band does not: one call, no retry, whatever COST_SINGLE_PASS says. Off: nothing runs.
    """
    from asset_validator.services.cost_service import everyday_band_mode

    if everyday_band_mode() == "off":
        return
    cost_market, priced_natively = market_for_request(req_body or {}, request_id)

    def second_valuation() -> dict | None:
        if not vision_model:
            return None
        logging.info(
            f"[{request_id}] Everyday cost band: the declared cost is inside today's band only; "
            f"valuing it once more"
        )
        timer.start("cost_second_valuation_ms")
        try:
            return _refine_cost_pass(
                output=output,
                pass1=valuation,
                vision_model=vision_model,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                acquisition_date=acquisition_date,
                request_id=request_id,
                user_asset_name=user_asset_name,
                user_description=user_description,
                user_category=user_category,
                user_subcategory=user_subcategory,
                user_makemodel=user_makemodel,
                user_model=user_model,
                use_user_identity=use_user_identity,
                market=cost_market if priced_natively else None,
                retries=1,
                usage_label="cost_second_valuation",
            )
        finally:
            timer.stop("cost_second_valuation_ms")

    apply_everyday_cost_band(
        output,
        valuation,
        req_body,
        user_cost=user_cost,
        second_valuation=second_valuation,
        cost_check_enabled=cost_check_enabled,
        has_asset_image=has_asset_image,
        priced_natively=priced_natively,
        request_id=request_id,
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_asset_name=user_asset_name,
    )


def _should_skip_cost_llm(req_body: dict, output: dict) -> bool:
    """True when the cost model can be skipped outright for this record.

    Tier-1 classes (Land & Building, intangibles, CWIP) have every cost figure nulled by
    apply_cost_category_policy, so running cost_check and cost_refine only buys an answer
    that is thrown away. The skip tier is decided from req_body, except that with
    COST_SKIP_YIELDS_TO_PHOTO_ENABLED the photo (output["detectedAsset"]) can make a skip
    class priced (cost_service._photo_cost_override); it is known before any cost call.

    Guard: only skip when Phase 1 produced vision context. When it did not, the cost call
    is also the fallback that backfills detectedAsset/imageAnalysis (see
    _merge_cost_vision_context), so it must still run.
    """
    if resolve_cost_category_policy(req_body, output) != "skip":
        return False
    return bool(
        str(output.get("detectedAsset") or "").strip()
        or str(output.get("imageAnalysis") or "").strip()
    )


def _apply_cost_skip_without_llm(
    output: dict,
    *,
    user_cost: float | None,
    acquisition_date: date | None,
) -> None:
    """Produce the Tier-1 cost outcome directly — identical to the post-policy result.

    cost_check PASS with no cost figures, except that a zero/negative declared cost is a
    data defect no category policy may excuse, so it still fails. The acquisition date is
    checked for the one thing that needs no model: a date in the future. Release-year
    validation is dropped because these classes have no model release year — the same
    rule already used for low-priceability furniture.
    """
    result = CostDateResult()
    result.cost_check = "PASS"
    if is_placeholder_cost(user_cost):
        result.cost_check = "FAIL"
    if acquisition_date is not None:
        if acquisition_date > date.today():
            result.acquisition_date_valid = False
            result.acquisition_date_invalid_reason = "future_date"
        else:
            result.acquisition_date_valid = True
    _apply_cost_result_to_output(output, result, acquisition_date)


def _apply_acquisition_cost_check(
    *,
    output: dict,
    cost_result: CheckResult | None,
    plan,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    user_asset_name: str,
    user_description: str,
    user_cost: float | None,
    acquisition_date: date | None,
    has_asset_image: bool,
    request_id: str,
    timer: PhaseTimer,
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
    req_body: dict | None = None,
) -> None:
    """Merge cost LLM output, optional refine, and server-side validation."""
    if cost_result and cost_result.error:
        apply_check_failure_defaults(
            output, "acquisitiondatecostmatch", cost_result.error, request_id
        )
        apply_acquisition_cost_input_echo(
            output, user_cost=user_cost, acquisition_date=acquisition_date
        )
        return
    if not cost_result:
        return

    llm_out = cost_result.llm_output
    for key in ("visibleModelYearMin", "visibleModelYearMax"):
        if key in llm_out:
            output[key] = llm_out[key]
    _merge_detected_description(output, llm_out)

    use_user_identity = _resolve_use_user_identity(plan, output)
    gemini_for_cost = _resolve_cost_gemini_output(
        output=output,
        pass1=llm_out,
        vision_model=vision_model,
        asset_b64=asset_b64,
        barcode_b64=barcode_b64,
        acquisition_date=acquisition_date,
        has_asset_image=has_asset_image,
        request_id=request_id,
        user_asset_name=user_asset_name,
        user_description=user_description,
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_makemodel=user_makemodel,
        user_model=user_model,
        use_user_identity=use_user_identity,
        req_body=req_body,
    )
    _apply_cost_from_phase1(
        output,
        gemini_for_cost,
        user_cost=user_cost,
        acquisition_date=acquisition_date,
        has_asset_image=has_asset_image,
        request_id=request_id,
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_asset_name=user_asset_name,
        priced_natively=market_for_request(req_body or {}, request_id)[1],
        user_description=user_description,
        user_makemodel=user_makemodel,
        user_model=user_model,
    )
    try:
        _apply_everyday_cost_band(
            output=output,
            valuation=gemini_for_cost,
            req_body=req_body or {},
            cost_check_enabled=plan.is_enabled("acquisitiondatecostmatch"),
            vision_model=vision_model,
            asset_b64=asset_b64,
            barcode_b64=barcode_b64,
            acquisition_date=acquisition_date,
            user_cost=user_cost,
            has_asset_image=has_asset_image,
            request_id=request_id,
            timer=timer,
            user_asset_name=user_asset_name,
            user_description=user_description,
            user_category=user_category,
            user_subcategory=user_subcategory,
            user_makemodel=user_makemodel,
            user_model=user_model,
            use_user_identity=use_user_identity,
        )
    except Exception as exc:
        # apply_everyday_cost_band puts its own fields back; this guards the wiring around it.
        logging.warning(f"[{request_id}] Everyday cost band skipped, today's band stands: {exc}")
    timer.phases_ms[f"{cost_result.check_id}_ms"] = cost_result.timing_ms
    apply_acquisition_cost_input_echo(
        output, user_cost=user_cost, acquisition_date=acquisition_date
    )


def _normalize_condition_output(output: dict) -> None:
    detected = output.get("detectedAsset") or ""
    image_readability = output.get("imageReadability")
    output["condition"] = normalize_condition(
        output.get("condition"), detected, image_readability=image_readability
    )


def _apply_identity_guards(output: dict, gemini_output: dict) -> None:
    enforce_identity_confidence(output, gemini_output)
    apply_generation_ambiguity_cap(output, gemini_output)
    enforce_match_percent_bounds(output)


def _apply_exact_master_match_lift(
    output: dict,
    pc_result: PineconePhaseResult,
    subcategory_null: bool,
    makemodel_null: bool,
) -> None:
    """
    Master data echoing the user's own classification verbatim confirms it, so lift
    subcatmodelmatch to Y and drop the recommendations.

    The N that reaches here is correct — it is what triggered the lookup in the first
    place (`_should_run_pinecone`). But master data is the customer's own catalogue, so
    a candidate identical to what they sent is the lookup answering "your entry is
    right", not "here is a correction". Leaving the N standing produced a self-
    contradicting response: subcatmodelmatch N, all three status fields `matched` at
    100%, and a recommendation echoing the customer's input back at them.

    Holds the invariant that an N verdict is always backed by at least one non-matching
    status field out of categorymatch / subcategorymatch / makemodelmatch.

    Comparison is `_norm_pinecone_value` (strip + casefold), the same identity notion
    `_resolve_recommended_model` already uses for master values — the recommendation
    round-trips through an LLM selection call, so casing and padding can drift on values
    that are otherwise character-for-character the customer's own.

    `model` takes no part: master data does not carry the field, so `recommendedmodel`
    is `None` and `modelmatch` is always `None`. Comparing it would make the lift fail
    for precisely those customers who do populate model.

    Nor does a trusted category (docs 6.6): it is never judged, so subcategory and make/model
    are what must echo the row, and a blank or differently spelt category no longer blocks it.
    """
    if output.get("error") or output.get("subcatmodelmatch") != "N":
        return
    # The photo contradicts the make/model's brand, so the customer's own catalogue row
    # confirms nothing — it is the entry the photo disproves (enforce_makemodel_brand_vs_vision).
    if output.get("_makemodel_brand_conflict"):
        return
    # The row the lift would rely on was withdrawn because the photo rules out its product type (6.2), and the
    # name check failed too: two signals that the photo shows something else, so the row confirms nothing. AssetId
    # 1520444 (customer 1004, 2026-11-01): a laptop registered Chairs / IKEA Markus had its chair row withdrawn and
    # was then lifted to Y on that same row. The withdrawal alone is a keyword judgement that misfires: the other 3
    # withdrawn-then-lifted records of the 30 days to 1 Nov passed their name check and were genuine (a laboratory
    # bench filed as a table, a POS charging unit, a steel writing stand), so they are still lifted.
    if (EXACT_LIFT_RESPECTS_PHOTO_FIT_ENABLED and output.get("_recommendation_withdrawn")
            and output.get("namedescriptionmatch") == "N"):
        return
    # REQ-9 territory (6.5) — the user supplied nothing to confirm in the blank field. A certain master-data row
    # fails it there instead (_fail_on_certain_master_row), and a pass shows the row only for the blank field.
    if subcategory_null or makemodel_null:
        return

    trusted = category_trusted()
    compared = [
        (pc_result.recommendedsubcategory, pc_result.user_subcategory),
        (pc_result.recommendedmakemodel, pc_result.user_makemodel),
    ]
    if not trusted:
        compared.insert(0, (pc_result.recommendedcategory, pc_result.user_category))
    for recommended, user in compared:
        rec_value = _norm_pinecone_value(recommended)
        user_value = _norm_pinecone_value(user)
        # A blank on either side is not a confirmation of anything.
        if not rec_value or not user_value or rec_value != user_value:
            return

    output["subcatmodelmatch"] = "Y"
    # enforce_match_percent_bounds clamps a Y to [75, 95], so 95 is the ceiling a lift
    # can hold. Anything higher is silently pulled back to it.
    output["subcatmodelmatchpercent"] = 95
    if trusted:
        # The category was not compared (docs 6.6), so the reason names only what was.
        entries = tuple(e for e in (pc_result.user_subcategory, pc_result.user_makemodel) if str(e or "").strip())
        output["subcatmodelreasoning"] = (
            f"Y: subcategory and make/model match customer master data exactly "
            f"({' / '.join(str(e) for e in entries)})."
        )
    else:
        entries = (pc_result.user_category, pc_result.user_subcategory, pc_result.user_makemodel)
        output["subcatmodelreasoning"] = (
            f"Y: classification matches customer master data exactly "
            f"({' / '.join(str(e) for e in entries)})."
        )
    _clear_pinecone_recommendations(output)


# The percent a make/model match is worth when it rests on category and subcategory alone,
# the photo having shown no branding to check it against. 85 is the bottom of the "matched"
# band the selection prompt defines (matched 85-100, failed 10-40).
MAKEMODEL_UNVERIFIED_PERCENT = 85


def _make_brand_reader(vision_model, asset_b64: str | None, request_id: str, output: dict):
    """A zero-arg reader the merge calls only if it actually needs the answer.

    Deferred on purpose: building it is free, and the vision call behind it happens only
    for a record whose make/model comparison has already failed.
    """
    from asset_validator.core.config import BRAND_READ_ENABLED

    if not vision_model or not asset_b64 or not BRAND_READ_ENABLED:
        return None

    def _read():
        from asset_validator.services.brand_reader import read_visible_brand

        return read_visible_brand(
            vision_model, asset_b64,
            target_asset=str(output.get("detectedAsset") or ""),
            request_id=request_id,
        )

    return _read


def _make_brand_conflict_confirmer(
    vision_model, asset_b64: str | None, barcode_b64: str | None, req_body: dict | None,
    output: dict, request_id: str,
):
    """The full-size brand read every brand rule consults before it fails a record (docs 6.3).

    Building it is free: nothing is decoded or read until a rule finds a conflict. Then each
    upload the request kept is read once — the original upload when it decodes (shrunk only to
    TAG_REREAD_MAX_EDGE), else the copy phase 1 saw. Both uploads, because swapped photos are
    exactly where the shrunk read goes wrong (AssetId 3824091's Voltas sat in the tag slot).
    """
    from asset_validator.core.config import BRAND_CONFLICT_CONFIRM_ENABLED

    slots = _view_slots(asset_b64, barcode_b64)
    if not BRAND_CONFLICT_CONFIRM_ENABLED or not vision_model or not slots:
        return None
    from asset_validator.services.brand_reader import BrandConflictConfirmer
    from asset_validator.services.image_service import tag_photo_for_focused_reread

    body = req_body or {}
    originals = {"asset": body.get("assetimage"), "barcode": body.get("barcodeimage")}
    if "barcode" in slots and originals["asset"] and originals["asset"] == originals["barcode"]:
        # One photo sent in both slots: the shrunk copies differ (896 vs 768 px), the upload does not.
        slots = {"asset": slots.get("asset") or slots["barcode"]}

    def full_size(slot: str, prepared: str):
        return lambda: tag_photo_for_focused_reread(originals.get(slot), request_id) or prepared

    # Each field that claims a brand — the asset name, and make/model with model — must agree with
    # the full-size reads. The name stands alone because the name rule reads it alone; the free-text
    # description is left out ("replaced old Carrier unit", "purchased from Croma").
    declared = [
        extract_brand_keys(str(body.get("assetname") or "")),
        extract_brand_keys(" ".join(str(body.get(k) or "") for k in ("makemodel", "makemodelname", "model"))),
    ]
    return BrandConflictConfirmer(
        vision_model,
        [full_size(slot, prepared) for slot, prepared in slots.items()],
        lambda: str(output.get("detectedAsset") or ""),
        declared=declared,
        request_id=request_id,
    )


def _relabel_confirmed_brand(output: dict, brand_confirm, req_body: dict | None = None) -> None:
    """Once the full-size photos settle the brand, the asset label says it (docs 6.3).

    The phase-1 label carries the misread brand ("Hitachi split air conditioner indoor unit"), and
    make/model, cost and the Y reasoning template all read it. It becomes the confirmed brand plus
    the label with every brand word removed. imageAnalysis is free text and is left as written.
    With req_body, the make/model Y reasoning, templated from the old label, is rebuilt.
    """
    confirmed = getattr(brand_confirm, "confirmed_brands", None)
    if not confirmed:
        return
    label = _brand_label(confirmed)
    detected = str(output.get("detectedAsset") or "").strip()
    if detected.lower().startswith(label.lower()):
        return
    # Only the misread brand leaves, with its product line ("Dell Latitude laptop" -> "HP laptop");
    # an everyday word or a "5 HP" rating stays.
    misread = extract_brand_keys(detected, prose=True) - set(confirmed)
    rest = (
        _strip_it_product_lines(strip_brand_words(detected, keys=misread), keys=misread)
        if misread else detected
    )
    output["detectedAsset"] = f"{label} {rest}".strip()
    if req_body is not None and output.get("subcatmodelmatch") == "Y":
        strengthen_subcatmodel_y_reasoning(output, req_body)


def _withhold_makemodel_without_visible_brand(
    output: dict, brand_reader=None, *, request_id: str = "", req_body: dict | None = None,
    vision_llm: dict | None = None,
) -> None:
    """Do not fail make/model from master data alone when the photo shows no branding.

    makemodelmatch compares two STRINGS — what the customer typed against the makemodel on
    the master-data row vision picked. Neither side is the photograph. When the asset body
    carries no legible manufacturer marking there is nothing in the image that can settle
    the comparison, so a "failed" here is a disagreement between two records, not a finding
    about the asset.

    AssetId 2302965 is the case. A laser coding machine, shrink-wrapped on a pallet in a
    warehouse: no nameplate, no maker's mark, and the system's own analysis said so — "No
    specific manufacturer branding is visible on the main body". The customer had typed
    makemodel "IJP Printer"; master data held subcategory "IJP Printer" and makemodel
    "Markem Image". Their fields were shifted by one — a product type where the catalogue
    keeps a brand — so the comparison failed at 10%, categorymatch and subcategorymatch both
    said matched at 100% and 99%, and the record was rejected with an empty msg.

    The brand is read by a separate call (brand_reader), not by the shared phase-1 vision
    prompt — see brand_reader.py for the measurement that ruled that out. It runs only for a
    record whose make/model has already failed, so it costs nothing on the common path.

    UNKNOWN (the call failed, or no reader was supplied) is not the same as "no brand": the
    comparison then stands exactly as it did before. Only a positive "nothing legible"
    withholds it.

    A missing logo says nothing about product type (docs 6), so a make/model that names a type the
    photo does not show is not withheld either — the photo, not a logo, settles that. Case 10 of the
    1 Nov test matrix (customer 1063): a 4 Tier Sorting Rack registered FF / CHAIRS / "Black Chair"
    came back make/model "matched" 85 beside the rack, and the same step passed TABLES / "Black
    Chair" on a table photo. req_body and vision_llm give that check the text Gate B reads.
    """
    from asset_validator.core.config import WITHHOLD_RESPECTS_MAKEMODEL_TYPE_ENABLED
    from asset_validator.services.brand_reader import NO_BRAND, UNKNOWN

    if output.get("error") or output.get("makemodelmatch") != "failed":
        return
    # A brand already legible in the photo contradicts the make/model: the failure rests on the
    # photograph, not on two records disagreeing, so there is nothing to withhold.
    if output.get("_makemodel_brand_conflict"):
        return
    # Nor when the blank-field rule kept an N because the filled make/model names another product
    # type (6.5), or 6.2 failed it on the type of a row the photo rules out: a missing logo says
    # nothing about that.
    if output.get("_makemodel_type_conflict"):
        return
    # Nor when a blank make/model failed on a certain master-data row (6.5): it failed for being blank, not on a
    # brand, and clearing it here would turn the N back into a Y (_restore_subcatmodel_without_backing).
    if output.get("_blank_field_master"):
        return
    if brand_reader is None:
        return
    # Nor when the make/model itself names a product type the photo does not show and the chosen row
    # does not share (6): case 10's "Black Chair" on a rack. AssetId 2302965's "IJP Printer" is still
    # withheld — its row, IJP Printer / Markem Image, is a printer too.
    if WITHHOLD_RESPECTS_MAKEMODEL_TYPE_ENABLED and makemodel_type_unsupported_by_vision(
        output, req_body, vision_llm
    ):
        output.setdefault("_decision_path", {})["withhold_skipped"] = "type"   # countable in the logs (2.9)
        # The subcategory matched, so the record now fails on its make/model alone: the make/model suggested beside
        # it must fit the photo too, or none is shown (owner, 2 Nov 2026: a sensible suggestion or none).
        if output.get("subcategorymatch") == "matched" and recommended_makemodel_unsupported_by_vision(
            output, vision_llm, req_body=req_body
        ):
            recommended = {k: output.get(k) for k in ("recommendedsubcategory", "recommendedmakemodel",
                                                      "recommendedmodel")}
            output["recommendedmakemodel"] = None
            output["recommendedmodel"] = None
            _restate_pinecone_reasoning(output, recommended)
        logging.info(
            f"[{request_id}] No-visible-brand withhold skipped: the make/model names a product "
            f"type the photo does not show"
        )
        return
    try:
        brand = brand_reader()
    except Exception as e:
        logging.warning(f"[{request_id}] Visible-brand read failed: {e}")
        return
    if brand is UNKNOWN or str(brand).strip() != NO_BRAND:
        return

    # Reported as matched, not as null. The response carries only "matched" or "failed",
    # so a null is a third state every caller would have to learn to interpret, and the
    # honest reading of an unverifiable comparison is that nothing contradicts the record.
    # 85 is the bottom of the matched band the selection prompt defines, which is what a
    # match resting on category and subcategory alone is worth.
    output["makemodelmatch"] = "matched"
    output["makemodelmatchpercent"] = MAKEMODEL_UNVERIFIED_PERCENT
    # Proposing "Markem Image" beside a field reported as matched contradicts itself, so
    # the make/model recommendation goes with the failure it belonged to. Any OTHER
    # recommendation is left alone here — a subcategory correction on a record that still
    # fails is exactly what the reviewer needs.
    recommended = {k: output.get(k) for k in ("recommendedsubcategory", "recommendedmakemodel",
                                              "recommendedmodel")}
    output["recommendedmakemodel"] = None
    output["recommendedmodel"] = None
    _restore_subcatmodel_without_backing(output)
    if WITHHOLD_RESPECTS_MAKEMODEL_TYPE_ENABLED and output.get("subcatmodelmatch") == "N":
        _restate_pinecone_reasoning(output, recommended)


def _restate_pinecone_reasoning(output: dict, recommended: dict) -> None:
    """The "Pinecone: master ..." suffix restates the recommendation, so it says only what is still recommended.

    enrich_subcatmodel_reasoning_with_pinecone wrote it before the no-visible-brand withhold dropped the
    make/model and model: case 10 of the 1 Nov test matrix read "master makemodel=4 tier sorting rack" while
    recommendedmakemodel was null (docs 6). The suffix as written is rebuilt from `recommended` — the values it
    was written from, which may hold commas and full stops — taken off, and written again from what remains.
    Enrich skips only an identical suffix, so calling it again without taking the old one off would add a second.
    """
    written = dict(recommended)
    enrich_subcatmodel_reasoning_with_pinecone(written)
    suffix = str(written.get("subcatmodelreasoning") or "")
    reasoning = str(output.get("subcatmodelreasoning") or "")
    if not suffix or not reasoning.endswith(suffix):
        return
    output["subcatmodelreasoning"] = reasoning[: -len(suffix)].rstrip() or None
    enrich_subcatmodel_reasoning_with_pinecone(output)


def _restore_subcatmodel_without_backing(output: dict) -> None:
    """Hold the invariant that an N is always backed by a non-matching status field.

    The rule this service already keeps: if subcatmodelmatch is N, at least one of
    categorymatch / subcategorymatch / makemodelmatch must be a non-match. Withholding
    make/model can remove the only one, and an N with three matching status fields is the
    self-contradicting response that rule exists to prevent. A trusted category (docs 6.6) is
    never a failure reason, so it backs no N: only subcategory and model can keep it.
    """
    if output.get("subcatmodelmatch") != "N":
        return
    backing = ("categorymatch", "subcategorymatch", "modelmatch")
    if category_trusted():
        backing = backing[1:]
    if any(output.get(k) == "failed" for k in backing):
        return

    output["subcatmodelmatch"] = "Y"
    # enforce_match_percent_bounds clamps a Y to [75, 95]. This is the weakest Y we issue —
    # category and subcategory agree and make/model could not be seen — so it sits at the floor.
    output["subcatmodelmatchpercent"] = 75
    output["subcatmodelreasoning"] = (
        ("Y: subcategory matches master data. " if category_trusted()
         else "Y: category and subcategory match master data. ")
        + "Make/model could not be verified "
        "because no manufacturer branding is legible on the asset in the photo, so it was "
        "not counted against the record."
    )
    # A Y carries no correction. Leaving the recommendations standing produced exactly the
    # self-contradicting response this service already has a rule against: AssetId 2302965
    # came back subcatmodelmatch Y while still recommending category "Plant and Machinery",
    # subcategory "IJP Printer" and makemodel "Markem Image" — "your entry is right" and
    # "here are three corrections" in the same payload. Every other path that reaches a Y
    # clears them; so does this one.
    #
    # Only the recommendations. _clear_pinecone_recommendations also nulls the status
    # fields, and makemodelmatch has just been set to "matched" precisely so that no caller
    # meets a null there.
    for key in ("recommendedcategory", "recommendedsubcategory",
                "recommendedmakemodel", "recommendedmodel"):
        output[key] = None


def _hold_makemodel_brand_conflict(output: dict) -> None:
    """Keep a make/model the photo contradicts reported as failed, whatever master data said.

    enforce_makemodel_brand_vs_vision fails the classification on the photograph's evidence.
    The master-data lookup that follows compares two records, not the photo: when the
    catalogue has no row in the photographed brand it can pick the customer's own entry and
    call make/model "matched" — an N with no failing status, the contradiction 2.3 forbids.
    Recommending that same entry back is no correction either, so it is dropped.
    """
    if not output.get("_makemodel_brand_conflict") or output.get("subcatmodelmatch") != "N":
        return
    if output.get("makemodelmatch") != "failed":
        output["makemodelmatch"] = "failed"
        output["makemodelmatchpercent"] = 25
    recommended = _norm_pinecone_value(output.get("recommendedmakemodel"))
    if recommended and recommended == _norm_pinecone_value(output.get("makemodel")):
        output["recommendedmakemodel"] = None


def _withdraw_suggestion_of_another_brand(output: dict, vision_llm: dict | None = None, *, request_id: str = "") -> None:
    """Never suggest a make/model whose brand contradicts the brand the photo shows on the asset (docs 6).

    The owner, 2 Nov 2026. AssetId 1142 (customer 1323): an HP laptop registered Desktop / Apple Mac16 was offered
    Laptop / "Dell", because that customer's master data holds Apple and Dell laptops only. The make/model and model
    suggestion goes; the subcategory stays, since a subcategory alone may be suggested (6.5). Runs before the
    "Pinecone:" suffix is written, so the reasoning never names the withdrawn make/model either.
    """
    from asset_validator.core.config import SUGGESTION_RESPECTS_PHOTO_BRAND_ENABLED

    # A Y shows the customer's own values on the way out (2.2): nothing here would reach the response.
    if output.get("subcatmodelmatch") == "Y":
        return
    if not SUGGESTION_RESPECTS_PHOTO_BRAND_ENABLED or not suggestion_names_another_brand(output, vision_llm):
        return
    suggested = output.get("recommendedmakemodel")
    output["recommendedmakemodel"] = None
    output["recommendedmodel"] = None
    # What is left would only repeat the customer's own subcategory, which corrects nothing.
    if output.get("subcategorymatch") == "matched":
        output["recommendedsubcategory"] = None
    output.setdefault("_decision_path", {})["suggestion_withdrawn"] = "brand"   # countable in the logs (2.9)
    logging.info(f"[{request_id}] Master-data make/model {suggested!r} withdrawn: its brand is not the photo's")


def _withdraw_recommendation_outside_photo_type(
    output: dict, req_body: dict | None, *, request_id: str = ""
) -> None:
    """A master-data row of a product type the photo rules out is no recommendation (docs 6.3).

    The lookup is filtered to the customer's category, and the selection picks the closest row
    among what it is offered — so when the catalogue holds nothing of the photographed type
    under that category, the pick is a row of some OTHER type, and its statuses are string
    comparisons with the customer's own entry. AssetId 4300993, an Asus laptop registered
    Telephones / Asus vivobook, was recommended "Telephones / Cisco IP Phone 8845" with
    subcategorymatch "matched": the wrong subcategory echoed back as a confirmation, and a desk
    phone proposed for a laptop.

    Fires only when the row and the photo both name a known product family and the families
    are disjoint; an untyped row or photo is left to the comparison exactly as before. The
    recommendation is withdrawn, a field that echoed the customer's entry from that row or
    names a family the photo rules out is `failed`, and a make/model that fits the photo is
    `matched` at the unverified floor — the N stays backed by the subcategory (2.3).

    A trusted category (docs 6.6) is the customer's own, not the row's: the row's type is read from
    its subcategory, make/model and model, and the category is judged neither way.
    """
    from asset_validator.core.config import WITHHOLD_RESPECTS_MAKEMODEL_TYPE_ENABLED

    if not MASTER_DATA_PHOTO_FIT_ENABLED or output.get("error"):
        return
    if output.get("subcatmodelmatch") != "N":
        return
    photo_text = _join_texts(output.get("detectedAsset"), output.get("imageAnalysis"))
    # An industrial record is judged by the families of before 2026-11-02 only (6.1, 5.6).
    photo_families = scoped_families(_detect_product_categories(photo_text) if photo_text else set(), req_body)
    if not photo_families:
        return
    row = {
        k: str(output.get(k) or "").strip()
        for k in ("recommendedcategory", "recommendedsubcategory", "recommendedmakemodel",
                  "recommendedmodel")
    }
    trusted = category_trusted()
    typed = [v for k, v in row.items() if not (trusted and k == "recommendedcategory")]
    row_families = scoped_families(_detect_product_categories(_join_texts(*typed)), req_body)
    # A family added on 2026-11-02 is ruled out only by the asset label, the families of before by label or
    # description (6.1, families_ruled_out).
    label_families = scoped_families(_detect_product_categories(str(output.get("detectedAsset") or "")), req_body)
    if not families_ruled_out(row_families, photo_families, label_families):
        return

    body = req_body or {}
    user_subcategory = str(body.get("subcategoryname") or "").strip()
    user_makemodel = str(body.get("makemodel") or body.get("makemodelname") or "").strip()

    def _judge(user_value: str, row_value: str, status_key: str, percent_key: str) -> bool:
        """True when the field was failed on its product type."""
        families = scoped_families(_detect_product_categories(user_value) if user_value else set(), body)
        if families_ruled_out(families, photo_families, label_families):
            output[status_key], output[percent_key] = "failed", 15     # the photo rules it out
            return True
        elif not families and user_value and (
            _norm_pinecone_value(user_value) == _norm_pinecone_value(row_value)
        ):
            output[status_key], output[percent_key] = "failed", 15     # the row's type is its type
            return True
        elif families and status_key == "makemodelmatch" and output.get("subcategorymatch") == "failed":
            output[status_key] = "matched"                              # fits the photo; unverified
            output[percent_key] = MAKEMODEL_UNVERIFIED_PERCENT
        return False

    _judge(user_subcategory, row["recommendedsubcategory"], "subcategorymatch", "subcategorymatchpercent")
    makemodel_failed_on_type = _judge(
        user_makemodel, row["recommendedmakemodel"], "makemodelmatch", "makemodelmatchpercent"
    )
    # A make/model failed on its type is settled by the photo, not by a logo, so the no-visible-brand
    # withhold (6) must not turn it back into "matched" 85. A table photo registered SEATING / Model X100,
    # offered CHAIRS / Model X100: the echo was failed here and then cleared by the withhold.
    if makemodel_failed_on_type and WITHHOLD_RESPECTS_MAKEMODEL_TYPE_ENABLED:
        output["_makemodel_type_conflict"] = True
        output.setdefault("_decision_path", {})["withhold_skipped"] = "6.2"   # countable in the logs (2.9)
    for key in row:
        output[key] = None
    output["_recommendation_withdrawn"] = True  # the exact-master lift (2.3) must not lift on this row
    logging.info(
        f"[{request_id}] Master-data recommendation withdrawn: row type "
        f"{sorted(row_families)} does not fit the photo {sorted(photo_families)}"
    )


# What REQ-9 may do with a record whose subcategory or make/model is blank (docs 6.5).
BLANK_FIELD_KEEP_N = "keep"                  # the N stands
BLANK_FIELD_APPROVE = "approve"              # approved, payload exactly as before
BLANK_FIELD_APPROVE_CHECKED = "checked"      # approved: the check found no clear contradiction
BLANK_FIELD_APPROVE_BOTH_BLANK = "both"      # approved: nothing was filled in to contradict


def _blank_field_decision(
    output: dict, subcategory_null: bool, makemodel_null: bool, *, request_id: str = ""
) -> str:
    """What REQ-9 may do with a record whose subcategory or make/model is blank (docs 6.5).

    REQ-9 says a blank field has nothing to disagree with, so the record is approved at 75. It
    fired on every blank-field record that reached the lookup, whatever the make/model check had
    said about the field the customer DID fill: customer 1063's "MONITORS" with a blank make/model,
    photographed as a laptop, was judged "N: subcategory MONITORS conflicts with Laptop" and then
    approved with that N still in the payload — 208 records in the week to 2026-10-22, 64 passing.

    The N now stands in two cases. (1) The photo contradicts the make/model's brand (6.0, confirmed
    on the full-size uploads, 6.3): nothing after that rule may undo it, and
    _hold_makemodel_brand_conflict backs it. (2) Two independent signals agree that the filled field
    is wrong: the make/model call itself said N — having seen the photo description and been told a
    blank field is never a mismatch (subcat_model_blank_fields.md) — and the filled field names a
    known product family that neither the photo's asset label nor, when the name check passed, the
    confirmed name shares. Either signal alone was measured to fail good records: the call on its
    own rejected "Printer table with storage" for the printer standing on it; the keyword rules on
    their own read "SAFE LOCKER" as a cabinet against a Godrej safe. A kept type N is backed by the
    filled field reported `failed` (2.3) and flagged so the no-visible-brand withhold cannot clear
    it; the blank field reads `matched`, the "nothing to disagree with" reading of 2.2, so no status
    is null beside the verdict (2.1); the master-data row stays on show as the suggestion.
    """
    from asset_validator.core.config import (
        MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED,
        NULL_OVERRIDE_RESPECTS_PHOTO_ENABLED,
    )

    if not NULL_OVERRIDE_RESPECTS_PHOTO_ENABLED or output.get("subcatmodelmatch") != "N":
        return BLANK_FIELD_APPROVE
    if output.get("_makemodel_brand_conflict"):
        if subcategory_null and output.get("subcategorymatch") is None:
            output["subcategorymatch"] = "matched"
            output["subcategorymatchpercent"] = CLASSIFICATION_MATCHED_FLOOR_PERCENT
        logging.info(f"[{request_id}] Blank-field approval withheld: the photo contradicts the make/model's brand")
        return BLANK_FIELD_KEEP_N
    if subcategory_null and makemodel_null:
        return BLANK_FIELD_APPROVE_BOTH_BLANK
    if output.get("namedescriptionmatch") == "Y" and not MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED:
        return BLANK_FIELD_APPROVE      # that N came from the call without the photo description (6.4 off)
    if output.get("_makemodel_ai_verdict") != "N":
        return BLANK_FIELD_APPROVE_CHECKED  # a server keyword rule said N, not the call that read the photo
    filled_value = output.get("makemodel") if subcategory_null else output.get("subcategoryname")
    filled_families = scoped_families(_detect_product_categories(str(filled_value or "").lower()), output)
    photo_families = _detect_product_categories(str(output.get("detectedAsset") or "").lower())
    name_families = (
        _detect_product_categories(str(output.get("assetname") or "").lower())
        if output.get("namedescriptionmatch") == "Y" else set()
    )
    if not families_disjoint(filled_families, photo_families) or (
        name_families and not families_disjoint(filled_families, name_families)
    ):
        return BLANK_FIELD_APPROVE_CHECKED  # no clear product-type contradiction to stand on

    filled, blank = (
        ("subcategorymatch", "makemodelmatch") if makemodel_null
        else ("makemodelmatch", "subcategorymatch")
    )
    if output.get(filled) != "failed":
        output[filled] = "failed"
        output[f"{filled}percent"] = 15
    output[blank] = "matched"
    output[f"{blank}percent"] = CLASSIFICATION_MATCHED_FLOOR_PERCENT
    output["_makemodel_type_conflict"] = True
    logging.info(
        f"[{request_id}] Blank-field approval withheld: the make/model check rejected the filled "
        f"{'subcategory' if makemodel_null else 'make/model'}"
    )
    return BLANK_FIELD_KEEP_N


def _blank_field_path(output: dict, pc_result: PineconePhaseResult) -> dict:
    """The blank-field entry of the Decision path line (docs 2.9, 6.5): what the lookup returned and picked."""
    path = output.setdefault("_decision_path", {})
    if "blank_field" not in path:
        pick = pc_result.pick_row or {}
        path["blank_field"] = {
            "filter": pc_result.lookup_filter,
            "returned": len(pc_result.pool),
            "complete": pc_result.pool_complete,
            "pick": " / ".join(str(pick.get(k) or "") for k in ("subcategory", "makemodel"))[:80] if pick else None,
        }
    return path["blank_field"]


def _blank_field_failure_reasoning(
    subcategory_null: bool, makemodel_null: bool, subcategory: str, makemodel: str
) -> str:
    """Why a blank field failed, inside the 25-word reasoning budget: a long make/model is shortened, never the
    sentence cut."""
    shown = makemodel.split()
    while True:
        value = " ".join(shown)
        if subcategory_null and makemodel_null:
            text = (f"N: subcategory and make/model were left blank. Master data lists this asset under "
                    f"{subcategory} as {value}.")
        elif subcategory_null:
            text = f"N: subcategory was left blank. Master data lists this make/model under {subcategory}."
        else:
            text = f"N: make/model was left blank. Master data lists this asset as {value} under {subcategory}."
        if len(text.split()) <= SUBCATMODEL_REASONING_MAX_WORDS or len(shown) <= 1:
            return text
        shown = shown[:-1]


def _blank_field_failure_fields(
    row: dict, req_body: dict, subcategory_null: bool, makemodel_null: bool
) -> dict:
    """What a certain blank-field match writes: N at 25, the blank field `failed` at 15 so it backs the N (2.3), a
    filled field `matched` (it is the row's own, by id), and the row as the recommendation."""
    user_category = str(req_body.get("categoryname") or "").strip()
    subcategory = str(row.get("subcategory") or "").strip()
    makemodel = str(row.get("makemodel") or "").strip()
    # The customer's category is always right (6.6): the correction names it, never the row's spelling of it.
    category = (
        user_category if user_category and category_trusted() else str(row.get("category") or "").strip() or None
    )
    reconciled = reconcile_classification_matches(
        user_category=user_category,
        user_subcategory=str(req_body.get("subcategoryname") or "").strip(),
        user_makemodel=str(req_body.get("makemodel") or req_body.get("makemodelname") or "").strip(),
        user_model=str(req_body.get("model") or "").strip(),
        recommended_category=category,
        recommended_subcategory=subcategory,
        recommended_model=None,
        recommended_makemodel=makemodel,
    )
    reasoning = _blank_field_failure_reasoning(subcategory_null, makemodel_null, subcategory, makemodel)
    fields = {
        "subcatmodelmatch": "N",
        "subcatmodelmatchpercent": 25,
        "subcatmodelreasoning": reasoning,
        "recommendedcategory": category,
        "recommendedsubcategory": subcategory or None,
        "recommendedmakemodel": makemodel or None,
        "recommendedmodel": None,  # master-data rows carry no model
        "categorymatch": reconciled["categorymatch"],
        "categorymatchpercent": reconciled["categorymatchpercent"],
        "modelmatch": None,
        "modelmatchpercent": None,
        "_blank_field_master": {"subcategory": subcategory_null, "makemodel": makemodel_null},
    }
    for blank, status in ((subcategory_null, "subcategorymatch"), (makemodel_null, "makemodelmatch")):
        if blank:
            fields[status], fields[f"{status}percent"] = "failed", 15
        else:
            measured = reconciled[f"{status}percent"] if reconciled[status] == "matched" else None
            fields[status] = "matched"
            fields[f"{status}percent"] = max(measured or 0, CLASSIFICATION_MATCHED_FLOOR_PERCENT)
    return fields


def _blank_field_failure(
    output: dict, pc_result: PineconePhaseResult, subcategory_null: bool, makemodel_null: bool, req_body: dict
) -> tuple[dict | None, str]:
    """The fields a certain blank-field match writes, or (None, why there is none). Reads only; writes nothing."""
    if output.get("error") or "assetmakemodelmatch" in (output.get("_checks_incomplete") or []):
        return None, "a check did not complete"
    if output.get("subcatmodelmatch") != "Y":
        return None, "the make/model check did not approve the record"
    if output.get("_makemodel_brand_conflict"):
        return None, "the photo contradicts the make/model's brand"
    if output.get("namedescriptionmatch") == "N":
        return None, "the name check failed"
    narrowed = pc_result.lookup_filter or {}
    if subcategory_null and makemodel_null:
        if narrowed or to_int_or_none(req_body.get("categoryid")) is None:
            return None, "the lookup was not held to the record's category"
    elif ("makemodelid" if subcategory_null else "subcategoryid") not in narrowed:
        return None, "the lookup was not narrowed to the filled field's id"
    row, why = blank_field_master.certain_row(
        pc_result.pool,
        photo_text=blank_field_master.photo_text(output),
        name_text=blank_field_master.confirmed_name_text(output, req_body),
        subcategory_blank=subcategory_null,
        makemodel_blank=makemodel_null,
        complete=pc_result.pool_complete,
    )
    if row is None:
        return None, why
    if makemodel_null:
        # Two independent signals: the word test above names the most relatable row, and the selection's own pick
        # names the same product, at most less precisely ("HP" beside "HP ProBook 440 G8"). The correction is the most
        # relatable row, not simply the one the selection returned (owner, 2 Nov 2026); the pick's own spelling of the
        # same make/model stands.
        pick = pc_result.pick_row or {}
        pick_words = blank_field_master.identity_words(pick.get("makemodel"))
        row_words = blank_field_master.identity_words(row.get("makemodel"))
        if (not pick_words or not pick_words <= row_words
                or _scope_id(pick.get("subcategoryid")) != _scope_id(row.get("subcategoryid"))):
            return None, "the selection did not pick that make/model"
        if pick_words == row_words:
            row = pick
        # A brand counts only when it is on the asset itself, never another object in the photo (6, 6.5; review,
        # 2 Nov 2026): an HP laptop beside a Dell monitor must not fail with DELL as the correction.
        from asset_validator.core.config import SUGGESTION_RESPECTS_PHOTO_BRAND_ENABLED

        if SUGGESTION_RESPECTS_PHOTO_BRAND_ENABLED and row_brand_not_on_asset(output, row.get("makemodel")):
            return None, "the row names a brand the asset in the photo does not show"
    return _blank_field_failure_fields(row, req_body, subcategory_null, makemodel_null), why


def _fail_on_certain_master_row(
    output: dict,
    pc_result: PineconePhaseResult,
    subcategory_null: bool,
    makemodel_null: bool,
    req_body: dict,
    request_id: str = "",
) -> bool:
    """Fail a blank field that master data fills in with certainty, with that row as the correction (docs 6.5).

    The owner, 2 Nov 2026: "if you are flipping the result make sure you got the best match in master data; many
    times the user fills all data correctly when this is blank". So only a certain match fails: the make/model check
    and its server rules approved the filled field (Y before REQ-9), the photo contradicts no brand, the name did
    not fail, the lookup was narrowed to the filled field's id (with both blank, held to the category) and returned
    fewer rows than it asked for, and blank_field_master.certain_row names one row — for a make/model, the one the
    selection picked as well. AssetId 999100004 (matrix case 04): make/model id 27144, subcategory left blank, is
    filed only under BATTERIES5000, so the record fails recommending BATTERIES5000. True when it failed the record;
    the caller then skips REQ-9, which would set it straight back to Y 75. Any error keeps today's approval.
    """
    if not (BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED and BLANK_FIELD_CERTAIN_MATCH_FAILS_ENABLED):
        return False
    path = _blank_field_path(output, pc_result)
    try:
        fields, why = _blank_field_failure(output, pc_result, subcategory_null, makemodel_null, req_body)
    except Exception as exc:
        logging.warning(f"[{request_id}] Blank-field master-data check failed, the approval stands: {exc}")
        fields, why = None, f"error: {type(exc).__name__}"
    path["certain"], path["why"] = bool(fields), why[:120]
    if not fields:
        return False
    output.update(fields)
    enforce_match_percent_bounds(output)
    logging.info(f"[{request_id}] Blank field failed on a certain master-data row: {why}")
    return True


def _keep_blank_field_recommendation(
    output: dict,
    pc_result: PineconePhaseResult,
    subcategory_null: bool,
    makemodel_null: bool,
    req_body: dict,
    request_id: str = "",
) -> None:
    """Keep master data's best match for a field the customer left blank, for the pass to show (docs 6.5, 2.2).

    The owner, 2 Nov 2026: "search the detected asset under that category under the same customer id and recommend
    them". The selection's pick is kept for each blank field only where it is sound: the name did not fail, the row
    agrees with every filled field (by id, or by exact name without one), and blank_field_master.recommendation finds
    the photo supports it. _normalize_matched_classification puts it in the response; a filled field keeps
    echoing the customer's own value. Never raises.
    """
    if not BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED or output.get("subcatmodelmatch") != "Y":
        return
    path = _blank_field_path(output, pc_result)
    row = pc_result.pick_row
    try:
        # The row that names the asset most closely, not simply the selection's pick (owner, 2 Nov 2026: "check the
        # most relatable with the asset and then recommend").
        row = blank_field_master.most_relatable(
            pc_result.pool or [], row,
            photo_text=blank_field_master.photo_text(output),
            name_text=blank_field_master.confirmed_name_text(output, req_body),
            req_body=req_body, subcategory_blank=subcategory_null, makemodel_blank=makemodel_null,
        )
        # A name that failed only for saying nothing (5.7) leaves the photo as the only evidence, so its blank fields
        # still get master data's best match (owner, 2 Nov 2026: "search the detected asset ... and recommend them").
        if output.get("namedescriptionmatch") == "N" and not output.get("_placeholder_name_reason"):
            values, why = {}, "the name check failed"
        elif not row:
            values, why = {}, "the selection picked no returned row"
        elif output.get("_recommendation_withdrawn"):
            values, why = {}, "the row was withdrawn as not fitting the photo"
        elif not blank_field_master.agrees_with_filled(
            row, req_body, subcategory_blank=subcategory_null, makemodel_blank=makemodel_null
        ):
            values, why = {}, "the row disagrees with a filled field"
        else:
            values, why = blank_field_master.recommendation(
                row,
                photo_text=blank_field_master.photo_text(output),
                name_text=blank_field_master.confirmed_name_text(output, req_body),
                subcategory_blank=subcategory_null,
                makemodel_blank=makemodel_null,
            )
            # Never a make/model whose brand contradicts the brand the asset shows (6; owner, 2 Nov 2026).
            from asset_validator.core.config import SUGGESTION_RESPECTS_PHOTO_BRAND_ENABLED

            if (SUGGESTION_RESPECTS_PHOTO_BRAND_ENABLED and values.get("recommendedmakemodel")
                    and suggestion_names_another_brand(
                        {**output, "recommendedmakemodel": values["recommendedmakemodel"], "recommendedmodel": None})):
                values = {k: v for k, v in values.items() if k != "recommendedmakemodel"}
                why = "; ".join(filter(None, [why, "the make/model names another brand than the photo shows"]))
    except Exception as exc:
        logging.warning(f"[{request_id}] Blank-field recommendation failed, none shown: {exc}")
        values, why = {}, f"error: {type(exc).__name__}"
    path["recommended"] = values or (f"none: {why}"[:120] if why else None)
    if values:
        output["_blank_field_recommendation"] = values


def _merge_pinecone_result(
    output: dict,
    pc_result: PineconePhaseResult,
    timer: PhaseTimer,
    subcategory_null: bool,
    makemodel_null: bool,
    brand_reader=None,
    request_id: str = "",
    req_body: dict | None = None,
    vision_llm: dict | None = None,
) -> None:
    output["recommendedcategory"] = pc_result.recommendedcategory
    output["recommendedsubcategory"] = pc_result.recommendedsubcategory
    output["recommendedmakemodel"] = pc_result.recommendedmakemodel
    output["makemodelmatch"] = pc_result.makemodelmatch
    output["makemodelmatchpercent"] = pc_result.makemodelmatchpercent
    output["recommendedmodel"] = pc_result.recommendedmodel
    output["categorymatch"] = pc_result.categorymatch
    output["categorymatchpercent"] = pc_result.categorymatchpercent
    output["subcategorymatch"] = pc_result.subcategorymatch
    output["subcategorymatchpercent"] = pc_result.subcategorymatchpercent
    output["modelmatch"] = pc_result.modelmatch
    output["modelmatchpercent"] = pc_result.modelmatchpercent
    enforce_classification_match_percent_bounds(output)
    _withdraw_recommendation_outside_photo_type(output, req_body, request_id=request_id)
    _withdraw_suggestion_of_another_brand(output, vision_llm, request_id=request_id)
    enrich_subcatmodel_reasoning_with_pinecone(output)
    timer.phases_ms.update(pc_result.phase_timings)
    if pc_result.apply_null_override:
        decision = _blank_field_decision(output, subcategory_null, makemodel_null, request_id=request_id)
        # A blank field master data fills in with certainty fails, with that row as the correction; REQ-9 would set
        # it straight back to Y 75, so it is skipped (owner, 2026-11-02; docs 6.5).
        if decision != BLANK_FIELD_KEEP_N and not _fail_on_certain_master_row(
            output, pc_result, subcategory_null, makemodel_null, req_body or {}, request_id
        ):
            over_an_n = str(output.get("subcatmodelreasoning") or "").startswith("N")
            apply_null_subcat_override(output, subcategory_null, makemodel_null)
            # The make/model call's "N: ... type mismatch" no longer sits beside the Y (6.5) — and a
            # reason is only given where it is true.
            if over_an_n and decision == BLANK_FIELD_APPROVE_CHECKED:
                blank = "subcategory" if subcategory_null else "make/model"
                output["subcatmodelreasoning"] = (
                    f"Y: {blank} was left blank, and nothing filled in clearly contradicts the photo."
                )
            elif over_an_n and decision == BLANK_FIELD_APPROVE_BOTH_BLANK:
                output["subcatmodelreasoning"] = (
                    "Y: subcategory and make/model were left blank, so there is nothing to compare."
                )
            # The pass shows master data's best match for the blank field, where the photo supports it (6.5, 2.2).
            _keep_blank_field_recommendation(
                output, pc_result, subcategory_null, makemodel_null, req_body or {}, request_id
            )
    _apply_exact_master_match_lift(output, pc_result, subcategory_null, makemodel_null)
    _withhold_makemodel_without_visible_brand(
        output, brand_reader, request_id=request_id, req_body=req_body, vision_llm=vision_llm
    )


@reads_record_product_types
def _run_post_phase1(
    *,
    output: dict,
    gemini_output: dict,
    req_body: dict,
    text_model,
    prefetch_embed_state: dict,
    barcode_image_b64: str | None,
    user_asset_name: str,
    user_description: str,
    user_cost: float | None,
    acquisition_date: date | None,
    has_asset_image: bool,
    inc_pinecone: bool,
    subcategory_null: bool,
    makemodel_null: bool,
    request_id: str,
    logger: StructuredLogger,
    timer: PhaseTimer,
) -> None:
    """Sequential post-Phase-1: tags, optional Pinecone (text-only LLM), cost from Phase 1."""
    _log_barcode_unreadable(barcode_image_b64, gemini_output, request_id)
    apply_tag_detection_to_output(output, gemini_output, None, request_id)

    _apply_cost_from_phase1(
        output,
        gemini_output,
        user_cost=user_cost,
        acquisition_date=acquisition_date,
        has_asset_image=has_asset_image,
        request_id=request_id,
        user_category=str(req_body.get("categoryname") or "").strip(),
        user_subcategory=str(req_body.get("subcategoryname") or "").strip(),
        user_asset_name=user_asset_name,
        priced_natively=market_for_request(req_body or {}, request_id)[1],
        user_description=user_description,
        user_makemodel=str(req_body.get("makemodel") or req_body.get("makemodelname") or "").strip(),
        user_model=str(req_body.get("model") or "").strip(),
    )

    from asset_validator.core.validation_plan import ValidationPlan

    legacy_plan = ValidationPlan.from_flags({"assetmakemodelmatch": True})

    if _should_run_pinecone(inc_pinecone, output, subcategory_null, makemodel_null, legacy_plan):
        try:
            search_text = _build_pinecone_search_text(
                legacy_plan,
                user_asset_name,
                user_description,
                output.get("detectedAsset", "") or "",
                output.get("imageAnalysis", "") or "",
            )
            phase1_embed = (
                start_speculative_embedding(search_text, request_id)
                if search_text
                else None
            )
            pc_result = _run_pinecone_phase(
                output,
                req_body,
                text_model,
                prefetch_embed_state,
                phase1_embed,
                timer,
                logger,
                request_id,
                subcategory_null,
                makemodel_null,
                legacy_plan,
            )
            _merge_pinecone_result(
                output, pc_result, timer, subcategory_null, makemodel_null,
                request_id=request_id, req_body=req_body,
            )
        except Exception as e:
            logging.error(f"[{request_id}] Pinecone phase failed: {e}")
    elif not inc_pinecone and not output.get("error"):
        output["subcatmodelmatch"] = "Y"
        output["subcatmodelmatchpercent"] = 75


@reads_record_product_types
def _run_post_checks(
    *,
    output: dict,
    check_results: dict,
    plan,
    req_body: dict,
    vision_model,
    asset_b64: str | None,
    barcode_b64: str | None,
    text_model,
    prefetch_embed_state: dict | None,
    user_asset_name: str,
    user_description: str,
    user_cost: float | None,
    acquisition_date: date | None,
    has_asset_image: bool,
    inc_pinecone: bool,
    subcategory_null: bool,
    makemodel_null: bool,
    request_id: str,
    logger: StructuredLogger,
    timer: PhaseTimer,
    decoded_codes: list[dict] | None = None,
    view_job=None,
):
    """Scoped post-processing — only for enabled checks.

    Returns the industrial name decision when it runs in shadow mode (5.6), for the caller to
    finish before the response; otherwise None.
    """
    path = output.setdefault("_decision_path", {})
    shadow_confirm = None
    if plan.is_enabled("tagnumbercheck"):
        tag_result = check_results.get("tagnumbercheck")
        if tag_result and tag_result.error:
            prior_error = output.get("error")
            apply_check_failure_defaults(output, "tagnumbercheck", tag_result.error, request_id)
            # A decoded barcode matching the registered tag settles the check the call could
            # not. The error it recorded is then withdrawn: downstream steps skip on "error",
            # and the tag check has in fact completed. A later failed check sets its own.
            if DECODED_TAG_RESCUE_ENABLED and settle_failed_tag_check_from_decode(
                output, decoded_codes, request_id
            ):
                output["error"] = prior_error
        elif tag_result:
            llm_out = tag_result.llm_output
            output.update({
                k: v
                for k, v in llm_out.items()
                if k not in ("error", "imageReadability")
            })
            output["barcodeposition"] = format_barcode_position(output.get("barcodeposition"))
            retry_output = None
            if effective_barcode_retry_on_unreadable() and (barcode_b64 or asset_b64):
                apply_tag_detection_to_output(
                    output, llm_out, None, request_id, decoded_codes=decoded_codes
                )
                if _tag_needs_reread(output):
                    retry_output = retry_barcode_read(
                        vision_model, barcode_b64 or "", request_id, asset_b64=asset_b64,
                        barcode_original_b64=(req_body or {}).get("barcodeimage"),
                        asset_original_b64=(req_body or {}).get("assetimage"),
                        registered=output.get("tagnumber"),
                    )
                    apply_tag_detection_to_output(
                        output, llm_out, retry_output, request_id,
                        decoded_codes=decoded_codes,
                    )
            else:
                apply_tag_detection_to_output(
                    output, llm_out, retry_output, request_id, decoded_codes=decoded_codes
                )

    vision_result = check_results.get(ASSET_VISION_KEY)
    if vision_result and not vision_result.error:
        _merge_asset_vision_output(output, vision_result.llm_output)
        timer.phases_ms[f"{ASSET_VISION_KEY}_ms"] = vision_result.timing_ms

    # One per request, shared by the name and make/model brand rules; it reads nothing unless
    # one of them finds a brand conflict (6.3).
    brand_confirm = _make_brand_conflict_confirmer(
        vision_model, asset_b64, barcode_b64, req_body, output, request_id
    )

    if plan.is_enabled("assetnamedescriptionmatch"):
        timer.start("assetnamedescriptionmatch_ms")
        name_result = run_name_match_phase2(
            vision_model=vision_model,
            asset_b64=asset_b64,
            barcode_b64=barcode_b64,
            user_asset_name=user_asset_name,
            user_description=user_description,
            detected_asset=str(output.get("detectedAsset") or "").strip(),
            image_analysis=str(output.get("imageAnalysis") or "").strip(),
            barcode_position=_barcode_position_text(output),
            request_id=request_id,
            req_body=req_body,
        )
        timer.stop("assetnamedescriptionmatch_ms")
        if name_result.error:
            apply_check_failure_defaults(
                output, "assetnamedescriptionmatch", name_result.error, request_id
            )
        else:
            vision_llm = (
                vision_result.llm_output
                if vision_result and not vision_result.error
                else name_result.llm_output
            )
            path["phase1_detected"] = str(output.get("detectedAsset") or "")[:80]
            path["name_ai"] = name_result.llm_output.get("namedescriptionmatch")
            _settle_name_check(
                output, name_result.llm_output, user_asset_name, vision_llm,
                confirm_brand=brand_confirm,
            )
            path["name_after_rules"] = output.get("namedescriptionmatch")
            name_llm_out = name_result.llm_output

            # An industrial record's name is decided from a photo reading that is not told the record,
            # then a judge (5.6). Placed here so make/model and cost, which run below, read the decided
            # name; when it decides, the stronger-model re-read below is not needed.
            views = (lambda: view_job.result(BLIND_VIEW_TIMEOUT_SEC)) if view_job is not None else {}
            shadow_confirm, decided = _decide_industrial_name(
                output=output,
                req_body=req_body,
                name_llm_out=name_llm_out,
                vision_llm=vision_llm,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                user_asset_name=user_asset_name,
                views=views,
                brand_confirm=brand_confirm,
                request_id=request_id,
                timer=timer,
            )

            # An industrial record the first photo read could not recognise is read again by a
            # stronger model, and the name check re-run on that description (see
            # _needs_industrial_vision_escalation). Placed here so make/model and cost, which
            # run below, read the corrected description without a call of their own.
            if not decided and _needs_industrial_vision_escalation(output, req_body, views, vision_model):
                timer.start("vision_escalation_ms")
                rescued = _reread_photo_with_stronger_model(
                    output=output,
                    req_body=req_body,
                    asset_b64=asset_b64,
                    barcode_b64=barcode_b64,
                    request_id=request_id,
                )
                timer.stop("vision_escalation_ms")
                path["escalated"] = bool(rescued)
                if rescued:
                    path["escalated_detected"] = str(output.get("detectedAsset") or "")[:80]
                    retry_name = run_name_match_phase2(
                        vision_model=vision_model,
                        asset_b64=asset_b64,
                        barcode_b64=barcode_b64,
                        user_asset_name=user_asset_name,
                        user_description=user_description,
                        detected_asset=str(output.get("detectedAsset") or "").strip(),
                        image_analysis=str(output.get("imageAnalysis") or "").strip(),
                        barcode_position=_barcode_position_text(output),
                        request_id=request_id,
                        req_body=req_body,
                    )
                    if not retry_name.error:
                        # The new description is already in `output`, which is what the server
                        # rules read; the stale phase-1 answer is deliberately not passed again.
                        _settle_name_check(
                            output, retry_name.llm_output, user_asset_name, retry_name.llm_output,
                            confirm_brand=brand_confirm,
                        )
                        path["name_ai_retry"] = retry_name.llm_output.get("namedescriptionmatch")
                        path["name_after_rules"] = output.get("namedescriptionmatch")

    # A brand the name rules found misread is corrected before make/model and cost read it (6.3).
    _relabel_confirmed_brand(output, brand_confirm)

    if plan.is_enabled("assetmakemodelmatch"):
        timer.start("assetmakemodelmatch_ms")
        use_user_identity = _resolve_use_user_identity(plan, output)
        user_subcategory = str(req_body.get("subcategoryname") or "").strip()
        user_model_raw = str(req_body.get("model") or "").strip()
        user_model_for_check = resolve_user_model_for_subcat_check(
            user_model_raw, user_subcategory
        )
        make_result = run_make_model_phase2(
            vision_model=vision_model,
            asset_b64=asset_b64,
            barcode_b64=barcode_b64,
            user_category=str(req_body.get("categoryname") or "").strip(),
            user_subcategory=user_subcategory,
            user_makemodel=str(
                req_body.get("makemodel") or req_body.get("makemodelname") or ""
            ).strip(),
            user_model=user_model_for_check,
            detected_asset=str(output.get("detectedAsset") or "").strip(),
            image_analysis=str(output.get("imageAnalysis") or "").strip(),
            include_user_identity=use_user_identity,
            user_asset_name=user_asset_name,
            user_description=user_description,
            request_id=request_id,
        )
        timer.stop("assetmakemodelmatch_ms")
        if make_result.error:
            apply_check_failure_defaults(output, "assetmakemodelmatch", make_result.error, request_id)
        else:
            llm_out = make_result.llm_output
            for key in llm_out:
                if key not in ("error",):
                    output[key] = llm_out[key]
            # The call's own verdict, before the server rules below adjust it (6.5 reads it).
            output["_makemodel_ai_verdict"] = llm_out.get("subcatmodelmatch")
            vision_llm: dict = {}
            if vision_result and not vision_result.error:
                vision_llm = vision_result.llm_output
            enforce_identity_confidence(output, vision_llm)
            apply_generation_ambiguity_cap(output, vision_llm)
            enforce_user_classification_vs_vision(output, req_body, vision_llm)
            reconcile_subcatmodel_with_vision(output, req_body)
            strengthen_subcatmodel_y_reasoning(output, req_body)
            enforce_makemodel_vs_subcategory_contradiction(output, req_body)
            if MAKEMODEL_BRAND_CHECK_ENABLED:
                enforce_makemodel_brand_vs_vision(
                    output, req_body, vision_llm, confirm_brand=brand_confirm
                )
                _relabel_confirmed_brand(output, brand_confirm, req_body)
            enforce_match_percent_bounds(output)

            if inc_pinecone and text_model and not output.get("error"):
                if _should_run_pinecone(inc_pinecone, output, subcategory_null, makemodel_null, plan):
                    try:
                        # A blank field is looked up with what the photo shows, never the customer's name, and the
                        # selection reads the photo alone (owner, 2026-11-02; docs 6.5).
                        search_identity = use_user_identity and not (
                            BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED and (subcategory_null or makemodel_null)
                        )
                        search_text = _build_pinecone_search_text(
                            plan,
                            user_asset_name,
                            user_description,
                            output.get("detectedAsset", "") or "",
                            output.get("imageAnalysis", "") or "",
                            use_user_identity=search_identity,
                        )
                        phase1_embed = (
                            start_speculative_embedding(search_text, request_id)
                            if search_text
                            else None
                        )
                        pc_result = _run_pinecone_phase(
                            output,
                            req_body,
                            text_model,
                            prefetch_embed_state,
                            phase1_embed,
                            timer,
                            logger,
                            request_id,
                            subcategory_null,
                            makemodel_null,
                            plan,
                            use_user_identity=search_identity,
                        )
                        _merge_pinecone_result(
                            output, pc_result, timer, subcategory_null, makemodel_null,
                            brand_reader=_make_brand_reader(
                                vision_model, asset_b64, request_id, output
                            ),
                            request_id=request_id,
                            req_body=req_body,
                            # The phase-1 reading Gate B used above, for the withhold's type check (6).
                            vision_llm=vision_llm,
                        )
                    except Exception as e:
                        logging.error(f"[{request_id}] Pinecone phase failed: {e}")
                elif output.get("subcatmodelmatch") == "Y":
                    _clear_pinecone_recommendations(output)
                elif not inc_pinecone:
                    output["subcatmodelmatch"] = "Y"
                    output["subcatmodelmatchpercent"] = 75
            _hold_makemodel_brand_conflict(output)

    if plan.is_enabled("acquisitiondatecostmatch"):
        if _should_skip_cost_llm(req_body, output):
            # Tier-1 (Land & Building / intangibles / CWIP): apply_cost_category_policy
            # nulls every cost figure for these anyway, so both the cost_check and
            # cost_refine calls would be paid for and discarded. Produce the same result
            # server-side instead. ~8,100 input + ~690 output tokens saved per record.
            logging.info(
                f"[{request_id}] Cost LLM skipped: category policy 'skip' "
                f"(no cost figures are returned for this class)"
            )
            _apply_cost_skip_without_llm(
                output,
                user_cost=user_cost,
                acquisition_date=acquisition_date,
            )
            timer.phases_ms["acquisitiondatecostmatch_ms"] = 0.0
            cost_result = None
        elif should_defer_cost_check(plan):
            use_identity = _resolve_use_user_identity(plan, output)
            cost_result = run_cost_check(
                vision_model=vision_model,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                req_body=req_body,
                acquisition_date=acquisition_date,
                request_id=request_id,
                user_asset_name=user_asset_name,
                user_description=user_description,
                use_user_identity=use_identity,
                detected_asset=str(output.get("detectedAsset") or "").strip(),
                image_analysis=str(output.get("imageAnalysis") or "").strip(),
            )
        else:
            cost_result = check_results.get("acquisitiondatecostmatch")

        # cost_result is None only on the Tier-1 skip path, where the result is already set.
        if cost_result is not None:
            _apply_acquisition_cost_check(
                output=output,
                cost_result=cost_result,
                plan=plan,
                vision_model=vision_model,
                asset_b64=asset_b64,
                barcode_b64=barcode_b64,
                user_asset_name=user_asset_name,
                user_description=user_description,
                user_cost=user_cost,
                acquisition_date=acquisition_date,
                has_asset_image=has_asset_image,
                request_id=request_id,
                timer=timer,
                user_category=str(req_body.get("categoryname") or "").strip(),
                user_subcategory=str(req_body.get("subcategoryname") or "").strip(),
                user_makemodel=str(
                    req_body.get("makemodel") or req_body.get("makemodelname") or ""
                ).strip(),
                user_model=str(req_body.get("model") or "").strip(),
                req_body=req_body,
            )

    if plan.is_enabled("assetcondition"):
        cond_result = check_results.get("assetcondition")
        if cond_result and cond_result.error:
            apply_check_failure_defaults(output, "assetcondition", cond_result.error, request_id)
        elif cond_result:
            llm_out = cond_result.llm_output
            if "condition" in llm_out:
                output["condition"] = llm_out["condition"]
            _merge_detected_description(output, llm_out)
            if "detectedAsset" in llm_out and not output.get("detectedAsset"):
                output["detectedAsset"] = llm_out["detectedAsset"]
            _normalize_condition_output(output)

    return shadow_confirm


def run_asset_analysis(req: func.HttpRequest) -> func.HttpResponse:
    request_id = str(__import__("uuid").uuid4())[:8]
    start_time = time.time()
    logger = StructuredLogger(request_id)
    log_memory_usage(request_id, "start")

    try:
        raw_body = req.get_json()
    except ValueError:
        return create_error_response("Invalid JSON format in request body", 400, request_id)

    try:
        validated = AssetAnalysisRequest.model_validate(raw_body)
    except ValidationError as e:
        messages = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        return create_error_response(f"Request validation failed: {messages}", 400, request_id)

    plan = validated.to_validation_plan()

    # Log the asset identifiers at the START — so requests are searchable by asset/tag
    # number, and any that log this but never log "Request completed" are the silent
    # (no-output) ones.
    logger.info(
        "Request started",
        assetid=validated.assetid,
        assettaggingdetailid=validated.assettaggingdetailid,
        tagnumber=validated.tagnumber,
        assetnumber=validated.assetnumber,
        customerid=validated.customerid,
    )
    req_body = validated.to_request_body()
    req_body["taggingai"] = validated.taggingai

    timer = PhaseTimer()
    start_request_usage()
    if not plan.requires_ai(validated.taggingai):
        return _build_no_ai_response(
            req_body,
            plan,
            request_id=request_id,
            start_time=start_time,
            timer=timer,
            logger=logger,
        )

    user_asset_name = str(req_body.get("assetname") or "").strip()
    user_description = normalize_user_description(req_body.get("description"))
    user_subcategory = str(req_body.get("subcategoryname") or "").strip()
    user_makemodel = str(
        req_body.get("makemodel") or req_body.get("makemodelname") or ""
    ).strip()
    subcategory_null = not user_subcategory
    makemodel_null = not user_makemodel

    asset_image_b64 = req_body.get("assetimage")
    barcode_image_b64 = req_body.get("barcodeimage")

    prefetch_embed_state = None
    if plan.requires_pinecone():
        # A blank field is looked up with the photo alone (docs 6.5), so its name is never embedded.
        if plan.is_enabled("assetnamedescriptionmatch") and not (
            BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED and (subcategory_null or makemodel_null)
        ):
            prefetch_text = f"{user_asset_name} {user_description}".strip()
            prefetch_embed_state = start_speculative_embedding(prefetch_text, request_id)
        else:
            prefetch_embed_state = start_speculative_embedding("", request_id)

    # Oversized images are dropped individually — one big upload must not sink the
    # request when the other image is fine.
    if asset_image_b64:
        try:
            validate_base64_size_first(asset_image_b64, 10)
        except ValueError as e:
            logging.warning(f"[{request_id}] Dropping oversized asset image: {e}")
            asset_image_b64 = None
    if barcode_image_b64:
        try:
            validate_base64_size_first(barcode_image_b64, 10)
        except ValueError as e:
            logging.warning(f"[{request_id}] Dropping oversized barcode image: {e}")
            barcode_image_b64 = None

    output = build_initial_output(req_body, plan)
    inc_pinecone = include_pinecone() and plan.is_enabled("assetmakemodelmatch")

    timer.start("image_prep_ms")
    decoded_codes: list[dict] = []
    try:
        asset_image_b64, barcode_image_b64, decoded_codes = (
            prepare_images_and_decode_parallel(
                asset_image_b64,
                barcode_image_b64,
                user_tagnumber=req_body.get("TagNumber"),
                request_id=request_id,
            )
        )
    except Exception:
        timer.stop("image_prep_ms")
        clear_request_usage()
        return create_error_response("Image processing failed", 500, request_id)
    timer.stop("image_prep_ms")

    # Internal only (stripped before the response): lets reconcile_asset_image_readability
    # tell a featureless frame from a real one when the model returns no verdict.
    from asset_validator.services.image_service import take_asset_image_detail

    output["_asset_image_detail"] = take_asset_image_detail(request_id)
    output["_request_id"] = request_id
    output["_barcode_image_present"] = bool(barcode_image_b64)

    # Only error out when EVERY provided image failed to decode — there is nothing
    # left to validate. A single bad image no longer aborts the request.
    if not asset_image_b64 and not barcode_image_b64:
        clear_request_usage()
        return create_error_response(
            "All provided images were unreadable or invalid",
            400,
            request_id,
            details={"phase": "image_processing"},
        )

    view_slots = _view_slots(asset_image_b64, barcode_image_b64)
    view_job = None
    recapture_job = None
    shadow_confirm = None
    try:
        vision_model, text_model = get_shared_models()
        # Started BEFORE the main vision call and joined after the checks, so the
        # name-free classification overlaps work that is already happening.
        view_job = _start_blind_view(plan, vision_model, view_slots, request_id)
        recapture_job = _start_recapture_detection(plan, vision_model, view_slots, request_id)
        timer.start("checks_ms")
        check_summary = run_enabled_checks(
            plan,
            vision_model=vision_model,
            asset_b64=asset_image_b64,
            barcode_b64=barcode_image_b64,
            req_body=req_body,
            acquisition_date=validated.acquisitiondate,
            request_id=request_id,
        )
        timer.stop("checks_ms")
        timer.phases_ms.update(check_summary.phase_timings)
        logger.log_api_call("gemini_vision_checks", timer.phases_ms.get("checks_ms", 0))

        shadow_confirm = _run_post_checks(
            output=output,
            check_results=check_summary.results,
            plan=plan,
            req_body=req_body,
            vision_model=vision_model,
            asset_b64=asset_image_b64,
            barcode_b64=barcode_image_b64,
            text_model=text_model,
            prefetch_embed_state=prefetch_embed_state,
            user_asset_name=user_asset_name,
            user_description=user_description,
            user_cost=validated.cost,
            acquisition_date=validated.acquisitiondate,
            has_asset_image=bool(asset_image_b64),
            inc_pinecone=inc_pinecone,
            subcategory_null=subcategory_null,
            makemodel_null=makemodel_null,
            request_id=request_id,
            logger=logger,
            timer=timer,
            decoded_codes=decoded_codes,
            view_job=view_job,
        )

    except Exception as e:
        logging.error(f"Vision checks failed: {e}")
        clear_request_usage()
        return create_error_response(
            "Gemini Vision API failed after multiple attempts",
            503,
            request_id,
            details={"phase": "gemini_vision"},
        )

    if "barcodeposition" in output:
        output["barcodeposition"] = format_barcode_position(output.get("barcodeposition"))

    apply_detected_description_policy(output, plan)
    reconcile_asset_image_readability(output)
    apply_cost_category_policy(
        output,
        req_body,
        cost_check_enabled=plan.is_enabled("acquisitiondatecostmatch"),
    )
    round_cost_fields(output)

    # Whether ANY provided photo shows the asset, decided by a call that saw only pixels.
    # The main vision call cannot be trusted with this: it is shown the declared record
    # first, and when a photo does not settle the question it reports the record — a black
    # frame became "a paper shredder", a tag sticker became "a centrifugal pump mounted on a
    # base frame". Placed after the cost policy on purpose, like the single-image policy
    # below: a failure decided here must not be undone by a later check passing.
    from asset_validator.core.config import BLIND_VIEW_TIMEOUT_SEC

    views = view_job.result(BLIND_VIEW_TIMEOUT_SEC) if view_job is not None else {}
    apply_blind_view_policy(output, plan, views, provided_slots=set(view_slots))

    # A photo of a screen or printout of the asset, rather than of the asset. Needs blind view's
    # answer to know which photo shows the asset, so it is applied straight after it. It changes
    # only imageReadability — the other checks ran on the asset's likeness and keep their results.
    # Placed before the pixel gate so a genuinely blank frame still overrides it.
    from asset_validator.core.config import RECAPTURE_ENFORCE, RECAPTURE_MIN_CONFIDENCE

    recapture = (
        recapture_job.result(BLIND_VIEW_TIMEOUT_SEC) if recapture_job is not None else {}
    )
    apply_recapture_policy(
        output,
        plan,
        recapture,
        views,
        enforce=RECAPTURE_ENFORCE,
        min_confidence=RECAPTURE_MIN_CONFIDENCE,
        request_id=request_id,
    )

    # Only one image provided → fail the checks that need the MISSING photo (asset or tag).
    # Runs last so it overrides any check that otherwise passed on the single image.
    apply_single_image_policy(
        output,
        plan,
        asset_image_present=bool(asset_image_b64),
        barcode_image_present=bool(barcode_image_b64),
    )

    # Last word on readability, measured from the PIXELS of the photo that shows the asset.
    # A deterministic backstop to the classifier above for frames that are objectively
    # empty — black, or one flat tone — however confidently anything describes them.
    if output.get("missing_image") != "asset" and any(
        plan.is_enabled(c) for c in ASSET_LANE_CHECKS
    ):
        from asset_validator.services.image_service import is_b64_frame_unusable

        unusable = is_b64_frame_unusable(
            _asset_photo_b64(views, view_slots, asset_image_b64), request_id, "asset"
        )
        if unusable:
            logging.warning(
                f"[{request_id}] Forcing imageReadability=E: asset frame {unusable}"
            )
            # A photo meant to show the asset WAS sent — it is unusable, not missing — so
            # the customer is told to retake it rather than that none was uploaded.
            output["_asset_image_provided"] = True
            fail_asset_side(output, plan, ASSET_PHOTO_UNUSABLE, detail=unusable)

    _normalize_matched_classification(output)
    _trust_customer_category(output)

    verdict, reasons = compute_final_verdict(output, enabled_checks=plan.enabled)
    output["finalverdict"] = verdict
    output["finalverdict_reason"] = reasons
    output["msg"] = build_failure_msg(output, enabled_checks=plan.enabled)
    output["llm_provider"] = active_llm_provider()

    # Logging only: the shadow industrial name decision (5.6) and the decision path (2.9) change no field.
    if shadow_confirm is not None:
        with product_types_scope(req_body):   # an industrial record reads the list of before here too (6.1)
            shadow_confirm.finish(output, INDUSTRIAL_PHOTO_DECISION_SHADOW_WAIT_SEC)
    _log_decision_path(logger, output, req_body)

    # Combine name/cost reasoning before stripping internal LLM fields.
    output = finalize_response_output(output, plan)
    _strip_internal_response_fields(output)

    total_time = time.time() - start_time
    timer.phases_ms["total_ms"] = round(total_time * 1000, 2)

    api_usage = get_api_usage_tracker()
    token_usage = get_request_usage()
    usage_log: dict[str, Any] = {}
    if token_usage:
        totals = token_usage.totals()
        usage_log = {
            "llm_prompt_tokens": totals["prompt_tokens"],
            "llm_completion_tokens": totals["completion_tokens"],
            "llm_total_tokens": totals["total_tokens"],
            "llm_calls": totals["llm_calls"],
            "llm_cached_tokens": totals["cached_tokens"],
            "llm_uncached_prompt_tokens": totals["uncached_prompt_tokens"],
            "llm_cache_hit_percent": totals["cache_hit_percent"],
            "llm_usage_by_phase": token_usage.by_phase(),
        }

    logger.log_request_complete(
        total_time_ms=timer.phases_ms["total_ms"],
        status_code=200,
        api_calls_today=api_usage["daily_calls"],
        **usage_log,
        **timer.to_dict(),
    )
    # Full response body — off by default; flip LOG_RESPONSE_BODY App Setting to inspect
    # exactly what the caller receives. No images/secrets are in the output.
    if log_response_body():
        logger.info("Response body", assetid=validated.assetid, response=output)
    clear_request_usage()

    return func.HttpResponse(
        json.dumps(output),
        mimetype="application/json",
        status_code=200,
        headers={
            "X-Request-ID": request_id,
            "X-Response-Time": f"{total_time:.2f}s",
            "X-Phase-Timing": json.dumps(timer.to_dict()),
            "X-Memory-Usage": get_memory_header(),
            "X-API-Calls-Today": str(api_usage["daily_calls"]),
            "X-API-Calls-Hour": str(api_usage["hourly_calls"]),
            "X-LLM-Provider": active_llm_provider(),
        },
    )
