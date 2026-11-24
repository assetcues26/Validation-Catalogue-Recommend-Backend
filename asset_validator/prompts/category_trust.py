"""The prompt lines that ask a model to judge the customer's category, and their rewrites (docs 6.6).

The customer selects the category, so it is always right and never validated (owner's decision,
2026-11-01). These are the only lines of the request-path prompts that ask a model to judge it: the
make/model call's three templates, and the master-data selection's category comparison. With
CATEGORY_ALWAYS_TRUSTED_ENABLED and CATEGORY_PROMPTS_TRUST_ENABLED each is rewritten so the call judges
subcategory, make/model and model alone. The category stays in the prompt as context, marked chosen by the
customer and always correct (review, 1 Nov 2026: without it a generic subcategory such as "Workstation",
"Rack" or "Others" loses its meaning), but no rule or example judges it. Every other line stays
byte-identical to its template, so a later edit to a shared rule reaches both versions; with either switch
off the template is sent exactly as before. The prompt switch is separate so that a prompt regression can be
rolled back without making the category a failure reason again.

The rewrites are exact strings, not patterns: tests/test_category_always_trusted.py fails if one no
longer appears in its template, so an edit to one of these lines cannot silently bring the category
judgement back.

Only the request-path builders use this: build_make_model_phase2_prompt and
build_pinecone_selection_prompt. build_make_model_prompt and build_merged_phase1_prompt still carry
subcat_model.md as it is; nothing in the pipeline calls them.
"""

from __future__ import annotations

from asset_validator.prompts.loader import load_prompt

# The customer's category line in the block of fields the call validates or compares: kept as context only.
_CATEGORY_FIELD = (
    "- Category: {user_category}\n",
    "- Category (chosen by the customer, always correct; context for reading the other fields, "
    "never judge it): {user_category}\n",
)
# "must name the same equipment class as Subcategory/Category" — make/model is held to the subcategory.
_MAKEMODEL_CLASS = (
    "as Subcategory/Category when it implies a type",
    "as Subcategory when it implies a type",
)
_INTERNAL_CONTRADICTION = (
    '- If Category, Subcategory, and Make/Model name different equipment classes → REJECT "N".',
    '- If Subcategory and Make/Model name different equipment classes → REJECT "N".',
)
_STATIC_DISSIPATER = (' + Category "laptop" vs static dissipater', " vs static dissipater")
_REASONING_EXAMPLE = ('"N: category laptop, makemodel mouse', '"N: subcategory laptop, makemodel mouse')
# The master-data selection's comparison of the customer's category with the chosen row's.
_SELECTION_REWRITES = (
    _CATEGORY_FIELD,
    ('"categorymatch":"matched or failed or null","categorymatchpercent":0-100 or null,', ""),
    ("STEP 2 — Compare user Category, Sub Category, Make/Model, and Model against",
     "STEP 2 — Compare user Sub Category, Make/Model, and Model against"),
    ("- categorymatch / categorymatchpercent: compare user category vs recommended category\n", ""),
    ('- Category: same domain only ("IT Equipment" ≈ "IT Assets" OK; "laptop" vs "IT Assets" may '
     'match only if same domain; "Furniture" vs "IT" → failed)\n', ""),
    ('- User category "Furniture", recommended "IT Equipment" → categorymatch failed, '
     "categorymatchpercent 10-20\n", ""),
)

CATEGORY_REWRITES: dict[str, tuple[tuple[str, str], ...]] = {
    # The photo call's own section; the make/model call uses it only with
    # MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED off (docs 6.4).
    "subcat_model.md": (
        _CATEGORY_FIELD,
        _MAKEMODEL_CLASS,
        _INTERNAL_CONTRADICTION,
        ('- Example: Category "laptop" + Make/Model "mouse" → N (even if Subcategory text overlaps '
         "the image).",
         '- Example: Subcategory "laptop" + Make/Model "mouse" → N.'),
        _STATIC_DISSIPATER,
        ('- Example: Category "laptop" + image shows Human Body Static Dissipater → N.\n', ""),
        _REASONING_EXAMPLE,
    ),
    # A failed or unchecked name.
    "subcat_model_phase2.md": (
        _CATEGORY_FIELD,
        _MAKEMODEL_CLASS,
        _INTERNAL_CONTRADICTION,
        ('- Example: Category "laptop" + Make/Model "mouse" → N (even if Subcategory text overlaps '
         "vision).",
         '- Example: Subcategory "laptop" + Make/Model "mouse" → N.'),
        _STATIC_DISSIPATER,
        ('- Example: Category "laptop" + vision Human Body Static Dissipater → N.\n', ""),
        _REASONING_EXAMPLE,
    ),
    # A passed name (docs 6.4).
    "subcat_model_identity.md": (
        _CATEGORY_FIELD,
        ("- or Category, Subcategory and Make/Model name different equipment classes from each "
         'other — e.g. Category\n  "laptop" + Make/Model "mouse".',
         "- or Subcategory and Make/Model name different equipment classes from each other — "
         'e.g. Subcategory\n  "laptop" + Make/Model "mouse".'),
    ),
    # The selection still returns the chosen row's category: the row's model is found by it.
    "pinecone_selection.md": _SELECTION_REWRITES,
    "pinecone_selection_vision.md": _SELECTION_REWRITES,
}


def load_prompt_trusting_category(name: str, **fmt) -> str:
    """load_prompt, with the template's category judgements rewritten while the category is trusted."""
    from asset_validator.core.config import CATEGORY_ALWAYS_TRUSTED_ENABLED, CATEGORY_PROMPTS_TRUST_ENABLED

    if not (CATEGORY_ALWAYS_TRUSTED_ENABLED and CATEGORY_PROMPTS_TRUST_ENABLED) or name not in CATEGORY_REWRITES:
        return load_prompt(name, **fmt)
    text = load_prompt(name)
    for old, new in CATEGORY_REWRITES[name]:
        text = text.replace(old, new)
    return text.format(**fmt) if fmt else text
