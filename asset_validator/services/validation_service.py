"""Validation rules, bounds, and final verdict aggregation."""

import logging
import re

from asset_validator.core.config import (
    SUBCATMODEL_N_MAX_PERCENT,
    TAG_MATCH_NEVER_NA_ENABLED,
    TAG_MATCH_WIDENED_ENABLED,
    TAG_REREAD_FOCUSED_ENABLED,
    TAG_REREAD_SPLIT_ENABLED,
    resolve_currency,
)
from asset_validator.core.constants import LLM_NULL_STRINGS, PROMPT_EXAMPLE_TAG_NUMBERS
from asset_validator.prompts.barcode import BARCODE_RETRY_PROMPT
from asset_validator.prompts.loader import load_prompt
from asset_validator.services.image_service import (
    build_llm_image_parts,
    split_tag_photo_for_reread,
    tag_photo_for_focused_reread,
)
from asset_validator.services.llm_provider import call_llm_with_retry
from asset_validator.utils.barcode_decode import decoded_values
from asset_validator.utils.date_utils import (
    acquisition_invalid_reason,
    format_acquisition_date,
    format_acquisition_invalid_message,
    parse_acquisition_date,
)
from asset_validator.utils.helpers import sanitize_output_text
from asset_validator.utils.tag_detection import (
    enrich_tag_detection_reasoning,
    is_readable_code,
    normalize_code_types_for_asset_tags,
    normalize_detected_codes,
    resolve_asset_tag_from_codes,
)


def _user_tag_empty(user_provided) -> bool:
    return not user_provided or not str(user_provided).strip()


def resolve_barcodereadability(
    tag_number_status: str | None,
    *,
    tag_match: str | None = None,
    tag_not_applicable: bool = False,
    llm_value: str | None = None,
) -> str:
    """Map tag_number_status (+ NA path) to Y / E / NA barcodereadability."""
    if tag_not_applicable or tag_match == "NA":
        return "NA"
    if tag_match == "Y":
        return "Y"
    if tag_number_status == "readable":
        return "Y"
    if tag_number_status == "present_but_unreadable":
        return "E"
    if llm_value in ("Y", "E", "NA"):
        return llm_value
    return "NA"


# Phrases that mean THE PHOTO SHOWS NOTHING. Every entry must be unambiguous on its own:
# these force imageReadability="E", which fails the whole record.
#
# Two earlier entries were removed because they matched ordinary descriptions:
#   "black frame"  — matched "a rigid black frame secured to a wall" (an iPad kiosk mount).
#                    Monitors, TVs, tablets and whiteboards are routinely described this way.
#   "not visible"  — matched "brand not visible on the body", "serial not visible", which say
#                    nothing about whether the ASSET is visible.
# Their intent is kept below in forms that can only describe a blank image.
_ASSET_NOT_VISIBLE_MARKERS = (
    "no asset is visible",
    "asset is not visible",
    "no asset visible",
    "nothing is visible",
    "no discernible asset",
    "image is blank",
    "image is completely black",
    "image is entirely black",
    "completely black frame",
    "entirely black frame",
    "blank frame",
    "non-informative",
    "cannot determine the asset",
    "cannot identify the asset",
    "images are completely black",
    "images are entirely black",
    "no visual content",
    "no asset or equipment visible",
    "no discernible object",
)


def _is_asset_not_visible(output: dict) -> bool:
    """True when model signals that no physical asset is visible.

    Only inspects the identification fields. Condition prose is excluded on
    purpose: phrases like "Cannot determine without power-on test" (remaining
    life) are unrelated to visibility and previously produced false positives.
    """
    signals = [
        output.get("detectedAsset"),
        output.get("detected_description"),
        output.get("imageAnalysis"),
    ]

    merged = " ".join(str(s or "").strip().lower() for s in signals if s is not None)
    if not merged:
        return False
    if merged.strip() in ("none", "n/a", "na", "null"):
        return True
    return any(marker in merged for marker in _ASSET_NOT_VISIBLE_MARKERS)


def _asset_confirmed_visible(output: dict) -> bool:
    """True when the model has affirmatively identified the asset in the photo.

    A "Y" name match combined with a concrete detectedAsset normally outranks any keyword
    marker — a record that matches its name at 95% is not showing nothing.

    CAVEAT worth knowing before trusting this signal: the name match may come from the
    TEXT-ONLY phase-2 pass, which compares the declared name against phase 1's imageAnalysis
    string and never sees the image. If phase 1 invents a description of an unusable frame,
    phase 2 "confirms" it from that same invention and the agreement is self-referential.
    The override is kept anyway because it fixes a real customer failure (a correct
    identification wrongly marked "E"), and because gating it here cannot tell an invented
    description from a real one. That case is prevented upstream instead, by the
    "nothing is visible" rule in the vision prompts.
    """
    if str(output.get("namedescriptionmatch") or "").upper() != "Y":
        return False
    detected = str(output.get("detectedAsset") or "").strip().lower()
    return bool(detected) and detected not in ("none", "n/a", "na", "null", "not visible")


def reconcile_asset_image_readability(output: dict) -> None:
    """Set imageReadability from asset visibility signals (not tag/barcode)."""
    if _asset_confirmed_visible(output):
        # Guard against the marker path contradicting a confirmed identification. This is the
        # failure customers report: every check passes, then imageReadability="E" fails the
        # record with "Asset is not visible in uploaded image(s)".
        if output.get("imageReadability") != "Y":
            output["imageReadability"] = "Y"
        return
    if _is_asset_not_visible(output):
        output["imageReadability"] = "E"
        return
    if output.get("imageReadability") == "Y":
        return
    if str(output.get("namedescriptionmatch") or "").upper() == "Y":
        output["imageReadability"] = "Y"
        return
    detected_asset = str(output.get("detectedAsset") or "").strip().lower()
    if detected_asset in ("none", "n/a", "na", "null", "not visible"):
        output["imageReadability"] = "E"
        return
    if detected_asset and str(output.get("imageAnalysis") or "").strip():
        output["imageReadability"] = "Y"


ASSET_PHOTO_TAG_ONLY = "tag_only"      # every provided photo is a tag close-up
ASSET_PHOTO_UNUSABLE = "unusable"      # a provided photo is empty / black / flat
ASSET_PHOTO_PACKAGING = "packaging"    # the photos show the asset's box, not the asset (3.6)

# A record whose asset IS a box, carton or packaging item: a photo of a box is then a photo of
# the asset, and the packaging view does not fail it. Checked on the identity fields only —
# a description may mention a box in passing.
_PACKAGING_IS_THE_ASSET_RE = re.compile(
    r"\b(?:box|boxes|boxed|carton|cartons|crate|crates|packaging|packing|package|packages)\b", re.I
)

ASSET_LANE_CHECKS = (
    "assetnamedescriptionmatch",
    "assetmakemodelmatch",
    "acquisitiondatecostmatch",
    "assetcondition",
)


def fail_asset_side(output: dict, plan, kind: str, *, detail: str | None = None) -> None:
    """No provided photo shows the asset — fail every asset-dependent check, once.

    Shared by every path that reaches this conclusion (the name-free view policy, the
    single-image policy and the pixel gate) so the response is the same whichever one got
    there. Anything the vision call SAID about the asset is discarded, not just flagged:
    it was produced from a photo that does not contain the asset, so it can only have
    come from the declared record. That includes the condition, which would otherwise
    grade a close-up of a sticker, or rate a blank wall "Bad".
    """
    output["missing_image"] = "asset"
    output["_asset_photo_kind"] = kind
    output["imageReadability"] = "E"
    output["detectedAsset"] = None
    if kind == ASSET_PHOTO_TAG_ONLY:
        output["imageAnalysis"] = (
            "Only a tag/barcode close-up was provided; the asset itself is not shown."
        )
    elif kind == ASSET_PHOTO_PACKAGING:
        output["imageAnalysis"] = (
            "The photo shows the asset's packaging (its box); the asset itself is not shown."
        )
    else:
        suffix = f" ({detail})" if detail else ""
        output["imageAnalysis"] = (
            f"The asset photo is unusable{suffix}; no asset can be identified in it."
        )
    output["condition"] = None
    # A carton's label names the product, so phase 1 answers "Y, matches the HP ProBook on the
    # label" about a laptop it never saw, and the N it is meant to explain contradicted it. The
    # other kinds keep the model's own words, which already say what the photo lacks.
    packaging_reason = (
        "N: the photo shows the asset's packaging (its box), not the asset itself."
        if kind == ASSET_PHOTO_PACKAGING else None
    )
    if plan.is_enabled("assetnamedescriptionmatch"):
        output["namedescriptionmatch"] = "N"
        output["namedescriptionmatchpercent"] = 0
        if packaging_reason:
            output["namedescriptionreasoning"] = packaging_reason
        elif output.get("_placeholder_name_reason"):
            # A placeholder name (5.7) still says nothing; its "The photo shows ..." came from this photo.
            output["namedescriptionreasoning"] = output["_placeholder_name_reason"]
    if plan.is_enabled("assetmakemodelmatch"):
        output["subcatmodelmatch"] = "N"
        output["subcatmodelmatchpercent"] = 0
        if packaging_reason:
            output["subcatmodelreasoning"] = packaging_reason
    if plan.is_enabled("acquisitiondatecostmatch") and output.get("cost") is not None:
        output["cost_check"] = "FAIL"


def apply_blind_view_policy(
    output: dict,
    plan,
    views: dict[str, str] | None,
    *,
    provided_slots: set[str],
) -> str | None:
    """Decide asset visibility from the name-free per-photo classification.

    `views` maps "asset" / "barcode" to "full_asset" | "tag_closeup" | "empty" for every
    slot that classified successfully (services/view_classifier.py). It is authoritative
    for the one question the main vision call cannot be trusted on — whether any provided
    photo shows the asset — because that call is shown the declared record first and
    reports it when the photo does not settle the question.

    Positive and negative conclusions have different bars:
      - ANY slot classified full_asset  -> the asset is shown; recorded, nothing failed.
      - NO slot shows the asset         -> only concluded when EVERY provided slot was
                                           classified. A slot that timed out might have
                                           been the asset photo, and failing a correct
                                           record on a missing answer is the bug this
                                           replaces, not a fix for it.

    Returns the fail kind when the asset side was failed, else None.
    """
    from asset_validator.core.config import PACKAGING_PHOTO_POLICY_ENABLED
    from asset_validator.services.view_classifier import ASSET_PRESENT_VIEWS, PACKAGING

    if not views:
        return None
    output["_blind_views"] = dict(views)
    kinds = set(views.values())
    # "partial_asset" counts: a frame filled by the asset's own surface, too close to show
    # its shape, still SHOWS the asset. Requiring a full view failed AssetId 104436, where a
    # close shot of a packaging table's top was treated as no photo at all and the record
    # lost four checks even though its tag decoded and matched. Whether the surface is the
    # RIGHT asset is the name match's job, not this one's.
    #
    # "packaging" shows the asset's box, not the asset (3.6) — unless the record's asset IS a
    # box, or the policy is off, in which case it is treated as the whole-object view it was
    # before the class existed.
    if PACKAGING in kinds and (not PACKAGING_PHOTO_POLICY_ENABLED or _packaging_is_the_asset(output)):
        kinds = (kinds - {PACKAGING}) | {"full_asset"}
    if kinds & ASSET_PRESENT_VIEWS:
        output["full_asset_photo_present"] = True
        return None
    if set(views) != set(provided_slots):
        return None
    if not any(plan.is_enabled(c) for c in ASSET_LANE_CHECKS):
        return None
    if PACKAGING in kinds:
        kind = ASSET_PHOTO_PACKAGING
    elif kinds == {"tag_closeup"}:
        kind = ASSET_PHOTO_TAG_ONLY
    else:
        kind = ASSET_PHOTO_UNUSABLE
    fail_asset_side(output, plan, kind)
    return kind


def _packaging_is_the_asset(output: dict) -> bool:
    """Whether the registered asset is itself a box, carton or packaging item."""
    identity = " ".join(
        str(output.get(k) or "") for k in ("assetname", "subcategoryname", "makemodel")
    )
    return bool(_PACKAGING_IS_THE_ASSET_RE.search(identity))


RECAPTURE_MESSAGE = (
    "The asset photo appears to be a photo of another image (a screen or printout), not of "
    "the asset itself. Please photograph the physical asset."
)


def recaptured_asset_evidence(
    findings: dict | None,
    views: dict[str, str] | None,
    *,
    min_confidence: float,
) -> tuple[str, ...] | None:
    """The cues behind a recapture verdict, or None when the evidence does not reach one.

    A verdict needs ALL of:
      - blind view to have said which photos show the asset. Without it there is no telling
        whether a recaptured photo is the asset photo or the tag photo, and a recaptured TAG
        photo is out of scope — it is logged, but the tag check stands.
      - EVERY photo that shows the asset to be flagged recaptured, with at least one named cue
        and confidence at or above the threshold. A genuine asset photo in either upload field
        overrides a recaptured one, mirroring blind view's "positive from any photo" rule.
      - an answer for each of those photos. A photo the call did not answer for is unknown,
        and unknown never fails a record.
    """
    from asset_validator.services.view_classifier import ASSET_PRESENT_VIEWS

    if not findings or not views:
        return None
    asset_slots = [slot for slot, view in views.items() if view in ASSET_PRESENT_VIEWS]
    if not asset_slots:
        return None
    cues: list[str] = []
    for slot in asset_slots:
        finding = findings.get(slot)
        if (
            finding is None
            or not finding.recaptured
            or not finding.cues
            or finding.confidence < min_confidence
        ):
            return None
        cues.extend(finding.cues)
    return tuple(dict.fromkeys(cues))


def apply_recapture_policy(
    output: dict,
    plan,
    findings: dict | None,
    views: dict[str, str] | None,
    *,
    enforce: bool,
    min_confidence: float,
    request_id: str = "",
) -> bool:
    """Mark a re-photographed asset photo with imageReadability E. Returns True when applied.

    A recapture is NOT routed through fail_asset_side. That cascade discards the analysis and
    forces the name, make/model and cost checks to fail, which is right for a blank frame or a
    tag close-up — there is no asset in them to judge — and wrong here: a photo of a screen
    showing the asset still shows the asset's likeness, so those checks keep their real results.
    Only imageReadability changes, and that alone fails the record with its own message.

    With enforce False the decision is still evaluated and logged, so real traffic can be reviewed
    before any genuine record can fail on a false detection.
    """
    if not findings:
        return False
    output["_recapture"] = {slot: f.as_dict() for slot, f in findings.items()}
    # Already E for a stronger reason: no asset shown at all, or a blank frame.
    if output.get("missing_image") == "asset":
        return False
    if not any(plan.is_enabled(c) for c in ASSET_LANE_CHECKS):
        return False
    cues = recaptured_asset_evidence(findings, views, min_confidence=min_confidence)
    if cues is None:
        return False
    if not enforce:
        logging.warning(
            f"[{request_id}] Recapture detected (log only, RECAPTURE_ENFORCE=false): "
            f"would set imageReadability=E — cues {list(cues)}"
        )
        return False
    logging.warning(
        f"[{request_id}] Recapture detected: imageReadability=E — cues {list(cues)}"
    )
    output["imageReadability"] = "E"
    output["_recaptured"] = list(cues)
    return True


def apply_single_image_policy(
    output: dict,
    plan,
    *,
    asset_image_present: bool,
    barcode_image_present: bool,
) -> str | None:
    """Only ONE image was provided → one required photo (asset OR tag) is missing.

    Proper validation needs both a full asset photo and a tag/barcode close-up. The tagging
    team may put either photo in either request field, so the single image's TYPE is read
    from CONTENT, not the field name: if a tag/barcode is readable in it, it is the tag
    photo (so the asset photo is missing); otherwise it is the asset photo (so the tag photo
    is missing). We then fail only the checks that depend on the MISSING photo.

    Returns 'asset' (asset photo missing), 'tag' (tag photo missing), or None (both present).
    """
    # Internal (stripped before the response): lets build_failure_msg say "unusable"
    # rather than "not provided" for a photo the customer genuinely sent.
    output["_asset_image_provided"] = bool(asset_image_present)
    output["_barcode_image_provided"] = bool(barcode_image_present)

    if int(bool(asset_image_present)) + int(bool(barcode_image_present)) != 1:
        return None

    # The name-free view policy already established that no photo shows the asset.
    if output.get("missing_image") == "asset":
        return "asset"

    # full_asset_photo_present: whether the single image is a proper full view of the asset
    # (true) or just a tag/barcode close-up (false). When the name-free classifier ran it
    # has already written True here; otherwise this is the main vision call's own answer.
    #
    # Tri-state on purpose. `bool(None)` would fold "the model did not answer" into "the model
    # said no", and that one absent boolean then outranks detectedAsset, imageAnalysis and a
    # Y name match. AssetId 3890748 reproduced this 4/4: the field came back absent for a plain
    # photo of a Dell laptop, and the record was returned as "no asset is visible in it".
    #
    # Two further overrides that once sat here were REMOVED as unsafe:
    #   - skipping this policy whenever tagnumbercheck is off. It decided the photo's TYPE
    #     from the upload field, which this function exists not to do: the one photo sent
    #     can itself be the tag close-up, and then the missing photo is the ASSET photo.
    #   - forcing full_asset when the name matched. The name match is computed from the
    #     same description the declared record contaminates, so it overruled the model's
    #     direct "this is a close-up" judgement with the contamination.
    raw_full_asset = output.get("full_asset_photo_present")
    full_asset_known = isinstance(raw_full_asset, bool)
    full_asset = bool(raw_full_asset)
    if not full_asset_known:
        # No judgement was given — fall back to what the frame actually produced. A named
        # object plus a real analysis IS a full asset view.
        full_asset = _asset_confirmed_visible(output) or bool(
            str(output.get("detectedAsset") or "").strip()
            and str(output.get("imageAnalysis") or "").strip()
        )

    detected_tag = str(output.get("detectedtagnumber") or "").strip().upper()
    tag_readable = (
        output.get("barcodereadability") == "Y"
        or output.get("detectedtagnumbermatch") == "Y"
        or (detected_tag and detected_tag != "UNREADABLE")
    )

    # Smart: one image that is a PROPER full asset photo AND carries a readable tag can verify
    # both the asset and the tag — let it through unchanged.
    if full_asset and tag_readable:
        return None

    if not full_asset:
        # Only a tag/barcode close-up (asset not properly shown) → the ASSET photo is missing.
        # Fail every asset-dependent check; the tag check may still pass. An explicit false
        # is the model saying "close-up"; an unanswered one with nothing identified is not.
        fail_asset_side(
            output,
            plan,
            ASSET_PHOTO_TAG_ONLY if full_asset_known else ASSET_PHOTO_UNUSABLE,
        )
        return "asset"

    # A full asset photo but no readable tag → the TAG/BARCODE photo is missing.
    # Fail the tag check; asset-dependent checks keep their real results.
    output["missing_image"] = "tag"
    if plan.is_enabled("tagnumbercheck"):
        output["detectedtagnumbermatch"] = "N"
        output["barcodereadability"] = "NA"
    return "tag"


# A leading digit + a run of >=3 zeros + suffix. No upper cap on the zero run: OCR can read one
# extra (or one fewer) zero, so "1000006295" (5 zeros) and "10000006295" (6) are the
# same tag, and so are "2000021001896" and "20000021001896" (AssetId 2911034). The leading
# digit and the suffix after the zero run must still match exactly.
_TAG_ZERO_RUN_RE = re.compile(r"^([1-9])(0{3,})(\d+)$")


def compare_tag_numbers(
    detected,
    user_provided,
    request_id: str = "",
    widened: bool = True,
) -> tuple[str, int | None, str | None]:
    """Compare resolved detectedtagnumber against user TagNumber.

    ``widened=False`` keeps the matching deployed before TAG_MATCH_WIDENED_ENABLED (docs §4.2).
    A decoded payload is compared that way: it is exact by checksum, so tolerances for a
    model's misreading do not apply to it, and the decoded path stays as it was.
    """
    wide = widened and TAG_MATCH_WIDENED_ENABLED
    if _user_tag_empty(user_provided):
        return "N", 0, None
    if not detected or str(detected).strip().upper() == "UNREADABLE":
        logging.info(f"[{request_id}] Tag comparison: detected tag unreadable or empty")
        return "N", 0, None
    user_str = str(user_provided).strip()
    detected_str = str(detected).strip()
    if detected_str == user_str:
        logging.info(f"[{request_id}] Tag MATCH: '{detected_str}' == '{user_str}'")
        return "Y", 100, user_str
    # OCR often miscounts zeros in digit+(>=3 zeros)+suffix tags; treat as match when lead and suffix equal.
    if user_str.isdigit() and detected_str.isdigit():
        user_m = _TAG_ZERO_RUN_RE.match(user_str)
        det_m = _TAG_ZERO_RUN_RE.match(detected_str)
        if (
            user_m
            and det_m
            and user_m.group(1) == det_m.group(1)
            and user_m.group(3) == det_m.group(3)
            and (wide or user_m.group(1) == "1")
        ):
            logging.info(
                f"[{request_id}] Tag MATCH (zero-run tolerant): "
                f"'{detected_str}' ~= '{user_str}'"
            )
            return "Y", 100, user_str
    # A label can print the asset number inside a longer code: customer 1197's labels read
    # "JLHL-DOM40000456" for the registered 40000456. That matches only when the registered digits
    # are the read's ONLY digit run — exact digits, never part of a longer run, never fuzzy, and
    # never beside another number: "10109964/10109965" hedges between neighbouring tags, and
    # "40000456-1" may be a sub-asset of 40000456.
    if (
        wide
        and user_str.isdigit()
        and len(user_str) >= 5
        and not detected_str.isdigit()
        and re.findall(r"\d+", detected_str) == [user_str]
    ):
        logging.info(
            f"[{request_id}] Tag MATCH (registered digits inside a printed code): "
            f"'{detected_str}' ~= '{user_str}'"
        )
        return "Y", 100, user_str
    logging.info(f"[{request_id}] Tag MISMATCH: '{detected_str}' != '{user_str}'")
    return "N", 0, None


def normalize_detected_tag(tag) -> str | None:
    if tag is None:
        return None
    s = str(tag).strip()
    if not s or s.upper() == "UNREADABLE":
        return "UNREADABLE" if s.upper() == "UNREADABLE" else None
    if len(s) > 20:
        return "UNREADABLE"
    return s


def normalize_detected_tags(raw) -> list[str]:
    """Normalize one or many detected tags; dedupe; exclude UNREADABLE."""
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = normalize_detected_tag(item)
        if normalized and normalized != "UNREADABLE" and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def merge_detected_tag_lists(*lists: list[str]) -> list[str]:
    """Union tag lists preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for lst in lists:
        for tag in lst:
            if tag not in seen:
                seen.add(tag)
                result.append(tag)
    return result


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        return _edit_distance(b, a)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(
                prev[j + 1] + 1,
                curr[j] + 1,
                prev[j] + (0 if ca == cb else 1),
            ))
        prev = curr
    return prev[-1]


_SUSPICIOUS_TAG_RE = re.compile(
    r"^0{7,}$"            # all zeros: 0000000000
    r"|^1(?:0{6,})$"       # 1 followed by 6+ zeros: 1000000, 1000000000
    r"|^1234567890$"       # sequential
    r"|^(\d)\1{6,}$"      # same digit repeated 7+ times: 1111111, 9999999999
)


def is_suspicious_tag_number(value: str | None, request_id: str = "") -> bool:
    """Return True if the detected tag looks like a hallucinated/fabricated number."""
    if not value or not isinstance(value, str):
        return False
    val = value.strip()
    if not val or val.upper() == "UNREADABLE":
        return False
    if val in PROMPT_EXAMPLE_TAG_NUMBERS:
        logging.warning(
            f"[{request_id}] Prompt example tag leaked into output: '{val}' — treating as UNREADABLE"
        )
        return True
    if _SUSPICIOUS_TAG_RE.match(val):
        logging.warning(
            f"[{request_id}] Suspicious hallucinated tag detected: '{val}' — treating as UNREADABLE"
        )
        return True
    # Check for a short prefix followed by only zeros (e.g. "10000000", "20000000")
    if val.isdigit() and len(val) >= 7:
        non_zero_prefix = val.rstrip("0")
        if len(non_zero_prefix) <= 2 and len(val) - len(non_zero_prefix) >= 5:
            logging.warning(
                f"[{request_id}] Suspicious tag with trailing zeros: '{val}' — treating as UNREADABLE"
            )
            return True
    return False


def apply_tag_detection_to_output(
    output: dict,
    gemini_output: dict,
    retry_output: dict | None,
    request_id: str,
    decoded_codes: list[dict] | None = None,
) -> None:
    """Populate detectedtagnumbers, detectedtagnumber, match fields from LLM output.

    ``decoded_codes`` carries checksum-verified barcode payloads. One that matches
    the declared TagNumber outranks the OCR read; otherwise the vision result is
    used exactly as before.
    """
    llm = dict(gemini_output)
    codes = normalize_detected_codes(llm.pop("detected_codes", None))
    if not codes and llm.get("detectedtagnumbers"):
        for val in normalize_detected_tags(llm.get("detectedtagnumbers")):
            codes.append({"type": "asset_tag", "value": val})
    if not codes:
        primary = normalize_detected_tag(llm.get("detectedtagnumber"))
        if primary and primary != "UNREADABLE":
            codes = [{"type": "asset_tag", "value": primary}]

    if retry_output:
        retry_codes = normalize_detected_codes(retry_output.get("detected_codes"))
        if not retry_codes:
            for val in normalize_detected_tags(retry_output.get("detectedtagnumbers")):
                retry_codes.append({"type": "asset_tag", "value": val})
        seen = {c["value"] for c in codes}
        for entry in retry_codes:
            if entry["value"] not in seen:
                codes.append(entry)
                seen.add(entry["value"])

    codes = normalize_code_types_for_asset_tags(
        codes, output.get("tagnumber"), request_id
    )
    # The capture app stamps the date onto every photo, and the first read listed it as an
    # asset tag: 28 records a week were told their tag "does not match [`22-Oct-2026`]" with
    # readability Y (docs 4.4). A date is never a tag, so it is not a read at all.
    stamped = [c["value"] for c in codes if _DATE_STAMP_RE.match(str(c.get("value") or "").strip())]
    if stamped:
        logging.info(f"[{request_id}] Date stamp read as a code, ignored: {stamped}")
        codes = [c for c in codes if c["value"] not in stamped]

    asset_tags = [
        c for c in codes if c.get("type") == "asset_tag" and is_readable_code(c.get("value"))
    ]
    resolved = resolve_asset_tag_from_codes(codes, output.get("tagnumber"), request_id)

    # Suspicious pattern detection: override hallucinated tags to UNREADABLE.
    _suspicious_override = False
    if is_suspicious_tag_number(resolved, request_id):
        resolved = "UNREADABLE"
        _suspicious_override = True
        asset_tags = [
            c for c in asset_tags if not is_suspicious_tag_number(c.get("value"), request_id)
        ]

    output["detectedtagnumbers"] = [c["value"] for c in asset_tags]
    output["detectedtagnumber"] = resolved

    decoded_vals = decoded_values(decoded_codes)
    output["decodedtagnumbers"] = decoded_vals
    # The decoded TAG number, not the first symbol the decoder met: a laptop carton carries the
    # maker's QR (a 90-character support URL) beside the asset tag, and that URL was reported
    # as the decoded tag on 17 records a week (4.5). A decoded value that matches the input
    # tag is chosen below; until then, the first tag-like value, or nothing.
    output["decodedtagnumber"] = next((v for v in decoded_vals if _is_tag_like(v)), None)
    output["multiplebarcodesdetected"] = len(decoded_vals) > 1

    user_tag_empty = _user_tag_empty(output.get("tagnumber"))
    # NA guard: an empty declared tag with no vision read stays NA. Decoded symbols
    # are deliberately excluded from this test so a stray sticker in frame cannot
    # flip a documented NA into a numeric mismatch.
    tag_not_applicable = user_tag_empty and not asset_tags

    match_status, match_pct, matched = compare_tag_numbers(
        resolved, output.get("tagnumber"), request_id
    )
    tag_source = "vision"

    # Any read label that carries the registered tag settles it — the rule
    # resolve_asset_tag_from_codes already applies to an EXACT match among the candidates,
    # extended to the tolerances compare_tag_numbers accepts (a miscounted zero run; the tag
    # inside a longer printed code). The focused re-read lists every label in the photo, and a
    # tolerant match was otherwise lost whenever another candidate scored higher. A read that
    # can never be the resolved tag (a leaked prompt example, a bare run of zeros) never settles it.
    if TAG_MATCH_WIDENED_ENABLED and match_status != "Y" and not user_tag_empty:
        for entry in asset_tags:
            candidate = entry.get("value")
            if (
                not candidate
                or candidate == resolved
                or is_suspicious_tag_number(candidate, request_id)
            ):
                continue
            c_status, c_pct, c_matched = compare_tag_numbers(
                candidate, output.get("tagnumber"), request_id
            )
            if c_status == "Y":
                resolved = candidate
                match_status, match_pct, matched = c_status, c_pct, c_matched
                _suspicious_override = False
                output["detectedtagnumber"] = candidate
                break

    # A decoded symbol carries its own checksum, so one that matches the declared tag
    # outranks an OCR read that disagrees. When no decoded value matches, the vision
    # result stands untouched — decoding can correct a read, never invent a failure.
    if not user_tag_empty and decoded_vals:
        for value in decoded_vals:
            d_status, d_pct, d_matched = compare_tag_numbers(
                value, output.get("tagnumber"), request_id, widened=False
            )
            if d_status != "Y":
                continue
            logging.info(
                f"[{request_id}] Tag resolved from decoded barcode '{value}' "
                f"(vision read '{resolved}')"
            )
            resolved = value
            match_status, match_pct, matched = d_status, d_pct, d_matched
            tag_source = "decoded"
            _suspicious_override = False
            output["decodedtagnumber"] = value
            output["detectedtagnumber"] = value
            if value not in output["detectedtagnumbers"]:
                output["detectedtagnumbers"] = [value] + list(output["detectedtagnumbers"])
            break

    output["tagnumbersource"] = "NA" if tag_not_applicable else tag_source

    if tag_not_applicable:
        output["detectedtagnumber"] = "NA"
        output["detectedtagnumbers"] = []
        # Nothing confirms the tag: no number was sent and none is in the photo. The match is
        # N and fails the check like any other N (4.6); the other tag fields keep their "NA".
        match_status, match_pct = ("N", 0) if TAG_MATCH_NEVER_NA_ENABLED else ("NA", None)
        matched = None
    output["detectedtagnumbermatch"] = match_status
    output["detectedtagnumbermatchpercent"] = match_pct
    output["matchedtagnumber"] = matched

    # A tolerant match (zero-run OCR drift) means the sticker and the declared tag are
    # the same asset tag, so report the declared value — otherwise the response shows
    # Y/100 next to two different numbers. Raw read stays in detectedtagnumbers + log.
    if matched and output["detectedtagnumber"] != matched:
        logging.info(
            f"[{request_id}] Tag normalized to declared value: "
            f"raw read '{output['detectedtagnumber']}' -> reported '{matched}'"
        )
        output["detectedtagnumber"] = matched

    output["tag_detection_reasoning"] = enrich_tag_detection_reasoning(
        llm.get("tag_detection_reasoning"),
        codes,
        output.get("tagnumber"),
        output.get("detectedtagnumber"),
    )

    if llm.get("barcodeposition") is not None:
        from asset_validator.services.image_service import format_barcode_position

        output["barcodeposition"] = format_barcode_position(llm.get("barcodeposition"))

    # Extract tag_number_status from LLM; infer from existing signals as fallback.
    llm_tag_status = gemini_output.get("tag_number_status")
    if retry_output and retry_output.get("tag_number_status"):
        retry_tag_status = retry_output["tag_number_status"]
        # Prefer the more informative status after retry
        if llm_tag_status == "not_found" and retry_tag_status != "not_found":
            llm_tag_status = retry_tag_status
        elif llm_tag_status == "present_but_unreadable" and retry_tag_status == "readable":
            llm_tag_status = retry_tag_status

    if tag_not_applicable:
        output["tag_number_status"] = "not_found"
    elif llm_tag_status in ("readable", "present_but_unreadable", "not_found"):
        output["tag_number_status"] = llm_tag_status
    else:
        # Fallback inference from existing signals
        if asset_tags:
            output["tag_number_status"] = "readable"
        elif resolved and str(resolved).upper() == "UNREADABLE":
            output["tag_number_status"] = "present_but_unreadable"
        elif codes:
            output["tag_number_status"] = "readable"
        else:
            output["tag_number_status"] = "not_found"

    # If suspicious detection overrode the tag to UNREADABLE, the LLM's status
    # (likely "readable") is wrong — force "present_but_unreadable".
    # Also catch contradictions: LLM says "readable" but resolved tag is UNREADABLE/empty.
    if output["tag_number_status"] == "readable":
        tag_is_unreadable = (
            _suspicious_override
            or not resolved
            or str(resolved).upper() == "UNREADABLE"
        )
        if tag_is_unreadable:
            output["tag_number_status"] = "present_but_unreadable" if codes else "not_found"

    # A decoded match is physical proof the label is readable, whatever the LLM
    # concluded from the pixels — otherwise the response pairs a Y match with an
    # "unreadable" barcodereadability and the msg contradicts the verdict.
    if tag_source == "decoded" and not tag_not_applicable:
        output["tag_number_status"] = "readable"

    # Near-miss detection: if detected tag is 1-2 edits from user tag,
    # the LLM likely misread a blurry digit on the correct tag sticker.
    # Not when a decoded barcode confirms the read: a checksum-verified number is no blurry
    # misread, it is a different tag — customer 1158's neighbouring label 10107331 against a
    # registered 10107324 was reported "E" (unreadable) instead of a readable mismatch.
    if (
        output["tag_number_status"] == "readable"
        and output.get("detectedtagnumbermatch") == "N"
        and resolved
        and str(resolved).upper() != "UNREADABLE"
        and output.get("tagnumber")
        and str(resolved).strip() not in decoded_vals
    ):
        user_tag = str(output["tagnumber"]).strip()
        detected_str = str(resolved).strip()
        if len(user_tag) == len(detected_str):  # same length = likely same tag
            dist = _edit_distance(detected_str, user_tag)
            if 1 <= dist <= 2:
                logging.info(
                    f"[{request_id}] Near-miss tag: '{detected_str}' vs '{user_tag}' "
                    f"(edit distance {dist}) — treating as present_but_unreadable"
                )
                output["tag_number_status"] = "present_but_unreadable"

    llm_primary = normalize_detected_tag(gemini_output.get("detectedtagnumber"))
    if (
        output.get("tag_number_status") == "not_found"
        and llm_primary == "UNREADABLE"
        and not user_tag_empty
    ):
        output["tag_number_status"] = "present_but_unreadable"

    llm_barcode_read = gemini_output.get("barcodereadability")
    if retry_output and retry_output.get("barcodereadability"):
        llm_barcode_read = retry_output["barcodereadability"]
    output["barcodereadability"] = resolve_barcodereadability(
        output.get("tag_number_status"),
        tag_match=output.get("detectedtagnumbermatch"),
        tag_not_applicable=tag_not_applicable,
        llm_value=llm_barcode_read,
    )


def settle_failed_tag_check_from_decode(
    output: dict,
    decoded_codes: list[dict] | None,
    request_id: str,
) -> bool:
    """Settle a tag check whose model call failed, from a decoded barcode that matches.

    The tag's QR code on AssetId 1951716 decoded locally as 1000856200 — the registered tag —
    while the model call that reads the tag failed twice. The record then failed as "the tag
    number check could not be completed", with a checksum-verified match already in hand.

    A decoded symbol that matches the registered tag is the same evidence the normal path
    already ranks above an OCR read (apply_tag_detection_to_output), so the check is settled
    through that same function, as a decoded read. A decoded value that does NOT match
    settles nothing: it may be the maker's own QR, and decoding may correct a read but never
    invent a failure. Returns True when the check was settled.
    """
    declared = output.get("tagnumber")
    decoded = decoded_values(decoded_codes)
    if not decoded or _user_tag_empty(declared):
        return False
    if not any(compare_tag_numbers(v, declared, request_id, widened=False)[0] == "Y" for v in decoded):
        return False

    # Settle on a copy, so a result that is not a clean decoded match leaves nothing behind.
    trial = dict(output)
    apply_tag_detection_to_output(trial, {}, None, request_id, decoded_codes=decoded_codes)
    if trial.get("tagnumbersource") != "decoded" or trial.get("detectedtagnumbermatch") != "Y":
        return False
    output.update(trial)
    incomplete = output.get("_checks_incomplete") or []
    if "tagnumbercheck" in incomplete:
        incomplete.remove("tagnumbercheck")
    logging.info(
        f"[{request_id}] Tag check call failed; settled from decoded barcode "
        f"'{output.get('decodedtagnumber')}'"
    )
    return True


def sanitize_custom_field_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in LLM_NULL_STRINGS:
            return None
        return stripped
    return value


def enforce_identity_confidence(output: dict, gemini_output: dict) -> None:
    """Cap subcat match percent when top identity candidate confidence is low.

    Never flips Y→N on confidence alone — product-type gates handle hard rejects.
    """
    if output.get("error") or output.get("subcatmodelmatch") != "Y":
        return
    reasoning = gemini_output.get("inspection_reasoning")
    if not isinstance(reasoning, dict):
        return

    flags = list(reasoning.get("uncertainty_flags") or [])
    flag_set = {str(f).lower() for f in flags if f}
    if "brand_not_visible_on_body" in flag_set:
        return

    candidates = reasoning.get("identity_candidates") or []
    if not candidates:
        return
    typed = [c for c in candidates if isinstance(c, dict)]
    if not typed:
        return
    top = max(typed, key=lambda c: float(c.get("confidence") or 0))
    try:
        confidence = float(top.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if confidence < 0.75:
        pct = output.get("subcatmodelmatchpercent", 0)
        if isinstance(pct, (int, float)) and pct > 60:
            output["subcatmodelmatchpercent"] = 60


def apply_generation_ambiguity_cap(output: dict, gemini_output: dict) -> None:
    """Cap subcat match percent when generation is ambiguous."""
    if output.get("error"):
        return
    reasoning = gemini_output.get("inspection_reasoning")
    flags: list = []
    if isinstance(reasoning, dict):
        flags = list(reasoning.get("uncertainty_flags") or [])
    flag_set = {str(f).lower() for f in flags if f}

    candidates = []
    if isinstance(reasoning, dict):
        candidates = [c for c in (reasoning.get("identity_candidates") or []) if isinstance(c, dict)]
    if len(candidates) >= 2:
        sorted_c = sorted(candidates, key=lambda c: float(c.get("confidence") or 0), reverse=True)
        try:
            gap = abs(float(sorted_c[0].get("confidence") or 0) - float(sorted_c[1].get("confidence") or 0))
            if gap < 0.15:
                flag_set.add("generation_ambiguous")
        except (TypeError, ValueError):
            pass

    if "generation_ambiguous" in flag_set:
        pct = output.get("subcatmodelmatchpercent", 0)
        if isinstance(pct, (int, float)) and pct > 60:
            output["subcatmodelmatchpercent"] = 60


def apply_detected_description_policy(output: dict, plan) -> None:
    """
    detected_description: one-line image description when name/description match is N only.
    Cleared when namedescriptionmatch is Y or the name check is disabled.
    """
    if not plan.is_enabled("assetnamedescriptionmatch"):
        output["detected_description"] = None
        return
    if output.get("namedescriptionmatch") == "Y":
        output["detected_description"] = None
        return
    if output.get("namedescriptionmatch") != "N":
        output["detected_description"] = None
        return
    desc = output.get("detected_description")
    if not desc or not isinstance(desc, str):
        output["detected_description"] = None
        return
    # Only whitespace has no first line: it used to raise IndexError, an HTTP 500 after every check had run.
    lines = desc.strip().splitlines()
    line = lines[0].strip() if lines else ""
    words = line.split()
    if len(words) > 20:
        line = " ".join(words[:20])
    output["detected_description"] = line or None


SUBCATMODEL_REASONING_MAX_WORDS = 25
REASONING_SINGLE_MAX_WORDS = 25
REASONING_BOTH_MAX_WORDS = 40

_ACQUISITION_REASON_SHORT = {
    "future_date": "Acquisition date is in the future.",
    "before_release": "Acquisition date predates model release year.",
    "after_discontinuation": "Acquisition date is after model discontinuation.",
    "invalid_release_year": "Model release year is missing or invalid.",
}


def truncate_reasoning_words(text: str | None, *, max_words: int = SUBCATMODEL_REASONING_MAX_WORDS) -> str | None:
    """Return text trimmed to at most max_words, cut at a sentence boundary when possible.

    A hard word-count cut used to slice mid-word ("...in 2022, accounting"), which reads
    as a broken response to the customer.
    """
    if not text or not isinstance(text, str):
        return None
    line = text.strip()
    if not line:
        return None
    words = line.split()
    if len(words) <= max_words:
        return line or None

    clipped = " ".join(words[:max_words])
    # Prefer ending on the last complete sentence, if one survives the cut.
    boundary = max(clipped.rfind("."), clipped.rfind("!"), clipped.rfind("?"))
    if boundary >= len(clipped) // 2:
        return clipped[: boundary + 1].strip() or None
    return clipped.rstrip(" ,;:-") + "…"


def enforce_subcatmodel_reasoning_word_limit(
    output: dict,
    *,
    max_words: int = SUBCATMODEL_REASONING_MAX_WORDS,
) -> None:
    """Cap subcatmodelreasoning length for API responses."""
    if output.get("subcatmodelreasoning") is None:
        return
    output["subcatmodelreasoning"] = truncate_reasoning_words(
        output.get("subcatmodelreasoning"),
        max_words=max_words,
    )


def _short_acquisition_reason(reason: str | None) -> str:
    if reason and reason in _ACQUISITION_REASON_SHORT:
        return _ACQUISITION_REASON_SHORT[reason]
    return "Acquisition date is invalid."


def _build_cost_date_reasoning_clause(output: dict) -> str:
    """Build a short cost/acquisition-date failure clause from LLM or server fallbacks."""
    parts: list[str] = []
    if output.get("cost_check") == "FAIL":
        cr = output.get("cost_reasoning")
        if isinstance(cr, str) and cr.strip():
            parts.append(cr.strip())
        else:
            parts.append("Declared cost outside acceptable market range.")
    if output.get("acquisition_date_valid") is False:
        rr = output.get("release_year_reasoning")
        if isinstance(rr, str) and rr.strip():
            parts.append(rr.strip())
        else:
            parts.append(
                _short_acquisition_reason(output.get("acquisition_date_invalid_reason"))
            )
    return " ".join(parts).strip()


def build_combined_reasoning(output: dict) -> None:
    """
    Set public `reasoning` from name and/or cost/date failure text.

    - Single-side failure (or name pass): max 25 words
    - Both name and cost/date failed: max 40 words
    Clears intermediate `namedescriptionreasoning`.
    """
    name_failed = output.get("namedescriptionmatch") == "N"
    cost_failed = (
        output.get("cost_check") == "FAIL"
        or output.get("acquisition_date_valid") is False
    )
    both_failed = name_failed and cost_failed
    max_words = REASONING_BOTH_MAX_WORDS if both_failed else REASONING_SINGLE_MAX_WORDS

    intermediate_name = output.pop("namedescriptionreasoning", None)
    if isinstance(intermediate_name, str):
        intermediate_name = intermediate_name.strip() or None
    else:
        intermediate_name = None

    # Prior finalize already built public reasoning — re-cap only.
    if intermediate_name is None and output.get("reasoning") is not None:
        output["reasoning"] = truncate_reasoning_words(
            output.get("reasoning"),
            max_words=max_words,
        )
        return

    name_text = intermediate_name or ""
    if not name_text:
        legacy = output.get("reasoning")
        if isinstance(legacy, str) and legacy.strip():
            name_text = legacy.strip()

    cost_text = _build_cost_date_reasoning_clause(output)

    if name_failed and cost_failed:
        parts = [p for p in (name_text, cost_text) if p]
        combined = " ".join(parts) if parts else None
    elif name_failed:
        combined = name_text or None
    elif cost_failed:
        combined = cost_text or None
    elif output.get("namedescriptionmatch") == "Y" and name_text:
        combined = name_text
    else:
        combined = None

    output["reasoning"] = truncate_reasoning_words(combined, max_words=max_words)


def enforce_namedescription_reasoning_word_limit(
    output: dict,
    *,
    max_words: int = REASONING_SINGLE_MAX_WORDS,
) -> None:
    """Backward-compatible alias — prefer build_combined_reasoning."""
    if output.get("namedescriptionreasoning") is None and output.get("reasoning") is None:
        return
    # Map intermediate name field onto reasoning, then apply single-side cap.
    if output.get("namedescriptionreasoning") and not output.get("reasoning"):
        output["reasoning"] = output["namedescriptionreasoning"]
    output["reasoning"] = truncate_reasoning_words(
        output.get("reasoning") or output.get("namedescriptionreasoning"),
        max_words=max_words,
    )
    output.pop("namedescriptionreasoning", None)


def enforce_reasoning_word_limit(
    output: dict,
    *,
    max_words: int = REASONING_SINGLE_MAX_WORDS,
) -> None:
    """Cap public reasoning length for API responses."""
    if output.get("reasoning") is None:
        return
    output["reasoning"] = truncate_reasoning_words(
        output.get("reasoning"),
        max_words=max_words,
    )


def enrich_subcatmodel_reasoning_with_pinecone(output: dict) -> None:
    """Append master-data context to subcatmodelreasoning when Pinecone recommendations exist."""
    parts = []
    if output.get("recommendedsubcategory"):
        parts.append(f"master subcategory={output['recommendedsubcategory']}")
    if output.get("recommendedmakemodel"):
        parts.append(f"master makemodel={output['recommendedmakemodel']}")
    if output.get("recommendedmodel"):
        parts.append(f"master model={output['recommendedmodel']}")
    if not parts:
        return

    base = str(output.get("subcatmodelreasoning") or "").strip()
    suffix = f"Pinecone: {', '.join(parts)}."
    if suffix in base:
        return
    output["subcatmodelreasoning"] = f"{base} {suffix}".strip() if base else suffix


def enforce_match_percent_bounds(output: dict) -> None:
    """Clamp match percentages — no pipeline short-circuit."""
    if output.get("error"):
        return

    if output.get("namedescriptionmatch") == "Y":
        pct = output.get("namedescriptionmatchpercent", 0)
        if isinstance(pct, (int, float)) and pct < 85:
            output["namedescriptionmatchpercent"] = 85
    elif output.get("namedescriptionmatch") == "N":
        pct = output.get("namedescriptionmatchpercent", 0)
        if isinstance(pct, (int, float)) and pct > 40:
            output["namedescriptionmatchpercent"] = 40

    if output.get("subcatmodelmatch") == "Y":
        sc_pct = output.get("subcatmodelmatchpercent", 0)
        if isinstance(sc_pct, (int, float)):
            if sc_pct < 75:
                output["subcatmodelmatchpercent"] = 75
            elif sc_pct > 95:
                output["subcatmodelmatchpercent"] = 95
    elif output.get("subcatmodelmatch") == "N":
        sc_pct = output.get("subcatmodelmatchpercent", 0)
        if isinstance(sc_pct, (int, float)):
            if sc_pct > SUBCATMODEL_N_MAX_PERCENT:
                output["subcatmodelmatchpercent"] = SUBCATMODEL_N_MAX_PERCENT
            elif sc_pct < 10:
                output["subcatmodelmatchpercent"] = 10


def apply_null_subcat_override(
    output: dict,
    subcategory_null: bool,
    makemodel_null: bool,
) -> None:
    """REQ-9: a null subcategory or makemodel has nothing to disagree with, so the record is approved at 75 (docs 6.5).

    The pass carries no master-data correction: the recommended fields echo the customer's own values, and a
    field left blank shows master data's best match only where the photo supports it (docs 2.2, 6.5).
    """
    if subcategory_null and makemodel_null:
        note = "subcategory and makemodel were null; nothing to disagree with, approved at 75"
    elif subcategory_null:
        note = "subcategory was null; nothing to disagree with, approved at 75"
    else:
        note = "makemodel was null; nothing to disagree with, approved at 75"

    output["subcatmodelmatch"] = "Y"
    output["subcatmodelmatchpercent"] = 75
    output["note"] = note


def apply_ai_failure_defaults(output: dict, error_message: str, request_id: str) -> None:
    logging.warning(f"[{request_id}] AI failure: {error_message[:120]}")
    output["imageAnalysis"] = None
    output["detectedAsset"] = None
    output["detected_description"] = None
    output["imageReadability"] = "E"
    output["namedescriptionmatch"] = "N"
    output["namedescriptionmatchpercent"] = 0
    output["namedescriptionreasoning"] = None
    output["reasoning"] = None
    output["subcatmodelmatch"] = "N"
    output["subcatmodelmatchpercent"] = 0
    output["subcatmodelreasoning"] = None
    output["condition"] = None
    output["recommendedcategory"] = None
    output["recommendedsubcategory"] = None
    output["recommendedmakemodel"] = None
    output["recommendedmodel"] = None
    output["categorymatch"] = None
    output["categorymatchpercent"] = None
    output["subcategorymatch"] = None
    output["subcategorymatchpercent"] = None
    output["makemodelmatch"] = None
    output["makemodelmatchpercent"] = None
    output["modelmatch"] = None
    output["modelmatchpercent"] = None
    output["detectedtagnumber"] = None
    output["detectedtagnumbers"] = []
    output["matchedtagnumber"] = None
    output["detectedtagnumbermatch"] = "N"
    output["detectedtagnumbermatchpercent"] = 0
    output["barcodeposition"] = {"position": None}
    output["barcodereadability"] = "E"
    if error_message:
        output["error"] = error_message


def apply_match_validation_failure(output: dict, error_message: str, request_id: str) -> None:
    logging.warning(f"[{request_id}] Match validation failed: {error_message[:120]}")
    output["namedescriptionmatch"] = "N"
    output["namedescriptionmatchpercent"] = 0
    output["namedescriptionreasoning"] = None
    output["reasoning"] = None
    output["subcatmodelmatch"] = "N"
    output["subcatmodelmatchpercent"] = 0
    output["subcatmodelreasoning"] = None
    if error_message:
        output["error"] = error_message


def apply_custom_fields_from_gemini(
    output: dict,
    gemini_output: dict,
    custom_fields_for_llm: dict,
    request_id: str,
) -> None:
    if not gemini_output or not custom_fields_for_llm:
        return
    response_by_lower = {
        k.lower(): v for k, v in gemini_output.items() if isinstance(k, str)
    }
    for field_name in custom_fields_for_llm:
        raw = None
        if field_name in gemini_output:
            raw = gemini_output[field_name]
        elif field_name.lower() in response_by_lower:
            raw = response_by_lower[field_name.lower()]
        else:
            logging.warning(f"[{request_id}] Custom field '{field_name}' missing from Gemini")
        output[field_name] = sanitize_custom_field_value(raw)


TAG_REREAD_FOCUSED_PROMPT = load_prompt("tag_reread_focused.md").strip()

# A read that cannot be an asset number: fewer than five digits, or a date such as the camera's
# stamp ("10-Oct-2026", "19/09/2026", "2026-09-19"), which the previous re-read returned as the tag.
_DATE_STAMP_RE = re.compile(
    r"^\d{1,2}[-/.](?:\d{1,2}|[A-Za-z]{3,9})[-/.]\d{2,4}$|^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}$"
)


def _reply_labels(llm_out: dict) -> list:
    """The labels of one focused re-read reply. A lone label object counts as one; anything else
    that is not a list (a number, a string, null) counts as none rather than breaking the request."""
    labels = llm_out.get("labels")
    if isinstance(labels, dict):
        return [labels]
    return labels if isinstance(labels, list) else []


def reread_min_digits(registered) -> int:
    """Fewest digits a re-read number needs to be kept: five, or fewer when the registered tag
    itself has fewer (customer 1158's "VBNT0427"), so that tag can still be read exactly."""
    return max(1, min(5, sum(ch.isdigit() for ch in str(registered or ""))))


def _focused_reread_candidates(llm_out: dict, min_digits: int = 5) -> list[str]:
    """Asset-number candidates from one focused re-read reply, in the order read."""
    out: list[str] = []
    for label in _reply_labels(llm_out):
        if not isinstance(label, dict):
            continue
        value = re.sub(r"\s+", "", str(label.get("number") or ""))
        if (
            value
            and sum(ch.isdigit() for ch in value) >= min_digits
            and not _DATE_STAMP_RE.match(value)
            and value not in out
        ):
            out.append(value)
    return out


def _focused_tag_reread(vision_model, photos: list[str], request_id: str, min_digits: int = 5) -> dict:
    """Read every tag label in each photo, ONE photo per call, in parallel (docs §4.2).

    Returns the shape the previous re-read returned, so apply_tag_detection_to_output merges it
    with the first read unchanged: a candidate equal to the registered tag settles the match.
    """
    from concurrent.futures import ThreadPoolExecutor

    from asset_validator.utils.token_usage import bind_request_usage, get_request_usage

    usage = get_request_usage()          # captured on the request thread

    def _read(b64: str) -> dict:
        bind_request_usage(usage)        # otherwise these calls' tokens go unrecorded
        reply = call_llm_with_retry(
            vision_model,
            TAG_REREAD_FOCUSED_PROMPT,
            images=[{"mime_type": "image/jpeg", "data": b64}],
            max_tokens=600,
            response_mime_type="application/json",
            usage_label="barcode_retry",
        )
        return reply if isinstance(reply, dict) else {"error": "NO_REPLY"}

    with ThreadPoolExecutor(max_workers=len(photos)) as pool:
        replies = list(pool.map(_read, photos))
    answered = [r for r in replies if not r.get("error")]
    if not answered:
        return {"error": next((r.get("error") for r in replies if r.get("error")), "NO_REPLY")}
    candidates: list[str] = []
    labels = 0
    for reply in answered:
        labels += len(_reply_labels(reply))
        for value in _focused_reread_candidates(reply, min_digits):
            if value not in candidates:
                candidates.append(value)
    logging.info(
        f"[{request_id}] Focused tag re-read: {labels} label(s) on {len(answered)} photo(s), "
        f"candidates {candidates}"
    )
    if candidates:
        status = "readable"
    else:
        status = "present_but_unreadable" if labels else "not_found"
    return {
        "detectedtagnumbers": candidates,
        "detectedtagnumber": candidates[0] if candidates else "UNREADABLE",
        "tag_detection_reasoning": f"focused re-read: {labels} label(s) on {len(answered)} photo(s)",
        "tag_number_status": status,
    }


def _previous_tag_reread(
    vision_model,
    barcode_b64: str,
    request_id: str,
    asset_b64: str | None = None,
    barcode_original_b64: str | None = None,
) -> dict:
    """The re-read deployed before the per-photo one, unchanged (docs §4.2).

    With ``barcode_original_b64`` (and TAG_REREAD_SPLIT_ENABLED) the tag photo is shown as two
    halves of the original upload instead of the shrunk copy — see split_tag_photo_for_reread.
    The asset photo is still sent, so a tag that sits in the asset slot is read as before.
    """
    image_parts = build_llm_image_parts(asset_b64, barcode_b64)
    halves = (
        split_tag_photo_for_reread(barcode_original_b64, request_id)
        if TAG_REREAD_SPLIT_ENABLED and barcode_b64 else []
    )
    if halves:
        image_parts = [{"mime_type": "image/jpeg", "data": h} for h in halves]
        if asset_b64 and asset_b64 != barcode_b64:
            image_parts.append({"mime_type": "image/jpeg", "data": asset_b64})
    if not image_parts:
        return {}
    logging.info(
        f"[{request_id}] Tag not settled — running focused re-read"
        + (" on the split original tag photo" if halves else "")
    )
    return call_llm_with_retry(
        vision_model,
        BARCODE_RETRY_PROMPT,
        images=image_parts,
        max_tokens=200,
        response_mime_type="application/json",
        usage_label="barcode_retry",
    ) or {}


def _focused_match(focused: dict, registered, request_id: str) -> str | None:
    """The per-photo read's number that IS the registered tag, if any — never a suspicious one."""
    for value in focused.get("detectedtagnumbers") or []:
        if is_suspicious_tag_number(value, request_id):
            continue
        if compare_tag_numbers(value, registered, request_id)[0] == "Y":
            return value
    return None


def retry_barcode_read(
    vision_model,
    barcode_b64: str,
    request_id: str,
    asset_b64: str | None = None,
    barcode_original_b64: str | None = None,
    asset_original_b64: str | None = None,
    registered=None,
) -> dict:
    """Second read of a tag the first read did not settle (docs §4.2).

    With TAG_REREAD_FOCUSED_ENABLED (default) every photo is also read on its own, from the
    ORIGINAL upload, by a short prompt that asks only for the asset number on each tag label, while
    the previous re-read runs beside it, unchanged. When one of those numbers IS the registered tag,
    that number alone is returned and the tag matches on it. Otherwise the previous re-read's reply
    is returned exactly as before, so a record the per-photo read cannot rescue is reported exactly
    as it was: its numbers never become the detected tag, the barcode readability or the message.

    ``registered`` is used only to pick that match and the digit floor; it never reaches a prompt.
    A photo is re-read only if its prepared copy exists — an upload dropped as oversized or
    undecodable stays out — and falls back to that copy when the original cannot be prepared.
    """
    if not barcode_b64 and not asset_b64:
        return {}
    if not TAG_REREAD_FOCUSED_ENABLED:
        return _previous_tag_reread(vision_model, barcode_b64, request_id, asset_b64, barcode_original_b64)

    from concurrent.futures import ThreadPoolExecutor

    from asset_validator.utils.token_usage import bind_request_usage, get_request_usage

    photos: list[str] = []
    for prepared, original in ((barcode_b64, barcode_original_b64), (asset_b64, asset_original_b64)):
        if not prepared:
            continue
        photo = tag_photo_for_focused_reread(original, request_id) or prepared
        if photo not in photos:
            photos.append(photo)
    usage = get_request_usage()          # captured on the request thread

    def _previous() -> dict:
        bind_request_usage(usage)        # otherwise this call's tokens go unrecorded
        return _previous_tag_reread(vision_model, barcode_b64, request_id, asset_b64, barcode_original_b64)

    logging.info(
        f"[{request_id}] Tag not settled — focused re-read of {len(photos)} photo(s) "
        f"beside the previous re-read"
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        previous_future = pool.submit(_previous)
        focused = _focused_tag_reread(vision_model, photos, request_id, reread_min_digits(registered))
        previous = previous_future.result()
    match = _focused_match(focused, registered, request_id)
    if match is None:
        return previous
    logging.info(f"[{request_id}] Focused re-read read the registered tag as '{match}'")
    return {
        "detectedtagnumbers": [match],
        "detectedtagnumber": match,
        "tag_detection_reasoning": "focused re-read: the registered tag was read on its label",
        "tag_number_status": "readable",
    }


def pinecone_classification_evaluated(output: dict) -> bool:
    """True when Pinecone ran and produced recommendations or classification match fields."""
    rec_keys = (
        "recommendedcategory",
        "recommendedsubcategory",
        "recommendedmakemodel",
        "recommendedmodel",
    )
    if any(output.get(k) for k in rec_keys):
        return True
    match_keys = ("categorymatch", "subcategorymatch", "makemodelmatch", "modelmatch")
    return any(output.get(k) is not None for k in match_keys)


def compute_final_verdict(
    output: dict,
    *,
    enabled_checks: frozenset[str] | None = None,
) -> tuple[str, list[str]]:
    """Return (finalverdict, finalverdict_reason) — only enabled checks are evaluated."""
    failed: list[str] = []
    enabled = enabled_checks or frozenset()

    # A check that could not run leaves its verdict null so that an internal error is
    # never written down as evidence against the asset. Null means "no finding", which in
    # a two-state verdict silently means approval — so the absence has to be reported in
    # its own right. Simulating a Gemini outage for a customer with tagnumbercheck off
    # returned PASS on a record where nothing whatsoever had been checked; with the tag
    # check on it returned FAIL, but only because the emptied tag fields happened to read
    # as unreadable. Approving unvalidated records is the worse of those two accidents,
    # and neither was deliberate.
    #
    # So an incomplete check fails the record on its own terms. It is not a finding about
    # the asset, and build_failure_msg says so plainly: the check could not be completed,
    # this is a processing issue, not a problem with the uploaded photos. That leaves the
    # record for a retry or a human instead of passing it off as validated.
    incomplete = [c for c in (output.get("_checks_incomplete") or []) if c in enabled]
    if incomplete:
        failed.append("checks_incomplete")

    if "assetnamedescriptionmatch" in enabled and output.get("namedescriptionmatch") == "N":
        failed.append("namedescriptionmatch")

    if "assetmakemodelmatch" in enabled:
        from asset_validator.core.config import CATEGORY_ALWAYS_TRUSTED_ENABLED

        if output.get("subcatmodelmatch") == "N":
            failed.append("subcatmodelmatch")
        if pinecone_classification_evaluated(output):
            # The customer's category is always right (docs 6.6): never a failure reason, whatever
            # a status field says. Only subcategory, make/model and model are judged.
            statuses = ("categorymatch", "subcategorymatch", "makemodelmatch", "modelmatch")
            if CATEGORY_ALWAYS_TRUSTED_ENABLED:
                statuses = statuses[1:]
            for key in statuses:
                if output.get(key) == "failed":
                    failed.append(key)

    readability_checks = {
        "assetnamedescriptionmatch",
        "assetmakemodelmatch",
        "acquisitiondatecostmatch",
        "assetcondition",
    }
    if enabled & readability_checks and output.get("imageReadability") == "E":
        failed.append("imagereadability")

    if "tagnumbercheck" in enabled and "tagnumbercheck" not in incomplete:
        # An incomplete tag check is reported once, as incomplete. Without the guard the
        # emptied fields below read as "no tag was found anywhere", and a transport error
        # came back as a tag mismatch on a record whose own message said the check had not
        # run.
        tag_match = output.get("detectedtagnumbermatch")
        if tag_match == "NA":
            pass
        else:
            tags = output.get("detectedtagnumbers") or []
            detected_tag = output.get("detectedtagnumber")
            tag_unreadable = (
                not tags
                and (not detected_tag or str(detected_tag).upper() == "UNREADABLE")
            )
            if tag_unreadable or tag_match == "N":
                failed.append("detectedtagnumbermatch")

    if "acquisitiondatecostmatch" in enabled:
        if output.get("cost") is not None and output.get("cost_check") == "FAIL":
            failed.append("cost_check")
        if (
            output.get("acquisitiondate") is not None
            and output.get("acquisition_date_valid") is False
        ):
            failed.append("acquisition_date_valid")

    verdict = "FAIL" if failed else "PASS"
    return verdict, failed


# Strips the "<check> failed:" prefix from each msg line — clients want the plain
# explanation; finalverdict_reason already carries the machine-readable check keys.
_MSG_FAILED_PREFIX_RE = re.compile(r"^\w+ failed:\s*", re.IGNORECASE)


_TAG_LIKE_RE = re.compile(r"^(?=(?:[^0-9]*[0-9]){4})[A-Za-z0-9][A-Za-z0-9/._-]{3,19}$")


def _is_tag_like(value) -> bool:
    """A payload that could be an asset number: 4-20 characters, at least four digits, no
    spaces — not a URL, a product code with punctuation, or a stray label fragment ("S3")."""
    text = str(value or "").strip()
    return bool(text) and not _DATE_STAMP_RE.match(text) and bool(_TAG_LIKE_RE.match(text))


def _reportable_reads(reads: list, *, limit: int = 3) -> list[str]:
    """The reads worth quoting in a mismatch message (4.5): tag-like values first, numeric
    before alphanumeric, at most ``limit`` of them. A photo of a rack lists every label the
    reader saw ("1239800", "S3", "UTP28X2MBL", ...); the reviewer needs the numbers."""
    seen: list[str] = []
    for value in reads:
        text = str(value or "").strip()
        if text and text not in seen and _is_tag_like(text):
            seen.append(text)
    seen.sort(key=lambda v: (not v.isdigit(), -sum(ch.isdigit() for ch in v)))
    return seen[:limit]


def build_multiple_barcode_note(output: dict) -> str | None:
    """Say so when the photo held more than one decodable symbol.

    Which one was used is not obvious from a single tag field, so the message
    names the tag-like values and which of them matched the declared tag; a maker's
    QR code (a URL) or a product barcode is counted, not quoted (4.5).
    """
    values = output.get("decodedtagnumbers") or []
    if len(values) <= 1:
        return None
    tag_like = [v for v in values if _is_tag_like(v)]
    others = len(values) - len(tag_like)
    listed = ", ".join(f"'{v}'" for v in tag_like[:5])
    if others:
        listed = (listed + ", " if listed else "") + (
            f"{others} other code{'s' if others > 1 else ''} (product QR/barcode)"
        )
    if output.get("tagnumbersource") == "decoded" and output.get("detectedtagnumbermatch") == "Y":
        return (
            f"Multiple barcodes detected ({len(values)}): {listed}. "
            f"Used '{output.get('decodedtagnumber')}' which matches the input tag number."
        )
    return (
        f"Multiple barcodes detected ({len(values)}): {listed}. "
        f"None matches the input tag number '{output.get('tagnumber')}'."
    )


def _tag_check_messages(output: dict) -> list[str]:
    """The tag check's lines of the failure message, when it failed (4.5).

    Shared by the ordinary message and the missing-asset-photo message: the tag is read from
    the OTHER photo, so its result is not a consequence of the missing asset photo and is
    reported either way.
    """
    messages: list[str] = []
    # msg is shown to the reviewer as the rejection reason, so it may only carry
    # REASONS. The multiple-barcode note explains which code was used, which matters
    # when the tag check failed and is noise when it passed. On the 8 Lenovo laptops
    # (customer 1001) every tag decoded and matched, yet this note was the whole
    # message on a record that failed on COST: "Multiple barcodes detected (2):
    # https://support.lenovo.com/qrcode?... Used 10109128 which matches the input tag
    # number." A laptop carries its maker's QR beside the asset tag, so that pairing
    # is normal and says nothing about why the record failed.
    if output.get("detectedtagnumbermatch") != "Y":
        multi_note = build_multiple_barcode_note(output)
        if multi_note:
            messages.append(multi_note)
    if output.get("missing_image") == "tag":
        if output.get("_barcode_image_provided"):
            messages.append(
                "The uploaded tag/barcode photo could not be read, so the tag number "
                "could not be verified. Please retake the tag photo."
            )
        else:
            messages.append(
                "Tag/barcode photo not provided — only an asset photo was uploaded, so "
                "the tag number could not be verified."
            )
    else:
        barcode_read = output.get("barcodereadability")
        tags = output.get("detectedtagnumbers") or []
        detected_tag = output.get("detectedtagnumber")
        # Tag numbers actually READ from the photo. The mismatch is reported from these
        # rather than from barcodereadability, because the two can disagree: the vision
        # model may return a concrete number AND label tag_number_status
        # "present_but_unreadable" in the same response, which resolves to
        # barcodereadability "E". Readability was tested first here, so that one flag
        # suppressed the mismatch text and the customer was told "Tag number is available
        # but not readable" about a tag the UI was showing them — tag 10109967 declared,
        # 10109965 detected and displayed, message claiming it could not be read.
        # Same contradiction the decoded-tag rule near resolve_barcodereadability already
        # fixes for decoded reads; this is the vision-read half.
        readings = _reportable_reads(tags or ([detected_tag] if is_readable_code(detected_tag) else []))
        if output.get("detectedtagnumbermatch") == "N" and readings:
            messages.append(
                f"detectedtagnumbermatch failed: Input barcode '{output.get('tagnumber')}' "
                f"does not match any detected tag {readings}."
            )
        elif barcode_read == "E":
            messages.append("Tag number is available but not readable")
        elif barcode_read == "NA":
            messages.append("Tag number is not available")
        elif output.get("detectedtagnumbermatch") == "N":
            if detected_tag and str(detected_tag).upper() == "UNREADABLE":
                messages.append("detectedtagnumbermatch failed: Barcode image is unreadable.")
            elif not tags and not detected_tag:
                messages.append("detectedtagnumbermatch failed: Barcode number could not be detected.")
            else:
                messages.append(
                    "detectedtagnumbermatch failed: Input barcode does not match detected barcode."
                )
    return messages


def _with_tag_result(text: str, output: dict, enabled, *, tag_photo_absent: bool = False) -> str:
    """Append the tag check's failure to a missing-asset-photo message (3.3, 4.5).

    The tag was read from the OTHER photo, so a mismatch or an unreadable tag is not a
    consequence of the missing asset photo. Left out, the customer added the asset photo and
    failed again on a tag problem they had never been told about (7-20 a week).

    `tag_photo_absent`: the message already says no tag photo was sent, so a line that only
    says the tag was not found is not added again.
    """
    if "tagnumbercheck" not in (enabled or ()):
        return text
    if output.get("detectedtagnumbermatch") != "N" and output.get("barcodereadability") != "E":
        return text
    lines = _tag_check_messages(output)
    if tag_photo_absent:
        lines = [line for line in lines if line != "Tag number is not available"]
    lines = [line if line.endswith((".", "!", "?")) else f"{line}." for line in lines]
    return f"{text} {' '.join(lines)}".strip() if lines else text


def build_failure_msg(
    output: dict,
    *,
    enabled_checks: frozenset[str] | None = None,
) -> str | None:
    messages = []
    enabled = enabled_checks or frozenset()

    # A check that could not RUN is reported as exactly that. Its verdict is null, so it
    # adds no failure below; saying nothing at all would leave the reviewer with a record
    # that is silently less validated than it appears.
    incomplete = [c for c in (output.get("_checks_incomplete") or []) if c in enabled]
    if incomplete:
        names = {
            "tagnumbercheck": "tag number",
            "assetnamedescriptionmatch": "asset name/description",
            "assetmakemodelmatch": "make/model",
            "acquisitiondatecostmatch": "cost",
            "assetcondition": "condition",
        }
        listed = ", ".join(names.get(c, c) for c in incomplete)
        messages.append(
            f"The {listed} check could not be completed, so it was not verified. "
            f"This is a processing issue, not a problem with the uploaded photos."
        )

    # A missing required photo is THE reason for every downstream failure — say so plainly
    # and skip the per-check messages, which are just consequences of the missing photo.
    if output.get("missing_image") == "asset":
        # Every provided photo is a tag close-up. Say so directly — which upload FIELD it
        # arrived in is irrelevant, and fields are routinely swapped (the tag close-up in
        # AssetImage, the asset in BarcodeImage), so the field-based wording below would
        # tell this customer their asset photo "could not be used" when none was sent.
        if output.get("_asset_photo_kind") == ASSET_PHOTO_TAG_ONLY:
            return _with_tag_result(
                "Asset photo not provided — only a tag/barcode close-up was uploaded, so "
                "the asset name, make/model, condition and cost could not be verified. "
                "Please add a photo showing the whole asset.",
                output, enabled,
            )
        # The photos show the asset's box. A label on a carton says what was shipped, not
        # what is inside it today or in what condition, so nothing about the asset was seen.
        if output.get("_asset_photo_kind") == ASSET_PHOTO_PACKAGING:
            return _with_tag_result(
                "The photo shows the asset's packaging (its box), not the asset itself, so "
                "the asset name, make/model, condition and cost could not be verified. "
                "Please unpack the asset and photograph it.",
                output, enabled,
            )
        # The photo TYPE is inferred from content, so a blank/unusable asset photo lands
        # here too. Telling a customer who did upload one that it was "not provided" sends
        # them to re-upload the same unusable file. Say which situation it actually is.
        if output.get("_asset_image_provided"):
            text = (
                "The uploaded asset photo could not be used — no asset is visible in it, "
                "so the asset name, make/model, condition and cost could not be verified. "
                "Please retake the asset photo."
            )
            tag_photo_absent = not output.get("_barcode_image_provided")
            if tag_photo_absent:
                text += " No tag/barcode photo was provided either."
            return _with_tag_result(text, output, enabled, tag_photo_absent=tag_photo_absent)
        return _with_tag_result(
            "Asset photo not provided — only a tag/barcode photo was uploaded, so the "
            "asset name, make/model, condition and cost could not be verified.",
            output, enabled,
        )

    recaptured = bool(output.get("_recaptured"))
    if recaptured:
        # The root cause, so it comes first. The other checks ran on the asset's likeness and
        # keep their real results, so any real failure among them is still reported below.
        messages.append(RECAPTURE_MESSAGE)

    if (
        "assetnamedescriptionmatch" in enabled
        and output.get("namedescriptionmatch") == "N"
        # When the photo itself is unusable, the readability line below already says so.
        # Adding a name-match line too produces "Asset not visible in the uploaded image.
        # Asset is not visible in uploaded image(s)." -- the same fact stated twice to the
        # customer. The unusable photo is the root cause; report it once.
        # A RECAPTURED photo is not unusable: it shows a picture of some asset, so a name
        # mismatch on it is a separate, real reason and is still reported.
        and (output.get("imageReadability") != "E" or recaptured)
    ):
        # Use the model's specific reason so the client sees WHY it failed, not a generic
        # line. Strip the leading "N:" verdict prefix and cap to the reasoning word budget.
        reason = str(
            output.get("namedescriptionreasoning") or output.get("reasoning") or ""
        ).strip()
        # Strip the leading verdict prefix, whichever separator the model used ("N:" or "N.").
        # The model prefixes its reason with the verdict in several styles: "N:", "N.",
        # "N," and "N - ". All of them leak into the customer-facing msg, so strip any of
        # them. Requires a separator so a real sentence starting with "No ..." survives.
        reason = re.sub(
            r"^[NY]\s*[:.,\-–—]+\s*", "", reason, flags=re.IGNORECASE
        ).strip()
        reason = truncate_reasoning_words(reason, max_words=REASONING_SINGLE_MAX_WORDS)
        if reason:
            messages.append(f"namedescriptionmatch failed: {reason}")
        else:
            messages.append(
                "namedescriptionmatch failed: Asset name/description does not match the uploaded image."
            )
    # subcatmodelmatch / category / subcategory / makemodel / model failures are
    # intentionally excluded from msg — the client reads those from the structured
    # fields (subcatmodelmatch, categorymatch, makemodelmatch, ...) directly.

    readability_checks = {
        "assetnamedescriptionmatch",
        "assetmakemodelmatch",
        "acquisitiondatecostmatch",
        "assetcondition",
    }
    # Not for a recapture: the asset's image IS visible, and the recapture line above already
    # says what is wrong with the photo.
    if (
        enabled & readability_checks
        and output.get("imageReadability") == "E"
        and not recaptured
    ):
        messages.append("Asset is not visible in uploaded image(s).")

    if "tagnumbercheck" in enabled:
        messages.extend(_tag_check_messages(output))

    if "acquisitiondatecostmatch" in enabled:
        # A cost failure used to be left out of msg, on the grounds that the client reads
        # cost_check and the bounds directly. But msg is the reviewer's "AI Rejected
        # Reason", so a record failing ONLY on cost arrived with the reason blank — eight
        # Lenovo laptops (customer 1001) each declared at Rs 1 against a Rs 40,000-70,000
        # market, failed, and said nothing. State the figures; the structured fields stay
        # the machine-readable source.
        if output.get("cost") is not None and output.get("cost_check") == "FAIL":
            from asset_validator.services.cost_service import is_placeholder_cost

            declared = output.get("cost")
            lo, hi = output.get("cost_acceptance_lower"), output.get("cost_acceptance_upper")
            currency = output.get("currency") or ""
            if isinstance(declared, (int, float)) and not isinstance(declared, bool) and declared <= 0:
                messages.append(
                    f"cost_check failed: Declared acquisition cost is {declared}, "
                    f"which is not a valid cost. Please enter the actual purchase cost."
                )
            elif is_placeholder_cost(declared):
                messages.append(
                    f"cost_check failed: Declared acquisition cost {currency} {declared} is a "
                    f"placeholder, not the asset's cost. Please enter the actual purchase cost."
                )
            elif isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and output.get("_cost_priced_from_photo"):
                # A never-priced class priced from a photo of another item (7.1): say what was priced.
                from asset_validator.utils.classification_match import _with_article

                messages.append(
                    f"cost_check failed: The photo shows {_with_article(output['_cost_priced_from_photo'])} "
                    f"({currency} {lo} - {hi}), which does not match the declared acquisition cost "
                    f"{currency} {declared} for this asset."
                )
            elif isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                messages.append(
                    f"cost_check failed: Declared acquisition cost {currency} {declared} is "
                    f"outside the expected range {currency} {lo} - {hi} for this asset."
                )
            else:
                messages.append(
                    f"cost_check failed: Declared acquisition cost {currency} {declared} "
                    f"could not be reconciled with the expected market value."
                )
        if output.get("acquisitiondate") is not None and output.get("acquisition_date_valid") is False:
            acq_parsed = parse_acquisition_date(output.get("acquisitiondate"))
            release_year = output.get("model_release_year")
            if acq_parsed is not None and release_year is not None:
                reason = acquisition_invalid_reason(
                    acq_parsed,
                    int(release_year),
                    model_end_year=output.get("model_end_year"),
                ) or "invalid_release_year"
                messages.append(
                    format_acquisition_invalid_message(
                        reason,
                        acquisition=acq_parsed,
                        release_year=int(release_year),
                        model_end_year=output.get("model_end_year"),
                    )
                )
            else:
                messages.append(
                    "acquisition_date_valid failed: Acquisition date or model release year is missing."
                )

    # Make/model reasons are normally left to the structured fields. But when make/model is the
    # ONLY reason a record failed, that left the reviewer a FAIL with no message at all: AssetId
    # 2818594, a single-seater sofa failed as sofa-vs-chair on 2026-09-09 with msg null. Said only
    # when nothing else is, so no record that already has a message changes.
    if (
        not messages
        and "assetmakemodelmatch" in enabled
        and output.get("subcatmodelmatch") == "N"
    ):
        messages.append(_classification_mismatch_message(output))

    if not messages:
        return None
    cleaned = [_as_sentence(_MSG_FAILED_PREFIX_RE.sub("", m).strip()) for m in messages]
    return " ".join(m for m in cleaned if m)


def _classification_mismatch_message(output: dict) -> str:
    blank = output.get("_blank_field_master")
    if isinstance(blank, dict):
        # A field the customer left blank, which master data fills in with certainty (docs 6.5): say that, not
        # "does not match the asset in the photo" — nothing the customer filled in was wrong.
        subcategory = str(output.get("recommendedsubcategory") or "").strip()
        makemodel = str(output.get("recommendedmakemodel") or "").strip()
        if blank.get("subcategory") and blank.get("makemodel"):
            return (f"The subcategory and make/model were left blank. Master data lists this asset under "
                    f"{subcategory} as {makemodel}.")
        if blank.get("subcategory"):
            return f"The subcategory was left blank. Master data lists this asset under {subcategory}."
        return f"The make/model was left blank. Master data lists this asset as {makemodel}."
    conflict = output.get("_makemodel_brand_conflict")
    if isinstance(conflict, dict):
        # The make/model's brand is what the photo contradicts, so say that, not the
        # generic "does not match": the product type itself was right.
        makemodel = str(output.get("makemodel") or "").strip()
        text = "The registered make/model"
        if makemodel:
            text += f" ({makemodel})"
        text += (f" says {conflict.get('declared')}, but the photo shows "
                 f"{conflict.get('photo')} branding.")
        suggested = str(output.get("recommendedmakemodel") or "").strip()
        if suggested:
            text += f" Master data suggests {suggested}."
        return text
    registered = " / ".join(
        str(output.get(k)).strip() for k in ("subcategoryname", "makemodel")
        if str(output.get(k) or "").strip()
    )
    text = "The registered classification"
    if registered:
        text += f" ({registered})"
    detected = str(output.get("detectedAsset") or "").strip()
    text += " does not match the asset in the photo"
    text += f" ({detected})." if detected else "."
    suggested = " / ".join(
        str(output.get(k)).strip() for k in ("recommendedsubcategory", "recommendedmakemodel")
        if str(output.get(k) or "").strip()
    )
    if suggested:
        text += f" Master data suggests {suggested}."
    return text


def _as_sentence(text: str) -> str:
    """Start with a capital and end with a full stop, so joined reasons read as sentences.

    Reasons often come from the model with a verdict prefix ("N, the name describes a tank…").
    Stripping the prefix left the reviewer a message opening in lower case — AssetId 2694085 read
    "the name describes storage racks and a table, but…". Only a leading lower-case LETTER is
    changed, so a message opening with a tag number, a backtick or a digit is left as it is.
    """
    if not text:
        return text
    if text[0].isalpha() and text[0].islower():
        text = text[0].upper() + text[1:]
    if text[-1].isalnum():
        text += "."
    return text


def _response_value_is_null(value) -> bool:
    """True when a response field should be treated as empty/null for ordering."""
    return value is None


def order_response_output(result: dict) -> dict:
    """
    Order JSON response: populated values first, null fields last.
    Preserves RESPONSE_KEY_ORDER within each group.
    """
    from asset_validator.core.constants import RESPONSE_KEY_ORDER

    populated_keys: list[str] = []
    null_keys: list[str] = []

    for key in RESPONSE_KEY_ORDER:
        if key not in result:
            continue
        if _response_value_is_null(result[key]):
            null_keys.append(key)
        else:
            populated_keys.append(key)

    ordered_keys = populated_keys + null_keys
    return {key: result[key] for key in ordered_keys}


def finalize_response_output(output: dict, plan) -> dict:
    """
    Return full response schema — disabled check fields are null;
    enabled check fields keep model/post-process values.
    """
    from asset_validator.core.constants import (
        ALL_RESPONSE_KEYS,
        CHECK_FLAGS,
        CHECK_OUTPUT_KEYS,
        SHARED_CHECK_OUTPUT_KEYS,
    )

    build_combined_reasoning(output)

    result = {key: output.get(key) for key in ALL_RESPONSE_KEYS}

    for check_id in CHECK_FLAGS:
        if plan.is_enabled(check_id):
            continue
        for key in CHECK_OUTPUT_KEYS.get(check_id, frozenset()):
            if key in SHARED_CHECK_OUTPUT_KEYS:
                continue
            result[key] = None

    checks_with_readability = {
        cid for cid, keys in CHECK_OUTPUT_KEYS.items() if "imageReadability" in keys
    }
    if not (plan.enabled & checks_with_readability):
        result["imageReadability"] = None

    # reasoning is shared — null only when neither name nor cost check is enabled
    if not (
        plan.is_enabled("assetnamedescriptionmatch")
        or plan.is_enabled("acquisitiondatecostmatch")
    ):
        result["reasoning"] = None

    enforce_subcatmodel_reasoning_word_limit(result)

    # Last step before the response leaves: make every string safe for a consumer that
    # inlines values into SQL (apostrophes → backticks). See sanitize_output_text.
    return order_response_output(sanitize_output_text(result))


def filter_response_fields(output: dict, plan) -> dict:
    """Alias for finalize_response_output (full schema, nulls for disabled checks)."""
    return finalize_response_output(output, plan)


def apply_check_failure_defaults(
    output: dict,
    check_id: str,
    error_message: str,
    request_id: str,
) -> None:
    """Leave a check that could not RUN unevaluated — a failure here is not evidence.

    These defaults fire when a check errors internally: a transport error, a timeout, or a
    prompt the client refused to send. None of that says anything about the asset or the
    photograph, so none of it may produce a negative verdict.

    It used to. Customer 1212's civil assets — buildings, roads, compound walls, drains,
    warehouses — append an 8,194-char structure block to the name-match prompt, which took
    it to 28,453 chars against a 25,000 cap, and the call was refused as PROMPT_TOO_LONG.
    This handler then blanked the analysis and set imageReadability="E", so the reviewer was
    shown "Asset is not visible in uploaded image(s)" for a clear photograph of a road, on
    all 24 records. The same shape applied to the tag check, where an internal error
    reported the tag as unreadable AND mismatched.

    So each check now records that it did not complete and leaves its verdict null, which
    compute_final_verdict treats as "no failure" and build_failure_msg reports plainly.
    That follows the rule the cost branch below already states: "An LLM/transport failure
    is not evidence against the declared values." Identification produced by an EARLIER
    phase (imageAnalysis, detectedAsset, imageReadability) is kept — it did not come from
    the check that failed, and discarding it is what manufactured the false "not visible".
    """
    from asset_validator.core.config import COST_INCOMPLETE_TRUSTS_DECLARED_ENABLED

    logging.warning(f"[{request_id}] Check {check_id} failed: {error_message[:120]}")
    # The cost check is the exception the owner chose (2.4): when its valuation call cannot
    # complete, the declared cost is trusted and the record is not failed as incomplete — a
    # placeholder cost still fails (7.8).
    cost_trusted = check_id == "acquisitiondatecostmatch" and COST_INCOMPLETE_TRUSTS_DECLARED_ENABLED
    incomplete = output.setdefault("_checks_incomplete", [])
    if check_id not in incomplete and not cost_trusted:
        incomplete.append(check_id)
    if check_id == "tagnumbercheck":
        output["detectedtagnumber"] = None
        output["detectedtagnumbers"] = []
        output["matchedtagnumber"] = None
        output["detectedtagnumbermatch"] = None
        output["detectedtagnumbermatchpercent"] = None
        output["barcodeposition"] = {"position": None}
        output["barcodereadability"] = None
    elif check_id == "assetnamedescriptionmatch":
        output["namedescriptionmatch"] = None
        output["namedescriptionmatchpercent"] = None
        output["namedescriptionreasoning"] = None
        output["reasoning"] = None
    elif check_id == "assetmakemodelmatch":
        output["subcatmodelmatch"] = None
        output["subcatmodelmatchpercent"] = None
        output["subcatmodelreasoning"] = None
        output["categorymatch"] = None
        output["categorymatchpercent"] = None
        output["subcategorymatch"] = None
        output["subcategorymatchpercent"] = None
        output["makemodelmatch"] = None
        output["makemodelmatchpercent"] = None
        output["modelmatch"] = None
        output["modelmatchpercent"] = None
    elif check_id == "acquisitiondatecostmatch":
        # An LLM/transport failure is not evidence against the declared values.
        from asset_validator.core.config import COST_TRUST_DECLARED_WHEN_UNSURE
        from asset_validator.services.cost_service import (
            compute_declared_fallback_bounds,
            format_cost_range,
            is_placeholder_cost,
        )

        if output.get("cost") is not None:
            trusted = cost_trusted or COST_TRUST_DECLARED_WHEN_UNSURE
            output["cost_check"] = "PASS" if trusted else "FAIL"
            if is_placeholder_cost(output["cost"]):
                output["cost_check"] = "FAIL"
            accept_min, accept_max = compute_declared_fallback_bounds(output["cost"])
            output["cost_acceptance_lower"] = accept_min
            output["cost_acceptance_upper"] = accept_max
            output["cost_range"] = format_cost_range(accept_min, accept_max)
            output["currency"] = output.get("currency") or "INR"
        if output.get("acquisitiondate"):
            output["acquisition_date_valid"] = (
                True if (cost_trusted or COST_TRUST_DECLARED_WHEN_UNSURE) else False
            )
    elif check_id == "assetcondition":
        output["condition"] = None
    if error_message and not cost_trusted:
        output["error"] = error_message


def apply_acquisition_cost_input_echo(
    output: dict,
    *,
    user_cost: float | None,
    acquisition_date,
) -> None:
    """Echo null for cost/date omitted from request; clear related check fields."""
    if user_cost is None:
        output["cost"] = None
        output["cost_check"] = None
    else:
        output["cost"] = user_cost

    if acquisition_date is None:
        output["acquisitiondate"] = None
        output["acquisition_date_valid"] = None
    else:
        output["acquisitiondate"] = format_acquisition_date(acquisition_date)


def build_initial_output(req_body: dict, plan) -> dict:
    """Seed full response schema — user inputs echoed as-is; check fields null until checks run."""
    acquisition_raw = req_body.get("acquisitiondate") or req_body.get("acquisition_date")
    acquisition_str = format_acquisition_date(acquisition_raw) if acquisition_raw else None

    output: dict = {
        "taggingai": req_body.get("taggingai", True),
        "assetid": req_body.get("assetid"),
        "assettaggingdetailid": req_body.get("assettaggingdetailid"),
        "customerid": req_body.get("customerid"),
        "companyid": req_body.get("companyid"),
        "assetclassid": req_body.get("assetclassid"),
        "assetclassname": req_body.get("assetclassname"),
        "classnumber": req_body.get("classnumber"),
        "tagnumber": req_body.get("tagnumber"),
        "assetname": req_body.get("assetname"),
        "description": req_body.get("description"),
        "categoryid": req_body.get("categoryid"),
        "categoryname": req_body.get("categoryname"),
        "subcategoryid": req_body.get("subcategoryid"),
        "subcategoryname": req_body.get("subcategoryname"),
        "makemodelid": req_body.get("makemodelid"),
        "makemodel": req_body.get("makemodel"),
        "modelid": req_body.get("modelid"),
        "model": req_body.get("model"),
        "acquisitiondate": acquisition_str,
        "cost": req_body.get("cost") if req_body.get("cost") is not None else None,
        "currency": resolve_currency(
            req_body.get("customerid"), req_body.get("currency")
        ),
        "imageAnalysis": None,
        "detectedAsset": None,
        "detected_description": None,
        "imageReadability": None,
        "namedescriptionmatch": None,
        "namedescriptionmatchpercent": None,
        "reasoning": None,
        "subcatmodelmatch": None,
        "subcatmodelmatchpercent": None,
        "subcatmodelreasoning": None,
        "recommendedcategory": None,
        "recommendedsubcategory": None,
        "recommendedmakemodel": None,
        "recommendedmodel": None,
        "categorymatch": None,
        "categorymatchpercent": None,
        "subcategorymatch": None,
        "subcategorymatchpercent": None,
        "makemodelmatch": None,
        "makemodelmatchpercent": None,
        "modelmatch": None,
        "modelmatchpercent": None,
        "detectedtagnumber": None,
        "detectedtagnumbermatch": None,
        "detectedtagnumbermatchpercent": None,
        "decodedtagnumber": None,
        "tagnumbersource": None,
        "barcodeposition": None,
        "barcodereadability": None,
        "condition": None,
        "cost_lower_bound": None,
        "cost_upper_bound": None,
        "cost_acceptance_lower": None,
        "cost_acceptance_upper": None,
        "cost_range": None,
        "cost_check": None,
        "model_release_year": None,
        "model_end_year": None,
        "acquisition_date_valid": None,
        "finalverdict": "FAIL",
        "finalverdict_reason": [],
        "msg": None,
        "error": None,
    }

    return output
