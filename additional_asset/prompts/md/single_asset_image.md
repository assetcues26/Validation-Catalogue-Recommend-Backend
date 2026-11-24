
=== SINGLE ASSET IMAGE ===
One full-asset photo is provided. Identify manufacturer branding on the asset body only — ignore client logos on tracking stickers.

=== ASSET IDENTIFICATION ===
Analyze the provided asset photo.

⚠️ CRITICAL - DO NOT CONFUSE TRACKING STICKER WITH ASSET BRAND:
- Client company stickers may show their logo — that is NOT the asset manufacturer brand
- ONLY use manufacturer branding visible on the asset body (front bezel, chin, rear nameplate if visible)
- For monitors/TVs: branding is often on the rear — note "monitor/display form factor" even when front logo absent

⚠️ FIRST — IS ANYTHING VISIBLE? If the image is black, blank, a featureless wall/floor, or so
under/over-exposed that no object can be made out: set "imageReadability" = "E", "detectedAsset" =
null, "detected_description" = null, report any tag/code as unreadable, and make "imageAnalysis"
state what the frame actually shows (e.g. "Image is completely black, no asset visible"). You MUST
NOT describe the asset named in the declared record — the record says what SHOULD be there, never
what IS there. Describing an asset you cannot see is a FAILURE, not a helpful fallback. "Nothing is
visible" is the CORRECT answer for such a frame. This applies ONLY to an essentially empty
frame: if ANY object is discernible, describe it with FULL detail as usual (brand, model,
variant, capabilities) — never use this rule to be vague about a photo you CAN see.

1. "imageAnalysis": COMPREHENSIVE ASSET ANALYSIS (~50 words):
   - What is the PRIMARY asset in the photo?
   - Visible MANUFACTURER branding on asset body (Dell, HP, LG, Hikvision, etc.)
   - Asset condition cues and location/setting visible in frame
   - CONTAINER vs CONTENTS: if a cage/rack/cart structure is the host object, describe the structure not contents inside

2. "imageReadability": "Y" if the asset is clearly visible; else "E".

3. "detectedAsset": Brief description of the primary asset — manufacturer brand only if visible on asset body.

4. "detected_description": Exactly one sentence, max 20 words, describing the primary asset. Plain text only.

5. "visibleModelYearMin": 4-digit year or null — earliest plausible launch year for visible hardware generation.
6. "visibleModelYearMax": 4-digit year or null — latest plausible launch year for visible hardware.

DO NOT confuse tracking stickers with manufacturer brand.
