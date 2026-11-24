"""Environment-driven configuration and feature flags."""
# NOTE: Gemini configuration is preserved as-is. GPT settings are additive.

import os

ASSET_IMAGE_MAX_EDGE = int(os.getenv("ASSET_IMAGE_MAX_EDGE", "896"))
BARCODE_IMAGE_MAX_EDGE = int(os.getenv("BARCODE_IMAGE_MAX_EDGE", "768"))
IMAGE_JPEG_QUALITY = int(os.getenv("IMAGE_JPEG_QUALITY", "80"))
BARCODE_JPEG_QUALITY = int(os.getenv("BARCODE_JPEG_QUALITY", "90"))
PINECONE_TOP_K = int(os.getenv("PINECONE_TOP_K", "10"))
PHASE2_IMAGE_ANALYSIS_MAX_CHARS = int(os.getenv("PHASE2_IMAGE_ANALYSIS_MAX_CHARS", "350"))
GEMINI_MIN_CALL_TIMEOUT_SEC = 10  # google-genai minimum HTTP deadline
# 30s covers real vision latency (10-25s) while a hung call gives up fast enough to
# fit multiple calls + retries inside the 120s functionTimeout (see host.json).
GEMINI_CALL_TIMEOUT_SEC = float(os.getenv("GEMINI_CALL_TIMEOUT_SEC", "30"))
BARCODE_RETRY_ON_UNREADABLE = (
    os.getenv("BARCODE_RETRY_ON_UNREADABLE", "false").strip().lower() == "true"
)
# Log-only measure of how much visual information an asset photo carries. It records a
# rate for unusable (black/blank) uploads and NEVER affects imageReadability or any
# verdict — blank frames are handled by the "nothing is visible" rule in the vision
# prompts, because a threshold here would risk failing legitimately plain assets.
BLANK_IMAGE_METRIC_ENABLED = (
    os.getenv("BLANK_IMAGE_METRIC_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# Below this edge-density value a frame carries little detail. LOGGING ONLY -- it must
# never gate a verdict. An earlier version forced imageReadability="E" below this value
# and caused a real customer false failure: a clear photo of a smooth plastic chair
# scored 7.95 because a large matte object close to the lens has few edges, and a
# correct record was rejected. A blank frame in the same corpus scored 7.73, so the
# blank and non-blank populations OVERLAP and no threshold can separate them. Blank
# frames are handled in the vision prompts instead.
BLANK_IMAGE_EDGE_THRESHOLD = float(os.getenv("BLANK_IMAGE_EDGE_THRESHOLD", "8.0"))

# Pixel gate for frames that carry no asset at all. This one DOES gate
# imageReadability, unlike the edge-density metric above. The difference is separation:
# edge density overlapped (chair 7.95 vs blank 7.73); pixel SHARES do not. Shares rather
# than whole-frame statistics because the tagging app burns a white date stamp into every
# photo, which on a black frame lifts p99 to ~206 and defeated a mean/p99 version of this
# gate in production (AssetId 2692042). Measured over 88 distinct real asset photos:
#     real photos         dark <= 30.8%   flat <= 61.5%
#     black + date stamp  dark    98.1%   flat    98.0%
#     grey/white + stamp  dark     0.0%   flat 98.0-99.9%
# Set BLANK_IMAGE_GATE_ENABLED=false to turn the gate off without a deploy.
BLANK_IMAGE_GATE_ENABLED = (
    os.getenv("BLANK_IMAGE_GATE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# A pixel below this luminance counts as black.
BLANK_IMAGE_DARK_LEVEL = float(os.getenv("BLANK_IMAGE_DARK_LEVEL", "25"))
# Frame is unlit when at least this share of pixels is black (real max 30.8%).
BLANK_IMAGE_DARK_FRACTION = float(os.getenv("BLANK_IMAGE_DARK_FRACTION", "0.85"))
# A pixel within this many luminance levels of the median counts as the same tone.
BLANK_IMAGE_FLAT_TOLERANCE = float(os.getenv("BLANK_IMAGE_FLAT_TOLERANCE", "8"))
# Frame is flat when at least this share of pixels is one tone (real max 61.5%).
BLANK_IMAGE_FLAT_FRACTION = float(os.getenv("BLANK_IMAGE_FLAT_FRACTION", "0.95"))

# Name-free photo classification (see services/view_classifier.py). Decides, from the
# pixels alone, whether each upload is a whole asset, a tag close-up, or empty — the two
# questions the main vision call answers unreliably because it is shown the declared
# record first. Runs in parallel with the main checks. Set false to fall back to the
# main call's own judgement without a deploy.
BLIND_VIEW_ENABLED = (
    os.getenv("BLIND_VIEW_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# Wait this long, measured from when classification started, before giving up on it.
# It runs alongside the ~3-8s main vision call, so a generous bound costs nothing extra.
BLIND_VIEW_TIMEOUT_SEC = float(os.getenv("BLIND_VIEW_TIMEOUT_SEC", "20"))
# A photo that shows only the asset's box (the classifier's "packaging" view) fails the
# asset-side checks like a tag-only photo (docs 3.6). Set false to count it as a whole-object
# view again, as before 2026-10-22.
PACKAGING_PHOTO_POLICY_ENABLED = (
    os.getenv("PACKAGING_PHOTO_POLICY_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# Photo-of-a-photo detection (see services/recapture_detector.py). A tagger without the
# physical asset photographs an existing image of it — on a phone or laptop screen, or a
# printout — and uploads that. A separate name-free call, run in parallel with blind view,
# asks whether each upload is such a re-photograph. It is deliberately NOT a question added
# to the blind-view or phase-1 prompts: both are load-bearing, and adding one field to phase 1
# once took AssetId 2294700 from 15/15 passing to 4/15.
#
# RECAPTURE_DETECTION_ENABLED runs the call. RECAPTURE_ENFORCE decides whether a detection
# changes the response; while false the would-be decision is only logged. A detection counts
# only at or above RECAPTURE_MIN_CONFIDENCE, and only with at least one named visual cue.
#
# Enforced by default since 2026-09-17, on the owner's decision. It shipped log-only first; on
# 63 regression records it flagged no genuine photo, and it caught both photo-of-a-photo uploads
# tested by hand. Set RECAPTURE_ENFORCE=false in app settings to go back to log-only without a
# deploy.
RECAPTURE_DETECTION_ENABLED = (
    os.getenv("RECAPTURE_DETECTION_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
RECAPTURE_ENFORCE = (
    os.getenv("RECAPTURE_ENFORCE", "true").strip().lower() in ("1", "true", "yes")
)
RECAPTURE_MIN_CONFIDENCE = float(os.getenv("RECAPTURE_MIN_CONFIDENCE", "0.8"))

# Name-free read of the manufacturer marking on the asset body (services/brand_reader.py).
# Runs ONLY for a record whose make/model comparison against master data already failed —
# roughly one in forty — and decides whether the photo can settle that comparison at all.
# Set false to go back to failing make/model on the master-data strings alone.
BRAND_READ_ENABLED = (
    os.getenv("BRAND_READ_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# A missing logo says nothing about product type (docs 6): the brand read above is not consulted, and a
# failed make/model stays failed, when the make/model names a product type the photo does not show and
# the chosen master-data row does not share. Case 10 of the 1 Nov test matrix (customer 1063): a 4 Tier
# Sorting Rack registered FF / CHAIRS / Black Chair showed make/model "matched" 85, and TABLES / Black Chair
# on a table photo passed. Set false to let a missing logo clear any failed make/model, as before.
WITHHOLD_RESPECTS_MAKEMODEL_TYPE_ENABLED = (
    os.getenv("WITHHOLD_RESPECTS_MAKEMODEL_TYPE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# A master-data suggestion never names a make/model whose brand contradicts the brand the photo shows on the asset
# (owner, 2026-11-02; docs 6): the make/model and model suggestion is withdrawn, the subcategory stays. AssetId 1142
# (customer 1323): an HP laptop registered Desktop / Apple Mac16 was offered "Dell", because that customer's master
# data holds Apple and Dell laptops only. Set false to show the selection's make/model whatever brand it names.
SUGGESTION_RESPECTS_PHOTO_BRAND_ENABLED = (
    os.getenv("SUGGESTION_RESPECTS_PHOTO_BRAND_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# A make/model that names a brand the photo contradicts fails the classification. The make/model
# prompt judges product type only ("brand differences alone do NOT reject"), so Pidilite tag
# 1000005803 — makemodel "Split Air Conditioner LLoyd", an LG AC in the photo — passed with every
# status "matched". Set false to go back to judging make/model on product type alone.
MAKEMODEL_BRAND_CHECK_ENABLED = (
    os.getenv("MAKEMODEL_BRAND_CHECK_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# Before a brand rule fails a record (name 6.1, make/model 6.0, IT 5.5), the full-size uploads are
# read name-free; when every brand read there is the record's own, the photo description misread
# it and the conflict is dropped (docs 6.3). AssetId 3824091: a Voltas AC, photos swapped, read as
# Hitachi from the shrunk copy and VOLTAS from the full-size one. Runs only on a conflict, which is
# a handful of records a week. Set false to fail on the description's brand alone, as before.
BRAND_CONFLICT_CONFIRM_ENABLED = (
    os.getenv("BRAND_CONFLICT_CONFIRM_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# With the name check passed, the make/model call used subcat_model.md, written for the photo call
# ("validate ... against the image", "use detectedAsset, imageAnalysis") but sent text-only with no
# slot for the photo description — so it compared the customer's fields with each other (72% of
# responses in the week to 2026-10-22). On: that call uses subcat_model_identity.md, which carries
# the confirmed name and the photo description and rejects only a clearly different kind of
# equipment (docs 6.4). Set false for the old prompt.
MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED = (
    os.getenv("MAKEMODEL_SEES_PHOTO_DESCRIPTION_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# REQ-9 approved every record with a blank subcategory or make/model at Y 75, whatever the make/model
# check had said about the fields the customer DID fill: customer 1063's "MONITORS" with a blank
# make/model, photographed as a laptop, was judged N and then approved (208 records in the week to
# 2026-10-22, 64 passing). On: the make/model call is told a blank field is never a mismatch, and
# its N on a filled field stands, backed by that field (docs 6.5). Set false to approve every
# blank-field record as before.
NULL_OVERRIDE_RESPECTS_PHOTO_ENABLED = (
    os.getenv("NULL_OVERRIDE_RESPECTS_PHOTO_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# A blank subcategory or make/model is looked up with what the photo shows, inside the filled field's own id once the
# make/model check approved that field, and a pass shows master data's best match for the blank field where the photo
# supports it (owner, 2026-11-02: "search the detected asset under that category under the same customer id and
# recommend them"; docs 6.5, 2.2). Set false to search with the name again and leave a blank field's recommendation
# null.
BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED = (
    os.getenv("BLANK_FIELD_PHOTO_RECOMMENDATION_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# ...and a blank field master data fills in with certainty fails the classification, with that row as the correction
# (owner, 2026-11-02: "if you are flipping the result make sure you got the best match in master data"; docs 6.5).
# AssetId 999100004 (matrix case 04): a Quanta battery left without a subcategory passed, though its make/model id is
# filed only under BATTERIES5000. Needs the switch above. Set false to recommend for a blank field but never fail on it.
BLANK_FIELD_CERTAIN_MATCH_FAILS_ENABLED = (
    os.getenv("BLANK_FIELD_CERTAIN_MATCH_FAILS_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# ...where a make/model that is only a known brand ("HP", "VOLTAS") names one product, as a rating or model number does
# (owner, 2026-11-02: "if we find that thing HP in master data under the same category then fail and show
# recommendations"; docs 6.5). Customer 1278's LAPTOPS rows are ACER, ASUS, DELL, HP and LENOVO. Every other certainty
# check still applies. Set false for the strict variant, where only a rating or model number names one product.
BLANK_FIELD_BRAND_IS_IDENTITY_ENABLED = (
    os.getenv("BLANK_FIELD_BRAND_IS_IDENTITY_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# Waste bins built into a cabinet ("STEEL DUST BIN SWING 8X24", customer 1158). The name rescue turns
# the name check's "a cabinet, not a steel dust bin" into Y when the tagged cabinet carries the bins;
# the cost note makes the cost check price the bin, not the cabinet around it. Both apply only to
# records whose name is a waste bin. Set false to turn either off.
WASTE_BIN_NAME_RESCUE_ENABLED = (
    os.getenv("WASTE_BIN_NAME_RESCUE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
WASTE_BIN_COST_NOTE_ENABLED = (
    os.getenv("WASTE_BIN_COST_NOTE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# Industrial records (plant & machinery, civil structures) whose name check failed on a photo
# that shows the asset are read once more by a stronger photo model, and the name check re-run on
# that description. gemini-2.5-flash-lite could not recognise customer 1299's smelter equipment
# (13 of 22 name rejections); gemini-3.1-flash-lite named it (1 of 22). Paid only on those
# records: measured +3% on a general mix, +35% if every request looked like the smelter.
# Set false to keep the first description whatever the name check says.
INDUSTRIAL_VISION_ESCALATION_ENABLED = (
    os.getenv("INDUSTRIAL_VISION_ESCALATION_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
ESCALATION_VISION_MODEL = os.getenv("ESCALATION_VISION_MODEL", "gemini-3.1-flash-lite").strip()

# The name decision for industrial records is made from a photo reading that is not told the record,
# then a judge (docs 5.6, industrial_decision.py). "on" (default, owner 2026-11-01: "keep everything default
# on") applies it, only for the customers listed in INDUSTRIAL_PHOTO_DECISION_CUSTOMERS when that list is set
# (comma-separated ids; empty = every customer) - the rest are not decided and pay nothing; "shadow" runs it
# beside the remaining checks and only logs what it would decide; "off" never runs it, and the
# stronger-model re-read of 5.3 decides as before. Each is an app setting: no redeploy needed.
INDUSTRIAL_PHOTO_DECISION_MODE = os.getenv("INDUSTRIAL_PHOTO_DECISION_MODE", "on").strip().lower()
INDUSTRIAL_PHOTO_DECISION_CUSTOMERS = frozenset(
    c.strip() for c in os.getenv("INDUSTRIAL_PHOTO_DECISION_CUSTOMERS", "").split(",") if c.strip()
)
# Its own setting, not ESCALATION_VISION_MODEL: setting that to the photo model is how 5.3 is switched off,
# and it must not change or break this decision.
INDUSTRIAL_PHOTO_DECISION_MODEL = os.getenv("INDUSTRIAL_PHOTO_DECISION_MODEL", "gemini-3.1-flash-lite").strip()
# In "on" mode the request waits at most this long for the reading and the judge; past it the first verdict
# stands and the 5.3 re-read is not started on the same, slow model (a stack of both could pass the
# function's 120 s limit).
INDUSTRIAL_PHOTO_DECISION_TIMEOUT_SEC = float(os.getenv("INDUSTRIAL_PHOTO_DECISION_TIMEOUT_SEC", "25"))
# Approvals are checked too: phase 1 is told the record and can write it back as what it sees. Set false
# to check rejections only (cheaper; gross mismatches the name check approves then pass as before).
INDUSTRIAL_PHOTO_DECISION_ON_APPROVALS = (
    os.getenv("INDUSTRIAL_PHOTO_DECISION_ON_APPROVALS", "true").strip().lower() in ("1", "true", "yes")
)
# In shadow mode the decision starts after the name check; the response waits at most this long more for
# it before logging a timeout. It overlaps make/model and cost, so it is usually done by then.
INDUSTRIAL_PHOTO_DECISION_SHADOW_WAIT_SEC = float(os.getenv("INDUSTRIAL_PHOTO_DECISION_SHADOW_WAIT_SEC", "3"))

# One "Decision path" log line per request: what the photo reading said, each name verdict, whether
# the photo was re-read, the second check, and the final result, so a verdict that changes between
# runs can be explained from the logs (docs 2.9). Logging only; the response is unchanged.
DECISION_PATH_LOG_ENABLED = (
    os.getenv("DECISION_PATH_LOG_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# Installed industrial plant (smelter pot-line equipment, capital works, plant structures):
# the name check is told that such plant is photographed in sections with the tag on a panel,
# support or duct of it; and the cost check treats it like installed building services — a photo
# cannot bound its cost, so it is not rejected on cost (a zero or negative cost still fails).
INSTALLED_PLANT_NAME_RULES_ENABLED = (
    os.getenv("INSTALLED_PLANT_NAME_RULES_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
INSTALLED_PLANT_COST_SOFTEN_ENABLED = (
    os.getenv("INSTALLED_PLANT_COST_SOFTEN_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# Deterministic QR/barcode decode of the tag label, used alongside the vision read.
# Kill-switch only: with it off the tag path behaves exactly as it did before.
BARCODE_DECODE_ENABLED = (
    os.getenv("BARCODE_DECODE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# When the plain decode finds nothing, retry on an upscaled / contrast-corrected / sharpened
# copy before falling back to the model reading the digits. Of the 13 tag failures in the
# 250-record preprod run, 7 decode exactly on a retry, 2 are real mismatches and 4 are
# unreadable. Set false to make one plain attempt, as before.
BARCODE_DECODE_ENHANCE_ENABLED = (
    os.getenv("BARCODE_DECODE_ENHANCE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# A tag-reading reply that is not valid JSON is retried with a corrective note instead of the
# identical prompt. At temperature 0 the identical prompt returns the identical broken reply:
# AssetId 1951716 on gemini-2.5-flash-lite transcribed its tag's QR as an invented URL, looped
# one digit to the output cap and failed both attempts. Only the retry changes — a first reply
# that parses never sees the note. Set false to retry with the identical prompt again.
TAG_REPLY_RETRY_NOTE_ENABLED = (
    os.getenv("TAG_REPLY_RETRY_NOTE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# When the tag-reading call fails outright, a decoded barcode that matches the registered tag
# settles the tag check instead of leaving it incomplete. A decoded value that does not match
# changes nothing, so decoding still cannot invent a tag failure. Set false to leave a failed
# tag call incomplete whatever the decoder read.
DECODED_TAG_RESCUE_ENABLED = (
    os.getenv("DECODED_TAG_RESCUE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# A tag read that does NOT match the registered tag gets the focused re-read, which until now ran
# only for an unreadable tag. Small sideways digits are misread, not just missed: AssetId 1986988's
# sticker reads 1001101602 and gemini-2.5-flash-lite reported 100101002, a false mismatch. The
# re-read never sees the registered number, so it cannot echo it; a genuinely different sticker
# still fails. Set false to re-read unreadable tags only.
TAG_MISMATCH_REREAD_ENABLED = (
    os.getenv("TAG_MISMATCH_REREAD_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# The focused re-read shows the tag photo as two overlapping halves cut from the ORIGINAL upload,
# instead of the copy shrunk to BARCODE_IMAGE_MAX_EDGE. The model gives every image the same small
# token budget whatever its size, so the halves carry about twice the detail. On AssetId 1986988
# the halves read 1001101602 every run; halves of the shrunk copy still misread it. Set false to
# re-read from the shrunk photo as before.
TAG_REREAD_SPLIT_ENABLED = (
    os.getenv("TAG_REREAD_SPLIT_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# The focused re-read reads EACH original photo in a call of its own, with a short prompt that
# only asks for the asset number printed on every tag label in frame. Digit reading collapses when
# several photos share one call: on the 17 tag-bearing records a customer reported as false tag
# failures (2026-10-21), the previous re-read — the halves plus the asset photo in one call, under
# the long BARCODE_RETRY_PROMPT — read 3 correctly and returned the camera date stamp
# ("10-Oct-2026") as the tag; the same model reading one full photo per call read 14, unchanged
# over three repeats. The model is still never shown the registered number: told the number and
# asked to find it, it "found" a wrong neighbouring tag 36 times in 70. Set false to restore the
# previous re-read exactly (TAG_REREAD_SPLIT_ENABLED then applies as before).
TAG_REREAD_FOCUSED_ENABLED = (
    os.getenv("TAG_REREAD_FOCUSED_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# A photo whose long edge exceeds this is shrunk before the focused re-read; anything within it is
# sent byte-for-byte as uploaded (every photo measured was 720x1280). Uploads are capped at 10 MB.
TAG_REREAD_MAX_EDGE = int(os.getenv("TAG_REREAD_MAX_EDGE", "2048"))
# The tag matching added with the focused re-read (docs §4.2): the zero-run tolerance after any
# leading digit (it was "1" only), the registered digits as the only long digit run of a printed
# code, and any candidate, not only the resolved one, settling the tag. Set false to restore the
# matching deployed before; with TAG_REREAD_FOCUSED_ENABLED=false too, the tag path is the previous one.
TAG_MATCH_WIDENED_ENABLED = (
    os.getenv("TAG_MATCH_WIDENED_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# detectedtagnumbermatch is always Y or N (docs 4.6). With no tag number sent and no tag in the
# photo it was "NA", which skipped the tag check; it is N and fails the check. Set false to send
# "NA" again.
TAG_MATCH_NEVER_NA_ENABLED = (
    os.getenv("TAG_MATCH_NEVER_NA_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
SUBCATMODEL_N_MAX_PERCENT = int(os.getenv("SUBCATMODEL_N_MAX_PERCENT", "29"))
COST_USER_TOLERANCE = float(os.getenv("COST_USER_TOLERANCE", "0.25"))
COST_MIN_VALUATION_CONFIDENCE = float(os.getenv("COST_MIN_VALUATION_CONFIDENCE", "0.50"))
# Refine re-runs the whole valuation, so it costs a second full cost prompt (~3,850 in +
# ~260 out). At 0.8 it fired on every request measured, because photo-based valuation
# rarely reports that much confidence. 0.6 keeps it for genuinely weak valuations only.
COST_REFINE_MAX_CONFIDENCE = float(os.getenv("COST_REFINE_MAX_CONFIDENCE", "0.6"))
# Cost is a major-discrepancy guard, not a price audit: FAIL only outside L/factor .. U*factor.
# A photo cannot pin an exact SKU or configuration, so a narrow band rejects correct records.
# Raised 3.0 -> 4.0 after measuring the recorded eval set: at 3.0, six assets that
# passed every other check were rejected on cost alone, all of them plausible
# declared values the model had over-estimated. See COST_EQUIPMENT_MAJOR_FACTOR
# for the process-equipment classes that needed more room than 4.0 gives.
COST_MAJOR_FACTOR = float(os.getenv("COST_MAJOR_FACTOR", "4.0"))
# Furniture-like assets: FAIL cost only outside L/factor .. U*factor (major discrepancy).
# Wider than COST_MAJOR_FACTOR because a photo genuinely cannot pin assembled/custom
# furniture cost — the model over- and under-values it. Calibrated to PASS a real
# worn desk (declared ~4x below the model range) while still FAILing an order-of-
# magnitude error (a chair declared at Rs.10 against a Rs.700-1100 market range).
COST_FURNITURE_MAJOR_FACTOR = float(os.getenv("COST_FURNITURE_MAJOR_FACTOR", "6.0"))
# Industrial process equipment (pumps, heat exchangers, compressors, sealing
# machines): a photo shows the casing, not the duty point, materials of
# construction, or fabrication spec that set the price. The model reliably
# over-values these — measured 3.2x-4.6x above declared on real records — so
# they are judged against the same wide band as furniture.
COST_EQUIPMENT_MAJOR_FACTOR = float(os.getenv("COST_EQUIPMENT_MAJOR_FACTOR", "6.0"))
# Everyday assets get a narrower band than COST_MAJOR_FACTOR (docs 7.1; the owner, 2026-11-03): an everyday record
# (helpers.asset_scope) with a normal cost policy that is not enterprise IT gear (switches, access points, routers,
# firewalls, servers, storage, UPS, racks, NVR/DVR and the vendors of cost_service._ENTERPRISE_GEAR_RE) fails outside
# L/factor .. U*factor, never wider than today's band. A cost that today's band accepts and this one does not is
# valued once more first, and fails only when that valuation puts it outside its own band too. At F=6 a USD 1,000
# laptop declared at USD 6,000 passed; at 4 it fails. Measured on the 30 Oct-2 Nov logs: of 1,408 everyday records
# passing at 6, 4 newly fails 22, about 12 once enterprise gear keeps 6. Industrial, "other", furniture and
# industrial equipment keep their bands.
COST_EVERYDAY_MAJOR_FACTOR = float(os.getenv("COST_EVERYDAY_MAJOR_FACTOR", "4.0"))
# "watch" (default) computes the everyday band and its second valuation and only logs them (Decision path,
# cost_everyday_band); "on" applies them; "off" (or any other value) never runs it, exactly as before 2026-11-03.
# An app setting: no redeploy needed.
_COST_EVERYDAY_BAND_MODE = os.getenv("COST_EVERYDAY_BAND_MODE", "watch").strip().lower()
COST_EVERYDAY_BAND_MODE = _COST_EVERYDAY_BAND_MODE if _COST_EVERYDAY_BAND_MODE in ("off", "watch", "on") else "off"
# When the model cannot price an asset (no bounds, or low confidence), trust the declared cost
# instead of failing it — "the model could not tell" is not evidence the customer is wrong.
COST_TRUST_DECLARED_WHEN_UNSURE = (
    os.getenv("COST_TRUST_DECLARED_WHEN_UNSURE", "true").strip().lower() == "true"
)
# The acquisition date is judged against a model's release / end-of-sales years only when those
# years are evidence about THIS asset (docs 7.7): the generation the cost reply named is written
# in the photo or the register, the model could know the market in the acquisition year, and a
# year of channel stock is allowed after the last production year. Set false to fail on the
# reply's years as before 2026-10-22.
ACQUISITION_DATE_EVIDENCE_GATE_ENABLED = (
    os.getenv("ACQUISITION_DATE_EVIDENCE_GATE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# The last year the pricing model's product-lifecycle knowledge covers (its training data ends
# early 2025 for gemini-2.5-flash-lite). It cannot know what was still sold after this, so an
# "already discontinued" verdict on a later acquisition is a guess. Raise it with the model.
MODEL_LIFECYCLE_KNOWLEDGE_YEAR = int(os.getenv("MODEL_LIFECYCLE_KNOWLEDGE_YEAR", "2024"))
# Corporate buyers take a generation from stock after its last production year.
MODEL_END_YEAR_STOCK_MARGIN_YEARS = int(os.getenv("MODEL_END_YEAR_STOCK_MARGIN_YEARS", "1"))
# A declared cost at or below this is a placeholder, not a cost, and fails the cost check in every
# policy tier — including the classes whose cost is otherwise never judged (docs 7.8). Customer
# 1158 files pedestals and switches under "Land and Building" at Rs 1: 186 passed in a week.
# 0 keeps only the zero/negative rule.
COST_PLACEHOLDER_MAX = float(os.getenv("COST_PLACEHOLDER_MAX", "1"))
# A cost check whose model call could not complete trusts the declared cost and does not fail
# the record as incomplete (owner's decision, docs 2.4). Set false to fail it as incomplete again.
COST_INCOMPLETE_TRUSTS_DECLARED_ENABLED = (
    os.getenv("COST_INCOMPLETE_TRUSTS_DECLARED_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# A skip class (Buildings, land, software, CWIP) returns no cost range. When this is true the photo
# decides (docs 7.1; the owner, 2026-11-03): a photo of one item a price can be put on (an HP laptop
# filed under Buildings, a MacBook under Computer Software) is priced and judged as usual, and the
# message says what the photo shows when it is not what the record names; a photo of the building, a
# room, an area or a panel is not priced and returns the band around the declared cost (±25%)
# instead of an empty range. Default false: those classes stay unpriced with no range, as before.
COST_SKIP_YIELDS_TO_PHOTO_ENABLED = (
    os.getenv("COST_SKIP_YIELDS_TO_PHOTO_ENABLED", "false").strip().lower() in ("1", "true", "yes")
)
# The product types the keyword rules read (docs 6.1) are read by the noun that names the thing, and the list has the
# families the data justified (owner, 2026-11-02: "the product-type list needs to be edited properly"). A device word in
# front of another object names that object, not the device: "HP laptop power adapter" is a charger and "Laptop
# keyboard" a keyboard, so the master-data row "HP / Laptop keyboard" is withdrawn for an adapter photo (AssetId
# 4411007). New families: power backup (UPS, inverter, battery), power bank, charger, keyboard, AC, refrigerator, water
# dispenser, projector, camera, network, tablet, stabilizer, microwave, scanner, server, speaker, docking station. Set
# false to read the eleven families of before 2026-11-02, a family word anywhere in the text, exactly as before.
PRODUCT_TYPES_V2_ENABLED = (
    os.getenv("PRODUCT_TYPES_V2_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# A master-data row whose product type the photo rules out is withdrawn instead of recommended,
# and a status that merely echoed the customer's own entry from it is not a match (docs 6.3).
MASTER_DATA_PHOTO_FIT_ENABLED = (
    os.getenv("MASTER_DATA_PHOTO_FIT_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# The exact-master lift (docs 2.3) does not lift a record whose master-data row was withdrawn because the photo rules
# out its product type (docs 6.2) AND whose name check failed: that row confirms nothing. AssetId 1520444 (customer
# 1004, 2026-11-01), a laptop registered Chairs / IKEA Markus, had its chair row withdrawn and was then lifted to Y on
# that same row. A withdrawn row with a passed name check is still lifted. Set false to lift such records again.
EXACT_LIFT_RESPECTS_PHOTO_FIT_ENABLED = (
    os.getenv("EXACT_LIFT_RESPECTS_PHOTO_FIT_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# The customer selects the asset's category, so it is always right (owner's decision, 2026-11-01):
# it is never validated, never fails, and is never a failure reason or part of the message. Only
# subcategory, make/model and model are judged. categorymatch reads "matched" at 100 wherever it is
# stated (null where no comparison ran), a recommendation names the customer's own category, and no
# server rule, make/model prompt or master-data selection prompt judges the category any more (docs
# 6.6). Set false to judge the category again as before 2026-11-01.
CATEGORY_ALWAYS_TRUSTED_ENABLED = (
    os.getenv("CATEGORY_ALWAYS_TRUSTED_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# The make/model and master-data selection prompts stop asking the model to judge the category (it stays as
# context, marked always correct). Separate from the switch above so that a prompt regression can be rolled
# back alone, without making the category a failure reason again (docs 6.6). Needs the switch above on too.
CATEGORY_PROMPTS_TRUST_ENABLED = (
    os.getenv("CATEGORY_PROMPTS_TRUST_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# On a pass (subcatmodelmatch Y) the four recommended fields carry the customer's own category, subcategory,
# make/model and model instead of null, so an approved response states the classification it approved; a blank
# input stays null. A fail keeps the master-data recommendation as before (owner, 2026-11-01; docs 2.2). Set
# false to send null recommendations on a pass again.
# Every response string is reduced to the characters the consumer accepts (owner, 2026-11-01): letters, digits,
# space and ! # $ ( ) * + , - . = ? @ [ ] ^ _ { } ~ / & | : < >. Backticks, apostrophes and double quotes are
# removed, a semicolon becomes a comma, % becomes " percent", line breaks become spaces, anything else is dropped
# (docs 2.8). Set false to send the plain-ASCII text of 2.8 again (apostrophes as backticks).
OUTPUT_ALLOWED_CHARACTERS_ENABLED = (
    os.getenv("OUTPUT_ALLOWED_CHARACTERS_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
PASS_RECOMMENDS_CUSTOMER_VALUES_ENABLED = (
    os.getenv("PASS_RECOMMENDS_CUSTOMER_VALUES_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# A master-data suggestion stays inside the record's own customer, asset class and category: the
# Pinecone filter adds assetclassid to customerid and categoryid, and a candidate whose metadata
# names another customer, asset class or category is dropped before the selection sees it (docs
# 6.6). A request without an asset class or category id is searched as before. Set false to filter
# on customer and category only, with no server-side drop, as before 2026-11-01.
PINECONE_ASSET_CLASS_SCOPE_ENABLED = (
    os.getenv("PINECONE_ASSET_CLASS_SCOPE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# The customer and the category are fixed filters for every master-data lookup (owner, 2026-11-02; docs 6.6), whatever
# the asset-class switch above says: without a category id a row is held to the record's category by name (the 1 Nov
# rule, which never ran on pre-prod because it sat behind the asset-class switch, set false there), and a record with
# neither an id nor a name is not searched at all. Set false to tie the name check to the asset-class switch again.
PINECONE_CATEGORY_SCOPE_ENABLED = (
    os.getenv("PINECONE_CATEGORY_SCOPE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
# An IT record (laptop, desktop, monitor, printer, phone, tablet, server...) whose photo shows a
# legible brand on the device must name that brand somewhere — name, description, make/model or
# model (docs 5.5). A bare "Laptop" against an Apple or Lenovo laptop fails the name check with a
# message asking for the brand/model; a different brand anywhere in the record fails it too.
# Other asset classes are untouched. Off by default since 2026-11-03 (the owner): an IT record is
# approved by product family, as before 2026-10-23. Set true to require the brand again.
IT_IDENTITY_STRICT_ENABLED = (
    os.getenv("IT_IDENTITY_STRICT_ENABLED", "false").strip().lower() in ("1", "true", "yes")
)
# ...and the asset NAME or description itself must carry that brand, by maker's name or product
# line ("LaserJet", "ThinkCentre", "Latitude"). A brand that sits only in make/model leaves
# "Laptop" as the name an auditor searches the register for six months on (the owner,
# 2026-10-23), so "Laptop" with make/model "MacBook Pro" against an Apple laptop fails the name
# check with a suggested name. Only runs while IT_IDENTITY_STRICT_ENABLED is on. Off by default
# since 2026-11-03 (the owner): the brand is accepted anywhere in the record. Set true to require it
# in the name again.
IT_NAME_MUST_NAME_BRAND_ENABLED = (
    os.getenv("IT_NAME_MUST_NAME_BRAND_ENABLED", "false").strip().lower() in ("1", "true", "yes")
)
# A name that says nothing about the asset — "Asset 9799", "Item 12", "333", with a blank or filler description —
# fails the name check at 20 with a fixed reason, whatever the model said, and the industrial decision (5.6) and the
# second photo read (5.3) are not run for it (docs 5.7). AssetId 316770 (QA customer 1004, 2026-11-01) passed at 95 on
# a MacBook photo. Shorthand with letters ("A/C", "T.V.", "P-101A") is never a placeholder. Set false to leave such
# names to the model, the judge and the second read as before.
PLACEHOLDER_NAME_RULE_ENABLED = (
    os.getenv("PLACEHOLDER_NAME_RULE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)

# ── Per-customer currency (fallback when the request carries none) ─────────
# A request's own Currency field wins. This map answers only when that field is absent,
# naming the currency a customer's declared costs are in.
#
# It used to do more harm than good. A customer resolved from this map was priced with the
# INDIA prompt and the rupee range divided by a fixed rate from CURRENCY_INR_RATES, so
# customer 1158 (The Executive Centre, Dubai) was quoted converted rupees: every AED band in
# the 2026-09-16 export multiplies back to a round rupee figure at 22.7 — a workstation at
# AED 352-881 is Rs 8,000-20,000, an Aeron chair at AED 5,286-6,608 is Rs 1,20,000-1,50,000
# — and the reasoning shown to a Dubai customer read "Based on 2026 India market pricing".
#
# Now a map-resolved currency is priced natively, exactly as if the request had sent it
# (core/market.py::market_for_request): the prompt is rendered for that market and no FX
# conversion is applied. The entry is kept rather than removed because none of the 12
# customer-1158 payloads on record carry Currency; without it their AED costs were compared
# against rupee ranges — a Samsung display declared at AED 4,007 read as INR 4,007 against
# Rs 1,20,000-1,50,000 and failed.
#
# Extend or override via the CUSTOMER_CURRENCY_MAP App Setting (JSON, e.g. {"1207":"USD"}).
def _load_customer_currency() -> dict:
    import json
    mapping = {
        "1158": "AED",  # The Executive Centre, Dubai
    }
    raw = os.getenv("CUSTOMER_CURRENCY_MAP", "").strip()
    if raw:
        try:
            mapping.update({str(k): str(v).strip().upper() for k, v in json.loads(raw).items()})
        except Exception:
            pass
    return mapping


CUSTOMER_CURRENCY = _load_customer_currency()

# INR per 1 unit of the currency (approximate; tune via CURRENCY_INR_RATES App Setting,
# JSON e.g. {"AED":22.7}). The wide cost acceptance bands absorb ordinary FX drift.
def _load_currency_inr_rates() -> dict:
    import json
    rates = {"INR": 1.0, "AED": 22.7, "USD": 83.0, "EUR": 90.0, "GBP": 105.0, "SAR": 22.1}
    raw = os.getenv("CURRENCY_INR_RATES", "").strip()
    if raw:
        try:
            rates.update({str(k).upper(): float(v) for k, v in json.loads(raw).items()})
        except Exception:
            pass
    return rates


CURRENCY_INR_RATES = _load_currency_inr_rates()


# The backend will start sending a per-request Currency field. Until it does, every
# request omits it and this path is inert. Set false to ignore the field even once
# it ships (falls back to the customer-id map, i.e. today's behaviour).
CURRENCY_FROM_REQUEST_ENABLED = (
    os.getenv("CURRENCY_FROM_REQUEST_ENABLED", "true").strip().lower()
    in ("1", "true", "yes")
)


# A customer-map currency is priced natively (see the map above). Set false to go back to
# pricing map customers with the India prompt and converting at CURRENCY_INR_RATES, without
# a deploy.
CUSTOMER_MAP_PRICES_NATIVELY = (
    os.getenv("CUSTOMER_MAP_PRICES_NATIVELY", "true").strip().lower()
    in ("1", "true", "yes")
)


def request_currency_sent(request_currency) -> bool:
    """A Currency field is SENT only when it holds something other than blanks.

    resolve_currency and market_for_request must agree on this, or they pick different
    currencies for one request. They did not: an empty Currency was absent to one and an
    explicit INR to the other, so a map customer sending "" would be priced in AED and have
    its figures labelled INR.
    """
    return request_currency is not None and str(request_currency).strip() != ""


def resolve_currency(customerid, request_currency=None, request_id: str = "") -> str:
    """Return the ISO currency code to price in.

    Precedence: the request's own Currency field, then the customer-id map, then
    INR. An unrecognised code resolves to INR with a warning rather than being
    trusted — see asset_validator.core.market.
    """
    if request_currency_sent(request_currency) and CURRENCY_FROM_REQUEST_ENABLED:
        from asset_validator.core.market import resolve_market

        profile = resolve_market(request_currency, request_id)
        return profile.currency

    if customerid is None:
        return "INR"
    return CUSTOMER_CURRENCY.get(str(customerid).strip(), "INR")


def currency_inr_rate(currency: str) -> float:
    """INR per 1 unit of `currency` (1.0 for INR/unknown)."""
    return CURRENCY_INR_RATES.get((currency or "INR").strip().upper(), 1.0)


API_LIMITS = {
    "MAX_CALLS_PER_MINUTE": 2000,
    "MAX_CALLS_PER_HOUR": 10000,
    "MAX_CALLS_PER_DAY": 100000,
    # 2 total attempts: at 30s/call the worst case (2x30=60s) leaves room for the
    # other calls inside the 120s functionTimeout, avoiding gateway kills under load.
    "MAX_RETRIES": 2,
    "CIRCUIT_BREAKER_THRESHOLD": 15,
    "CIRCUIT_BREAKER_TIMEOUT": 300,
}

MAX_JSON_LENGTH = 50000

# Gemini token limits — override via local.settings.json / environment
#
# The input cap is OURS, not the model's: gemini-2.5-flash-lite and 3.1-flash-lite accept
# on the order of a million tokens. At the old 6,250 (25,000 chars) it silently refused
# legitimate prompts. Customer 1212's civil assets — buildings, roads, compound walls,
# drains, warehouses — append an 8,194-char block of structure rules to the name match,
# and the phase-2 prompt reached 28,453 chars against the 25,000 cap. The call was refused
# as PROMPT_TOO_LONG, the check-failure handler then blanked the analysis and set
# imageReadability="E", and the reviewer was told "Asset is not visible in uploaded
# image(s)" about a clear photograph of a road. All 24 of those records failed that way.
# 15,000 tokens leaves every prompt we build comfortably inside and is still a fraction of
# what the model accepts. tests/test_prompt_size_budget.py fails if a prompt nears the cap.
GEMINI_MAX_INPUT_TOKENS = int(os.getenv("GEMINI_MAX_INPUT_TOKENS", "15000"))
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "2000"))
GEMINI_DEFAULT_OUTPUT_TOKENS = int(os.getenv("GEMINI_DEFAULT_OUTPUT_TOKENS", "200"))
# The separate cost call writes its reasoning (identity candidates, cues for and against, tier
# evidence) before the price, so the price comes last. At 850 tokens the longer
# replies were cut just before "cost_lower_bound" and the cost check came back incomplete (docs
# §7.6): 10 broken cost calls in the requests of 12–17 Oct 2026, which needed 836–1,068 tokens. The
# cap only stops a reply, it does not shape one: 17 of 17 cost replies that fitted were
# byte-identical at 1,360. The phase-1 calls and the cost refine pass keep their budgets. Set 850 to restore.
COST_CALL_MAX_TOKENS = int(os.getenv("COST_CALL_MAX_TOKENS", "1400"))
# A reply that is not valid JSON (docs §2.6) — almost always one cut at its token cap because the
# model repeated itself: a list written again and again, or a code transcribed as one digit run.
# Only the calls whose broken reply makes a check incomplete are covered (JSON_RECOVERY_LABELS);
# calls whose failure is tolerated today (blind view, recapture, master-data pick, cost refine,
# brand, escalation, tag re-read) keep their identical retry, so records that complete today are
# unchanged. Their retry carries prompts/md/json_reply_retry.md unless the call has its own note
# (the tag-reading lanes, §4.1); if EVERY attempt on the model broke, one last attempt goes to
# JSON_FALLBACK_MODEL with the original prompt — never for a reply that carries a price, and only
# within JSON_FALLBACK_TIME_BUDGET_SEC of the call starting (functionTimeout is 120 s). On the 27
# requests above, gemini-3.1-flash-lite answered 28 of 28 broken phase-1 calls.
JSON_RECOVERY_LABELS = frozenset({
    "barcode_lane", "asset_lane", "unified_phase1", "name_match_phase2", "make_model_phase2", "cost_check",
})
JSON_FALLBACK_TIME_BUDGET_SEC = float(os.getenv("JSON_FALLBACK_TIME_BUDGET_SEC", "40"))
JSON_RETRY_NOTE_ENABLED = (
    os.getenv("JSON_RETRY_NOTE_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
JSON_FALLBACK_ENABLED = (
    os.getenv("JSON_FALLBACK_ENABLED", "true").strip().lower() in ("1", "true", "yes")
)
JSON_FALLBACK_MODEL = os.getenv("JSON_FALLBACK_MODEL", ESCALATION_VISION_MODEL).strip()
# The cap guards against a runaway *input* — a pasted document in a description field, a
# repeated image analysis. It was never meant to police our own static templates, and it
# must never be set below them: a deployment carrying GEMINI_MAX_INPUT_TOKENS=6250 in its
# app settings would keep refusing the civil-asset prompt no matter what the default above
# says. Raising the default alone fixes nothing where the variable is actually set, which
# is every environment we run. So the configured value is a ceiling on request content and
# this floor is the guarantee our own prompts always fit.
#
# Largest builder measured in production: cost check at 29,864 chars (AssetId 2294698).
# The floor clears it with room for a long name, description and image analysis on top.
MIN_PROMPT_CHARS = 60000

# Prompt char cap (~4 chars/token) unless GEMINI_MAX_PROMPT_CHARS is set explicitly
MAX_PROMPT_LENGTH = max(
    int(os.getenv("GEMINI_MAX_PROMPT_CHARS", str(GEMINI_MAX_INPUT_TOKENS * 4))),
    MIN_PROMPT_CHARS,
)

# GPT token limits — used when GEMINI_USE=false
GPT_MAX_INPUT_TOKENS = int(os.getenv("GPT_MAX_INPUT_TOKENS", "128000"))
GPT_MAX_OUTPUT_TOKENS = int(os.getenv("GPT_MAX_OUTPUT_TOKENS", "16000"))
GPT_DEFAULT_OUTPUT_TOKENS = int(os.getenv("GPT_DEFAULT_OUTPUT_TOKENS", "200"))
GPT_CALL_TIMEOUT_SEC = float(os.getenv("GPT_CALL_TIMEOUT_SEC", "90"))
GPT_MAX_RETRIES = int(os.getenv("GPT_MAX_RETRIES", "5"))
GPT_MIN_CALL_INTERVAL_SEC = float(os.getenv("GPT_MIN_CALL_INTERVAL_SEC", "2.0"))
GPT_IMAGE_DETAIL = os.getenv("GPT_IMAGE_DETAIL", "low").strip().lower()
GPT_RATE_LIMIT_MAX_WAIT_SEC = float(os.getenv("GPT_RATE_LIMIT_MAX_WAIT_SEC", "90"))
GPT_TPM_COOLDOWN_SEC = float(os.getenv("GPT_TPM_COOLDOWN_SEC", "65"))
GPT_POST_VISION_COOLDOWN_SEC = float(os.getenv("GPT_POST_VISION_COOLDOWN_SEC", "3.0"))
GPT_ASSET_IMAGE_MAX_EDGE = int(os.getenv("GPT_ASSET_IMAGE_MAX_EDGE", "768"))
GPT_BARCODE_IMAGE_MAX_EDGE = int(os.getenv("GPT_BARCODE_IMAGE_MAX_EDGE", "512"))
GPT_BARCODE_RETRY_ON_UNREADABLE = (
    os.getenv("GPT_BARCODE_RETRY_ON_UNREADABLE", "false").strip().lower() == "true"
)


def effective_asset_image_max_edge() -> int:
    """Smaller resize when GPT is active to stay within OpenAI TPM limits."""
    if use_gemini():
        return ASSET_IMAGE_MAX_EDGE
    return GPT_ASSET_IMAGE_MAX_EDGE


def effective_barcode_image_max_edge() -> int:
    if use_gemini():
        return BARCODE_IMAGE_MAX_EDGE
    return GPT_BARCODE_IMAGE_MAX_EDGE


def effective_barcode_retry_on_unreadable() -> bool:
    if use_gemini():
        return BARCODE_RETRY_ON_UNREADABLE
    return GPT_BARCODE_RETRY_ON_UNREADABLE


def use_gemini() -> bool:
    """Return True when Gemini is the active LLM provider (default)."""
    return os.getenv("GEMINI_USE", "true").strip().lower() == "true"


def clamp_output_tokens(requested: int | None = None) -> int:
    """Clamp requested output tokens to GEMINI_MAX_OUTPUT_TOKENS."""
    base = GEMINI_DEFAULT_OUTPUT_TOKENS if requested is None else requested
    return min(max(base, 1), GEMINI_MAX_OUTPUT_TOKENS)


def clamp_gpt_output_tokens(requested: int | None = None) -> int:
    """Clamp requested output tokens to GPT_MAX_OUTPUT_TOKENS."""
    base = GPT_DEFAULT_OUTPUT_TOKENS if requested is None else requested
    return min(max(base, 1), GPT_MAX_OUTPUT_TOKENS)


def effective_gemini_call_timeout_sec() -> float:
    """Return configured timeout, clamped to google-genai minimum (0 = no limit)."""
    if GEMINI_CALL_TIMEOUT_SEC <= 0:
        return 0
    return max(GEMINI_CALL_TIMEOUT_SEC, GEMINI_MIN_CALL_TIMEOUT_SEC)


def effective_gpt_call_timeout_sec() -> float:
    """Return configured GPT timeout (0 = no limit)."""
    return max(GPT_CALL_TIMEOUT_SEC, 0)


def active_llm_provider() -> str:
    """Return the name of the active LLM provider for API responses."""
    return "gemini" if use_gemini() else "gpt"


# ── Hybrid model routing ───────────────────────────────────────────────────
# Keep the accuracy-sensitive calls on the vision model (GEMINI_MODEL_NAME): unified_phase1
# (the only call that reads tag digits — OCR) AND cost_check (a cheaper model diverges on
# ~20% of cost verdicts on diverse assets). Run the remaining text-only calls — name /
# make-model / pinecone match, which agreed with the vision model on our benchmarks — on the
# cheaper model when GEMINI_TEXT_MODEL_NAME is set. Unset -> everything uses the vision model
# (no behaviour change). Routing is keyed on usage_label so it needs no pipeline threading.
# NOTE: cost_check is deliberately NOT here — see the 25-asset A/B (tag + cost stay on 3.1).
TEXT_CALL_USAGE_LABELS = frozenset({
    "name_match_phase2",
    "make_model_phase2",
    "pinecone_selection",
})


def hybrid_model_enabled() -> bool:
    """Master ON/OFF switch for the hybrid model split.

    OFF (default) → every call uses GEMINI_MODEL_NAME (full rollback, current behaviour).
    ON            → the text-only calls (name/make-model/pinecone) use GEMINI_TEXT_MODEL_NAME.
    Set the HYBRID_MODEL_ENABLED App Setting to true/yes to enable, false to roll back —
    no redeploy needed.
    """
    return os.getenv("HYBRID_MODEL_ENABLED", "false").strip().lower() in ("true", "yes", "1", "on")


def text_model_name() -> str:
    """Model for text-only phase-2 calls.

    When the hybrid switch is OFF, this returns the main model, so routing is a no-op and
    every call runs on GEMINI_MODEL_NAME. When ON, it returns GEMINI_TEXT_MODEL_NAME (the
    cheaper model), falling back to the main model if that is unset.
    """
    main = os.getenv("GEMINI_MODEL_NAME", "").strip()
    if not hybrid_model_enabled():
        return main
    return os.getenv("GEMINI_TEXT_MODEL_NAME", "").strip() or main


def include_pinecone() -> bool:
    return os.getenv("INCLUDE_PINECONE_LOOKUP", "false").strip().lower() == "true"


def log_response_body() -> bool:
    """When true, log the full response JSON per request (toggle via App Setting;
    no redeploy needed). Response carries no images/secrets, so it is safe to log."""
    return os.getenv("LOG_RESPONSE_BODY", "false").strip().lower() == "true"


def cost_single_pass() -> bool:
    """When true, do the cost valuation in ONE call instead of check + refine.

    The refine pass receives no new evidence — no images, same vision text — so it is the
    model re-reasoning over identical inputs with a correction checklist. That checklist is
    appended to the first call instead (cost_self_check.md), saving a full second cost prompt
    (~3,800 input + ~255 output tokens). Set COST_SINGLE_PASS=false to restore the two-pass
    behaviour without a redeploy.
    """
    return os.getenv("COST_SINGLE_PASS", "true").strip().lower() != "false"


def condition_detail_minimal() -> bool:
    """When true, ask the model for grade/score/summary only and null the rest.

    The consumer displays a single condition line; the other fields were being generated
    at output-token rates and discarded. The response SHAPE is unchanged — every key is
    still returned, just null/empty — so no backend contract breaks. Set
    CONDITION_DETAIL_LEVEL=full to restore the verbose assessment without a redeploy.
    """
    return os.getenv("CONDITION_DETAIL_LEVEL", "minimal").strip().lower() != "full"
