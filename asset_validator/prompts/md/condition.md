
=== DUAL IMAGE INPUT ===
Two images are always provided. Field names (AssetImage / BarcodeImage) may be swapped.
Inspect BOTH images and classify each as either (a) barcode/asset-tag close-up or (b) full asset photo from visual content — not from upload order.
For condition assessment: use the full-asset photo (whichever slot it is in).

=== CONDITION & DAMAGE (asset-class repair rules) ===
Apply the playbook matching detected asset class (HVAC, IT, furniture, vehicle, industrial).

REPAIR TOLERANCE BY CLASS (apply to every damage_items[] row):
| Class | Minor scratch/scuff | Screen/display crack | Color fade/discoloration |
| phone/laptop/tablet | cosmetic_only, acceptable_wear=false | repair_needed=required, urgency=immediate | cosmetic_only if body only |
| it_equipment/printer | cosmetic_only | N/A unless control panel broken | cosmetic_only |
| vehicle/fleet | acceptable_wear=true | cosmetic_only for bumper/panel | acceptable_wear=true |
| industrial/machine | acceptable_wear=true, repair_needed=none | N/A unless HMI screen | acceptable_wear=true |
| furniture | cosmetic_only | N/A | acceptable_wear=true |
| hvac outdoor | monitor rust severity | N/A | acceptable_wear on cosmetic panels |

Each damage_items[] object MUST include:
  location, type, severity (minor|moderate|severe), seen_in_image (1 or 2), detail,
  affects_function, repair_action,
  repair_needed (none|cosmetic_only|recommended|required),
  repair_urgency (none|monitor|scheduled|immediate),
  acceptable_wear (boolean)

"condition": {
  "grade": "Excellent"|"Good"|"Fair"|"Poor"|"Bad",
  "overall_score": 0-100,
  "condition_score": 1-10 integer (10=perfect; server maps to overall_score),
  "condition_summary": "1-2 sentence narrative",
  "summary": "1-2 sentences (same as condition_summary if needed)",
  "cosmetic_condition": "...",
  "structural_condition": "...",
  "functional_status": "Fully functional"|"Appears functional"|"Partially functional"|"Non-functional"|"Cannot determine",
  "cleanliness": "Clean"|"Lightly soiled"|"Moderately dirty"|"Heavily soiled",
  "wear_level": "None"|"Minimal"|"Moderate"|"Heavy"|"Severe",
  "usability": "...",
  "repair_recommendation": "... or None needed",
  "estimated_remaining_life": "...",
  "missing_parts": [],
  "functional_issues": [],
  "positive_aspects": [],
  "has_damage": boolean,
  "damage_count": integer,
  "damage_by_severity": {"minor": 0, "moderate": 0, "severe": 0},
  "damage_items": [{"location","type","severity","seen_in_image","detail","affects_function","repair_action","repair_needed","repair_urgency","acceptable_wear"}]
}

Map condition_grade from overall damage: Excellent/Good = light cosmetic wear only; Fair = multiple moderate issues or one functional concern; Poor/Bad = severe damage or major functional failure.
Vehicle: bumper dents/scratches/paint chips are MINOR cosmetic — acceptable_wear=true; condition_score 7-8/10 for light cosmetic-only fleet wear.
If no damage: has_damage=false, damage_count=0, damage_by_severity zeros, damage_items=[].
