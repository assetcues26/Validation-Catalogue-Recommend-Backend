
=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For asset identification, condition, and cost: use the full-asset photo (whichever slot it is in).

=== STEP 1 — REASONING (required before conclusions) ===
Fill "inspection_reasoning" completely in the same JSON object:
"inspection_reasoning": {
  "identity_candidates": [
    {"label": "brand + model", "visual_cues_for": ["..."], "visual_cues_against": ["..."], "confidence": 0.0-1.0}
  ],
  "selected_identity_rationale": "Why chosen brand/model won; cite ports, bezels, nameplate, camera layout. NEVER guess iPhone/generation from color alone.",
  "age_estimation_rationale": "Weigh mfg date stickers, serial format, design generation, wear vs acquisition date if provided.",
  "valuation_deliberation_notes": "Reason in India ₹ about like-new replacement for IDENTIFIED brand/model; flag if MRP uncertain.",
  "uncertainty_flags": [],
  "damage_notes": "Synthesize defects visible in asset image.",
  "repair_judgement_notes": "Apply asset-class repair tolerance (see CONDITION section)."
}

Rules:
- identity_candidates[]: at least TWO for phones/laptops/tablets.
- uncertainty_flags[] when appropriate: generation_ambiguous, image_quality_low, partial_view, label_unreadable, age_uncertain, valuation_uncertain.
- If top identity confidence would be below 0.75: add uncertainty_flags, do NOT invent a specific generation.
- If model year uncertain by more than ~2 years: widen visibleModelYearMin/Max, add age_uncertain.

Also set (internal — stripped before API response):
  "confidence_asset_name": 0.0-1.0,
  "valuation_confidence": 0.0-1.0
