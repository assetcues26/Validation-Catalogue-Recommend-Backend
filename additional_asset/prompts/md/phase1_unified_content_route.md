=== CONTENT-BASED DUAL IMAGE ROUTING (MANDATORY) ===
Two images are always provided as Image 1 and Image 2. Upload field names (AssetImage / BarcodeImage)
and upload order are UNTRUSTED — they may be swapped or mislabeled.

Before filling any field, classify EACH image from pixels only:
- (a) tag_closeup — zoomed barcode / asset-tag sticker; asset body only partial or incidental
- (b) full_asset — stand-back (or clear) view of the equipment suitable for identity + condition
- (c) other / empty — blank, packaging-only, or unusable

Routing rules (content wins; never trust slot names):
1. Use the full_asset view for: imageAnalysis, detectedAsset, detected_description, condition,
   full_asset_photo_present, visible_assets, visibleModelYear*, expected_asset_visible.
2. Use the tag_closeup view for: detected_codes, detectedtagnumber, tag_detection_reasoning,
   barcodeposition, barcodereadability, tag_number_status. Still scan BOTH images for codes.
3. If BOTH look like full_asset: pick the clearer whole-object view for identity/condition;
   still scan both for tags.
4. If BOTH look like tag_closeup: read tags from both; for identity use whatever asset surface
   is visible; set full_asset_photo_present=false when no proper stand-back view exists.
5. Cross-reference: the tagged primary asset is the host object the sticker is attached to
   (never the sticker logo brand). Do NOT invent an asset that neither image shows.

There is NO prior blind-view classification and NO "Image 1 = asset / Image 2 = tag" hint.
Decide roles from pixels, then fill one JSON object.

[[VARCTX_START]]
=== EXPECTED ASSET (from request — disambiguation hint only) ===
Expected Asset Name: {expected_asset_name}
Expected Description: {expected_description}
Expected Category / Subcategory: {expected_category} / {expected_subcategory}
[[VARCTX_END]]

HOW TO USE THIS HINT — read carefully:
- Photos are taken in busy workplaces. A single frame almost always contains SEVERAL assets
  (a monitor on a desk, chairs around a table, equipment on a rack). The asset being validated
  is often NOT the largest or most eye-catching object in the frame.
- Scan the ENTIRE image and list every distinct asset you can see. Only then choose the primary one.
- If an object matching the Expected Asset IS genuinely visible anywhere in the frame, select THAT
  object as the primary tagged asset — even when another object is closer, larger, or brighter.
- The hint NEVER creates evidence. If nothing resembling the Expected Asset is visible, do NOT
  pretend it is there. Set "expected_asset_visible": false.
- Never copy brand, model, or size figures from the hint. Report only what the image shows.

PRIMARY ASSET SELECTION — apply in this order:
1. Tag anchor: the object the asset-tag sticker is physically attached to wins outright.
2. Expected-asset match: if no tag anchor is visible, pick the visible object matching the hint.
3. Visual dominance: only when neither 1 nor 2 applies, pick the most prominent asset.

CIVIL / STRUCTURE EXCEPTION — rule 1 does NOT apply when the Expected Asset or its Category names
a structure or land improvement (building, shed, road, drain, fencing, "Land and Building", etc.).
Report the STRUCTURE itself; the pole/column holding the tag is only a mounting point.

=== ASSET IDENTIFICATION ===
Analyze BOTH images. Cross-reference the full-asset view and the tag close-up (whichever slots
they landed in after your content classification).

EMPTY-FRAME EXCEPTION (rare): if an image is entirely black/blank with no object at all, set
"imageReadability"="E", "detectedAsset"=null, "detected_description"=null, report any tag as
unreadable, and make "imageAnalysis" say what the frame shows. This applies ONLY to an
essentially empty frame.

CRITICAL - DO NOT CONFUSE BARCODE STICKER WITH ASSET BRAND:
- The tag close-up may show a client company sticker with their logo
- This sticker logo is NOT the asset manufacturer brand
- ONLY look at the full-asset view to identify manufacturer branding on the asset body
- IGNORE any logos visible on the barcode sticker

1. "imageAnalysis": COMPREHENSIVE ASSET ANALYSIS (~60 words) — Focus on the full-asset view.
   Later steps see ONLY this text, never the image. Always capture SIGNAGE (verbatim) and
   BUILT SETTING when relevant. Photos may be rotated — read text in any orientation.
   - TAG DEFINES PRIMARY ASSET: the tagged asset is the host object the sticker is attached to.
   - MOUNTING SURFACES ARE NOT ASSETS: a board/bracket that only carries a device is not the asset.
   - CONTAINER vs CONTENTS: tag on cage/rack → structure is primary; contents are secondary.
   - FURNITURE vs ELECTRONICS: if sticker is on desk/table surface, furniture is primary.
   - MULTIPLE ASSETS: list ALL distinct assets; name the PRIMARY tagged one clearly.
   - Include manufacturer brand ONLY if visible on the PRIMARY ASSET BODY.

2. "imageReadability": "Y" if the asset is clearly visible in at least one image; else "E".

2b. "full_asset_photo_present": true / false — from what the images ACTUALLY show.
   - true  = a proper full view of the asset exists
   - false = only tag/barcode close-ups; asset only partial/incidental

3. "detectedAsset": Brief description of the PRIMARY TAGGED asset from the full-asset view
   (object the sticker is attached to — not the sticker). Include manufacturer brand only if
   clearly visible on the asset body.

4. "detected_description": Exactly one sentence, max 20 words, plain text.

5. "visibleModelYearMin": 4-digit year or null
6. "visibleModelYearMax": 4-digit year or null

7. "visible_assets": array of short strings — every distinct asset visible, primary first.

8. "expected_asset_visible": true / false / null — from the image alone; null if no hint.

DO NOT confuse tracking stickers (company logos on asset tags) with manufacturer brand.

=== TAG / BARCODE READING ===
Scan BOTH images for all readable codes. Prefer the tag_closeup view for primary tag digits,
but never skip the other image.

CRITICAL - READ ONLY WHAT IS PRINTED IN THE IMAGE:
- Read ONLY numbers/codes physically visible in the images
- Do NOT use, guess, infer, or echo any externally provided tag number
- NEVER output example numbers or placeholders from these instructions
- Classify the client's PRIMARY numeric asset tracking sticker as type "asset_tag"
- Classify site/location codes, part numbers, model numbers as "printed_number"
- Classify manufacturer serial plates as "serial"

{barcode_rules}

4. "detected_codes": MULTI-BARCODE SCAN (MANDATORY):
   Scan BOTH images for ALL machine-readable codes and human-readable number labels.
   Return an array of objects with ONLY "type" and "value".
   Types: "asset_tag", "barcode", "serial", "qr", "printed_number"
   - "asset_tag": ONLY the primary client numeric tracking number on the company sticker
   - Never invent a number; omit asset_tag if not visible

5. "detectedtagnumber": Best single primary asset tag read (asset_tag type only).

6. "tag_detection_reasoning": Max 60 words — list every detected code with type; note rotation/zoom.

7. "barcodeposition": Max 25 words — where the tag sits on the asset and in the frame.

8. "barcodereadability": "Y" | "E" | "NA"

9. "tag_number_status": "readable" | "present_but_unreadable" | "not_found"

=== CONDITION ===
{condition_extension}

=== OUTPUT SCHEMA ===
Return one JSON object with ONLY these keys:
"detected_codes", "detectedtagnumber", "tag_detection_reasoning", "barcodeposition", "barcodereadability", "tag_number_status",
"imageAnalysis", "detectedAsset", "detected_description", "full_asset_photo_present", "visibleModelYearMin", "visibleModelYearMax",
"visible_assets", "expected_asset_visible",
"inspection_reasoning", "condition"

No markdown.
IMPORTANT: There is no user-declared asset name or tag number. Observe the photos and extract what is visible. Use null when uncertain.
IMPORTANT: Classify each image by content before filling fields. Do not trust upload field names or Image 1/Image 2 order as role labels.
