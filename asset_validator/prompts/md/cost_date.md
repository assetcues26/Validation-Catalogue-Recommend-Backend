
=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For cost identification: use the full-asset photo (whichever slot it is in). Service labels on tracking tags are NOT manufacturer brand.

=== VALIDATION CONTEXT ===
Current Date: {current_date}
Acquisition Date: {acquisition_date}
Declared Cost {currency}: {declared_cost}

=== COST / DATE (INDIAN market — INR only) ===
Derive identity and price **solely from the asset image(s)** — use detectedAsset, imageAnalysis, visibleModelYearMin/Max, and inspection_reasoning from this same response.
Do NOT use request metadata, asset names, descriptions, or declared make/model hints for identification or tier.

User Acquisition Date: {acquisition_date}

**Pricing year rule:**
- If acquisition date is provided → L/U = {market} **new corporate buying price range in the acquisition year** (NOT resale/used/refurbished/FMV, NOT today's price)
- If acquisition date is not provided → L/U = {market} **new corporate buying price range as of today**

Before filling cost fields, complete valuation_deliberation_notes in inspection_reasoning:
  - Document visible setting, mounting, build quality, brand on asset body, and tier chosen
  - State pricing year used (acquisition year or today)
  - Cite specific image evidence before stating the market range

Visual tier rules (image-only):
  - Read product-line text on asset body (molded/printed names) before pricing — e.g. Turbo Force, HD, Pro
  - Small fixed bullet/dome CCTV (IR ring, no PTZ) → entry CCTV tier (low thousands INR new India), not mid/enterprise
  - Large wall fan with metal blades and speed controls on motor head → premium wall-fan tier (high thousands), not domestic ₹3k class
  - Warehouse setting alone does not justify premium — use unit size and visible features
  - Do NOT use declared cost for pricing

Tasks:
1. IDENTIFIED_MODEL: FULL make/model or product line when readable on the asset body (brand + line/SKU from nameplate or molded text). Read housing labels before generic class names like "bullet camera" or "wall fan".

2. ASSET_CLASS_DETECTED: Short class label (e.g. laptop, printer, office_chair, induction, hvac, industrial, furniture, other).
   For desks/tables/modular workstations/cabinets/custom office furniture → use "furniture" (or office_chair). Wide L–U; model_end_year null. Do NOT apply to IT PC/laptop workstations or desk/table fans.

3. VISUAL_TIER: One of entry | standard | premium | industrial — class-relative tier from image form factor, size, and features (NOT from brand prestige or warehouse setting alone).

4. TIER_EVIDENCE: Max 30 words citing visible proof for visual_tier (mounting, size, metal blades, IR ring, speed controls, product-line text on body).

5. COST_LOWER_BOUND (L) and COST_UPPER_BOUND (U): {market} **new** standard/base-config **corporate buying price range** for the pricing year (see rule above).
   NOT used/resale/refurbished/FMV; NOT maxed-out SKU; NOT US price converted loosely.
   Set L and U from image evidence and tier — do NOT compute as predicted_cost × 0.75/1.25.
{cost_calibration}

6. PREDICTED_COST: Midpoint (L + U) / 2 — round to 2 decimal places. Must lie within [L, U].

7. COST_RANGE: string "<L>-<U>" with no spaces or currency symbols (e.g. "1800-2800").

8. MODEL_RELEASE_YEAR: 4-digit year this model generation first sold new in {market}. Must align with visible hardware era.
   When acquisition date is provided: model_release_year must be ≤ acquisition year (server validates separately).

9. MODEL_END_YEAR: 4-digit last year this exact hardware generation was sold new in {market} (e.g. MacBook Pro 15-inch 2016–2019 → 2019). Null if still current or unknown.
   Today is {current_date} — if this model is still being manufactured and sold as of today, model_end_year MUST be null.
   If you are unsure whether the model has been discontinued, return null — do NOT guess or estimate.
   NEVER derive model_end_year from the user's acquisition date. Acquisition date minus 1/2/3 years is NOT a valid end year.
   NEVER set model_end_year to the user's acquisition date, pricing year, or visibleModelYearMax — use product lifecycle knowledge only.
   Acquisition date is for pricing year only, not for inferring discontinuation.

10. VALUATION_CONFIDENCE: 0.0–1.0 — confidence in the market range (see calibration rules).

11. CURRENCY: always "{currency}".

Uncertainty / spec ambiguity (image-only):
- When asset CLASS is clear but exact SKU or configuration cannot be determined from the photo alone, STILL provide cost_lower_bound and cost_upper_bound.
- Set L and U to cover **plausible spec variants** visible in the same product line (e.g. base-config vs high-spec laptop that look identical in the photo). Do NOT shrink the range to a single guess.
- Example: generic Dell/HP/Lenovo laptop photo with no readable spec sticker → range should span entry and mid-tier new {market} corporate prices for that class in the pricing year.
- Return null cost fields ONLY when the asset class itself cannot be determined from the image.

=== MANDATORY SELF-CHECK (complete BEFORE returning cost fields) ===
After estimating the market range, perform these checks inside valuation_deliberation_notes:
  A. TIER CHECK: Does your visual_tier match the observed form factor?
  B. PRICING YEAR CHECK: Did you use acquisition-year new buying price (not resale/today) when date is provided?
  C. RANGE CHECK: Is L ≤ predicted_cost ≤ U and does the range match the visual tier?
  D. SANITY CHECK: Is the range realistic for {market} corporate new procurement in that year?
  E. Ensure predicted_cost = (L + U) / 2 and cost_range matches L-U

Add to the same JSON object:
  "identified_model": "<full brand + model or null>",
  "asset_class_detected": "<short class or null>",
  "visual_tier": "<entry|standard|premium|industrial or null>",
  "tier_evidence": "<max 30 words from image or null>",
  "predicted_cost": <number or null>,
  "cost_lower_bound": <number or null>,
  "cost_upper_bound": <number or null>,
  "cost_range": "<lower>-<upper> or null>",
  "currency": "{currency}",
  "model_release_year": <integer or null>,
  "model_end_year": <integer or null>,
  "valuation_confidence": <0.0-1.0>,
  "cost_reasoning": "<max 40 words — {market} new corporate buying price, cite pricing year>",
  "release_year_reasoning": "<max 40 words>"
