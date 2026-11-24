
=== SUBCATEGORY & MAKE/MODEL VALIDATION (independent from namedescriptionmatch) ===
Validate ONLY the user's master-data classification fields below against the image.
Do NOT use Asset Name or Description for this check — those belong to namedescriptionmatch.

[[VARCTX_START]]
USER CLASSIFICATION TO VALIDATE (compare these ONLY):
- Category: {user_category}
- Subcategory: {user_subcategory}
- Make/Model: {user_makemodel}
- Model: {user_model}

Reference fields (for context only — NOT substitutes for user classification):
- Asset Name: {user_asset_name} — ignore for subcatmodelmatch verdict
- Description: {user_description}
[[VARCTX_END]]

Use detectedAsset, imageAnalysis, and inspection_reasoning.identity_candidates from the **full-asset photo** (either upload slot when images are swapped).

=== DECISION RULES ===

Bias toward APPROVE when classification fields tell one consistent product story that matches the image.
Hard-reject on product-type contradictions — not on brand-only differences.

1) SUBCATEGORY (primary gate) — user Subcategory vs visible product type (monitor, laptop, printer, air cooler, chair, etc.)
2) MAKE/MODEL — must name the same equipment class as Subcategory/Category when it implies a type; brand differences alone do NOT reject.
3) MODEL — skip when empty or stale vs Subcategory/Make-Model. Never fail the overall verdict solely on Model.

APPROVE "Y" (75-95%) when user fields are mutually consistent AND compatible with the visible asset.
Semantic match is OK: approve Y when wording differs but equipment type is the same (e.g. notebook/laptop; "6Kg Abc Type Fire Extinguisher" vs "ABC fire extinguisher").
REGIONAL / TRADE VOCABULARY counts as the SAME type, never a "type mismatch". Indian-English and
trade names for the same equipment must approve Y:
- geyser = water heater = instant water heater = storage water heater
- almirah = cupboard = wardrobe;  bureau = chest of drawers
- dispenser = water cooler (when the form factor matches);  AC = air conditioner
- DG set = diesel generator = genset;  stabiliser = voltage stabilizer = servo stabilizer
- lift = elevator;  cabin = enclosure;  rack = shelving unit
This list is illustrative, not exhaustive: whenever the user wording and the visible asset are two
names for the SAME piece of equipment, that is a MATCH. Reject only when they are genuinely
different equipment classes (a laptop is not a mouse).

REJECT "N" (10-40%) for product-type mismatch (internal or vs image).

⚠️ INTERNAL FIELD TYPE CONTRADICTION (mandatory reject):
- If Category, Subcategory, and Make/Model name different equipment classes → REJECT "N".
- Example: Category "laptop" + Make/Model "mouse" → N (even if Subcategory text overlaps the image).
- Example: Subcategory "Human BodyStatic" + Make/Model "mouse" + Category "laptop" vs static dissipater image → N.
- Do NOT reject for brand-only differences when product type matches.

⚠️ PRODUCT TYPE VS IMAGE (mandatory reject):
- User classification names a clear product class (laptop, mouse, monitor, TV, chair, printer, …) that is NOT what the image shows → REJECT "N" at 10-25%.
- Partial Subcategory wording overlap must NOT override a conflicting known product class.
- Example: Subcategory "laptop" + image shows Thomson television → N.
- Example: Category "laptop" + image shows Human Body Static Dissipater → N.

⚠️ BRAND / IDENTITY (soft — never mandatory reject):
- If product type matches but brands differ (e.g. user Make/Model "LG monitor" + image shows Dell logo) → still APPROVE "Y" at 75-85%.
- Brand not visible on body, low identity confidence, or generation_ambiguous → keep Y; cap percent at 60–85 as appropriate. Do NOT flip to N.
- Generic Make/Model codes without a brand (e.g. "55 TVn", "27 Monitor") → approve when product type matches.

=== DISPLAY EQUIPMENT (monitors, TVs, projectors, AIO displays) ===
- Subcategory: approve when user subcategory product type matches form factor (flat panel, bezel, stand).
- Manufacturer logos are often on the rear label only — check front bezel/chin or rear nameplate if visible.
- Brand uncertain or not visible → Y at 75-85% with uncertainty_flags: brand_not_visible_on_body. Do NOT reject for brand alone.
- Do NOT use Asset Name for this verdict.

=== REASONING (mandatory) ===
"subcatmodelreasoning": ONE sentence, max 25 words. Start with Y/N. Name actual user subcategory and makemodel values. Cite the visible asset cue.
Example (Y): "Y: subcategory MONITORS, makemodel dell monitor match Dell monitor visible in image."
Example (Y): "Y: subcategory AIR COOLER, makemodel Voltas match Voltas air cooler visible in image."
Example (Y): "Y: subcategory MONITORS, makemodel LG monitor match Dell monitor visible in image."
Example (N): "N: subcategory laptop, makemodel Dell Latitude vs Thomson television — type mismatch."
Example (N): "N: category laptop, makemodel mouse conflict with static dissipater — type mismatch."
Never use Asset Name; cite user Subcategory/Make/Model only.

If generation_ambiguous in uncertainty_flags: cap subcatmodelmatchpercent at 60 — keep Y when product type matches; do NOT reject.
If top two identity_candidates differ by < 0.15 confidence: treat as generation_ambiguous.

Return: "subcatmodelmatch", "subcatmodelmatchpercent" (max 95), "subcatmodelreasoning".
