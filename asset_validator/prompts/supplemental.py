"""Supplemental vision prompt — condition and cost/date."""

from __future__ import annotations

from datetime import date

from asset_validator.prompts.cost_date import build_cost_date_prompt
from asset_validator.prompts.loader import load_output_schema, load_prompt
from asset_validator.prompts.phase1 import CONDITION_EXTENSION, REASONING_EXTENSION

OUTPUT_SCHEMA = load_output_schema()


def build_condition_prompt(
    *,
    asset_name: str,
    description: str,
    detected_asset: str,
    image_analysis: str,
) -> str:
    """Dedicated condition-only supplemental vision prompt."""
    return build_supplemental_prompt(
        include_cost_date=False,
        asset_name=asset_name,
        description=description,
        detected_asset=detected_asset,
        image_analysis=image_analysis,
    )


def build_supplemental_prompt(
    *,
    include_cost_date: bool,
    asset_name: str,
    description: str,
    detected_asset: str,
    image_analysis: str,
    acquisition_date: date | None = None,
) -> str:
    """Build supplemental vision prompt for condition and/or cost/date checks."""
    desc_display = description if description else "(empty)"
    prompt = (
        "Analyze the ASSET image. Use Phase 1 context below.\n"
        + load_prompt(
            "supplemental_context.md",
            detected_asset=detected_asset or "(unknown)",
            image_analysis=image_analysis or "(unknown)",
            asset_name=asset_name or "(empty)",
            description=desc_display,
        )
    )
    prompt += REASONING_EXTENSION
    prompt += CONDITION_EXTENSION

    if include_cost_date:
        prompt += "\n" + build_cost_date_prompt(
            detected_asset or "",
            image_analysis or "",
            acquisition_date=acquisition_date,
        )

    prompt += OUTPUT_SCHEMA
    return prompt
