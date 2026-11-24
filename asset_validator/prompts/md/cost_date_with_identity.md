
=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For cost identification: use the full-asset photo (whichever slot it is in). Service labels on tracking tags are NOT manufacturer brand.

=== VALIDATION CONTEXT ===
Current Date: {current_date}
Acquisition Date: {acquisition_date}
Declared Cost {currency}: {declared_cost}

{declared_record}

=== COST / DATE (INDIAN market — INR only) ===
Derive identity and price from **AssetName, Description, and the asset image(s)** together.
Use detectedAsset, imageAnalysis, visibleModelYearMin/Max, and inspection_reasoning from this same response.
Parse specific configuration from the declared record when stated — physical dimensions, unit/seat count, capacity/tonnage, and CPU/RAM/storage/screen size/variant. Stated size drives furniture pricing.
Set a **tighter** cost_lower_bound / cost_upper_bound anchored to that specific SKU or configuration for the pricing year.

Do NOT use declared cost for pricing — use acquisition date only to select the pricing year.

User Acquisition Date: {acquisition_date}

**Pricing year rule:**
- If acquisition date is provided → L/U = {market} **new corporate buying price range in the acquisition year** (NOT resale/used/refurbished/FMV, NOT today's price)
- If acquisition date is not provided → L/U = {market} **new corporate buying price range as of today**

Before filling cost fields, complete valuation_deliberation_notes in inspection_reasoning:
  - Document how AssetName/Description and image evidence combine to identify the configuration
  - State pricing year used (acquisition year or today)
  - Cite specific evidence before stating the market range

Tasks:
1. IDENTIFIED_MODEL: FULL make/model + configuration from AssetName, Description, and readable labels on the asset body.

2. ASSET_CLASS_DETECTED: Short class label (e.g. laptop, printer, office_chair, induction, hvac, industrial, furniture, other).
   For desks/tables/modular workstations/cabinets/custom office furniture → use "furniture" (or office_chair). model_end_year null. Not for IT PC/laptop workstations.
   Keep L–U **wide** when the record states no size — a photo cannot pin custom/assembled cost. When dimensions or unit counts ARE stated, narrow the range around that stated size instead.

3. VISUAL_TIER: One of entry | standard | premium | industrial — from image form factor and stated specs in Description.

4. TIER_EVIDENCE: Max 30 words citing visible proof and/or stated specs for visual_tier.

5. COST_LOWER_BOUND (L) and COST_UPPER_BOUND (U): {market} **new** corporate buying price range for the **identified configuration** in the pricing year.
   NOT used/resale/refurbished/FMV. Set L and U from identity + image evidence — do NOT compute as predicted_cost × 0.75/1.25.
   Range should be **narrower** than a generic class guess because name/description are confirmed.
{cost_calibration}

6. PREDICTED_COST: Midpoint (L + U) / 2 — round to 2 decimal places. Must lie within [L, U].

7. COST_RANGE: string "<L>-<U>" with no spaces or currency symbols (e.g. "1800-2800").

8. MODEL_RELEASE_YEAR: 4-digit year this model generation first sold new in {market}.
   When acquisition date is provided: model_release_year must be ≤ acquisition year (server validates separately).

9. MODEL_END_YEAR: 4-digit last year this exact hardware generation was sold new in {market}. Null if still current or unknown.
   Today is {current_date} — if this model is still being manufactured and sold as of today, model_end_year MUST be null.
   If you are unsure whether the model has been discontinued, return null — do NOT guess or estimate.
   NEVER derive model_end_year from the user's acquisition date. Acquisition date minus 1/2/3 years is NOT a valid end year.
   NEVER set model_end_year to the user's acquisition date, pricing year, or visibleModelYearMax — use product lifecycle knowledge only.

10. VALUATION_CONFIDENCE: 0.0–1.0 — higher when specific SKU/config is clear from name + description + image.

11. CURRENCY: always "{currency}".

=== MANDATORY SELF-CHECK (complete BEFORE returning cost fields) ===
After estimating the market range, perform these checks inside valuation_deliberation_notes:
  A. IDENTITY CHECK: Does identified_model match AssetName/Description and image?
  B. PRICING YEAR CHECK: Did you use acquisition-year new buying price when date is provided?
  C. RANGE CHECK: Is L ≤ predicted_cost ≤ U and is the range appropriate for the stated configuration?
  D. SANITY CHECK: Is the range realistic for {market} corporate new procurement in that year?
  E. Ensure predicted_cost = (L + U) / 2 and cost_range matches L-U

Add to the same JSON object:
  "identified_model": "<full brand + model or null>",
  "asset_class_detected": "<short class or null>",
  "visual_tier": "<entry|standard|premium|industrial or null>",
  "tier_evidence": "<max 30 words or null>",
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
