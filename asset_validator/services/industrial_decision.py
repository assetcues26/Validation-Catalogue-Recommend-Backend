"""The name decision for industrial records: a photo reading that is not told the record, then a judge (5.6).

WHY THIS EXISTS. On a bench of 99 real photo requests (31 Oct 2026) the name check let 15 of 26 gross
mismatches through and rejected 28 of 59 genuine industrial records. Both come from the same design:
- Phase 1 is told the record and writes it back as what it sees: a smelter pot shell registered as a forklift
  was described as "Fork lift truck", a coal feeder registered as a ball mill as "Ball Mill". The name check
  then compares the record with itself and approves.
- The name check never sees the photo. It reads phase 1's description, which names enclosed machinery by its
  casing ("vertical cylindrical vessel"), and rejects a coal feeder for not being a vessel.
Attaching the photos to the same text check only moved the balance (20 wrong caught, 26 genuine passed, of 40
and 59); a reading written without the record caught all 26 gross mismatches.

So, for an industrial record (its category or asset class names plant, machinery or industry; the customer's
category is trusted, team rule 1 Nov 2026) that is not an IT or furniture record:
1. It runs after the name check on the model's own approval, and on its rejection for the asset's kind or the
   tag's position. A rejection for brand, logo or colour, a verdict set by a server rule (a rescue or a brand
   rule), a name that says nothing (5.7), and a photo that shows no asset (the name-free blind view, 3.1) are left
   alone.
2. A model that is NOT told the record reads the photo(s) the blind view says show the asset.
3. A judge (text only) compares the record with that reading under the owner's rules: huge machinery is
   photographed by a component (motor, drive, chute, casing, panel), equipment on the same system counts, and
   only a gross mismatch is a wrong record (1 Nov 2026).
4. A server rule turns the judge's relation into the verdict: "different_kind" fails, anything else passes.
   Heavy machinery is decided from the photo, never from the record: on heavy machinery a rejection for the
   tag's position never stands; on anything else it does, as before.
Any error, timeout or unreadable reply keeps the first verdict.

INDUSTRIAL_PHOTO_DECISION_MODE: "shadow" (the default) runs it beside the remaining checks and only logs what it
would decide; "on" applies it, only for the customers in INDUSTRIAL_PHOTO_DECISION_CUSTOMERS when that list is
set (the rest are not decided at all, so they pay nothing); "off" never runs it. "on" waits at most
INDUSTRIAL_PHOTO_DECISION_TIMEOUT_SEC; past it the first verdict stands.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ON = "on"
USAGE_OBSERVE = "industrial_name_observe"
USAGE_JUDGE = "industrial_name_decide"
TRIGGER_REJECTION = "rejection"
TRIGGER_APPROVAL = "approval"
ACTION_PASS = "pass"
ACTION_FAIL = "fail"
ACTION_KEEP = "keep"
RELATIONS = frozenset({"same_kind", "component", "same_system", "cannot_tell", "different_kind"})
NAMED_BODIES = frozenset({"vessel_or_casing", "duct_or_structure", "recognisable_machine", "unknown"})
# Relations that pass a component or same-system photo. The name's brand rule (6.0) does not apply to them: a
# motor or a connected machine carries its own maker's badge, not the named asset's.
_PART_RELATIONS = frozenset({"component", "same_system"})
PASS_PERCENT = 85
FAIL_PERCENT = 20

# A rejection for brand, logo, maker or colour is a judgement about markings, not about what the object is.
_NOT_A_KIND_REASON_RE = re.compile(r"\b(?:brand|logo|colou?r|manufactur|maker|make\s+(?:is|of|shown|visible))",
                                   re.IGNORECASE)
# A colour rejection rarely says "colour": "the asset is grey, not the blue named in the record". It names a
# colour the record does not; a colour that only repeats the name ("not a black liquor pump") is not one.
_COLOUR_WORD_RE = re.compile(
    r"\b(?:red|blue|green|yellow|black|white|grey|gray|silver|orange|brown|pink|purple|violet|golden|maroon"
    r"|beige|cream)\b",
    re.IGNORECASE,
)
# A rejection because the tag sits on something else (name_match.md REJECT rule 6). Production words it many
# ways: "The tag is on a shelf, not the projector", "the tag sticker is on a network switch", "the image analysis
# and tag location indicate a fan controller". Matching too much only keeps today's rejection on an asset that
# is not heavy machinery; matching too little would overturn it. The 14 days to 29 Oct 2026 had 192 such
# rejections; the first alternative alone caught 103.
_TAG_POSITION_REASON_RE = re.compile(
    r"\btag\b.{0,80}?\b(?:separate|different|another|other\s+(?:object|device|item|equipment)|wall|pillar"
    r"|beam|column|railing|floor|not\s+(?:on|attached|affixed|fixed|mounted))"
    r"|\btag(?:\s+sticker|\s+label|\s+plate)?\s+is\s+(?:on|attached\s+to|affixed\s+to|placed\s+on|stuck\s+on"
    r"|mounted\s+on)\b"
    r"|\btag(?:\s+sticker)?\s+(?:is\s+)?on\s+(?:a|the)\s+(?:wall|concrete|column|separate|plaque|plate|floor"
    r"|pillar|beam|box|board|door|frame|structure|surface)"
    r"|\bnot\s+(?:attached|affixed)\s+to\s+the\b|\btag\s+is\s+not\s+on\b|\btag\s+location\b",
    re.IGNORECASE | re.DOTALL,
)
# Industrial by the customer's own classification (trusted), never by words in the name: "room", "building" or
# "machines" in a name or description made furniture and office records industrial in helpers.is_industrial_record.
_INDUSTRIAL_CLASS_RE = re.compile(r"\b(?:plant|machinery|industrial)\b", re.IGNORECASE)
_READING_SPECIFICITIES = frozenset({"specific", "component", "generic"})


def mode_for(req_body: dict) -> str:
    """The mode for this record: "on" only for a listed customer when a list is set, shadow for the rest."""
    from asset_validator.core.config import (
        INDUSTRIAL_PHOTO_DECISION_CUSTOMERS,
        INDUSTRIAL_PHOTO_DECISION_MODE,
        use_gemini,
    )

    mode = INDUSTRIAL_PHOTO_DECISION_MODE
    if mode not in (MODE_SHADOW, MODE_ON) or not use_gemini():
        return MODE_OFF
    if mode == MODE_ON and INDUSTRIAL_PHOTO_DECISION_CUSTOMERS:
        customer = str((req_body or {}).get("customerid") or "").strip()
        if customer not in INDUSTRIAL_PHOTO_DECISION_CUSTOMERS:
            return MODE_OFF
    return mode


def is_tag_position_reason(reason: str) -> bool:
    return bool(_TAG_POSITION_REASON_RE.search(str(reason or "")))


def in_scope(req_body: dict) -> str | None:
    """None when the record is decided here, else why not: industrial by its classification, not IT or furniture."""
    from asset_validator.utils.classification_match import (
        _FURNITURE_FAMILIES,
        _detect_product_categories,
        _is_it_record,
        _join_texts,
    )

    body = req_body or {}
    classes = " ".join(str(body.get(f) or "") for f in ("categoryname", "assetclassname"))
    if not _INDUSTRIAL_CLASS_RE.search(classes):
        return "not_industrial"
    if _is_it_record(body):
        return "it_record"
    named = _join_texts(*(body.get(f) for f in ("assetname", "subcategoryname", "makemodel")))
    if _detect_product_categories(named) & _FURNITURE_FAMILIES:
        return "furniture_record"
    return None


def _is_colour_reason(asset_name, reason: str) -> bool:
    named = {c.lower() for c in _COLOUR_WORD_RE.findall(str(asset_name or ""))}
    said = {c.lower() for c in _COLOUR_WORD_RE.findall(str(reason or ""))}
    return bool(named) and bool(said - named)


def trigger_for(
    output: dict, req_body: dict, name_ai_verdict, first_reason: str, photo_shows_asset
) -> tuple[str | None, str | None]:
    """(trigger, None) when the decision runs on this record, else (None, why not).

    `photo_shows_asset` is a callable, asked last: it may wait on the blind view.
    """
    from asset_validator.core.config import INDUSTRIAL_PHOTO_DECISION_ON_APPROVALS, PLACEHOLDER_NAME_RULE_ENABLED
    from asset_validator.utils.classification_match import extract_brand_keys
    from asset_validator.utils.helpers import is_placeholder_name

    body = req_body or {}
    if output.get("error"):
        return None, "check_error"
    out_of_scope = in_scope(body)
    if out_of_scope:
        return None, out_of_scope
    if PLACEHOLDER_NAME_RULE_ENABLED and is_placeholder_name(body.get("assetname"), body.get("description")):
        # A name that says nothing is failed by a server rule (5.7). The judge would take it for a plant code it
        # cannot place and pass it ("cannot_tell"), and a component pass would skip the name rules altogether.
        return None, "placeholder_name"
    verdict = output.get("namedescriptionmatch")
    ai = str(name_ai_verdict or "").upper()
    if verdict == "Y":
        if not INDUSTRIAL_PHOTO_DECISION_ON_APPROVALS:
            return None, "approvals_not_checked"
        if ai != "Y":
            # A server rescue (the named asset elsewhere in frame, 5.2) found the asset beside the main object;
            # judging the main object again would undo it.
            return None, "approved_by_server_rule"
        trigger = TRIGGER_APPROVAL
    elif verdict == "N":
        if ai != "N":
            return None, "rejected_by_server_rule"
        reason = str(first_reason or "")
        if (
            _NOT_A_KIND_REASON_RE.search(reason)
            or extract_brand_keys(reason, prose=True)
            or _is_colour_reason(body.get("assetname"), reason)
        ):
            return None, "brand_or_colour_reason"
        trigger = TRIGGER_REJECTION
    else:
        return None, "no_verdict"
    if not photo_shows_asset():
        return None, "no_asset_in_photo"
    return trigger, None


def _text(value, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _items(value, limit: int = 5) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_text(v, 60) for v in value if _text(v, 60)][:limit]


def observe(images: list, *, request_id: str) -> dict:
    """Read the photo(s) without the record. Never raises; {"error": ...} on failure."""
    from asset_validator.core.config import INDUSTRIAL_PHOTO_DECISION_MODEL
    from asset_validator.prompts.loader import load_prompt
    from asset_validator.services.llm_provider import call_llm_with_retry

    if not images:
        return {"error": "no image"}
    try:
        reply = call_llm_with_retry(
            INDUSTRIAL_PHOTO_DECISION_MODEL,
            load_prompt("industrial_photo_observe.md"),
            images=images,
            response_mime_type="application/json",
            usage_label=USAGE_OBSERVE,
            model_fallback=False,
        ) or {}
    except Exception as exc:  # the decision must never break a request
        return {"error": f"{type(exc).__name__}: {exc}"[:160]}
    if not isinstance(reply, dict) or reply.get("error"):
        return {"error": _text((reply or {}).get("error") if isinstance(reply, dict) else reply, 160)}
    reading = {
        "main_object": _text(reply.get("main_object"), 80),
        "specificity": _text(reply.get("specificity"), 20).lower(),
        "could_be": _items(reply.get("could_be"), 4),
        "visible_parts": _items(reply.get("visible_parts"), 5),
        "fixed_installed_machinery": reply.get("fixed_installed_machinery") is True,
        "asset_visible": reply.get("asset_visible"),
    }
    # A reply without its fields is not a reading: taken as one, it would look like a plain photo and pass.
    if (
        not reading["main_object"]
        or reading["specificity"] not in _READING_SPECIFICITIES
        or not isinstance(reading["asset_visible"], bool)
    ):
        return {"error": f"incomplete reading: {str(reply)[:120]}"}
    return reading


def judge(req_body: dict, reading: dict, *, request_id: str) -> dict:
    """Relate the record to the reading. Never raises; {"error": ...} on failure."""
    from asset_validator.core.config import INDUSTRIAL_PHOTO_DECISION_MODEL
    from asset_validator.prompts.loader import load_prompt
    from asset_validator.services.llm_provider import call_llm_with_retry
    from asset_validator.utils.helpers import normalize_user_description

    body = req_body or {}
    try:
        prompt = load_prompt(
            "industrial_photo_judge.md",
            asset_name=_text(body.get("assetname"), 200) or "(empty)",
            description=_text(normalize_user_description(body.get("description")), 300) or "(empty)",
            category=_text(body.get("categoryname"), 80) or "(empty)",
            subcategory=_text(body.get("subcategoryname"), 120) or "(empty)",
            main_object=reading.get("main_object") or "(not described)",
            specificity=reading.get("specificity") or "(not given)",
            could_be="; ".join(reading.get("could_be") or []) or "(none)",
            visible_parts="; ".join(reading.get("visible_parts") or []) or "(none)",
        )
        reply = call_llm_with_retry(
            INDUSTRIAL_PHOTO_DECISION_MODEL,
            prompt,
            response_mime_type="application/json",
            usage_label=USAGE_JUDGE,
            model_fallback=False,
        ) or {}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:160]}
    if not isinstance(reply, dict) or reply.get("error"):
        return {"error": _text((reply or {}).get("error") if isinstance(reply, dict) else reply, 160)}
    relation = _text(reply.get("relation"), 20).lower()
    if relation not in RELATIONS:
        return {"error": f"unknown relation {relation!r}"}
    named_body = _text(reply.get("named_body"), 24).lower()
    return {"relation": relation, "named_body": named_body if named_body in NAMED_BODIES else "unknown",
            "seen_kind": _text(reply.get("seen_kind"), 80), "reason": _text(reply.get("reason"), 240)}


def decide(trigger: str, first_reason: str, reading: dict, judgement: dict) -> dict:
    """The server's rule over the reading and the judge. Pure; no model call."""
    if reading.get("error") or judgement.get("error"):
        return {"action": ACTION_KEEP, "why": "error"}
    if reading.get("asset_visible") is False:
        # Whether an asset is in the photo is the blind view's call (3.1), not this decision's.
        return {"action": ACTION_KEEP, "why": "no_asset_in_reading"}
    if (
        trigger == TRIGGER_REJECTION
        and is_tag_position_reason(first_reason)
        and not reading.get("fixed_installed_machinery")
    ):
        # The owner's exemption covers heavy machinery only; anything else keeps REJECT rule 6.
        return {"action": ACTION_KEEP, "why": "tag_position_not_heavy_machinery"}
    if judgement.get("relation") == "different_kind":
        if (
            reading.get("specificity") == "generic"
            and judgement.get("named_body") != "recognisable_machine"
        ):
            # A plain casing or structure cannot rule out an asset whose body is one. Measured on the bench
            # (1 Nov 2026): the judge alone called the same "vertical cylindrical vessel" photo of genuine coal
            # feeders a different kind on 4 of 20 genuine records, and a mismatch on the next prompt wording.
            return {"action": ACTION_PASS, "why": "plain_photo_cannot_rule_out"}
        return {"action": ACTION_FAIL, "why": "different_kind"}
    return {"action": ACTION_PASS, "why": judgement.get("relation")}


def run(images: list, req_body: dict, trigger: str, first_reason: str, *, request_id: str) -> dict:
    """Reading, judge and decision for one record. Never raises."""
    started = time.perf_counter()
    reading = observe(images, request_id=request_id)
    judgement = {} if reading.get("error") else judge(req_body, reading, request_id=request_id)
    if reading.get("error"):
        judgement = {"error": "no reading"}
    outcome = decide(trigger, first_reason, reading, judgement)
    result = {"trigger": trigger, "reading": reading, "judgement": judgement, **outcome,
              "ms": round((time.perf_counter() - started) * 1000)}
    logging.info(
        f"[{request_id}] Industrial name decision: trigger={trigger} action={outcome['action']} "
        f"why={outcome['why']} saw={reading.get('main_object')!r} relation={judgement.get('relation')}"
    )
    return result


def passed_name_output(name_llm_out: dict, result: dict) -> dict:
    """The name check's answer turned into a pass, for the server rules to judge like any Y."""
    reading = result.get("reading") or {}
    if result.get("why") == "plain_photo_cannot_rule_out":
        # The judge argued for a mismatch the server overruled; its reason would contradict the verdict.
        reason = (f"the photo shows {reading.get('main_object') or 'a plain casing'}, which can be the registered "
                  f"asset's body, casing or a section of it.")
    else:
        reason = (result.get("judgement") or {}).get("reason") or "the photo shows the registered asset."
    return dict(name_llm_out or {}, namedescriptionmatch="Y", namedescriptionmatchpercent=PASS_PERCENT,
                namedescriptionreasoning=f"Y: {reason}")


def is_part_pass(result: dict) -> bool:
    """A pass on a component or same-system photo, which the name's brand rule must not undo."""
    return result.get("action") == ACTION_PASS and result.get("why") in _PART_RELATIONS


def apply_fail(output: dict, result: dict) -> None:
    """Fail the name and describe the photo by the reading that was not told the record.

    The phase-1 description of a wrong record repeats the record ("Fork lift truck" on a pot shell); left in
    place, make/model would approve the same echo and the message would contradict the verdict.
    """
    reading = result.get("reading") or {}
    judgement = result.get("judgement") or {}
    seen = judgement.get("seen_kind") or reading.get("main_object") or "a different kind of asset"
    output["namedescriptionmatch"] = "N"
    output["namedescriptionmatchpercent"] = FAIL_PERCENT
    output["namedescriptionreasoning"] = f"N: {judgement.get('reason') or 'the photo shows ' + seen + '.'}"
    if reading.get("main_object"):
        parts = ", ".join(reading.get("visible_parts") or [])
        described = f"{reading['main_object']}." + (f" Visible: {parts}." if parts else "")
        output["detectedAsset"] = reading["main_object"]
        output["imageAnalysis"] = described
        output["visible_assets"] = [reading["main_object"]]
        if output.get("detected_description"):
            output["detected_description"] = described


class BackgroundDecision:
    """The decision run beside the remaining checks (shadow mode); joined before the response."""

    def __init__(self, **kwargs):
        from asset_validator.utils.token_usage import bind_request_usage, get_request_usage

        self._started = time.time()
        usage = get_request_usage()  # captured on the request thread

        def _run() -> dict:
            bind_request_usage(usage)  # otherwise this call's tokens go unrecorded
            return run(**kwargs)

        self._executor = ThreadPoolExecutor(max_workers=1)
        self._future = self._executor.submit(_run)

    def result(self, extra_wait_sec: float) -> dict:
        try:
            return self._future.result(timeout=max(extra_wait_sec, 0.0))
        except FutureTimeout:
            return {"action": ACTION_KEEP, "why": "timeout",
                    "ms": round((time.time() - self._started) * 1000)}
        except Exception as exc:
            return {"action": ACTION_KEEP, "why": f"error: {type(exc).__name__}: {exc}"[:160]}
        finally:
            self._executor.shutdown(wait=False)
