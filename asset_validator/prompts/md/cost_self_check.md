
=== SELF-CHECK BEFORE YOU EMIT COST FIELDS ===
This replaces a second correction pass, so do it properly. Having chosen a tier and a range,
re-read your own answer against the failure patterns below and CORRECT it before returning.

1. PRICING YEAR: if an acquisition date was given, is your range the **new corporate buying
   price in that year** — not today's price, and not a used/resale/refurbished figure?
   A 2019 laptop belongs near its 2019 new price (~₹1.5L-2.2L for a business MacBook),
   NOT at a ₹45,000 second-hand value.

2. TIER SANITY — the three mistakes made most often:
   - Small fixed bullet/dome CCTV: ENTRY tier, roughly ₹1,800-2,800 new. Do NOT price a
     compact fixed-lens camera at ₹10,000+ just because the brand is known.
   - Large wall-mounted fan with metal blades and a heavy bracket: PREMIUM tier, roughly
     ₹6,500-9,000 new. Do NOT price it as a small domestic desk fan at ₹2,000-4,000.
   - Desks, tables, modular workstations, cabinets: a photo cannot pin a configuration.
     Use a WIDE L-U, set asset_class_detected "furniture" and model_end_year null. Do NOT
     emit a tight SKU-style range.

3. BOUNDS: are L and U set from the evidence in front of you, or did you quietly derive them
   as predicted_cost × 0.75/1.25? If the latter, redo them from evidence.

4. DECLARED COST: it must not have influenced your range. The declared figure is being
   validated against your estimate, so using it would make the check meaningless.

5. CONFIDENCE: set valuation_confidence to what the evidence actually supports. A generic
   class label with no readable brand or model line is LOW confidence — say so rather than
   inflating it.

Emit the corrected values only. Do not describe the correction.
