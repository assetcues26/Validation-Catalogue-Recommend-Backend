
=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For condition assessment: use the full-asset photo (whichever slot it is in).

=== DAMAGE REASONING (required before condition grade) ===
Fill "inspection_reasoning" in the same JSON object:
"inspection_reasoning": {
  "damage_notes": "Synthesize defects visible in asset image.",
  "repair_judgement_notes": "Apply asset-class repair tolerance (see CONDITION section).",
  "uncertainty_flags": []
}
