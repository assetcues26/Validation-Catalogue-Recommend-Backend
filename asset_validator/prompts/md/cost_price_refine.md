[[VARCTX_START]]
Re-estimate cost from the asset image{identity_mode_label} — pass 1 may have the wrong visual tier or price range.

{identity_context}

Context (from vision{identity_context_note}):
- Detected Asset: {detected_asset}
- Image Analysis: {image_analysis}
- Visible hardware era: {visible_era}
- User Acquisition Date: {acquisition_date}
- Current Date: {current_date}
- Market: {market} | Currency: {currency} only
[[VARCTX_END]]

**Pricing year:** If acquisition date provided → new corporate buying price in **acquisition year** (NOT resale/used). Else → today.

[[VARCTX_START]]
Pass 1 estimate (may be wrong — re-examine the image):
- identified_model: {pass1_identified_model}
- visual_tier: {pass1_visual_tier}
- predicted_cost: {pass1_predicted_cost}
- valuation_confidence: {pass1_confidence}
[[VARCTX_END]]

Instructions:
1. Re-inspect the asset image. Pass 1 often mis-prices when tier is wrong or uses today's/resale price for a historical acquisition.
2. Set visual_tier from image evidence: entry | standard | premium | industrial
3. Set tier_evidence citing visible form factor, size, mounting, controls, and product-line text on the body.
4. Set cost_lower_bound (L) and cost_upper_bound (U) for {market} **new corporate buying price** in the pricing year matching the corrected visual_tier.
5. Set predicted_cost = (L + U) / 2 and cost_range = "L-U".
6. Do NOT use declared cost for pricing. Do NOT use used/resale/refurbished/FMV pricing.
7. Do NOT derive bounds from predicted_cost × 0.75/1.25 — L and U must come from image evidence.
8. COMMON PASS 1 ERRORS TO CORRECT:
   - Historical acquisition priced with today's used/resale values (e.g. 2019 MacBook at ₹45k instead of ~₹1.5L–2.2L new 2019)
   - Small fixed bullet/dome CCTV priced at ₹10,000+ when range should be ~₹1,800–2,800 (entry tier)
   - Large premium wall fans priced at ₹2,000–4,000 when range should be ~₹6,500–9,000 (premium tier)
   - Generic desk/table/modular furniture priced as a tight SKU — use wide L–U, asset_class_detected "furniture", model_end_year null

{cost_calibration}

Return JSON only:
{{
  "identified_model": "<string or null>",
  "asset_class_detected": "<string or null>",
  "visual_tier": "<entry|standard|premium|industrial or null>",
  "tier_evidence": "<max 30 words from image>",
  "predicted_cost": <number or null>,
  "cost_lower_bound": <number or null>,
  "cost_upper_bound": <number or null>,
  "cost_range": "<lower>-<upper> or null>",
  "currency": "{currency}",
  "model_release_year": <integer or null — must be ≤ acquisition year when date provided>,
  "model_end_year": <integer or null — last year generation sold new in {market}; null if still current/unknown; NEVER derive from acquisition date>,
  "valuation_confidence": <0.0-1.0>,
  "cost_reasoning": "<max 40 words — {market} new buying price, cite pricing year>",
  "release_year_reasoning": "<max 40 words>"
}}
