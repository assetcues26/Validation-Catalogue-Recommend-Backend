"""Prompt builders for Additional Asset (vision + tag + condition + Pinecone selection)."""

from __future__ import annotations

import re

from additional_asset.core import config
from additional_asset.prompts.loader import (
    hoist_variable_context,
    load_barcode_section,
    load_prompt,
)


def _strip_dual_image_blocks(text: str) -> str:
    return re.sub(
        r"=== DUAL IMAGE INPUT ===\s*\n.*?(?=\n\n|\Z)",
        "",
        text,
        flags=re.DOTALL,
    ).lstrip("\n")


def _barcode_section_without_dual_header() -> str:
    body = load_prompt(
        "barcode_standalone.md",
        barcode_rules=load_barcode_section("rules"),
    )
    marker = "Analyze the provided images carefully"
    idx = body.find(marker)
    if idx != -1:
        return body[idx:]
    return _strip_dual_image_blocks(body)


def _condition_extension() -> str:
    if config.condition_detail_level() == "minimal":
        return load_prompt("condition_minimal.md")
    return load_prompt("condition.md")


def build_unified_phase1_prompt() -> str:
    """Legacy Phase 1 (blind-view path): vision + tag + condition (no cost)."""
    prompt = load_prompt(
        "vision_identification.md",
        expected_asset_name="(not provided)",
        expected_description="(not provided)",
        expected_category="(not provided)",
        expected_subcategory="(not provided)",
    )
    prompt += _barcode_section_without_dual_header()

    try:
        reasoning_damage = load_prompt("reasoning_damage.md")
    except FileNotFoundError:
        reasoning_damage = ""
    prompt += _strip_dual_image_blocks(reasoning_damage)
    prompt += _strip_dual_image_blocks(_condition_extension())

    prompt += load_prompt("output_schema_phase1_unified.md")
    prompt += (
        "\nReturn one JSON object combining ALL fields from the sections above. "
        "No markdown.\n"
        "IMPORTANT: There is no user-declared asset name or tag number. "
        "Observe the photos and extract what is visible. Use null when uncertain.\n"
    )
    return hoist_variable_context(prompt)


def build_unified_phase1_content_route_prompt() -> str:
    """Content-route Phase 1 — only when PHASE1_CONTENT_ROUTE_ENABLED=true.

    Loads phase1_unified_content_route.md exclusively for the Phase 1 body.
    Does not call build_unified_phase1_prompt() or load vision_identification.md / blind_view.md.
    """
    try:
        reasoning_damage = load_prompt("reasoning_damage.md")
    except FileNotFoundError:
        reasoning_damage = ""
    condition_extension = (
        _strip_dual_image_blocks(reasoning_damage)
        + "\n"
        + _strip_dual_image_blocks(_condition_extension())
    )
    prompt = load_prompt(
        "phase1_unified_content_route.md",
        expected_asset_name="(not provided)",
        expected_description="(not provided)",
        expected_category="(not provided)",
        expected_subcategory="(not provided)",
        barcode_rules=load_barcode_section("rules"),
        condition_extension=condition_extension,
    )
    return hoist_variable_context(prompt)


def build_pinecone_selection_prompt(
    *,
    detected_asset: str,
    detected_description: str,
    candidate_summary: str,
) -> str:
    """Selection uses identity only (asset + description); analysis stays in search/type filter."""
    return hoist_variable_context(
        load_prompt(
            "pinecone_selection_vision.md",
            detected_asset=detected_asset or "(empty)",
            detected_description=detected_description or "(empty)",
            candidate_summary=candidate_summary,
        )
    )


def compute_unified_max_tokens() -> int:
    return min(1800, config.gemini_max_output_tokens())
