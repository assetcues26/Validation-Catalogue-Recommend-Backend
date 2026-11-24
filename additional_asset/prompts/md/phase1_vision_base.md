=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For asset identification and condition: use the full-asset photo (whichever slot it is in).
For tag/barcode reading: scan BOTH images for all readable codes.

Analyze the provided images. Classify each image by content — not upload order:
- One image is the barcode/asset-tag close-up (read digits from whichever image shows the tag close-up)
- One image is the full ASSET photo (identify asset type, brands on body, condition)

CRITICAL: Client logos on the barcode sticker are NOT the asset manufacturer. Brand on asset body only.

{barcode_phase1}

=== PRIORITY 2: ASSET IDENTIFICATION ===
⚠️ FIRST — IS ANYTHING VISIBLE? If the image is black, blank, a featureless wall/floor, or so
under/over-exposed that no object can be made out: set "imageReadability" = "E", "detectedAsset" =
null, "detected_description" = null, report any tag/code as unreadable, and make "imageAnalysis"
state what the frame actually shows (e.g. "Image is completely black, no asset visible"). You MUST
NOT describe the asset named in the declared record — the record says what SHOULD be there, never
what IS there. Describing an asset you cannot see is a FAILURE, not a helpful fallback. "Nothing is
visible" is the CORRECT answer for such a frame. This applies ONLY to an essentially empty
frame: if ANY object is discernible, describe it with FULL detail as usual (brand, model,
variant, capabilities) — never use this rule to be vague about a photo you CAN see.

"imageAnalysis": ~60 words on full asset photo. Downstream checks see ONLY this text, never the image — so anything you omit here cannot be validated later. ALWAYS include, when present:
 (a) PRIMARY OBJECT — type, colour, manufacturer on body, labels/stickers on it.
 (b) SIGNAGE TEXT — quote verbatim any room board, door plate, department sign, bay marking or painted area label, e.g. "SERVER ROOM", "EXECUTIVE LUNCH ROOM", "SUBSTATION", "MCC ROOM". This is frequently the ONLY identification a building, room or plant area has. Never skip it.
 (c) BUILT SETTING — roof sheeting, trusses, beams, columns, pillars, walls, shutters, flooring, road, paving, kerbs, drain, fence or yard visible around the object. Civil assets (shed, building, room, road, drain, fence, compound wall) ARE this setting — they can never be the "primary object", so describing the surroundings is the only way they get recorded.
Photos may arrive rotated 90° or 180° — read text and describe the scene in whatever orientation it arrives. Cross-reference tag close-up for sticker placement. Ignore barcode sticker client logos.
"imageReadability": "Y" if the asset is clearly visible in the full-asset photo; else "E".
"detectedAsset": FULL brand + model when visually identifiable; asset type only if brand/model unknown. Primary TAGGED object — cross-reference tag close-up. If a desk/table has electronics on it, CAREFULLY check if the tag is on the wooden/metal furniture surface. If yes, the desk is the primary asset, NOT the electronics.
"visibleModelYearMin": 4-digit year or null — earliest plausible launch year for the hardware generation visible (chassis, ports, keyboard, bezels, Apple Silicon vs Intel era).
"visibleModelYearMax": 4-digit year or null — latest plausible launch year for visible hardware.
Estimate from physical design (e.g. thin USB-C MacBook 2016+, Touch Bar 2016-2020, Apple M1/M2 2020+). Must align with age_estimation_rationale. Use null only if era truly unknown.

=== ASSET-CLASS PLAYBOOKS (infer class from images) ===
HVAC (Split AC, Window AC, Cooler): tag on service label or outdoor chassis; fin corrosion and outdoor rust are material.

IT Equipment (Laptop, Desktop, Printer, Display): tag on bottom chassis, rear label, or side barcode — NOT manufacturer logos alone. Screen cracks and port damage are functional.

Furniture (Chair, Desk, Cabinet): tag underside or rear frame; scratches and fabric wear often acceptable_wear.

Vehicle (Car, SUV, Van, Truck, Fleet): bumper dents/scratches/paint chips are MINOR cosmetic — acceptable_wear=true.

Industrial (Generator, Machine): nameplate/chassis plate; heavy rust, oil leaks, missing guards are severe.

DO NOT confuse tracking stickers (company logos on asset tags) with manufacturer brand.
