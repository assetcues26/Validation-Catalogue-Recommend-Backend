Universal pricing rubric — {market}, NEW corporate buying price, standard/base config.
IMAGE-ONLY PRICING — derive the tier and price range from the asset photo(s), not from the declared cost alone.

PRICING YEAR:
- Acquisition date given → {market} new corporate buying price in that acquisition YEAR.
- No date → {market} new corporate buying price as of today.
- Use the NEW corporate buying price only — NOT resale, NOT used, NOT second-hand, NOT refurbished, NOT depreciated book value, NOT FMV, and never today's price for a historical acquisition date.

METHOD (write valuation_deliberation_notes in inspection_reasoning BEFORE the cost fields — justify the tier and the range):

Step 1 — Read the image: asset class (from shape/function), setting (home/office/warehouse/factory/outdoor), build (lightweight domestic vs industrial/metal/heavy housing), size vs surroundings, brand on the ASSET BODY only (ignore client tracking-sticker logos), and any readable model/SKU text.

Step 2 — Pick tier from VISUALS (not from the name/description):
- Warehouse/factory location alone is NOT premium — match tier to the actual unit size and features.
- Lightweight plastic / small / domestic → ENTRY (lower band). Heavy housing, metal build, large size, or pro features (PTZ, dual lens, large dome, speed controls on the head) → PREMIUM / COMMERCIAL.
- Brand visible but SKU unreadable → tier by FORM FACTOR and size, not brand prestige. No brand / generic commodity → ENTRY baseline, lower confidence.
- Batteries / UPS: when AH + voltage + chemistry (e.g. 12V SMF/VRLA) are stated, price by STATED CAPACITY — not a generic "lead-acid" class or warehouse-setting guess.

Step 3 — MARKET RANGE (cost_lower_bound L, cost_upper_bound U):
- L..U = plausible {market} new corporate buying range for the class, visual tier, and pricing year.
- Base L..U on evidence; do NOT derive it from predicted_cost × a fixed multiplier, and do NOT use the declared cost.

LOW-PRICEABILITY / ASSEMBLED FURNITURE — apply to desks, tables, modular office workstations, cabinets, cupboards, cubicles, partitions, almirahs, wardrobes, shelving, sofas, benches, office chairs (NOT to PC/laptop/desktop/monitor, nor desk/table fans):
- A single photo cannot pin material grade, size, fittings, labour, or catalog-vs-custom cost.
- Set asset_class_detected = "furniture" (or "office_chair" for a chair). Emit a WIDE L..U for the visible size/class — do NOT invent a tight SKU price. model_end_year: null. Expect valuation_confidence 0.4–0.65. Still return L/U when the class is clear.

PRICING MODES:
- IMAGE-ONLY PRICING MODE (name/description not confirmed): if the exact configuration is not visible, WIDEN L..U to cover plausible spec variants (e.g. 8GB vs 16GB RAM of one model line); do not collapse to a single point.
- IDENTITY-ASSISTED PRICING MODE (name/description confirmed to match the image): parse CPU/RAM/storage/screen/variant (and AH/voltage/chemistry for batteries) from the name/description and set a TIGHTER L..U for that specific SKU/config. For furniture, keep L..U WIDE even with a matching name — identity does not create a catalog price from a photo.

Step 4 — Other fields:
- predicted_cost = round((L + U) / 2, 2).
- model_release_year = first year this generation was sold new in {market}.
- model_end_year = last year this exact generation was sold new in {market}; null if still current or unknown. NEVER derive it from the acquisition date, pricing year, or visibleModelYearMax — the acquisition date only selects the pricing year.
- cost_range = "<L>-<U>" (no spaces, no currency symbol).
- valuation_confidence: 0.9+ when the exact SKU/product line is readable; 0.75–0.85 when class and tier are clear from form factor; 0.6–0.75 when tier is ambiguous; below 0.5 for a weak class/tier guess (may FAIL server-side, except furniture-like classes which the server softens).

Return null cost fields ONLY when the asset class cannot be determined from the image; when the class is clear but the SKU is unknown, estimate a tier-appropriate range (never null). The server only rejects GROSS discrepancies: it widens your range to L÷4 .. U×4 (wider still for furniture and industrial process equipment) before comparing. So do not pad L/U defensively to avoid a rejection — padding a range you believe is right makes the check meaningless. Set L/U to the range you actually believe from the image evidence, and widen it only when the evidence is genuinely ambiguous (unknown spec, variant, or configuration).

{market_examples}