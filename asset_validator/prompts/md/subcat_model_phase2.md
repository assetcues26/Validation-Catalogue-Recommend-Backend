
=== SUBCATEGORY & MAKE/MODEL VALIDATION (Phase 2 — vision context committed) ===
Validate ONLY the user's master-data classification fields below against the committed vision below.
Asset Name and Description are OUT OF SCOPE — do NOT use them for this check.

[[VARCTX_START]]
COMMITTED VISION (from prior pass — treat as ground truth for visible asset):
- detectedAsset: {detected_asset}
- imageAnalysis: {image_analysis}

USER CLASSIFICATION TO VALIDATE (compare these ONLY against committed vision):
- Category: {user_category}
- Subcategory: {user_subcategory}
- Make/Model: {user_makemodel}
- Model: {user_model}
[[VARCTX_END]]

Use ONLY the detectedAsset and imageAnalysis given in the COMMITTED VISION block — not asset name or description.

=== DECISION RULES ===

Bias toward APPROVE when classification fields tell one consistent product story that matches committed vision.
Hard-reject on product-type contradictions — not on brand-only differences.

1) SUBCATEGORY (primary gate) — user Subcategory vs visible product type in committed vision
2) MAKE/MODEL — must name the same equipment class as Subcategory/Category when it implies a type; brand differences alone do NOT reject.
3) MODEL — skip when empty or when Model clearly belongs to a different product type than user Subcategory/Make-Model (e.g. Subcategory television + Model "Latitude 5420" — evaluate Subcategory and Make/Model against vision only). Never fail the overall verdict solely on Model.

APPROVE "Y" (75-95%) when user fields are mutually consistent AND compatible with committed vision.
Semantic match is OK: approve Y when subcategory/makemodel describe the same equipment type and capacity as vision even if wording differs (e.g. user "6Kg Abc Type Fire Extinguisher" vs vision "ABC fire extinguisher").
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

REJECT "N" (10-40%) for product-type mismatch (internal or vs committed vision).

⚠️ INTERNAL FIELD TYPE CONTRADICTION (mandatory reject):
- If Category, Subcategory, and Make/Model name different equipment classes → REJECT "N".
- Example: Category "laptop" + Make/Model "mouse" → N (even if Subcategory text overlaps vision).
- Example: Subcategory "Human BodyStatic" + Make/Model "mouse" + Category "laptop" vs static dissipater → N.

⚠️ PRODUCT TYPE VS VISION (mandatory reject):
- User classification names a clear product class (laptop, mouse, monitor, TV, chair, printer, …) that is NOT what committed vision shows → REJECT "N" at 10-25%.
- Partial Subcategory wording overlap must NOT override a conflicting known product class.
- Do NOT treat subcategory "Business tv" as aligned with a television image when user Subcategory is "laptop".
- Example: Subcategory "laptop" + vision Thomson television → N.
- Example: Category "laptop" + vision Human Body Static Dissipater → N.

⚠️ BRAND (soft — never mandatory reject):
- If product type matches but user Make/Model brand differs from visible branding → still APPROVE "Y" at 75-85%.
- Generic Make/Model codes without a brand (e.g. "55 TVn", "27 Monitor") → approve when product type matches.
- Do NOT combine Make/Model and Model into one comparison. Evaluate Make/Model separately from Model.
- Brand uncertainty → lower percent (75–85), keep Y; do NOT flip to N.

=== REASONING (mandatory) ===
"subcatmodelreasoning": ONE sentence, max 25 words. Start with Y/N. Name actual user subcategory and makemodel values. Cite the visible asset from committed vision.
Example (Y): "Y: subcategory television, makemodel 55 TVn match Thomson television visible in image."
Example (Y): "Y: subcategory air cooler, makemodel Voltas match Voltas air cooler visible in image."
Example (Y): "Y: subcategory fire extinguishers, makemodel 6Kg Abc Type match ABC fire extinguisher visible in image."
Example (Y): "Y: subcategory MONITORS, makemodel LG monitor match Dell monitor visible in image."
Example (N): "N: subcategory laptop, makemodel Dell Latitude vs Thomson television — type mismatch."
Example (N): "N: category laptop, makemodel mouse conflict with static dissipater — type mismatch."

Return: "subcatmodelmatch", "subcatmodelmatchpercent" (max 95), "subcatmodelreasoning".
