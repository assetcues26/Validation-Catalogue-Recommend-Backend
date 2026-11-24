
=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For asset identification, condition, and cost: use the full-asset photo (whichever slot it is in).

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
  Example: expected "Reception Table", frame shows a monitor sitting on a white table →
  the primary asset is the TABLE, and the monitor is a secondary object resting on it.
- ⚠️ The hint NEVER creates evidence. If nothing resembling the Expected Asset is visible anywhere
  in the frame, do NOT pretend it is there. Report what you actually see and set
  "expected_asset_visible": false. A later step decides pass/fail — your job is honest observation.
- Never copy brand, model, or size figures from the hint into your output. Report only what the
  image shows on the asset body.

PRIMARY ASSET SELECTION — apply in this order:
1. Tag anchor: the object the asset-tag sticker is physically attached to wins outright.
2. Expected-asset match: if no tag anchor is visible, pick the visible object matching the hint.
3. Visual dominance: only when neither 1 nor 2 applies, pick the most prominent asset.

⚠️ CIVIL / STRUCTURE EXCEPTION — rule 1 does NOT apply when the Expected Asset or its Category names
a structure or land improvement: building, shed, godown, warehouse, room, cabin, canteen, pump house,
substation, block, platform, mezzanine, staircase, road, pathway, paving, parking, drainage, drain,
culvert, kerb, fencing, compound wall, boundary wall, gate, landscaping, yard, plinth, civil work —
or any Expected Category of "Land and Building" or "Civil Work".
- These assets CANNOT physically carry a tag. The surveyor fixes it to the nearest column, pole,
  wall, bollard or signboard. That host is a MOUNTING POINT, not the asset.
- So for these records: do NOT report the pole, the column, or a tank standing nearby as the primary
  asset. Reporting "support pole" for a road, or "storage tank" for a shed, is the failure to avoid.
- Report the STRUCTURE or LAND IMPROVEMENT itself as detectedAsset — "internal concrete road",
  "industrial storage shed", "storm water drain", "canteen and server room block".
- List the equipment standing in or on it as secondary entries in visible_assets.
- Set "expected_asset_visible": true when the structure is genuinely present in the frame — a roof,
  columns, walls, a road surface or a fence line all count as the structure being visible.

=== ASSET IDENTIFICATION ===
Analyze the provided images. Cross-reference BOTH the full asset photo and the barcode/tag close-up.

EMPTY-FRAME EXCEPTION (rare): if an image is entirely black/blank with no object at all, set
"imageReadability"="E", "detectedAsset"=null, "detected_description"=null, report any tag as
unreadable, and make "imageAnalysis" say what the frame shows (e.g. "Image is completely black,
no asset visible"). You MUST NOT describe the asset named in the declared record — the record is
not evidence of what the photo contains. This applies ONLY to an essentially empty frame: if ANY
object is visible, describe it in FULL detail as the steps below require (brand, model, variant,
capabilities, condition). Poor angle, glare, low light or a missing label are NOT this case.

⚠️ CRITICAL - DO NOT CONFUSE BARCODE STICKER WITH ASSET BRAND:
- The barcode close-up may show a client company sticker with their logo
- This sticker logo is NOT the asset manufacturer brand
- ONLY look at the full asset photo to identify the actual manufacturer brand on the asset body
- The asset body itself may have manufacturer branding like: Rheem, Dell, HP, LG, Samsung, Hitachi, Carrier, etc.
- IGNORE any logos visible on the barcode sticker

1. "imageAnalysis": COMPREHENSIVE ASSET ANALYSIS (~60 words) — Focus on the full asset photo.
   ⚠️ Later validation steps see ONLY this text, never the image. Anything you leave out here can
   never be checked. Two things are omitted most often and must always be captured:
   - SIGNAGE — quote verbatim any room board, door plate, department sign, bay marking or painted
     area label: "SERVER ROOM", "EXECUTIVE LUNCH ROOM", "SUBSTATION", "MCC ROOM", "STORES". For a
     building, room or plant area this is usually the ONLY identification that exists. Never skip it.
   - BUILT SETTING — roof sheeting, trusses, beams, columns, pillars, walls, shutters, flooring,
     road surface, kerbs, paving, drain, fence or yard around the object. Civil assets ARE this
     setting, so omitting it makes them impossible to validate downstream.
   Photos may arrive rotated 90° or 180° — read text and describe the scene in any orientation.
   - TAG DEFINES PRIMARY ASSET: Cross-reference BOTH images — the barcode close-up shows which surface the sticker is on; the asset image shows that same structure. The tagged asset is the host object the sticker is attached to, NEVER contents stored inside.
     · MOUNTING SURFACES ARE NOT ASSETS: a plank, board, bracket, backing panel or batten that exists
       only to CARRY a device (fan, AC, heater, camera, light, sensor, control box) is NOT itself the
       asset — the device it carries is. Wood grain behind a sticker on a wall- or column-mounted unit is
       a mounting board, not a desk, table or counter. When the wide photo shows such a device as the
       prominent object, THAT is the tagged asset: name it, and never report it as "not visible".
   - CONTAINER vs CONTENTS: If the tag is on a metal frame, cage, rack, cart, trolley, bin, or enclosure → the primary asset is that STRUCTURE. Bags, boxes, pallets, or products inside are secondary — list them here but do NOT treat them as the primary asset.
   - FURNITURE vs ELECTRONICS: If a workstation is visible (e.g., a monitor/printer sitting on a desk), DO NOT automatically assume the electronic device is the primary asset. Carefully check the barcode close-up image to see the surface texture/material. If the sticker is attached to a wooden or metal furniture surface, the desk/table IS the primary asset. Only choose the monitor/laptop if the sticker is directly on its plastic chassis/bezel.
     · A laminate, wooden, or painted-metal EDGE or flat panel in the tag close-up means furniture — a table, desk, counter, or cabinet — not the device standing on it.
     · PRECEDENCE: this whole furniture rule applies ONLY when the asset photo actually shows a device
       resting on or built into furniture. If the asset photo shows a DIFFERENT primary asset — e.g. a wall-
       or ceiling-mounted fan, AC, heater, camera or light — then wood or metal behind the sticker is its
       MOUNTING BOARD, bracket or backing panel, NOT a desk. The wide asset photo decides what the asset
       is; the tag close-up only shows where the sticker sits. NEVER name an asset (a desk, a table) that
       the wide photo does not show, and never state that the asset in the wide photo is "not visible".
     · When no tag close-up is available and the Expected Asset is furniture (table, desk, counter, cabinet, workstation), the desk/table surface supporting the electronics IS the primary asset whenever such a surface is visible in the frame.
   - MULTIPLE ASSETS: List ALL distinct assets/equipment visible — not only the primary tagged one.
   - What is the PRIMARY tagged asset? (object the barcode sticker is physically attached to)
   - What OTHER assets/equipment are visible?
   - What does each look like? (color, size, shape, design features)
   - Visible MANUFACTURER branding on any ASSET BODY? (Dell, HP, LG, Hikvision, etc.)
   - Primary asset condition (new, used, damaged) and location/setting
   - ⚠️ DO NOT mention the barcode sticker logo — focus ONLY on actual assets and their manufacturers
   - CRITICAL: Include every distinct asset visible. Distinguish TV vs computer monitor vs laptop vs printer when applicable.

   Example: "Dark metal roll cage with asset tag on horizontal frame bar. Also visible: white industrial bulk bag stored inside cage. Warehouse setting."
   Example: "White Hikvision dome security camera mounted on warehouse racking. HIK branding on camera body. Good condition. Industrial warehouse setting."
   Example (road): "Concrete internal factory road with painted yellow kerbs and a speed breaker, buildings and palm trees along both sides. Asset tag on a roadside support pole — the pole is only the mounting point."
   Example (shed): "Industrial shed interior: stainless steel vessel, green piping and blue drums under metal roof sheeting carried on steel beams and columns. Tag on a yellow painted column."
   Example (signage): "Plant building wall carrying blue signboards reading 'SERVER ROOM' and 'EXECUTIVE LUNCH ROOM'. LG split-AC outdoor unit wall-mounted, blue water tank in foreground. Photo rotated 90 degrees."
   Example (room interior): "Control room interior: wooden desks, black office chairs, a white printer, files and daylight windows, plastered walls and ceiling. Tag close-up shows dark wood grain, so the tag is on a desk — but the record is the ROOM, which is the whole scene."

2. "imageReadability": "Y" if the asset is clearly visible in at least one image; else "E".

2b. "full_asset_photo_present": true / false — judge from what the image ACTUALLY shows, not the field it arrived in.
   - true  = a PROPER FULL view of the asset exists (a stand-back photo showing the whole object), good enough to identify it and judge its condition — even if an asset tag is also visible on it.
   - false = the ONLY view of the asset is a TAG/BARCODE CLOSE-UP — a shot zoomed in on the tag/sticker where the asset itself is only partly or incidentally visible (a fragment of it, not the whole object). A close-up of a tag on a chair leg/back panel is false even though you can tell it is a chair.

3. "detectedAsset": Brief description of the PRIMARY TAGGED asset in the full asset photo (object the barcode sticker is attached to — not the sticker itself):
   - Cross-reference the barcode close-up to see which surface/frame the sticker is on
   - CONTAINER vs CONTENTS: If tag is on cage/rack/cart → describe the STRUCTURE, not contents inside
   - Identify the primary asset category (laptop, AC, camera, roll cage, rack, chair, desk, printer, TV, computer monitor, etc.)
   - Include manufacturer brand ONLY if clearly visible on the PRIMARY ASSET BODY
   - If no brand visible on asset → just describe the asset type
   - DO NOT use the logo from the barcode sticker as the brand

   Examples:
   - "Office desk" (if tag is on wooden table surface, even if a prominent HP monitor is sitting on it)
   - "HP computer monitor" (ONLY if tag is on the plastic monitor bezel)
   - "Roll cage" (tag on metal cage frame, bulk bag visible inside)
   - "Split-type air conditioning unit" (if no brand visible on AC body)
   - "Dell laptop" (if Dell logo visible on laptop body)
   - "Dell computer monitor" (if Dell logo on bezel — NOT a TV)
   - "Hikvision dome security camera" (if HIK/HIKVISION branding visible)
   - "LG air conditioner" (if LG logo visible on AC unit body)
   - "Internal concrete road" (Expected Category "Land and Building"; tag on a roadside pole — report the ROAD, never the pole)
   - "Industrial storage shed" (tag on a shed column; vessels and drums inside are contents, not the asset)
   - "Canteen and server room block" (wall signboards name the rooms; a tank standing outside is not the asset)
   - "Control room" (Expected "Control Room UTPT Plant", Category "Land and Building"; desks, chairs and a printer inside are contents — report the ROOM, never the printer, even when the tag sits on a desk)

4. "detected_description": Exactly one sentence, max 20 words, describing the primary tagged asset visible in the full asset photo. Plain text only.

5. "visibleModelYearMin": 4-digit year or null — earliest plausible launch year for visible hardware generation.
6. "visibleModelYearMax": 4-digit year or null — latest plausible launch year for visible hardware.

7. "visible_assets": array of short strings — EVERY distinct asset visible anywhere in the frame,
   primary one first. Include supporting furniture (desk, table, rack, cabinet) even when the
   electronics on it are more prominent.
   Example: ["white office table", "HP computer monitor", "keyboard", "red plastic chairs"]

8. "expected_asset_visible": true / false / null — is an object matching the Expected Asset hint
   genuinely visible anywhere in the frame? Use null only when no hint was supplied.
   Answer from the image alone; never answer true just because the hint says so.

DO NOT confuse tracking stickers (company logos on asset tags) with manufacturer brand.
