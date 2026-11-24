"""Phase 1 merged vision prompt assembly."""

from __future__ import annotations

from datetime import date

from asset_validator.prompts.cost_date import build_cost_date_extension
from asset_validator.prompts.loader import load_barcode_section, load_output_schema, load_prompt

OUTPUT_SCHEMA = load_output_schema()
PROMPT_FOOTER = OUTPUT_SCHEMA
CONDITION_EXTENSION = load_prompt("condition.md")
REASONING_EXTENSION = load_prompt("reasoning.md")


def build_merged_phase1_prompt(
    user_asset_name: str,
    user_description: str,
    user_subcategory: str,
    user_makemodel: str,
    custom_fields_for_llm: dict,
    validate_subcatmodel: bool = False,
    include_cost_date: bool = False,
    acquisition_date: date | None = None,
    user_model: str = "",
    user_category: str = "",
) -> str:
    """Build merged prompt — barcode, reasoning, match, condition, cost/date in one call."""
    prompt = load_prompt(
        "phase1_vision_base.md",
        barcode_phase1=load_barcode_section("phase1"),
    )
    prompt += REASONING_EXTENSION

    if custom_fields_for_llm:
        lines = "\n".join(f'- "{k}" = {v}' for k, v in custom_fields_for_llm.items())
        keys_list = ", ".join(f'"{k}"' for k in custom_fields_for_llm)
        prompt += load_prompt(
            "custom_fields.md",
            custom_field_lines=lines,
            custom_field_keys=keys_list,
        )

    prompt += load_prompt(
        "name_match.md",
        user_asset_name=user_asset_name or "(empty)",
        user_description=user_description if user_description else "(empty)",
        detected_asset="(from your detectedAsset above)",
        image_analysis="(from your imageAnalysis above)",
        barcode_position="(from your barcodeposition above)",
    )

    if validate_subcatmodel:
        prompt += load_prompt(
            "subcat_model.md",
            user_asset_name=user_asset_name or "(empty)",
            user_description=user_description if user_description else "(empty)",
            user_category=user_category or "(empty)",
            user_subcategory=user_subcategory or "(empty)",
            user_makemodel=user_makemodel or "(empty)",
            user_model=user_model or "(empty)",
        )

    prompt += CONDITION_EXTENSION

    if include_cost_date:
        prompt += build_cost_date_extension(acquisition_date=acquisition_date)

    prompt += OUTPUT_SCHEMA
    return prompt


def compute_phase1_max_tokens(
    *,
    include_cost_date: bool,
    custom_field_count: int,
    max_output_tokens: int,
) -> int:
    """Dynamic output token budget for merged Phase 1."""
    base = 900
    base += 1400  # condition always included
    base += 400   # reasoning block
    if include_cost_date:
        base += 600
    base += custom_field_count * 50
    return min(max(base, 1), max_output_tokens)
