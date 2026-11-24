
=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For tag/barcode reading: scan BOTH images for all readable codes.

Analyze the provided images carefully. You will receive TWO types of images:
1. ASSET IMAGE — Shows the full asset/equipment
2. BARCODE IMAGE — Close-up of the barcode/asset tag sticker ONLY

⚠️ CRITICAL - READ ONLY WHAT IS PRINTED IN THE IMAGE:
- Read ONLY numbers/codes physically visible in the images
- Do NOT use, guess, infer, or echo any externally provided tag number
- NEVER output example numbers, sample codes, or placeholder values from these instructions — format illustrations only
- Classify the client's PRIMARY numeric asset tracking sticker as type "asset_tag" (digits only, variable length)
- Classify site/location codes (e.g. site codes with letters and dashes), part numbers (long alphanumeric COMP… labels), and model numbers as type "printed_number" — NOT asset_tag
- Classify manufacturer serial plates as type "serial"

⚠️ CRITICAL - DO NOT CONFUSE BARCODE STICKER WITH ASSET BRAND:
- The BARCODE IMAGE may show a client company sticker with their logo (Americana, Cipla, TCS, etc.)
- This sticker logo is NOT the asset manufacturer brand — it is just the client's tracking tag
- IGNORE logos visible on the barcode sticker — they are irrelevant to tag reading

{barcode_rules}

4. "detected_codes": MULTI-BARCODE SCAN (MANDATORY — internal processing field):
   Scan BOTH images for ALL machine-readable codes and human-readable number labels.
   Include: asset tracking stickers, 1D/2D barcodes, QR codes, manufacturer serial plates, printed numbers.
   Do NOT stop after the first code. List every distinct code found, ordered top-to-bottom, left-to-right.
   Return an array of objects, each with:
   - "type": one of "asset_tag", "barcode", "serial", "qr", "printed_number"
   - "value": the exact characters read (or "UNREADABLE" for that specific code if unclear)
   - Each object has ONLY "type" and "value" — no other fields

   TYPE CLASSIFICATION (critical):
   - "asset_tag": ONLY the primary client numeric tracking number printed on the company asset-tag sticker (usually beside a QR code; digits only, variable length)
   - If the same number appears both as printed digits AND inside a 1D barcode, classify the printed client sticker digits as "asset_tag" (not "barcode")
   - "printed_number": site/location codes, part numbers, model numbers, short printed labels
   - "serial": manufacturer serial plates
   - "barcode" / "qr": machine-readable barcode or QR content (HP/Dell/support QR stickers are "qr", NOT asset_tag)

   READING RULES (apply to EACH code separately):
   - MULTI-ANGLE SCAN: Mentally rotate 90°/180°/270° until text is horizontal
   - VIRTUAL OPTICAL ZOOM: Crop background and zoom 5X-10X on each label
   - Read EXACTLY as printed, character-by-character, verify twice
   - PRESERVE ALL ZEROS: leading zeros must be preserved exactly as printed
   - ZERO-RUN (mandatory): for 3 or more consecutive zeros, tally each closed oval stroke at max zoom; assign zero_run_count=N; re-tally LTR and RTL — mismatch → UNREADABLE; glare/bloom next to a bezel is NOT a digit (no phantom zero); never pad/shorten a vertical bezel zero stack to a familiar length
   - Keep all spaces/dashes exactly as printed
   - Distinguish: 0 vs O, 1 vs I vs l, 5 vs S, 8 vs B, 6 vs G, 2 vs Z, 3 vs 8
   - MAX LENGTH for asset_tag type: If > 20 characters → value "UNREADABLE" for that entry
   - NEVER guess — mark individual codes "UNREADABLE" rather than guessing
   - If no company asset-tag digits are visible, omit asset_tag entries (do not invent a number)

5. "detectedtagnumber": Your best single primary asset tag read (asset_tag type only). Server resolves final value from detected_codes.

6. "tag_detection_reasoning": MANDATORY REASONING (Max 60 words):
   - MUST list EVERY detected code with type label from detected_codes
   - Format: "Found [N] codes: AssetTag=[value or UNREADABLE], Serial=[value], Barcode=[value]. Rotated [Angle]°. Zoomed [Level]. [Match status]. [Confidence]."
   - When a zero-run was present, MUST include zero_run_count=N (e.g. zero_run_count=3)
   - Use only values actually read from the image — never copy from these instructions

7. "barcodeposition": A highly descriptive location of the barcode (max 25 words). Describe its position on the asset itself (e.g., "on the back panel, near the bottom edge") AND its general position within the image frame (e.g., "center-right of the image").

8. "barcodereadability": MANDATORY — one of:
   - "Y" — company asset_tag digits were fully read
   - "E" — tag/barcode sticker IS visible but asset_tag digits cannot be read (blur, damage, angle, glare)
   - "NA" — no company asset tag sticker to evaluate (not yet applied, or only serial/other codes visible)

9. "tag_number_status": MANDATORY — one of:
   - "readable" — a tag/barcode sticker is visible AND its digits were fully read
   - "present_but_unreadable" — a tag/barcode sticker IS physically visible in the image but digits cannot be read (blur, damage, angle, glare, obstruction)
   - "not_found" — NO asset tag or barcode sticker exists anywhere in the images

Return one JSON object only. No markdown.
