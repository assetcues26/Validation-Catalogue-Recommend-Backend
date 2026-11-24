
Return one JSON object with ONLY these keys:
"imageAnalysis", "detectedAsset", "detected_description", "imageReadability", "visibleModelYearMin", "visibleModelYearMax",
"visible_assets", "expected_asset_visible",
"inspection_reasoning", "identified_model", "asset_class_detected", "visual_tier", "tier_evidence",
"model_release_year", "model_end_year", "predicted_cost", "cost_lower_bound", "cost_upper_bound", "cost_range",
"cost_reasoning", "release_year_reasoning", "valuation_confidence"

cost_lower_bound and cost_upper_bound are the authoritative model-estimated market range (L, U) — not derived from predicted_cost × fixed multipliers.
When image-only: wider L–U when specs are ambiguous. When name/description confirmed: tighter L–U for the identified configuration.
When acquisition date is provided: price for {market} new corporate buying in acquisition year — NOT resale/used.
predicted_cost must be midpoint (L + U) / 2. Do NOT derive bounds from predicted_cost × fixed multipliers.

No markdown.
