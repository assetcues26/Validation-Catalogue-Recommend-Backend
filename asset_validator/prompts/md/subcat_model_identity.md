
=== SUBCATEGORY & MAKE/MODEL VALIDATION (Phase 2 — the asset name is CONFIRMED) ===
The name check has already compared the asset name with the photo and PASSED it: the photo shows the
asset the name describes. Decide whether the user's master-data classification below names the same
kind of equipment as that confirmed asset.

[[VARCTX_START]]
CONFIRMED ASSET (the name check passed it against the photo):
- Asset Name: {user_asset_name}
- Description: {user_description}

PHOTO DESCRIPTION (from the photo pass — it may describe only part of the asset, use a generic label, or show only the asset tag):
- detectedAsset: {detected_asset}
- imageAnalysis: {image_analysis}

USER CLASSIFICATION TO VALIDATE:
- Category: {user_category}
- Subcategory: {user_subcategory}
- Make/Model: {user_makemodel}
- Model: {user_model}
[[VARCTX_END]]

=== DECISION RULES ===

The confirmed asset name says what the asset is; the photo description supports it. A photo description
is often partial or generic — the motor of a motor-pump set, a desk for a workstation, a counter for a retail
display unit, "industrial machine", or only the asset tag. A partial or generic description is never a
mismatch on its own: judge the classification against the confirmed asset and the photo together.

Bias toward APPROVE. Hard-reject only on product-type contradictions — not on wording, not on brand.

APPROVE "Y" (75-95%) when Subcategory and Make/Model name the confirmed asset's kind of equipment, a broader
class that contains it, or the plant system it belongs to.
Semantic match is OK: approve Y when the wording differs but the equipment type is the same.
REGIONAL / TRADE VOCABULARY counts as the SAME type, never a "type mismatch":
- geyser = water heater = instant water heater = storage water heater
- almirah = cupboard = wardrobe;  bureau = chest of drawers;  workstation = office desk
- dispenser = water cooler (when the form factor matches);  AC = air conditioner
- DG set = diesel generator = genset;  stabiliser = voltage stabilizer = servo stabilizer
- lift = elevator;  cabin = enclosure;  rack = shelving unit

REJECT "N" (10-40%) only when:
- Subcategory or Make/Model names a clearly DIFFERENT kind of equipment from the confirmed asset and the
  photo — e.g. a laptop registered under subcategory Telephones, a chair under Printers, a monitor under Laptop;
- or Category, Subcategory and Make/Model name different equipment classes from each other — e.g. Category
  "laptop" + Make/Model "mouse".

BRAND and MODEL never reject:
- If the equipment type matches but the Make/Model brand differs → still APPROVE "Y" at 75-85%.
- Generic Make/Model codes without a brand (e.g. "55 TVn", "27 Monitor") → approve when the type matches.
- Model: skip when empty or when it clearly belongs to another product type. Never fail solely on Model.

=== REASONING (mandatory) ===
"subcatmodelreasoning": ONE sentence, max 25 words. Start with Y/N. Name actual user subcategory and makemodel values. Cite the confirmed asset.
Example (Y): "Y: subcategory air cooler, makemodel Voltas match Voltas air cooler visible in image."
Example (Y): "Y: subcategory MONITORS, makemodel LG monitor match Dell monitor visible in image."
Example (N): "N: subcategory laptop, makemodel Dell Latitude vs Thomson television — type mismatch."

Return: "subcatmodelmatch", "subcatmodelmatchpercent" (max 95), "subcatmodelreasoning".
