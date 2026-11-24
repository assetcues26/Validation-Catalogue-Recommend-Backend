"""Pinecone candidate selection prompt."""


def build_pinecone_selection_prompt(
    detected_asset: str,
    image_analysis: str,
    user_name: str,
    user_desc: str,
    candidate_summary: str,
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
    *,
    include_user_identity: bool = True,
) -> str:
    """The selection still returns the chosen row's category, but no longer compares the customer's
    with it while CATEGORY_ALWAYS_TRUSTED_ENABLED is on (prompts/category_trust.py, docs 6.6)."""
    from asset_validator.prompts.category_trust import load_prompt_trusting_category
    from asset_validator.prompts.loader import hoist_variable_context

    template = "pinecone_selection.md" if include_user_identity else "pinecone_selection_vision.md"
    kwargs = {
        "detected_asset": detected_asset,
        "image_analysis": image_analysis,
        "candidate_summary": candidate_summary,
        "user_category": user_category or "(empty)",
        "user_subcategory": user_subcategory or "(empty)",
        "user_makemodel": user_makemodel or "(empty)",
        "user_model": user_model or "(empty)",
    }
    if include_user_identity:
        kwargs["user_name"] = user_name
        kwargs["user_desc"] = user_desc or "(empty)"
    return hoist_variable_context(load_prompt_trusting_category(template, **kwargs))
