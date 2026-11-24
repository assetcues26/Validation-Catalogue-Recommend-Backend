
=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For condition assessment: use the full-asset photo (whichever slot it is in).

=== CONDITION (concise) ===
Judge the asset's physical state from the full-asset photo. Return ONLY these three keys — no other
condition fields, no damage array. The server fills the remaining schema fields itself.

"condition": {
  "grade": "Excellent"|"Good"|"Fair"|"Poor"|"Bad",
  "condition_score": 1-10 integer (10 = perfect; server maps this to overall_score),
  "condition_summary": "ONE sentence, max 25 words, plain text. State the overall state and name any
     visible damage or wear that matters. This single line is the entire condition report shown to
     the reviewer, so make it specific: 'Good condition, light scuffing on the left panel' beats
     'Good condition'."
}

Grade mapping: Excellent/Good = light cosmetic wear only; Fair = multiple moderate issues or one
functional concern; Poor/Bad = severe damage or major functional failure.
Class tolerance — vehicle bumper dents/scratches, industrial surface rust, and furniture scuffs are
normal wear: do NOT drop the grade for them. A cracked screen on a phone/laptop/tablet, or a broken
control panel on IT equipment, IS a functional defect and must lower the grade.
