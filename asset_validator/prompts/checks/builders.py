"""Per-check and dual-lane prompt builders — only invoked when the matching flag is true."""

from __future__ import annotations

import re

from datetime import date

from asset_validator.core.validation_plan import ValidationPlan
from asset_validator.core.market import MarketProfile
from asset_validator.prompts.cost_date import (
    build_cost_date_extension,
    load_cost_output_schema,
)
from asset_validator.prompts.loader import (
    hoist_variable_context,
    load_barcode_section,
    load_prompt,
)
from asset_validator.utils.helpers import truncate_for_phase2

REASONING_EXTENSION = load_prompt("reasoning.md")
REASONING_DAMAGE = load_prompt("reasoning_damage.md")


def _condition_extension() -> str:
    """Condition rules — concise by default, verbose when CONDITION_DETAIL_LEVEL=full.

    Read per call rather than at import so the App Setting takes effect without a redeploy.
    """
    from asset_validator.core.config import condition_detail_minimal

    name = "condition_minimal.md" if condition_detail_minimal() else "condition.md"
    return load_prompt(name)


# Legacy importers (phase1, supplemental) still reference the verbose text directly.
CONDITION_EXTENSION = load_prompt("condition.md")


def build_asset_identification(
    *,
    user_asset_name: str = "",
    user_description: str = "",
    user_category: str = "",
    user_subcategory: str = "",
) -> str:
    """
    Vision identification prompt with the request's expected asset injected.

    Photos routinely contain several assets; the hint lets the model pick the right
    one among them. The prompt forbids inventing an asset that is not visible, so a
    genuine mismatch still surfaces as a mismatch.

    Records naming an in-pit or in-ground vessel as the asset get one extra rule, inserted
    ahead of the identification steps. Every other record receives the shared prompt
    byte-for-byte: the same rule added for everyone moved an unrelated display record from
    10/10 passing to 3/10 (see utils/helpers.names_in_ground_vessel).
    """
    from asset_validator.utils.helpers import names_in_ground_vessel

    prompt = load_prompt(
        "vision_identification.md",
        expected_asset_name=user_asset_name.strip() or "(not provided)",
        expected_description=user_description.strip() or "(not provided)",
        expected_category=user_category.strip() or "(not provided)",
        expected_subcategory=user_subcategory.strip() or "(not provided)",
    )
    if names_in_ground_vessel(user_asset_name, user_subcategory):
        prompt = prompt.replace(
            _IDENTIFICATION_HEADER,
            load_prompt("vision_in_ground_vessel.md") + _IDENTIFICATION_HEADER,
            1,
        )
    return prompt


# The in-ground vessel rule sits immediately before this header — after the civil/structure
# exception, which it extends, and before the identification steps it qualifies.
_IDENTIFICATION_HEADER = "=== ASSET IDENTIFICATION ==="


# Back-compat for callers that never had request context to inject.
DUAL_ASSET_IDENTIFICATION = build_asset_identification()

COST_CHECK_ID = "acquisitiondatecostmatch"
TAG_CHECK_ID = "tagnumbercheck"


def should_defer_cost_in_plan(plan: ValidationPlan) -> bool:
    """Defer cost until after name/description match when both checks are enabled."""
    return plan.is_enabled(COST_CHECK_ID) and plan.is_enabled("assetnamedescriptionmatch")


def asset_lane_check_ids(plan: ValidationPlan) -> frozenset[str]:
    """Check IDs handled by the asset-image lane (excludes tag, deferred cost, deferred name/make-model)."""
    enabled = set(plan.enabled) - {TAG_CHECK_ID}
    if should_defer_cost_in_plan(plan):
        enabled.discard(COST_CHECK_ID)
    enabled.discard("assetnamedescriptionmatch")
    enabled.discard("assetmakemodelmatch")
    return frozenset(enabled)


def requires_vision_lane(plan: ValidationPlan) -> bool:
    """Vision pass needed for deferred name/make-model match or other asset-lane checks."""
    return (
        bool(asset_lane_check_ids(plan))
        or plan.is_enabled("assetnamedescriptionmatch")
        or plan.is_enabled("assetmakemodelmatch")
    )


def requires_asset_lane(plan: ValidationPlan) -> bool:
    return requires_vision_lane(plan)


def _strip_dual_image_blocks(text: str) -> str:
    """Remove duplicate dual-image preamble blocks from appended prompt sections."""
    return re.sub(
        r"=== DUAL IMAGE INPUT ===\s*\n.*?(?=\n\n|\Z)",
        "",
        text,
        flags=re.DOTALL,
    ).lstrip("\n")


def _barcode_section_without_dual_header() -> str:
    """Tag/barcode instructions without duplicating the shared dual-image header."""
    body = load_prompt(
        "barcode_standalone.md",
        barcode_rules=load_barcode_section("rules"),
    )
    marker = "Analyze the provided images carefully"
    idx = body.find(marker)
    if idx != -1:
        return body[idx:]
    return _strip_dual_image_blocks(body)


def build_unified_phase1_prompt(
    plan: ValidationPlan,
    *,
    user_asset_name: str,
    user_description: str,
    user_category: str,
    user_subcategory: str,
    user_makemodel: str,
    user_model: str = "",
    acquisition_date: date | None = None,
    declared_cost: str = "",
) -> str:
    """Single Phase 1 vision call combining tag + asset-lane checks."""
    prompt = build_asset_identification(
        user_asset_name=user_asset_name,
        user_description=user_description,
        user_category=user_category,
        user_subcategory=user_subcategory,
    )
    prompt += _barcode_section_without_dual_header()

    lane_ids = asset_lane_check_ids(plan)
    include_damage_reasoning = "assetcondition" in lane_ids
    if include_damage_reasoning:
        prompt += _strip_dual_image_blocks(REASONING_DAMAGE)
        prompt += _strip_dual_image_blocks(_condition_extension())

    prompt += load_prompt("output_schema_phase1_unified.md")
    prompt += (
        "\nReturn one JSON object combining ALL fields from the sections above. "
        "No markdown.\n"
    )
    return hoist_variable_context(prompt)


def compute_unified_phase1_max_tokens(plan: ValidationPlan, *, max_output_tokens: int) -> int:
    """Token budget for unified Phase 1 (tag + asset lane, vision counted once)."""
    total = compute_tag_max_tokens(max_output_tokens=max_output_tokens)
    total += compute_asset_lane_max_tokens(plan, max_output_tokens=max_output_tokens)
    return min(total, max_output_tokens)


def build_tag_number_prompt() -> str:
    """Tag check — scan both images; field names may be swapped."""
    return load_prompt(
        "barcode_standalone.md",
        barcode_rules=load_barcode_section("rules"),
    ) + load_prompt("output_schema_tag.md")


def build_barcode_lane_prompt() -> str:
    """Barcode lane — same as tag prompt when tagnumbercheck is enabled."""
    return build_tag_number_prompt()


def requires_barcode_lane(plan: ValidationPlan) -> bool:
    return plan.is_enabled(TAG_CHECK_ID)


def build_name_match_phase2_prompt(
    *,
    user_asset_name: str,
    user_description: str,
    detected_asset: str,
    image_analysis: str,
    barcode_position: str,
    include_structure_rules: bool = False,
    include_plant_rules: bool = False,
) -> str:
    """Phase 1.5 name match — injected vision context from prior pass (pre-prd aligned).

    The civil/structure ruleset (~2,100 tokens) is appended only for records that look like
    a building, room or road. Ordinary equipment — the overwhelming majority — never sees it.
    """
    prompt = load_prompt(
        "name_match.md",
        user_asset_name=user_asset_name or "(empty)",
        user_description=user_description if user_description else "(empty)",
        detected_asset=detected_asset if detected_asset else "(not detected)",
        image_analysis=truncate_for_phase2(image_analysis) if image_analysis else "(not available)",
        barcode_position=barcode_position if barcode_position else "(not detected)",
    )
    if include_structure_rules:
        prompt += load_prompt("name_match_structure.md")
    # Installed industrial plant (utils.helpers.is_installed_plant) — appended only for those
    # records, so every other record's name prompt stays byte-identical.
    if include_plant_rules:
        prompt += load_prompt("name_match_installed_plant.md")
    prompt += load_prompt("output_schema_name_phase2.md")
    return hoist_variable_context(prompt)


def build_name_description_prompt(
    user_asset_name: str,
    user_description: str,
) -> str:
    """Legacy helper — returns phase-2 prompt with empty vision context."""
    return build_name_match_phase2_prompt(
        user_asset_name=user_asset_name,
        user_description=user_description,
        detected_asset="",
        image_analysis="",
        barcode_position="",
    )


def build_make_model_phase2_prompt(
    *,
    user_category: str,
    user_subcategory: str,
    user_makemodel: str,
    user_model: str,
    detected_asset: str,
    image_analysis: str,
    include_user_identity: bool = True,
    user_asset_name: str = "",
    user_description: str = "",
) -> str:
    """Phase 2 make/model match — injected vision context (mirrors name Phase 1.5).

    The call is sent text-only whenever a photo description exists, so the prompt must carry it.
    subcat_model.md does not (it is the photo call's section, written to sit beside the images).
    With MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED a passed name selects subcat_model_identity.md
    instead: the confirmed name says what the asset is and the photo description supports it, so a
    partial or generic description ("electric motor" for a pump set) is not a mismatch (docs 6.4).

    Whichever template it is, the customer's category is not shown as a field to validate and no
    rule rejects on it while CATEGORY_ALWAYS_TRUSTED_ENABLED is on (prompts/category_trust.py,
    docs 6.6).
    """
    from asset_validator.core.config import (
        MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED,
        NULL_OVERRIDE_RESPECTS_PHOTO_ENABLED,
    )
    from asset_validator.prompts.category_trust import load_prompt_trusting_category

    blind = include_user_identity and not MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED
    if blind:
        prompt = load_prompt_trusting_category(
            "subcat_model.md",
            user_asset_name=user_asset_name or "(empty)",
            user_description=user_description if user_description else "(empty)",
            user_category=user_category or "(empty)",
            user_subcategory=user_subcategory or "(empty)",
            user_makemodel=user_makemodel or "(empty)",
            user_model=user_model or "(empty)",
        )
    elif include_user_identity:
        prompt = load_prompt_trusting_category(
            "subcat_model_identity.md",
            user_asset_name=user_asset_name or "(empty)",
            user_description=user_description if user_description else "(empty)",
            detected_asset=detected_asset if detected_asset else "(not detected)",
            image_analysis=truncate_for_phase2(image_analysis) if image_analysis else "(not available)",
            user_category=user_category or "(empty)",
            user_subcategory=user_subcategory or "(empty)",
            user_makemodel=user_makemodel or "(empty)",
            user_model=user_model or "(empty)",
        )
    else:
        prompt = load_prompt_trusting_category(
            "subcat_model_phase2.md",
            detected_asset=detected_asset if detected_asset else "(not detected)",
            image_analysis=truncate_for_phase2(image_analysis) if image_analysis else "(not available)",
            user_category=user_category or "(empty)",
            user_subcategory=user_subcategory or "(empty)",
            user_makemodel=user_makemodel or "(empty)",
            user_model=user_model or "(empty)",
        )
    if (
        NULL_OVERRIDE_RESPECTS_PHOTO_ENABLED and not blind
        and not (str(user_subcategory or "").strip() and str(user_makemodel or "").strip())
    ):
        # A blank field is never a mismatch, so the N this call returns is about a filled field
        # and may stand (docs 6.5). Added only when a field is blank: every other record's prompt
        # stays byte-identical.
        prompt += load_prompt("subcat_model_blank_fields.md")
    prompt += load_prompt("output_schema_make_model_phase2.md")
    return hoist_variable_context(prompt)


def build_make_model_prompt(
    user_asset_name: str,
    user_description: str,
    user_category: str,
    user_subcategory: str,
    user_makemodel: str,
    user_model: str = "",
) -> str:
    """Subcategory/make-model match — dual-image swap-safe + identity reasoning."""
    prompt = DUAL_ASSET_IDENTIFICATION
    prompt += REASONING_EXTENSION
    prompt += load_prompt(
        "subcat_model.md",
        user_asset_name=user_asset_name or "(empty)",
        user_description=user_description if user_description else "(empty)",
        user_category=user_category or "(empty)",
        user_subcategory=user_subcategory or "(empty)",
        user_makemodel=user_makemodel or "(empty)",
        user_model=user_model or "(empty)",
    )
    prompt += load_prompt("output_schema_make_model.md")
    return prompt


def build_acquisition_cost_prompt(
    *,
    acquisition_date: date | None = None,
    declared_cost: str = "",
    user_asset_name: str = "",
    user_description: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
    use_user_identity: bool = False,
    market: MarketProfile | None = None,
) -> str:
    """Cost/date validation — dual-image swap-safe + cost extraction."""
    prompt = build_asset_identification(
        user_asset_name=user_asset_name,
        user_description=user_description,
        user_category=user_category,
        user_subcategory=user_subcategory,
    )
    prompt += REASONING_EXTENSION
    prompt += build_cost_date_extension(
        acquisition_date=acquisition_date,
        declared_cost=declared_cost,
        user_asset_name=user_asset_name,
        user_description=user_description,
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_makemodel=user_makemodel,
        user_model=user_model,
        use_user_identity=use_user_identity,
        market=market,
    )
    from asset_validator.prompts.cost_date import _cost_self_check_block

    prompt += _cost_self_check_block()
    prompt += load_cost_output_schema(market)
    # Hoist per-asset blocks to the tail (and strip the VARCTX markers so they never
    # leak into the prompt) — consistent with every other builder.
    return hoist_variable_context(prompt)


def build_condition_check_prompt() -> str:
    """Condition assessment — dual-image swap-safe + damage reasoning."""
    prompt = DUAL_ASSET_IDENTIFICATION
    prompt += REASONING_DAMAGE
    prompt += _condition_extension()
    prompt += load_prompt("output_schema_condition.md")
    return prompt


def build_asset_lane_prompt(
    plan: ValidationPlan,
    *,
    user_asset_name: str,
    user_description: str,
    user_category: str,
    user_subcategory: str,
    user_makemodel: str,
    user_model: str = "",
    acquisition_date: date | None = None,
    declared_cost: str = "",
    market: MarketProfile | None = None,
) -> str:
    """Merge enabled asset-lane sections (name/make-model deferred to Phase 2)."""
    lane_ids = asset_lane_check_ids(plan)
    vision_only = not lane_ids and (
        plan.is_enabled("assetnamedescriptionmatch")
        or plan.is_enabled("assetmakemodelmatch")
    )
    if not lane_ids and not vision_only:
        return ""

    prompt = build_asset_identification(
        user_asset_name=user_asset_name,
        user_description=user_description,
        user_category=user_category,
        user_subcategory=user_subcategory,
    )

    if vision_only:
        prompt += load_prompt("output_schema_vision.md")
        prompt += "\nReturn one JSON object only. No markdown.\n"
        return prompt

    include_full_reasoning = COST_CHECK_ID in lane_ids
    include_damage_reasoning = (
        "assetcondition" in lane_ids and not include_full_reasoning
    )
    if include_full_reasoning:
        prompt += REASONING_EXTENSION
    elif include_damage_reasoning:
        prompt += REASONING_DAMAGE

    if "assetcondition" in lane_ids:
        prompt += _condition_extension()

    if COST_CHECK_ID in lane_ids:
        prompt += build_cost_date_extension(
            acquisition_date=acquisition_date,
            declared_cost=declared_cost,
            user_asset_name=user_asset_name,
            user_description=user_description,
            use_user_identity=False,
            market=market,
        )

    if "assetcondition" in lane_ids:
        prompt += load_prompt("output_schema_condition.md")
    if COST_CHECK_ID in lane_ids:
        prompt += load_cost_output_schema(market)

    prompt += load_prompt("output_schema_vision.md")

    prompt += (
        "\nReturn one JSON object combining ALL fields from the sections above. "
        "No markdown.\n"
    )
    return hoist_variable_context(prompt)


def compute_tag_max_tokens(*, max_output_tokens: int) -> int:
    return min(700, max_output_tokens)


def compute_name_max_tokens(*, max_output_tokens: int) -> int:
    return min(450, max_output_tokens)


def compute_make_model_max_tokens(*, max_output_tokens: int) -> int:
    return min(600, max_output_tokens)


def compute_cost_max_tokens(*, max_output_tokens: int) -> int:
    """The cost check's share of a combined lane's budget (compute_asset_lane_max_tokens)."""
    return min(850, max_output_tokens)


def compute_cost_call_max_tokens(*, max_output_tokens: int) -> int:
    """Budget of the separate cost call (docs §7.6)."""
    from asset_validator.core.config import COST_CALL_MAX_TOKENS

    return min(COST_CALL_MAX_TOKENS, max_output_tokens)


def compute_condition_max_tokens(*, max_output_tokens: int) -> int:
    return min(900, max_output_tokens)


def compute_vision_max_tokens(*, max_output_tokens: int) -> int:
    return min(500, max_output_tokens)


def compute_asset_lane_max_tokens(plan: ValidationPlan, *, max_output_tokens: int) -> int:
    """Sum token budgets for enabled asset-lane checks (name/make-model excluded — Phase 2)."""
    calculators = {
        "acquisitiondatecostmatch": compute_cost_max_tokens,
        "assetcondition": compute_condition_max_tokens,
    }
    lane_ids = asset_lane_check_ids(plan)
    if not lane_ids and (
        plan.is_enabled("assetnamedescriptionmatch")
        or plan.is_enabled("assetmakemodelmatch")
    ):
        return compute_vision_max_tokens(max_output_tokens=max_output_tokens)
    total = 0
    for check_id in lane_ids:
        total += calculators[check_id](max_output_tokens=max_output_tokens)
    total += compute_vision_max_tokens(max_output_tokens=max_output_tokens)
    return min(max(total, 1), max_output_tokens)


def compute_barcode_lane_max_tokens(*, max_output_tokens: int) -> int:
    return compute_tag_max_tokens(max_output_tokens=max_output_tokens)
