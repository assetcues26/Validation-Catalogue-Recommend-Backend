Analyze the asset for the INDIAN market with maximum accuracy — use **vision context and confirmed AssetName/Description** together.

[[VARCTX_START]]
Context (from vision):
- Detected Asset: {detected_asset}
- Image Analysis: {image_analysis}
- Visible hardware era (Phase 1 estimate): {visible_era}
- User Acquisition Date: {acquisition_date}
- Current Date: {current_date}
- Market: {market}
- Currency: {currency} ONLY — never any other currency

{declared_record}
[[VARCTX_END]]

**Pricing year:** If acquisition date provided → new corporate buying price range in **acquisition year** (NOT resale/used). If not provided → today's new buying price.

Do NOT use declared cost for pricing — use acquisition date only to select the pricing year.

Tasks:
1. IDENTIFIED_MODEL: FULL make/model + configuration from the declared record and vision context. Prefer stated dimensions/capacity/brand/SKU/specs in the record over a generic detectedAsset class label. Include the size when stated, e.g. "Reception table 2700x800x750/1050mm".

2. ASSET_CLASS_DETECTED: Short class label (laptop, printer, office_chair, furniture, hvac, industrial, etc.).
   For desks/tables/modular furniture workstations/cabinets → "furniture"; model_end_year null. Not IT PC/laptop workstations or desk/table fans.
   Keep L–U wide when the record gives no size; NARROW it around the stated size when dimensions or unit counts ARE given.

3. VISUAL_TIER: entry | standard | premium | industrial — from image form factor and stated specs in AssetName/Description.

4. TIER_EVIDENCE: Max 30 words citing visible proof and/or stated specs for visual_tier.

5. COST_LOWER_BOUND (L) and COST_UPPER_BOUND (U): {market} **new** corporate buying price range for the **identified configuration** in the pricing year.
   NOT used/resale/refurbished/FMV. Set L and U from confirmed identity + vision context — do NOT compute as predicted_cost × 0.75/1.25.
   Parse dimensions, unit/seat count, capacity, brand, SKU, CPU/RAM/tonnage/variant from the declared record when stated.
   Range should be **narrower** than a generic class guess because the record is confirmed.
   Scale the range to the stated size: a 2700mm two-tier reception counter belongs in a far higher
   band than a generic 1200mm desk. Do not return a small-table range for a large stated unit.
{cost_calibration}

6. PREDICTED_COST: Midpoint (L + U) / 2 — round to 2 decimal places.

7. COST_RANGE: string "<L>-<U>" with no spaces or currency symbols.

8. MODEL_RELEASE_YEAR: First year this generation sold new in {market}. When acquisition date provided: release year must be ≤ acquisition year (server validates separately).

9. MODEL_END_YEAR: Last calendar year this exact hardware generation was sold new in {market} (null if unknown or still current).
   Today is {current_date} — if this model is still being manufactured and sold as of today, model_end_year MUST be null.
   If you are unsure whether the model has been discontinued, return null — do NOT guess or estimate.
   NEVER derive model_end_year from the user's acquisition date. Acquisition date minus 1/2/3 years is NOT a valid end year.
   NEVER set model_end_year to the user's acquisition date, pricing year, or visibleModelYearMax — use product lifecycle knowledge only.

10. VALUATION_CONFIDENCE: 0.0–1.0 — higher when specific SKU/config is clear from name + description + vision.

11. CURRENCY: Always "{currency}".

Return JSON only:
{{
  "identified_model": "<full brand + model or null>",
  "asset_class_detected": "<string or null>",
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
  "cost_reasoning": "<max 40 words — {market} new buying price, cite pricing year>",
  "release_year_reasoning": "<max 40 words>"
}}
