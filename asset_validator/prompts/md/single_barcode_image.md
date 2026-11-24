
=== SINGLE BARCODE / TAG IMAGE ===
One barcode or asset-tag close-up image is provided. Read all codes visible in this image only.

⚠️ CRITICAL - READ ONLY WHAT IS PRINTED IN THE IMAGE:
- Read ONLY numbers/codes physically visible in the image
- Do NOT use, guess, infer, or echo any externally provided tag number
- NEVER output example numbers or placeholder values from these instructions
- Classify the client's PRIMARY numeric asset tracking sticker as type "asset_tag" (digits only, variable length)
- Classify site/location codes, part numbers, and model numbers as type "printed_number" — NOT asset_tag
- Classify manufacturer serial plates as type "serial"

⚠️ CRITICAL - DO NOT CONFUSE BARCODE STICKER WITH ASSET BRAND:
- The sticker may show a client company logo — IGNORE it for tag reading

{barcode_rules}

4. "detected_codes": MULTI-BARCODE SCAN (MANDATORY — internal processing field):
   Scan this image for ALL machine-readable codes and human-readable number labels.
   Include: asset tracking stickers, 1D/2D barcodes, QR codes, manufacturer serial plates, printed numbers.
   Do NOT stop after the first code. List every distinct code found, ordered top-to-bottom, left-to-right.
   Return an array of objects, each with:
   - "type": one of "asset_tag", "barcode", "serial", "qr", "printed_number"
   - "value": the exact characters read (or "UNREADABLE" for that specific code if unclear)
   - Each object has ONLY "type" and "value" — no other fields

   TYPE CLASSIFICATION (critical):
   - "asset_tag": ONLY the primary client numeric tracking number printed on the company asset-tag sticker (beside QR code; digits only)
   - If the same number appears both as printed digits AND inside a 1D barcode, classify the printed client sticker digits as "asset_tag" (not "barcode")
   - "printed_number": site/location codes, part numbers, model numbers, short printed labels
   - "serial": manufacturer serial plates
   - "barcode" / "qr": machine-readable barcode or QR content (manufacturer/support QR stickers are "qr", NOT asset_tag)

   READING RULES (apply to EACH code separately):
   - MULTI-ANGLE SCAN: Mentally rotate 90°/180°/270° until text is horizontal
   - VIRTUAL OPTICAL ZOOM: Crop background and zoom 5X-10X on each label
   - Read EXACTLY as printed, character-by-character, verify twice
   - PRESERVE ALL ZEROS: "00123" stays "00123" NOT "123"
   - ZERO-RUN (mandatory): for 3 or more consecutive zeros, tally each closed oval stroke at max zoom; assign zero_run_count=N; re-tally LTR and RTL — mismatch → UNREADABLE; glare/bloom next to a bezel is NOT a digit (no phantom zero); never pad/shorten a vertical bezel zero stack to a familiar length
   - Keep all spaces/dashes: "A-123-B" stays "A-123-B"
   - Distinguish: 0 vs O, 1 vs I vs l, 5 vs S, 8 vs B, 6 vs G, 2 vs Z, 3 vs 8
   - MAX LENGTH for asset_tag type: If > 20 characters → value "UNREADABLE" for that entry
   - NEVER guess — mark individual codes "UNREADABLE" rather than guessing

5. "detectedtagnumber": Your best single primary asset tag read (asset_tag type only). Server resolves final value from detected_codes.

6. "tag_detection_reasoning": MANDATORY REASONING (max 20 words):
   - List detected codes with type labels from detected_codes
   - When a zero-run was present, include zero_run_count=N

7. "barcodeposition": Location of the barcode on the asset and within the image frame (max 20 words).

8. "barcodereadability": MANDATORY — one of:
   - "Y" — company asset_tag digits were fully read
   - "E" — tag/barcode sticker IS visible but asset_tag digits cannot be read
   - "NA" — no company asset tag sticker to evaluate

9. "tag_number_status": MANDATORY — one of:
   - "readable" — a tag/barcode sticker is visible AND its digits were fully read
   - "present_but_unreadable" — a tag/barcode sticker IS physically visible in the image but digits cannot be read (blur, damage, angle, glare, obstruction)
   - "not_found" — NO asset tag or barcode sticker exists anywhere in the image

Return one JSON object only. No markdown.
