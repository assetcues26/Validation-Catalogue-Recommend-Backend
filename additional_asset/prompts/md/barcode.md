<!-- section: rules -->
Phase 1 — Virtual optical scan (per tag):
  1. Multi-angle scan: mentally rotate 0°, 90°, 180°, 270° until horizontal.
  2. Virtual zoom 5x–10x on each tag region; account for curved labels (mentally flatten).
  3. Count visible characters BEFORE reading; verify count AFTER reading.

Phase 2 — Character reading (two-pass per tag):
  1. First pass: read left-to-right, character-by-character.
  2. Second pass: read right-to-left (backwards verification).
  3. Preserve ALL leading zeros and internal whitespace exactly.
  4. Consecutive zeros — MANDATORY PROCEDURE (do this BEFORE assembling the full number):
     a. After rotating the tag to horizontal, isolate the digit string visually.
     b. If a run of 3 or more consecutive zeros appears (typical after a leading 1), PAUSE — do not assemble the full number yet.
     c. Tally zeros only: point to each closed oval stroke; assign an exact integer zero_run_count.
     d. Re-tally right-to-left; if LTR and RTL zero_run_count disagree → ENTIRE tag is UNREADABLE.
     e. Glare / bloom rule: a bright vertical streak or soft white blob next to a bezel is NOT a digit — only count ink with a clear closed oval outline. Never invent a phantom extra zero.
     f. Vertical sticker rule: when text is printed vertically on a bezel, zeros stack as separate ovals — count each oval; never pad or shorten to a "familiar" length (e.g. do not turn 3 zeros into 4).
     g. Thin gaps or smudges between zeros are still separate digits; do not merge or split carelessly.
     h. Assemble only after tally: leading digits + exactly zero_run_count zeros + remaining digits. Total character count must equal the Phase 1 visual count.
     i. If zero_run_count is uncertain (could be 3 vs 4 vs 5) → ENTIRE tag is UNREADABLE — do NOT guess.
     j. When a zero-run was present, include zero_run_count=N in tag_detection_reasoning (e.g. zero_run_count=3).
  5. Distinguish: 0/O, 1/I/l, 5/S, 8/B, 6/G, 6/8, 2/Z, 3/8.
  6. Max 20 characters per tag — omit tags where visual count > 20 or any digit uncertain.
  7. If ANY single digit is ambiguous between two possibilities (e.g. could be 6 or 8), the ENTIRE tag is UNREADABLE — do NOT pick the more likely digit.

Phase 3 — Unreadable conditions (strict):
  Only include tags read with full confidence. Never guess. Zero error tolerance.
  If you cannot clearly see and distinguish EACH individual digit stroke, return "UNREADABLE".
  QR code content is NOT the asset tag number — only read human-visible printed/stamped digits.

Phase 4 — Hallucination prevention (critical):
  NEVER fabricate or infer a tag number. NEVER echo example numbers from these instructions.
  Common hallucination patterns to watch for:
  - Round numbers like 1000000000, 0000000000, 1234567890 — these are almost never real asset tags.
  - If you find yourself "reading" a suspiciously uniform number (e.g. all same digit, all zeros after a prefix), STOP and re-examine at maximum zoom.
  - If digits are too small, blurry, or obscured to distinguish individual strokes, the tag is UNREADABLE — do NOT output a guess.
  - If only a few digits are confidently readable but others are uncertain, the ENTIRE tag is UNREADABLE.

<!-- section: phase1 -->
=== PRIORITY 1: BARCODE & SERIAL NUMBERS (zero-error tolerance, multi-tag) ===

Scan BOTH images for ALL distinct asset tags, barcodes, and serial numbers:
- IMAGE 1 (barcode close-up): every tag/sticker in frame
- IMAGE 2 (full asset): any visible asset tags, serial labels, or barcode stickers on the asset body

{barcode_rules}

"detectedtagnumbers": array of ALL distinct readable tag/serial strings found (deduplicated).
"detectedtagnumber": clearest primary tag (first/best), or "UNREADABLE" if none readable.
"tag_detection_reasoning": max 25 words (count of tags found, locations); if a zero-run was present include zero_run_count=N.
"barcodeposition": max 25 words — location of primary tag on asset and in frame.
"tag_number_status": MANDATORY — one of:
  - "readable" — a tag/barcode sticker is visible AND its digits were fully read
  - "present_but_unreadable" — a tag/barcode sticker IS physically visible in the image but digits cannot be read (blur, damage, angle, glare, obstruction)
  - "not_found" — NO asset tag or barcode sticker exists anywhere in the images

<!-- section: retry -->
Read ALL asset tag / barcode / serial numbers in this image.

=== ZERO ERROR TOLERANCE — MULTI-TAG BARCODE READING ===

Scan the ENTIRE image for every distinct asset tag, barcode sticker, or serial number.

{barcode_rules}
  If NO tag is fully readable, return "detectedtagnumbers": [] and "detectedtagnumber": "UNREADABLE".

Return JSON only:
{{
  "detectedtagnumbers": ["<tag1>", "<tag2>"],
  "detectedtagnumber": "<clearest primary tag or UNREADABLE>",
  "tag_detection_reasoning": "<max 25 words>",
  "tag_number_status": "<readable | present_but_unreadable | not_found>"
}}
