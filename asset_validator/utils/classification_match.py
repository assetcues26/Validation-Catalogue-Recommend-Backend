"""Server-side user vs Pinecone master-data classification matching."""

from __future__ import annotations

import contextlib
import contextvars
import functools
import inspect
import re
from typing import Any, NamedTuple

_SUBCATEGORY_ALIASES: dict[str, frozenset[str]] = {
    "laptop": frozenset({"laptop", "notebook", "note book", "note-book", "mobile workstation"}),
    "notebook": frozenset({"laptop", "notebook", "note book", "note-book"}),
    "desktop": frozenset({"desktop", "pc", "computer", "workstation"}),
    "printer": frozenset({"printer", "multifunction", "mfp"}),
}

_CATEGORY_DOMAIN_TOKENS = frozenset({
    "it", "equipment", "assets", "asset", "electronics", "computer", "computers",
    "laptop", "laptops", "furniture", "vehicle", "vehicles", "hvac", "industrial",
})

# The percent a trusted category is reported at (docs 6.6): it is the customer's own choice, never
# measured against anything, so it is matched in full rather than at the floor of the matched band.
TRUSTED_CATEGORY_PERCENT = 100


def category_trusted() -> bool:
    """The customer selects the category, so it is always right and never judged (docs 6.6).

    Read per call, like every switch the rules below consult, so the App Setting takes effect
    without a redeploy. Off, every rule judges the category exactly as before 2026-11-01.
    """
    from asset_validator.core.config import CATEGORY_ALWAYS_TRUSTED_ENABLED

    return CATEGORY_ALWAYS_TRUSTED_ENABLED


# Set while a request for an industrial record is processed (product_types_scope): every rule then reads the product
# types of before 2026-11-02, exactly as with the switch off.
_V2_HELD_FOR_RECORD: contextvars.ContextVar[bool] = contextvars.ContextVar("product_types_v2_held", default=False)


def product_types_v2() -> bool:
    """Product types are read by the noun that names the thing, from the families of 2026-11-02 (docs 6.1).

    Read per call, like every switch here, so the App Setting takes effect without a redeploy. Off, every rule reads
    the eleven families of before 2026-11-02, a family word anywhere in the text, exactly as before. Off too inside
    product_types_scope for an industrial record.
    """
    from asset_validator.core.config import PRODUCT_TYPES_V2_ENABLED

    return PRODUCT_TYPES_V2_ENABLED and not _V2_HELD_FOR_RECORD.get()


@contextlib.contextmanager
def product_types_scope(record: dict | None):
    """Inside, an industrial record (helpers.asset_scope, docs 6.7) is read by every rule exactly as with
    PRODUCT_TYPES_V2_ENABLED off (owner, 2026-11-03: industrial records keep today's behaviour exactly). Everyday and
    other records read the product types of 2026-11-02. A record whose classing fails is industrial (fail safe).
    Decided from the switch itself, so a scope opened inside a held one still holds an industrial record."""
    from asset_validator.core.config import PRODUCT_TYPES_V2_ENABLED

    token = _V2_HELD_FOR_RECORD.set(bool(PRODUCT_TYPES_V2_ENABLED) and industrial_record(record))
    try:
        yield
    finally:
        _V2_HELD_FOR_RECORD.reset(token)


def reads_record_product_types(fn):
    """Runs `fn` inside product_types_scope for its argument `req_body`, passed by keyword or by position (docs 6.1,
    6.7). A call whose record cannot be found reads the list of before, as an industrial record does (fail safe)."""
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            record = signature.bind_partial(*args, **kwargs).arguments.get("req_body")
        except TypeError:
            record = None
        with product_types_scope(record) if record is not None else _reading_of_before():
            return fn(*args, **kwargs)
    return wrapper


@contextlib.contextmanager
def _reading_of_before():
    """Inside, every rule reads the eleven families of before 2026-11-02, as with PRODUCT_TYPES_V2_ENABLED off. Used
    to ask what the reading of before decides (docs 6.1: an absence-based failure the new list alone reaches)."""
    token = _V2_HELD_FOR_RECORD.set(True)
    try:
        yield
    finally:
        _V2_HELD_FOR_RECORD.reset(token)


def _join_texts(*parts: Any) -> str:
    """Several texts read as one, each kept apart by "; " (docs 6.1).

    The head-noun reading never runs a noun phrase across the separator, so a device word ending one field cannot
    read as the modifier of a noun that starts the next ("Laptop" + "Stand by the desk"). The reading of before
    2026-11-02 erases the separator, so it reads the joined text exactly as the plain space join it replaces.
    """
    return "; ".join(text for text in (str(part or "").strip() for part in parts) if text)


def normalize_classification_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().casefold()
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _token_set(value: str) -> set[str]:
    if not value:
        return set()
    return {t for t in value.split() if t}


def _alias_overlap(user: str, recommended: str, field: str) -> bool:
    if field != "subcategory":
        return False
    for aliases in _SUBCATEGORY_ALIASES.values():
        if user in aliases and recommended in aliases:
            return True
    return False


def compute_similarity(user: str, recommended: str, *, field: str) -> float:
    """Return 0.0–1.0 similarity between user and recommended master-data strings."""
    user_norm = normalize_classification_value(user)
    rec_norm = normalize_classification_value(recommended)
    if not user_norm or not rec_norm:
        return 0.0
    if user_norm == rec_norm:
        return 1.0
    if user_norm in rec_norm or rec_norm in user_norm:
        return 0.92
    if _alias_overlap(user_norm, rec_norm, field):
        return 0.9

    user_tokens = _token_set(user_norm)
    rec_tokens = _token_set(rec_norm)
    if not user_tokens or not rec_tokens:
        return 0.0

    intersection = user_tokens & rec_tokens
    union = user_tokens | rec_tokens
    jaccard = len(intersection) / len(union)

    if field == "category" and intersection & _CATEGORY_DOMAIN_TOKENS:
        if jaccard >= 0.25:
            return max(jaccard, 0.55)

    return jaccard


def _similarity_to_percent(similarity: float, matched: bool) -> int:
    if matched:
        return min(100, max(85, int(round(85 + similarity * 15))))
    if similarity >= 0.25:
        return max(25, min(40, int(round(20 + similarity * 20))))
    return max(10, min(20, int(round(10 + similarity * 10))))


def _match_threshold(field: str) -> float:
    if field == "category":
        return 0.25
    if field == "subcategory":
        return 0.30
    return 0.35


def evaluate_classification_field(
    user_value: Any,
    recommended_value: Any,
    *,
    field: str,
    llm_match: str | None = None,
    llm_percent: Any = None,
) -> tuple[str | None, int | None]:
    """
    Return (matched|failed|null, percent|null) for one classification field.

    Server string comparison overrides LLM when both values are present.
    """
    user = normalize_classification_value(user_value)
    recommended = normalize_classification_value(recommended_value)

    if not user or not recommended:
        return None, None

    similarity = compute_similarity(user, recommended, field=field)
    server_matched = similarity >= _match_threshold(field)

    if server_matched:
        match = "matched"
    else:
        match = "failed"

    percent = _similarity_to_percent(similarity, server_matched)

    if isinstance(llm_percent, (int, float)) and llm_match == match:
        llm_pct = int(llm_percent)
        if match == "matched":
            percent = max(percent, min(100, llm_pct))
        else:
            percent = min(percent, max(10, llm_pct))

    return match, percent


def reconcile_classification_matches(
    *,
    user_category: str,
    user_subcategory: str,
    user_makemodel: str,
    user_model: str,
    recommended_category: Any,
    recommended_subcategory: Any,
    recommended_model: Any,
    recommended_makemodel: Any = None,
    llm_category_match: str | None = None,
    llm_subcategory_match: str | None = None,
    llm_makemodel_match: str | None = None,
    llm_model_match: str | None = None,
    llm_category_percent: Any = None,
    llm_subcategory_percent: Any = None,
    llm_makemodel_percent: Any = None,
    llm_model_percent: Any = None,
) -> dict[str, str | int | None]:
    """Reconcile LLM classification fields with deterministic string comparison."""
    cat_match, cat_pct = evaluate_classification_field(
        user_category,
        recommended_category,
        field="category",
        llm_match=llm_category_match,
        llm_percent=llm_category_percent,
    )
    if cat_match is not None and category_trusted():
        # The customer's category is always right (docs 6.6): wherever this comparison states it, it
        # is matched in full, whatever the row or the selection call said. Where no comparison ran —
        # a blank on either side — it stays null, as the response contract has it.
        cat_match, cat_pct = "matched", TRUSTED_CATEGORY_PERCENT
    sub_match, sub_pct = evaluate_classification_field(
        user_subcategory,
        recommended_subcategory,
        field="subcategory",
        llm_match=llm_subcategory_match,
        llm_percent=llm_subcategory_percent,
    )

    if normalize_classification_value(recommended_makemodel):
        makemodel_match, makemodel_pct = evaluate_classification_field(
            user_makemodel,
            recommended_makemodel,
            field="makemodel",
            llm_match=llm_makemodel_match,
            llm_percent=llm_makemodel_percent,
        )
    else:
        makemodel_match, makemodel_pct = None, None

    if normalize_classification_value(recommended_model):
        model_match, model_pct = evaluate_classification_field(
            user_model,
            recommended_model,
            field="model",
            llm_match=llm_model_match,
            llm_percent=llm_model_percent,
        )
    else:
        model_match, model_pct = None, None

    return {
        "categorymatch": cat_match,
        "categorymatchpercent": cat_pct,
        "subcategorymatch": sub_match,
        "subcategorymatchpercent": sub_pct,
        "makemodelmatch": makemodel_match,
        "makemodelmatchpercent": makemodel_pct,
        "modelmatch": model_match,
        "modelmatchpercent": model_pct,
    }


_BRAND_ALIASES: dict[str, frozenset[str]] = {
    "acer": frozenset({"acer"}),
    "apple": frozenset({"apple", "macbook", "imac", "ipad", "iphone"}),
    "asus": frozenset({"asus"}),
    "benq": frozenset({"benq"}),
    "boat": frozenset({"boat"}),       # boAt (Imagine Marketing) audio and power banks; AssetId 4411007
    "bosch": frozenset({"bosch"}),
    "canon": frozenset({"canon"}),
    "dell": frozenset({"dell", "alienware"}),
    "epson": frozenset({"epson"}),
    "hikvision": frozenset({"hikvision"}),
    "hisense": frozenset({"hisense"}),
    "hp": frozenset({"hp", "hewlett", "packard", "hewlett packard"}),
    "lenovo": frozenset({"lenovo", "thinkpad", "ideapad", "legion"}),
    "lg": frozenset({"lg"}),
    "microsoft": frozenset({"microsoft", "surface"}),
    "panasonic": frozenset({"panasonic"}),
    "philips": frozenset({"philips"}),
    "samsung": frozenset({"samsung"}),
    "sony": frozenset({"sony"}),
    "thomson": frozenset({"thomson"}),
    "toshiba": frozenset({"toshiba"}),
    "viewsonic": frozenset({"viewsonic"}),
    "zebra": frozenset({"zebra"}),
    "bajaj": frozenset({"bajaj"}),
    "bluestar": frozenset({"blue star", "bluestar"}),
    "carrier": frozenset({"carrier"}),
    "crompton": frozenset({"crompton"}),
    "croma": frozenset({"croma"}),
    "daikin": frozenset({"daikin"}),
    "godrej": frozenset({"godrej"}),
    "haier": frozenset({"haier"}),
    "havells": frozenset({"havells"}),
    "hitachi": frozenset({"hitachi"}),
    "ifb": frozenset({"ifb"}),
    "kelvinator": frozenset({"kelvinator"}),
    # "Lloyds" is how registers write it ("AC-1 TON Lloyds A0768301", Pidilite 2026-09-22).
    "lloyd": frozenset({"lloyd", "lloyds"}),
    "orient": frozenset({"orient"}),
    "symphony": frozenset({"symphony"}),
    "usha": frozenset({"usha"}),
    "voltas": frozenset({"voltas"}),
    "whirlpool": frozenset({"whirlpool"}),
    "oneplus": frozenset({"oneplus", "one plus", "nord"}),
    "xiaomi": frozenset({"xiaomi", "redmi", "poco"}),
    "realme": frozenset({"realme"}),
    "vivo": frozenset({"vivo", "iqoo"}),
    "oppo": frozenset({"oppo"}),
    "motorola": frozenset({"motorola", "moto"}),
    "nokia": frozenset({"nokia"}),
    "google": frozenset({"google", "pixel"}),
    "huawei": frozenset({"huawei", "honor"}),
    # IP desk phones registered as "Panasonic Handset" were Grandstream sets (customer 1063, 2026-07).
    "grandstream": frozenset({"grandstream"}),
}


# Aliases that are also everyday words. In the vision model's prose they are almost always
# the word, not the brand: "mounted on a ceiling surface", "beside a cable carrier tray",
# "a visible pixel grid", "a painted zebra crossing". AssetId 1692861 and five more Hikvision
# dome cameras (customer 1201, 2026-09-17) were rejected as "Hikvision vs Microsoft" because
# every description said the camera sat on a "ceiling surface". In PROSE these count only when
# written as a brand — capitalised — which is how the model writes a maker's name.
_ORDINARY_WORD_ALIASES = frozenset({
    "surface", "carrier", "pixel", "zebra", "apple", "canon", "orient", "symphony",
    "honor", "legion", "nord", "moto", "packard", "boat",
})

# "HP" is also horsepower. "1 HP", "0.5 HP" or "5-HP" after a number is a motor rating, never
# the brand: a "5 HP Pump" record photographed as a Crompton pump would otherwise read as an
# HP-vs-Crompton brand conflict.
_HP_AS_RATING = re.compile(r"(?<!\d\s)(?<!\d-)\bhp\b")


def extract_brand_keys(text: str, *, prose: bool = False) -> set[str]:
    """Return canonical brand keys mentioned in classification or vision text.

    `prose=True` is for the vision model's free-text description, where everyday words that
    double as brand aliases (see _ORDINARY_WORD_ALIASES) count only when capitalised. Short
    declared fields — asset name, make/model — keep matching them in any case, because
    registers are routinely typed in capitals ("CARRIER AC").
    """
    norm = normalize_classification_value(text)
    if not norm:
        return set()
    raw = str(text or "")
    found: set[str] = set()
    for brand_key, aliases in _BRAND_ALIASES.items():
        for alias in aliases:
            if alias == "hp":
                if _HP_AS_RATING.search(norm):
                    found.add(brand_key)
                    break
                continue
            pattern = r"\b" + re.escape(alias).replace(r"\ ", r"\s+") + r"\b"
            if not re.search(pattern, norm):
                continue
            if prose and alias in _ORDINARY_WORD_ALIASES:
                written = r"\b(?:" + re.escape(alias.capitalize()) + "|" + re.escape(alias.upper()) + r")\b"
                if not re.search(written, raw):
                    continue
            found.add(brand_key)
            break
    return found


def strip_brand_words(text: str, keys=None) -> str:
    """The label with listed brand words removed — every brand, or only those in `keys`.

    "White Haier split-type air conditioning unit" -> "White split-type air conditioning unit";
    "Apple iPad" -> "". Used to tell the full-size brand read WHICH object to inspect without
    telling it what brand to expect: the label is the system's own description, which may carry
    exactly the misread brand being checked (docs 6.3). "HP" after a number is a motor rating
    ("5 HP pump"), never the brand, and stays.
    """
    out = str(text or "")
    for brand_key, aliases in _BRAND_ALIASES.items():
        if keys is not None and brand_key not in keys:
            continue
        for alias in aliases:
            pattern = r"\b" + re.escape(alias).replace(r"\ ", r"\s+") + r"\b"
            if alias == "hp":
                pattern = r"(?<!\d\s)(?<!\d-)" + pattern
            out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


def _user_classification_text(req_body: dict) -> str:
    """The classification fields a rule may judge. The category is left out when it is trusted
    (docs 6.6): a record that filled in only its category has nothing left to judge."""
    keys = ("categoryname", "subcategoryname", "makemodel", "makemodelname")
    if category_trusted():
        keys = keys[1:]
    return _join_texts(*(req_body.get(key) for key in keys))


def _vision_detection_text(output: dict, gemini_output: dict | None) -> str:
    parts = [
        str(output.get("detectedAsset") or ""),
        str(output.get("imageAnalysis") or ""),
    ]
    if gemini_output:
        reasoning = gemini_output.get("inspection_reasoning")
        if isinstance(reasoning, dict):
            for candidate in reasoning.get("identity_candidates") or []:
                if isinstance(candidate, dict):
                    parts.append(str(candidate.get("label") or ""))
    return _join_texts(*parts)


# A desk or workstation is photographed with what stands on it, and described by that. AssetId
# 2663497, registered Furniture / SS Table, was described as "Stainless steel computer workstation
# with a keyboard, monitor, and a small computer tower": the description names a monitor and never a
# table, so the classification failed as table-vs-monitor while master data matched all three fields.
# "Workstation" alone can also be a computer (an HP Z workstation), so it counts as the furniture
# only when the description shows it IS furniture: devices on it, or a furniture material or build.
_WORKSTATION_RE = re.compile(r"\bwork\s?stations?\b")
# Devices that stand ON a workstation. Of the families added on 2026-11-02 (docs 6.1) the desk-top ones are in; the
# ones mounted on walls and ceilings or standing on the floor (camera, projector, AC, network gear, power backup,
# stabilizer, refrigerator, water dispenser, server, microwave) are not. The families of before only ever read the
# first six, so the reading of before is unchanged.
_DEVICES_ON_FURNITURE = frozenset({
    "monitor", "laptop", "printer", "phone", "mouse", "tv",
    "keyboard", "tablet", "scanner", "charger", "power_bank", "speaker", "docking_station",
})
_FURNITURE_EVIDENCE_RE = re.compile(
    r"\b(?:keyboard|cpu|computer tower|desktop tower|stainless|steel|ss|wooden|wood|laminate"
    r"|modular|metal|cubicle|partition|drawers?|pedestal|table ?top|desk ?top surface)\b"
)


def _with_furniture_under_devices(detected_text: str, req_body: dict, detected_asset: str) -> str:
    """The vision text, plus the table it describes as a workstation carrying devices.

    The TAGGED object must be the workstation — "workstation" in the detected-asset label. A
    workstation merely mentioned in the description is not enough: "Packaging Table" records whose
    tag sat on a tape dispenser, a step stool or a desktop tower beside a workstation (customer 1063,
    2026-07) rightly failed, and still do.

    Only for a table-family registration, and only for the downgrade rules
    (enforce_user_classification_vs_vision, enforce_makemodel_vs_subcategory_contradiction):
    the devices standing on the furniture are not the product type photographed. The upgrade
    guard in reconcile_subcatmodel_with_vision is deliberately not given this — a model's own N
    is never turned into a Y by it.
    """
    if "table" not in _user_claim_product_types(req_body or {}):
        return detected_text
    if not _WORKSTATION_RE.search(normalize_classification_value(detected_asset)):
        return detected_text
    norm = normalize_classification_value(detected_text)
    carries_devices = bool(_detect_product_categories(detected_text) & _DEVICES_ON_FURNITURE)
    if not (carries_devices or _FURNITURE_EVIDENCE_RE.search(norm)):
        return detected_text
    return f"{detected_text} (workstation table)"


_SENTENCE_BREAK = re.compile(r"(?<=[.;!?])\s+")

# Words in a detected-asset label that say nothing about WHICH object it is, so a sentence
# sharing only these with the label is not taken to be about the asset.
_NON_DISTINCTIVE_LABEL_WORDS = frozenset({
    "unit", "units", "system", "device", "equipment", "machine", "assembly", "object", "item",
    "asset", "setup", "type", "with", "white", "black", "grey", "gray", "silver", "blue", "green",
    "yellow", "small", "large", "mounted", "standard", "general", "industrial", "electric",
    "electrical", "electronic", "digital", "portable", "metal", "plastic", "steel", "wooden",
    "office", "manual", "primary", "main", "single", "double", "multi", "heavy", "light", "model",
    "brand", "branded", "visible", "used",
})


def suggestion_names_another_brand(output: dict, gemini_output: dict | None = None) -> bool:
    """True when the make/model master data suggests names a known brand and the photo shows another one on the asset.

    The owner, 2 Nov 2026: never suggest a make/model whose brand contradicts a brand clearly visible in the photo.
    AssetId 1142 (customer 1323): an HP laptop registered Desktop / Apple Mac16 was offered "Dell", because that
    customer's master data holds Apple and Dell laptops only. Only the asset's own brands count, as for the brand rule
    (_asset_photo_brand_keys): not a background object's, not the asset tag's owner. A suggestion that names no brand,
    or a photo that shows none, is left alone.
    """
    suggested = " ".join(str(output.get(key) or "") for key in ("recommendedmakemodel", "recommendedmodel")).strip()
    own = _suggestion_brand_keys(suggested)
    if not own:
        return False
    shown = _asset_photo_brand_keys(output, gemini_output)
    return bool(shown) and not (own & shown)


# An everyday word that doubles as a brand alias ("surface", "carrier", "legion") inside a master-data name, after
# another word, describes the thing rather than naming its maker: "Wooden Surface" (customer 1158) is no Microsoft,
# "Cable carrier tray" no Carrier. At the start of the name it is still the brand: "Surface Pro 7", "Carrier 1.5 Ton".
_EVERYDAY_WORD_INSIDE_RE = re.compile(
    r"(?<=\S)\s+(?:" + "|".join(sorted(_ORDINARY_WORD_ALIASES)) + r")\b", re.IGNORECASE
)


def _suggestion_brand_keys(name: str) -> set[str]:
    """The brands a master-data make/model names, leaving out an everyday word inside the name (review, 2 Nov 2026)."""
    if not extract_brand_keys(_EVERYDAY_WORD_INSIDE_RE.sub(" ", str(name or ""))):
        return set()
    return extract_brand_keys(str(name or ""))


def row_brand_not_on_asset(output: dict, makemodel: str, gemini_output: dict | None = None) -> bool:
    """True when a master-data make/model names a brand and the asset in the photo does not show that brand.

    For a blank make/model failed on a certain master-data row (6.5): a brand counts only when it is on the asset itself,
    read as the brand rule reads it, never from another object the description mentions. An HP laptop described with
    "A Dell monitor stands behind it." must not fail with DELL as the correction (review, 2 Nov 2026).
    """
    own = _suggestion_brand_keys(makemodel)
    return bool(own) and not (own & _asset_photo_brand_keys(output, gemini_output))


def _asset_photo_brand_keys(output: dict, gemini_output: dict | None = None) -> set[str]:
    """Brands the photo shows ON THE ASSET — not every brand the description mentions.

    The description names whatever else is in frame. AssetId 1692990, a Hikvision bullet camera:
    "Two white bullet security cameras are mounted under a concrete overhang. A Panasonic street
    lamp is visible above and to the left." — and the record failed as "the asset name says
    Hikvision, but the photo shows Panasonic branding". The 2026-07 logs hold the same mistake
    four more times: a Canon toner box behind a phone, a Voltas pallet jack behind the tagged one.

    Counted: brands in the asset's own labels (detectedAsset, the identity candidates), and brands
    in a description sentence that is about the asset — it names the same product type, or shares
    a distinctive word with the label ("White Haier split-type air conditioning unit mounted…").
    With no detected label to anchor on, the whole description counts, as before.
    """
    detected = str(output.get("detectedAsset") or "")
    labels = [detected]
    if gemini_output:
        reasoning = gemini_output.get("inspection_reasoning")
        if isinstance(reasoning, dict):
            labels += [str(c.get("label") or "") for c in reasoning.get("identity_candidates") or []
                       if isinstance(c, dict)]
    analysis = str(output.get("imageAnalysis") or "")
    label_text = _join_texts(*labels)
    if not label_text:
        return extract_brand_keys(analysis, prose=True)

    brands = extract_brand_keys(label_text, prose=True)
    categories = _detect_product_categories(label_text)
    words = {w for w in re.findall(r"[a-z]+", label_text.lower())
             if len(w) >= 4 and w not in _NON_DISTINCTIVE_LABEL_WORDS}
    if product_types_v2():
        # A device word in front of the label's own noun names another device (6.1): "laptop" in "HP laptop power
        # adapter" does not make "A Lenovo ThinkPad sits on the desk" a sentence about the adapter.
        words -= _v2_modifier_words(label_text)
    for sentence in _SENTENCE_BREAK.split(analysis):
        found = extract_brand_keys(sentence, prose=True)
        if not found:
            continue
        lowered = sentence.lower()
        about_asset = bool(categories & _detect_product_categories(sentence)) or any(
            re.search(r"\b" + re.escape(w) + r"(?:s|es)?\b", lowered) for w in words
        )
        if about_asset:
            # The client's own name on its asset tag ("a `Cipla` asset tag sticker on the lid")
            # is not the device's brand: a name that appears only on the tag is left out.
            brands |= found - (_tag_logo_brands(sentence) - brands)
    return brands


# "`Cipla` asset tag sticker", "Sun Pharma sticker", "Bosch asset label": the word before a
# tag/sticker/label is whoever owns the tag, not whoever made the device.
_TAG_LOGO_RE = re.compile(
    r"[`'\"]?([A-Za-z][\w-]*)[`'\"]?\s+(?:asset\s+|branding\s+)?(?:tags?|stickers?|labels?)\b"
    r"(?!\s+(?:printers?|makers?|machines?|rolls?|dispensers?))",   # "Zebra label printer" is the device
    re.IGNORECASE,
)


def _tag_logo_brands(sentence: str) -> set[str]:
    """Brand keys that this sentence mentions only as the name on a tag or sticker."""
    on_tag: set[str] = set()
    for m in _TAG_LOGO_RE.finditer(sentence):
        on_tag |= extract_brand_keys(m.group(1), prose=True)
    if not on_tag:
        return set()
    rest = _TAG_LOGO_RE.sub(" ", sentence)
    return on_tag - extract_brand_keys(rest, prose=True)


def _user_primary_product_categories(req_body: dict) -> set[str]:
    """Product types implied by user categoryname (preferred) or full classification text.

    A trusted category (docs 6.6) is never judged against the photo, so then only subcategory and
    make/model speak.
    """
    category = normalize_classification_value(req_body.get("categoryname"))
    if category and not category_trusted():
        cats = _detect_product_categories(category)
        if cats:
            return cats
    return _detect_product_categories(_user_classification_text(req_body))


def _user_field_product_types(req_body: dict) -> dict[str, set[str]]:
    """Known product types per classification field (Model excluded — often stale)."""
    if product_types_v2():
        return _v2_user_field_readings(req_body)[0]
    mm = str(req_body.get("makemodel") or req_body.get("makemodelname") or "").strip()
    fields = {
        "category": _detect_product_categories(str(req_body.get("categoryname") or "")),
        "subcategory": _detect_product_categories(str(req_body.get("subcategoryname") or "")),
        "makemodel": _detect_product_categories(mm),
    }
    # A laptop product line in one field is read against the whole registration: "Desktop"
    # in the subcategory and "Dell Vostro 3470" in make/model describe one desktop, but each
    # field alone reads the Vostro as a laptop. A trusted category (docs 6.6) counts only as context
    # that takes a laptop reading away: its laptop words ("Laptops") never keep a laptop claim that
    # the judged fields would drop, or the category would decide an N again (review, 1 Nov 2026).
    whole = normalize_classification_value(
        f"{req_body.get('categoryname') or ''} {req_body.get('subcategoryname') or ''} {mm}"
    )
    judged = (
        normalize_classification_value(f"{req_body.get('subcategoryname') or ''} {mm}")
        if category_trusted() else whole
    )
    if _NOT_A_LAPTOP_CONTEXT.search(whole) and not _LAPTOP_ONLY_WORDS.search(judged):
        for types in fields.values():
            types.discard("laptop")
    return fields


def _known_types_conflict(a: set[str], b: set[str]) -> bool:
    """True when both sides have known types and they are disjoint."""
    return bool(a and b and not (a & b))


def _user_claim_product_types(req_body: dict) -> set[str]:
    """Union of known types from category + subcategory + makemodel (Gate B claim set).

    A trusted category (docs 6.6) claims nothing: it is never judged, so a type it names can
    neither fail the record nor stand in for the subcategory's or make/model's.
    """
    fields = _user_field_product_types(req_body)
    claim = fields["subcategory"] | fields["makemodel"]
    return claim if category_trusted() else fields["category"] | claim


def _find_internal_type_conflict(
    req_body: dict,
) -> tuple[str, str, str, str] | None:
    """
    Return (field_a, value_a, field_b, value_b) when two claim fields conflict by type.
    Model is excluded (stale). Brand-only disagreement is ignored. A trusted category (docs 6.6)
    conflicts with nothing: only subcategory against make/model is compared.

    Read by the head noun (6.1), an accessory filed under the device it serves is no contradiction: "Laptop" with
    "HP laptop charger 65W", "Battery" with "Battery charger", "Table" with "Table chair sofa set".
    """
    v2 = product_types_v2()
    if v2:
        fields, served = _v2_user_field_readings(req_body)
    else:
        fields, served = _user_field_product_types(req_body), None
    values = {
        "category": str(req_body.get("categoryname") or "").strip(),
        "subcategory": str(req_body.get("subcategoryname") or "").strip(),
        "makemodel": str(
            req_body.get("makemodel") or req_body.get("makemodelname") or ""
        ).strip(),
    }
    order = ("category", "subcategory", "makemodel")
    if category_trusted():
        order = order[1:]
    for i, left in enumerate(order):
        for right in order[i + 1 :]:
            if not _known_types_conflict(fields[left], fields[right]):
                continue
            if v2 and not _v2_fields_conflict(fields[left], served[left], fields[right], served[right]):
                continue
            return left, values[left], right, values[right]
    return None


def _internal_classification_types_conflict(req_body: dict) -> bool:
    return _find_internal_type_conflict(req_body) is not None


def _photo_frame_text(detected_text: str, output: dict | None, gemini_output: dict | None = None) -> str:
    """Gate B's photo text: the detection text and every asset vision listed in the frame."""
    if output is not None:
        listed = _visible_asset_text(output, gemini_output)
        if listed:
            return _join_texts(detected_text, listed)
    return detected_text


def _photo_names_other_family(
    families, detected_text: str, output: dict | None = None, gemini_output: dict | None = None
) -> bool:
    """The photo text names a product family that rules these out (6.1, families_ruled_out): a positive conflict, not
    a mere absence. A family added on 2026-11-02 is ruled out only by the asset's own label."""
    frame_text = _photo_frame_text(detected_text, output, gemini_output)
    label = _asset_label_text(output, gemini_output) if output is not None else frame_text
    return families_ruled_out(
        families, _detect_product_categories(frame_text), _detect_product_categories(label)
    )


def _known_user_types_unsupported_by_vision(
    req_body: dict,
    detected_text: str,
    output: dict | None = None,
    gemini_output: dict | None = None,
) -> bool:
    """
    True when user claims a known product type that vision does not support.
    Includes the case where vision has no known types (empty).

    Every asset vision listed in the frame counts, not only the one it called primary —
    a tagged desk is still supported when vision named the monitor standing on it.

    With PRODUCT_TYPES_V2_ENABLED the new reading adds no failure on absence alone (owner's hand-check, 3 Nov 2026;
    docs 6.1): it stands when the photo names a family that rules the claim out, or when the reading of before fails
    too. Otherwise the reading of before decides: "FIRE EXTINGUISHERS5000", which only the new reading reads, does
    not fail on a "Super Ball" photo that names no product type.
    """
    unsupported = _gate_b_unsupported(req_body, detected_text, output, gemini_output)
    if not unsupported or not product_types_v2():
        return unsupported
    if _photo_names_other_family(_user_claim_product_types(req_body), detected_text, output, gemini_output):
        return True
    with _reading_of_before():
        return _gate_b_unsupported(req_body, detected_text, output, gemini_output)


def _gate_b_unsupported(
    req_body: dict,
    detected_text: str,
    output: dict | None = None,
    gemini_output: dict | None = None,
) -> bool:
    """Gate B as read by the current product types (_known_user_types_unsupported_by_vision)."""
    user_types = _user_claim_product_types(req_body)
    if not user_types or not detected_text:
        return False
    frame_text = _photo_frame_text(detected_text, output, gemini_output)
    detected_cats = _detect_product_categories(frame_text)
    if user_types & detected_cats or (detected_cats and not families_disjoint(user_types, detected_cats)):
        return False
    if product_types_v2() and not user_types & _LEGACY_FAMILIES:
        label = _asset_label_text(output, gemini_output) if output is not None else frame_text
        if not families_ruled_out(user_types, detected_cats, _detect_product_categories(label)):
            # A family added on 2026-11-02 is unsupported only when the asset's own label names another product type
            # (6.1): a photo that names none ("Enclosure", "Industrial unit", "LG appliance"), or names one only in
            # its description ("a table and chairs are partially visible"), says nothing against a battery, a water
            # cooler or a tablet. The families of before keep the rule: a "Chair" on a pump photo still fails.
            return False
    # The registered word, inflected, is still the registered thing: a "Patient Monitor" record
    # whose photo was described as a "patient monitoring system" is not unsupported. Only the
    # word the customer actually registered counts, and only with a grammatical ending, so a
    # frame naming some other object still fails this gate exactly as before.
    return not _registered_word_inflected_in_frame(req_body, frame_text)


_INFLECTION = r"(?:s|es|ing|ed)?"


def _registered_word_inflected_in_frame(req_body: dict, frame_text: str) -> bool:
    frame = normalize_classification_value(frame_text)
    if not frame:
        return False
    # Only the fields whose claim Gate B judges: a trusted category's word (docs 6.6) must not
    # vouch for a subcategory or make/model the photo does not support.
    fields = ("categoryname", "subcategoryname", "makemodel", "makemodelname")
    if category_trusted():
        fields = fields[1:]
    if product_types_v2():
        # The same head-noun reading on both sides (6.1): "Laptop keyboard" registers a keyboard, so "laptops" in
        # the frame vouches for nothing, and "printer" in "printer table" is no printer in the frame either.
        return _v2_registered_word_in_frame([req_body.get(field) for field in fields], frame_text)
    for field in fields:
        value = normalize_classification_value(req_body.get(field))
        if not value:
            continue
        for pattern in _PRODUCT_CATEGORY_PATTERNS.values():
            for match in pattern.finditer(value):
                word = match.group(0).split()[-1]
                word = re.sub(r"s$", "", word) if len(word) > 3 else word
                if re.search(r"\b" + re.escape(word) + _INFLECTION + r"\b", frame):
                    return True
    return False


def makemodel_type_unsupported_by_vision(
    output: dict,
    req_body: dict | None = None,
    gemini_output: dict | None = None,
) -> bool:
    """True when the make/model names a product type the photo does not show (docs 6).

    Then a failed make/model rests on that type, which the photo settles, and a missing logo says
    nothing about it. Case 10 of the 1 Nov test matrix (customer 1063): a 4 Tier Sorting Rack
    registered FF / CHAIRS / "Black Chair" carries no logo, so the no-visible-brand withhold reported
    make/model "matched" 85 beside a rack.

    The photo is read as Gate B reads it (enforce_user_classification_vs_vision): detectedAsset and
    imageAnalysis with the phase-1 identity candidates, every listed visible asset, a workstation
    carrying devices counted as the table it is, and the registered word counted when inflected.
    Only the make/model is the claim — the subcategory has its own status — but its type is read
    within the whole registration, so a laptop line under a desktop subcategory claims no laptop (6.1).

    Not when the chosen master-data row (recommended*) names that type too: the selection read the
    photo description and still picked a row of it, so the description's words missed the type.
    AssetId 2302965: "IJP Printer" on a "laser coding machine", row IJP Printer / Markem Image.
    A trusted category (6.6) types no row, as in the withdrawal of 6.2.

    With PRODUCT_TYPES_V2_ENABLED, as Gate B: the new reading adds no failure on absence alone (6.1). It stands when the
    photo names a family that rules the make/model's type out, or when the reading of before fails it too.
    """
    unsupported = _makemodel_type_unsupported(output, req_body, gemini_output)
    if not unsupported or not product_types_v2():
        return unsupported
    claimed, detected_text = _makemodel_claim(output, req_body, gemini_output)
    if claimed and _photo_names_other_family(claimed, detected_text, output, gemini_output):
        return True
    with _reading_of_before():
        return _makemodel_type_unsupported(output, req_body, gemini_output)


def _makemodel_claim(output: dict, req_body: dict | None, gemini_output: dict | None = None):
    """(the make/model's product families read in its registration, the photo text Gate B reads for it)."""
    body = req_body or {}
    makemodel = str(
        body.get("makemodel") or body.get("makemodelname")
        or output.get("makemodel") or output.get("makemodelname") or ""
    ).strip()
    if not makemodel:
        return set(), ""
    registration = {
        "categoryname": body.get("categoryname") or output.get("categoryname") or "",
        "subcategoryname": body.get("subcategoryname") or output.get("subcategoryname") or "",
        "makemodel": makemodel,
        "assetclassname": body.get("assetclassname") or output.get("assetclassname") or "",
    }
    claim = {"makemodel": makemodel,
             "assetclassname": _join_texts(registration["categoryname"], registration["assetclassname"])}
    detected_text = _with_furniture_under_devices(
        _vision_detection_text(output, gemini_output), claim, output.get("detectedAsset") or ""
    )
    return _user_field_product_types(registration)["makemodel"], detected_text


def _makemodel_type_unsupported(
    output: dict,
    req_body: dict | None = None,
    gemini_output: dict | None = None,
) -> bool:
    """makemodel_type_unsupported_by_vision as read by the current product types."""
    body = req_body or {}
    makemodel = str(
        body.get("makemodel") or body.get("makemodelname")
        or output.get("makemodel") or output.get("makemodelname") or ""
    ).strip()
    if not makemodel:
        return False
    registration = {
        "categoryname": body.get("categoryname") or output.get("categoryname") or "",
        "subcategoryname": body.get("subcategoryname") or output.get("subcategoryname") or "",
        "makemodel": makemodel,
        "assetclassname": body.get("assetclassname") or output.get("assetclassname") or "",
    }
    claimed = _user_field_product_types(registration)["makemodel"]
    if not claimed:
        return False    # "Markem Image", "Godrej Interio 4400": only a logo can settle those
    # The record's class rides along only to tell an industrial record (6.1, 5.6); it is never a claimed field.
    claim = {"makemodel": makemodel,
             "assetclassname": _join_texts(registration["categoryname"], registration["assetclassname"])}
    detected_text = _with_furniture_under_devices(
        _vision_detection_text(output, gemini_output), claim, output.get("detectedAsset") or ""
    )
    if not _known_user_types_unsupported_by_vision(claim, detected_text, output, gemini_output):
        return False
    # A passed name check whose asset name names that type is support, as 6.5 counts it: "Revolving Chair" on a
    # photo described as a "black mesh seat ... with castors" names no chair, and the name check settled what it
    # is (review, 2 Nov 2026). Case 10's "4 Tier Sorting Rack" names no chair, so the rack still fails.
    named = _detect_product_categories(str(body.get("assetname") or output.get("assetname") or ""))
    if output.get("namedescriptionmatch") == "Y" and named and not families_disjoint(claimed, named):
        return False
    keys = ("recommendedsubcategory", "recommendedmakemodel", "recommendedmodel")
    if not category_trusted():
        keys = ("recommendedcategory", *keys)
    row = _detect_product_categories(_join_texts(*(output.get(key) for key in keys)))
    return not (claimed & row) and not (row and not families_disjoint(claimed, row))


def recommended_makemodel_unsupported_by_vision(
    output: dict, gemini_output: dict | None = None, *, req_body: dict | None = None
) -> bool:
    """True when the make/model master data suggests names a product type the photo does not show (docs 6).

    Once a missing logo no longer clears a wrong product type, a record whose subcategory matched fails on its
    make/model alone, and the make/model suggested beside it must fit the photo too, or none is shown (owner,
    2 Nov 2026: a sensible suggestion or none). FURNITURE / "Black Chair" on a rack photo was offered FURNITURE /
    "Wooden table": the photo shows no table either. Read as the make/model itself is read.
    """
    row = str(output.get("recommendedmakemodel") or "").strip()
    if not row:
        return False
    record = req_body or output
    # The record's class rides along only to tell an industrial record (6.1, 5.6); it is never a claimed field.
    claim = {"makemodel": row, "assetclassname": _join_texts(record.get("categoryname"), record.get("assetclassname"))}
    detected_text = _with_furniture_under_devices(
        _vision_detection_text(output, gemini_output), claim, output.get("detectedAsset") or ""
    )
    return bool(_known_user_types_unsupported_by_vision(claim, detected_text, output, gemini_output))


def filter_pinecone_candidates_by_vision(candidates: list, detected_text: str, label_text: str | None = None) -> list:
    """Drop candidate rows whose product type clearly conflicts with vision.

    A row's type is read from its subcategory, make/model and model when the category is trusted
    (docs 6.6): the lookup is scoped to the customer's own category, so the category every row
    shares says nothing about which row fits the photo — and is never judged against it.
    """
    if not candidates or not detected_text:
        return candidates
    keys = ("category", "subcategory", "makemodel", "model")
    if category_trusted():
        keys = keys[1:]
    v2 = product_types_v2() and label_text is not None
    kept: list = []
    for candidate in candidates:
        row_text = _join_texts(*(candidate.get(key) for key in keys))
        if _product_category_conflict(detected_text, row_text):
            # A row of a family added on 2026-11-02 is ruled out only by the asset's own label (6.1): a chair the
            # description mentions in the background does not drop the AC rows offered for an air cooler.
            row_cats = _detect_product_categories(row_text) if v2 else set()
            if not (row_cats and not row_cats & _LEGACY_FAMILIES
                    and not _product_category_conflict(label_text, row_text)):
                continue
        kept.append(candidate)
    return kept if kept else candidates


def enforce_user_classification_vs_vision(
    output: dict,
    req_body: dict,
    gemini_output: dict | None = None,
) -> None:
    """
    Downgrade subcatmodelmatch on product-type conflicts.

    Gate A: internal known-type contradictions across category/subcategory/makemodel.
    Gate B: user claims a known type that vision does not support (incl. untyped vision).
    Brand mismatch alone must not fail classification.
    A trusted category (docs 6.6) takes part in neither gate: only subcategory and make/model
    are judged, against each other and against the photo.
    """
    if output.get("error"):
        return

    user_text = _user_classification_text(req_body)
    detected_text = _with_furniture_under_devices(
        _vision_detection_text(output, gemini_output), req_body, output.get("detectedAsset") or ""
    )
    if not user_text or not detected_text:
        return

    # Gate A — internal field type consistency (before vision soft-match)
    conflict = _find_internal_type_conflict(req_body)
    if conflict:
        left_field, left_val, right_field, right_val = conflict
        left_label = " ".join((left_val or left_field).split()[:3])
        right_label = " ".join((right_val or right_field).split()[:3])
        _downgrade_subcatmodel_with_note(
            output,
            "",
            20,
            reasoning=(
                f"N: {left_field} {left_label} conflicts with {right_field} {right_label}"
                f" — product type mismatch."
            ),
        )
        return

    detected_cats = _detect_product_categories(detected_text)

    user_sub = str(req_body.get("subcategoryname") or "").strip()
    if user_sub and _subcategory_product_type_conflicts_with_vision(
        user_sub, detected_text, req_body, _asset_label_text(output, gemini_output)
    ):
        _downgrade_subcatmodel_with_note(
            output,
            "",
            25,
            reasoning=_build_subcategory_vision_conflict_reasoning(
                user_sub, output, detected_text
            ),
        )
        return

    # Gate B — known user type unsupported by vision (empty vision cats count as unsupported)
    if _known_user_types_unsupported_by_vision(
        req_body, detected_text, output, gemini_output
    ):
        claim = _user_claim_product_types(req_body)
        claim_label = _family_words(claim)
        asset_label = _vision_asset_label(output, detected_text)
        _downgrade_subcatmodel_with_note(
            output,
            "",
            20,
            reasoning=(
                f"N: classification {claim_label} conflicts with {asset_label}"
                f" — product type mismatch."
            ),
        )
        return

    user_primary = _user_primary_product_categories(req_body)
    if user_primary and detected_cats and not (user_primary & detected_cats):
        # The families of 2026-11-02 carry keys a reading cannot find again in their own text ("power_bank"), so
        # the two sets are compared as sets; the reading of before compares them as it always did.
        conflict = (
            _families_conflict(user_primary, detected_cats) if product_types_v2()
            else _product_category_conflict(" ".join(user_primary), " ".join(detected_cats))
        )
        if conflict:
            _downgrade_subcatmodel_with_note(output, "Server: product type conflict.", 25)
            return


def _downgrade_subcatmodel_with_note(
    output: dict,
    note: str,
    percent: int,
    *,
    reasoning: str | None = None,
) -> None:
    """Downgrade subcatmodelmatch and keep reasoning verdict consistent with N."""
    output["subcatmodelmatch"] = "N"
    output["subcatmodelmatchpercent"] = min(int(output.get("subcatmodelmatchpercent") or percent), percent)
    if reasoning:
        output["subcatmodelreasoning"] = reasoning
        return
    base = str(output.get("subcatmodelreasoning") or "").strip()
    if base.upper().startswith("Y:"):
        base = "N:" + base[2:].lstrip()
    output["subcatmodelreasoning"] = f"{base} {note}".strip() if base else note


def _vision_asset_label(output: dict, detected_text: str, *, max_words: int = 5) -> str:
    """Short visible-asset label for reasoning (keeps messages within word limits)."""
    asset = str(output.get("detectedAsset") or "").strip()
    if asset:
        return " ".join(asset.split()[:max_words])
    categories = _detect_product_categories(detected_text)
    if categories:
        return _family_words(categories)
    return "visible asset"


def _short_field_label(prefix: str, value: str, *, max_words: int = 5) -> str:
    text = " ".join(str(value or "").split()[:max_words]).strip()
    return f"{prefix} {text}" if text else ""


def _build_subcatmodel_pass_reasoning(
    req_body: dict,
    output: dict,
    *,
    max_words: int = 25,
) -> str:
    """Concise Y verdict naming actual user classification fields and visible asset."""
    labels: list[str] = []
    sub = str(req_body.get("subcategoryname") or "").strip()
    mm = str(req_body.get("makemodel") or req_body.get("makemodelname") or "").strip()
    model = str(req_body.get("model") or "").strip()
    if sub:
        labels.append(_short_field_label("subcategory", sub))
    if mm:
        labels.append(_short_field_label("makemodel", mm))
    if model:
        labels.append(_short_field_label("model", model))

    detected_text = _vision_detection_text(output, None)
    asset_label = _vision_asset_label(output, detected_text, max_words=6)

    if labels:
        message = f"Y: {', '.join(labels)} match {asset_label} visible in image."
    else:
        message = f"Y: user classification matches {asset_label} visible in image."

    words = message.split()
    if len(words) <= max_words:
        return message

    if sub:
        message = f"Y: subcategory {sub}, makemodel {mm} match {asset_label} in image."
        words = message.split()
        if len(words) <= max_words:
            return message

    return " ".join(message.split()[:max_words])


def strengthen_subcatmodel_y_reasoning(
    output: dict,
    req_body: dict,
    *,
    max_words: int = 25,
) -> None:
    """Replace vague LLM Y reasoning with field-specific alignment text."""
    if output.get("error") or output.get("subcatmodelmatch") != "Y":
        return
    output["subcatmodelreasoning"] = _build_subcatmodel_pass_reasoning(
        req_body, output, max_words=max_words
    )


def _build_subcategory_vision_conflict_reasoning(
    user_subcategory: str,
    output: dict,
    detected_text: str,
    *,
    max_words: int = 25,
) -> str:
    """Concise N verdict when user subcategory product type conflicts with vision."""
    user_sub = str(user_subcategory or "").strip() or "user subcategory"
    asset_label = _vision_asset_label(output, detected_text)
    message = (
        f"N: subcategory {user_sub} conflicts with {asset_label} "
        f"in image — product type mismatch."
    )
    words = message.split()
    if len(words) > max_words:
        message = " ".join(words[:max_words])
    return message


def enforce_asset_name_vs_vision(
    output: dict,
    asset_name: str,
    gemini_output: dict | None = None,
    *,
    confirm_brand=None,
) -> None:
    """
    Downgrade namedescriptionmatch when asset name brand conflicts with vision.
    Prevents false Y when the LLM matches product type (monitor) but ignores brand (LG vs Dell).

    `confirm_brand(keys)` (docs 6.3) is asked before a brand conflict fails the name. A non-empty
    answer means the full-size photos show the name's own brand, so the description misread it and
    the brand conflict is dropped. No confirmer, or an empty answer, fails the name as before.
    """
    if output.get("error") or output.get("namedescriptionmatch") != "Y":
        return

    name = str(asset_name or "").strip()
    detected_text = _vision_detection_text(output, gemini_output)
    if not name or not detected_text:
        return

    name_brands = extract_brand_keys(name)
    # Only brands on the asset itself — not a street lamp or a toner box elsewhere in frame.
    detected_brands = _asset_photo_brand_keys(output, gemini_output)

    # The reason REPLACES the model's reasoning rather than being appended to it. The model's
    # text argued for the "Y" this rule overturns, and reasoning is capped at 25 words: appended,
    # the server's reason was cut off and the reviewer was shown the model's approval — "The
    # product family CAMERA is clearly visible … and matches the asset name" — as the reason the
    # record failed (5 of the 6 Hikvision records on 2026-09-17).
    if (
        name_brands and detected_brands and not (name_brands & detected_brands)
        and not (confirm_brand is not None and confirm_brand(name_brands))
    ):
        output["namedescriptionmatch"] = "N"
        output["namedescriptionmatchpercent"] = 25
        output["namedescriptionreasoning"] = (
            f"N: the asset name says {_brand_label(name_brands)}, but the photo shows "
            f"{_brand_label(detected_brands)} branding."
        )
        return

    if _product_category_conflict(name, detected_text):
        output["namedescriptionmatch"] = "N"
        output["namedescriptionmatchpercent"] = 20
        name_cats = _detect_product_categories(name)
        detected_cats = _detect_product_categories(detected_text) - name_cats
        output["namedescriptionreasoning"] = (
            f"N: the asset name describes {_with_article(_category_label(name_cats))}, but the "
            f"photo shows {_with_article(_category_label(detected_cats))}."
        )


def _declared_makemodel_text(req_body: dict) -> str:
    return " ".join(
        str(req_body.get(key) or "").strip()
        for key in ("makemodel", "makemodelname", "model")
    ).strip()


def enforce_makemodel_brand_vs_vision(
    output: dict,
    req_body: dict,
    gemini_output: dict | None = None,
    *,
    confirm_brand=None,
) -> bool:
    """Fail a classification whose make/model names a brand the photo contradicts.

    The make/model prompt treats brand as soft on purpose — "If product type matches but user
    Make/Model brand differs from visible branding → still APPROVE" — so it judges product type
    only. Pidilite tag 1000005803 (2026-09-22) is where that goes wrong: makemodel "Split Air
    Conditioner LLoyd", the photo an LG split AC with the LG logo beside the tag. Product type
    matched, so the classification passed and every status read "matched", while the name
    check on the same record failed for the same brand.

    Same evidence and the same brand list as enforce_asset_name_vs_vision: the declared field
    names brand X, the vision text names brand Y, and none of X appears there. A make/model
    without a known brand, or a photo whose description names none, changes nothing.

    Returns True when the brand is contradicted, and records it in
    ``output["_makemodel_brand_conflict"]`` so the master-data steps after it cannot turn the
    failure back into a match. Only a Y is downgraded; an N already fails.

    `confirm_brand(keys)` (docs 6.3): a non-empty answer means the full-size photos show the
    make/model's own brand, so the description misread it and there is no conflict.
    """
    if output.get("subcatmodelmatch") not in ("Y", "N"):
        return False
    declared = _declared_makemodel_text(req_body or {})
    detected_text = _vision_detection_text(output, gemini_output)
    if not declared or not detected_text:
        return False
    declared_brands = extract_brand_keys(declared)
    photo_brands = _asset_photo_brand_keys(output, gemini_output)
    if not declared_brands or not photo_brands or declared_brands & photo_brands:
        return False
    if confirm_brand is not None and confirm_brand(declared_brands):
        return False

    output["_makemodel_brand_conflict"] = {
        "declared": _brand_label(declared_brands),
        "photo": _brand_label(photo_brands),
    }
    if output.get("subcatmodelmatch") == "Y":
        output["subcatmodelmatch"] = "N"
        output["subcatmodelmatchpercent"] = 25
        # Replaces the model's text, which argued for the Y this overturns (see
        # enforce_asset_name_vs_vision for why appending does not work).
        output["subcatmodelreasoning"] = (
            f"N: the make/model says {_brand_label(declared_brands)}, but the photo shows "
            f"{_brand_label(photo_brands)} branding."
        )
    return True


_BRAND_DISPLAY = {
    "hp": "HP", "lg": "LG", "ifb": "IFB", "benq": "BenQ", "bluestar": "Blue Star", "boat": "boAt",
    "oneplus": "OnePlus", "viewsonic": "ViewSonic",
}


def _brand_label(keys: set[str]) -> str:
    return " / ".join(_BRAND_DISPLAY.get(k, k.capitalize()) for k in sorted(keys))


# How a family key reads in a reason: "power_bank" is "power bank", "ac" is "AC" ("an AC", _with_article), the
# power-backup family "UPS or battery" ("a UPS or battery").
_FAMILY_WORD = {"ac": "AC", "network": "network device", "power_backup": "UPS or battery"}


def _family_words(families) -> str:
    """Family keys as the words a reason shows, in sorted order. The keys of before 2026-11-02 read as they did."""
    return " ".join(_FAMILY_WORD.get(key, key.replace("_", " ")) for key in sorted(families))


def _category_label(categories: set[str]) -> str:
    labels = ["TV" if c == "tv" else _family_words({c}) for c in sorted(categories)]
    return " / ".join(labels) if labels else "different asset"


# IT device classes whose brand is printed on the body and legible in an ordinary photo (5.5).
_IT_DEVICE_RE = re.compile(
    r"\b(?:laptops?|notebooks?|desktops?|computers?|pcs?|cpus?|monitors?|printers?|scanners?"
    r"|(?:tele|smart|cell)?phones?|tablets?|ipads?|servers?|routers?|switch(?:es)?"
    r"|access points?|projectors?|all[- ]in[- ]ones?)\b"
)
_FURNITURE_FAMILIES = frozenset({"table", "cabinet", "chair", "sofa"})

# Product lines an IT auditor reads as the maker's name. "LASERJET PRINTER (P1606DN)" and "IBM
# THINKCENTRE" (customer 1009) name the asset well enough to find it; "Laptop" does not. Used only
# by the IT identity rule, so an ordinary word among them ("precision", "yoga") can at most accept
# a name inside an IT record — never fail one elsewhere.
_IT_PRODUCT_LINES: dict[str, frozenset[str]] = {
    "hp": frozenset({"laserjet", "deskjet", "officejet", "pagewide", "designjet", "elitebook",
                     "probook", "zbook", "elitedesk", "prodesk", "pavilion", "envy", "spectre",
                     "omen", "proliant"}),
    "dell": frozenset({"latitude", "inspiron", "vostro", "precision", "optiplex", "xps",
                       "poweredge"}),
    "lenovo": frozenset({"thinkcentre", "thinkcenter", "think centre", "think center", "thinkbook",
                         "thinkstation", "thinkvision", "ideacentre", "yoga"}),
    "apple": frozenset({"mac mini", "mac studio", "mac pro", "macbook pro", "macbook air"}),
    "asus": frozenset({"vivobook", "zenbook", "expertbook", "rog", "tuf"}),
    "acer": frozenset({"aspire", "predator", "nitro", "travelmate", "veriton", "swift"}),
    "canon": frozenset({"pixma", "imageclass", "imagerunner", "maxify"}),
    "epson": frozenset({"ecotank", "workforce"}),
    "samsung": frozenset({"galaxy"}),
}


def _it_product_line_brands(text: str) -> set[str]:
    """Brands named through a product line in a declared field ("Optiplex 3050" -> dell)."""
    norm = normalize_classification_value(text)
    if not norm:
        return set()
    found: set[str] = set()
    for brand_key, lines in _IT_PRODUCT_LINES.items():
        for line in lines:
            if re.search(r"\b" + re.escape(line).replace(r"\ ", r"\s+") + r"\b", norm):
                found.add(brand_key)
                break
    return found


def _strip_it_product_lines(text: str, keys=None) -> str:
    """The label without IT product-line words — every brand's, or only those of `keys`
    ("Dell Latitude laptop" -> "Dell laptop")."""
    out = str(text or "")
    for brand_key, lines in _IT_PRODUCT_LINES.items():
        if keys is not None and brand_key not in keys:
            continue
        for line in lines:
            out = re.sub(r"\b" + re.escape(line).replace(r"\ ", r"\s+") + r"\b", " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


def _is_it_record(output: dict) -> bool:
    """The record's own classification names an IT device and no furniture.

    "Laptop", "IT / Desktop With Monitor" and "Telephones / Asus vivobook" are IT records; "Printer
    table with storage" and a "Workstation" desk photographed with a monitor on it are furniture,
    whatever device the photo shows.

    Read by the head noun (6.1), an accessory is no IT device: "Laptop bag", "Laptop charger", "Monitor arm",
    "Printer cartridge" and a pharma "TABLET COATING M/C" are not held to the brand rule, and "10 PCS" is a quantity.
    """
    text = _join_texts(*(output.get(k) for k in ("categoryname", "subcategoryname", "assetname", "makemodel", "model")))
    if _detect_product_categories(text) & _FURNITURE_FAMILIES:
        return False
    if product_types_v2():
        return _v2_names_it_device(text)
    return bool(_IT_DEVICE_RE.search(normalize_classification_value(text)))


# Letters whose names begin with a vowel sound: "an HP laptop", "an LG monitor", but "a UPS".
_VOWEL_SOUND_LETTERS = frozenset("AEFHILMNORSX")
# The public reasoning is capped at validation_service.REASONING_SINGLE_MAX_WORDS (pinned equal
# in tests/test_it_identity_strict.py); kept here to avoid a circular import.
_REASON_WORD_BUDGET = 25


def _with_article(phrase: str) -> str:
    """'an Apple MacBook laptop', 'an HP laptop', 'a Lenovo laptop', 'an air conditioner'.

    An all-capitals initialism is said letter by letter, so its article follows the first
    letter's name; any other word follows its first letter.
    """
    first = phrase.split(" ", 1)[0]
    if first.isupper() and 2 <= len(first) <= 4:
        article = "an" if first[0] in _VOWEL_SOUND_LETTERS else "a"
    else:
        article = "an" if first[:1].lower() in "aeiou" else "a"
    return f"{article} {phrase}"


def enforce_it_identity_vs_photo(
    output: dict, gemini_output: dict | None = None, *, confirm_brand=None
) -> None:
    """An IT record must name the brand the photo shows on the device (docs 5.5).

    IT hardware carries its maker's name on the body, so a photo identifies it far better than
    any other asset class — and the record can be held to that. Two things used to pass the
    name check on a clearly branded device: a record that names no brand or model anywhere
    ("Laptop" / "Laptop", make/model blank — the name prompt's "missing brand" approval), and a
    record whose only brand sits in make/model and contradicts the photo ("Laptop" with make/model
    "Lenovo ThinkPad" against a Dell laptop; enforce_asset_name_vs_vision reads the name alone).
    In the week to 2026-10-22: 64 of the first kind, 43 passing; 1 of the second.

    The brand is read from the asset's own labels (_asset_photo_brand_keys), never from other
    things in frame or from the client's name on the asset tag; the record's brand from name,
    description, make/model and model, by maker's name or product line (_IT_PRODUCT_LINES).
    The asset NAME or description must carry it: a brand that sits only in make/model leaves
    "Laptop" as the name an auditor searches for six months on (the owner, 2026-10-23), so
    "Laptop" with make/model "MacBook Pro" against an Apple laptop fails with a suggested name,
    unless IT_NAME_MUST_NAME_BRAND_ENABLED is off. Furniture and every other class are untouched,
    and so is a device whose brand is not legible.

    `confirm_brand(keys)` (docs 6.3) is asked when the record's brand and the description's
    disagree. It returns the brands the full-size photos show when all of them are the record's
    own: the description misread the brand, and the record is judged against what was read.
    """
    from asset_validator.core.config import (
        IT_IDENTITY_STRICT_ENABLED,
        IT_NAME_MUST_NAME_BRAND_ENABLED,
    )

    if not IT_IDENTITY_STRICT_ENABLED or output.get("error") or output.get("missing_image"):
        return
    if output.get("namedescriptionmatch") != "Y":
        return
    if not _is_it_record(output):
        return
    photo_brands = _asset_photo_brand_keys(output, gemini_output)
    if not photo_brands:
        return

    def brands_in(*keys: str) -> set[str]:
        text = " ".join(str(output.get(k) or "") for k in keys)
        return extract_brand_keys(text) | _it_product_line_brands(text)

    name = str(output.get("assetname") or "").strip()
    detected = str(output.get("detectedAsset") or "").strip() or name or "device"
    # Maker's names only: a product-line word that is also an everyday word ("Precision
    # Tools division", "Swift Logistics") may accept a name below but never manufactures a
    # conflict.
    record_brands = extract_brand_keys(" ".join(
        str(output.get(k) or "") for k in ("assetname", "description", "makemodel", "model")
    ))
    if record_brands and not (brands_in("assetname", "description", "makemodel", "model") & photo_brands):
        confirmed = confirm_brand(record_brands) if confirm_brand is not None else set()
        if confirmed:
            # The full-size photos show the record's own brand (6.3): judge the record against
            # it, and keep the misread brand and its product line out of the suggested name.
            photo_brands = set(confirmed)
            misread = extract_brand_keys(detected, prose=True) - photo_brands
            detected = (
                _strip_it_product_lines(strip_brand_words(detected, keys=misread), keys=misread)
                or name or "device"
            )

    named = brands_in("assetname", "description")
    if named & photo_brands:
        return
    in_makemodel = brands_in("makemodel", "model") & photo_brands
    if in_makemodel and not IT_NAME_MUST_NAME_BRAND_ENABLED:
        return
    brand = _brand_label(photo_brands)
    # "Lenovo laptop", not "Lenovo Lenovo laptop", when the label already names the brand.
    shown = detected if any(w.lower() in detected.lower() for w in brand.split(" / ")) else f"{brand} {detected}"
    if in_makemodel:
        # The brand is only in make/model or model: the name itself must say what it is. The
        # suggested name is dropped, not cut off, when the reason would exceed the word budget.
        reasoning = (
            f"N: the photo shows {_with_article(shown)}; the asset name ('{name}') should name it "
            f"- e.g. '{shown}'."
        )
        if len(reasoning.split()) > _REASON_WORD_BUDGET:
            reasoning = f"N: the photo shows {_with_article(shown)}; the asset name ('{name}') should name it."
    else:
        # record_brands are disjoint from the photo's brands here, or a branch above returned.
        if record_brands:
            reasoning = (
                f"N: the record says {_brand_label(record_brands)}, but the photo shows "
                f"{brand} branding on the {detected}."
            )
        else:
            reasoning = f"N: the photo shows {_with_article(shown)}; the record ('{name}') names no brand or model."
    output["namedescriptionmatch"] = "N"
    output["namedescriptionmatchpercent"] = 20
    output["namedescriptionreasoning"] = reasoning


def placeholder_name_reason(asset_name: str, seen: str = "") -> str:
    """The name check's reason for a placeholder name (docs 5.7), with what the photo shows when that is known.

    Written in the consumer's allowed characters (2.8), so the name is quoted without quote marks, and within the
    25-word budget: the photo sentence is dropped, never cut off, when it would not fit.
    """
    from asset_validator.utils.helpers import _DISALLOWED_OUTPUT_CHARS_RE, to_db_safe_text

    def allowed(text: str, max_words: int) -> str:
        words = _DISALLOWED_OUTPUT_CHARS_RE.sub("", to_db_safe_text(str(text or ""))).split()
        return " ".join(words[:max_words])

    name = allowed(asset_name, 6)
    subject = f"the name {name}" if name else "the asset name"
    base = f"N: {subject} is a placeholder and does not say what the asset is."
    label = allowed(seen, _REASON_WORD_BUDGET)
    reason = f"{base} The photo shows {_with_article(label)}." if label else base
    return reason if len(reason.split()) <= _REASON_WORD_BUDGET else base


def enforce_placeholder_name(output: dict, asset_name: str) -> bool:
    """A name that says nothing about the asset fails the name check (docs 5.7). True when this rule set the verdict.

    "Asset 9799" / "Description of Asset 9799" (AssetId 316770, QA customer 1004, 2026-11-01) passed at 95 on a
    MacBook photo, and the same name failed on a battery photo (bench case 07): the name check never sees the
    category, and every other rule here keys on a brand or a product family the name does not have, so the model's
    guess decided it both ways. A Y or an N becomes N at 20 with one fixed reason, the same on every run and
    countable in the logs. The photo sentence comes from phase 1's label, so it is left out when phase 1 found the
    photo unreadable; `_placeholder_name_reason` keeps the reason without it for fail_asset_side, which runs later and
    discards everything said about a photo that turns out not to show the asset.
    """
    from asset_validator.core.config import PLACEHOLDER_NAME_RULE_ENABLED
    from asset_validator.utils.helpers import is_placeholder_name

    if not PLACEHOLDER_NAME_RULE_ENABLED or output.get("error"):
        return False
    if output.get("namedescriptionmatch") not in ("Y", "N"):
        return False
    if not is_placeholder_name(asset_name, output.get("description")):
        return False
    from asset_validator.core.constants import LLM_NULL_STRINGS

    readable = (
        str(output.get("imageReadability") or "").upper() != "E" and output.get("missing_image") != "asset"
    )
    # A label the model wrote as "None" / "unknown" names nothing; a trailing full stop would end the sentence twice.
    label = str(output.get("detectedAsset") or "").strip().rstrip(".").strip()
    if label.casefold() in LLM_NULL_STRINGS:
        label = ""
    output["namedescriptionmatch"] = "N"
    output["namedescriptionmatchpercent"] = 20
    output["namedescriptionreasoning"] = placeholder_name_reason(asset_name, label if readable else "")
    output["_placeholder_name_reason"] = placeholder_name_reason(asset_name)
    return True


_NEGATION_RE = re.compile(
    r"\b(?:no|not|none|without|absent|missing|lacks?|lacking|nor|neither|never)\b",
    re.IGNORECASE,
)


def strip_negated_clauses(text: str, *, sep: str = " ") -> str:
    """Drop clauses that deny an object's presence.

    Vision prose routinely says "No asset tag is visible" or "no table present"; a plain
    keyword scan would read those as sightings and wrongly confirm the object. `sep="; "` keeps
    the clauses apart for the head-noun reading (6.1); the plain space join is unchanged.
    """
    if not text:
        return ""
    kept: list[str] = []
    for clause in re.split(r"[.;,]| but | however | although ", str(text)):
        clause = clause.strip()
        if clause and not _NEGATION_RE.search(clause):
            kept.append(clause)
    return sep.join(kept)


def _visible_asset_items(output: dict, gemini_output: dict | None = None) -> list[str]:
    """Each asset vision reported in the frame, kept as separate entries.

    Item boundaries matter: joining first would merge "HP computer monitor" and
    "white office table" into one string that matches both families at once.
    """
    items: list[str] = []
    for source in (output, gemini_output or {}):
        listed = source.get("visible_assets")
        if isinstance(listed, list):
            items.extend(str(item).strip() for item in listed if item)
        elif isinstance(listed, str) and listed.strip():
            items.extend(part.strip() for part in re.split(r"[;,]", listed) if part.strip())
    return [item for item in items if item]


def _visible_asset_text(output: dict, gemini_output: dict | None = None) -> str:
    """All assets vision reported in the frame — not only the one it called primary."""
    return "; ".join(_visible_asset_items(output, gemini_output)).strip()


def reconcile_namedescription_with_vision(
    output: dict,
    asset_name: str,
    gemini_output: dict | None = None,
) -> None:
    """
    Upgrade a false N when the asset name's product family IS present in the frame.

    Guards against the two dominant false-reject modes: wording differences within one
    family ("dining table" vs "office table"), and vision naming a secondary object as
    primary while the real asset sits elsewhere in the same photo. Brand conflicts are
    left untouched — enforce_asset_name_vs_vision owns those.

    It lifts on the eleven families of before 2026-11-02 only (6.1). It lifts an N whatever its reason — colour, tag
    position, brand — so each new family would widen it: replayed over the 15-29 Oct logs, the new families lifted
    rejections that were right (a colour, a tag on another object, works records naming several assets).
    """
    if output.get("error") or output.get("namedescriptionmatch") != "N":
        return

    name = str(asset_name or "").strip()
    if not name:
        return

    name_cats = _detect_product_categories(name)
    if product_types_v2():
        name_cats &= _LEGACY_FAMILIES
    if not name_cats:
        return

    # A brand conflict is a genuine mismatch — never upgrade over it. Read brands exactly as
    # enforce_asset_name_vs_vision does, so the two rules never disagree about whether one exists.
    detected_text = _vision_detection_text(output, gemini_output)
    name_brands = extract_brand_keys(name)
    detected_brands = extract_brand_keys(detected_text, prose=True)
    if name_brands and detected_brands and not (name_brands & detected_brands):
        return

    # detectedAsset/visible_assets are inventory fields; imageAnalysis is prose and may
    # mention an object only to deny it ("no table present") — screen those out.
    frame_text = _join_texts(
        output.get("detectedAsset"),
        _visible_asset_text(output, gemini_output),
        strip_negated_clauses(str(output.get("imageAnalysis") or ""), sep="; "),
    )
    if not frame_text:
        return

    # A safe is the same kind only as a safe (6.1): "Storage cabinet" is not lifted on a metal safe.
    if not same_kind_families(name_cats & _detect_product_categories(frame_text), name, frame_text):
        return

    output["namedescriptionmatch"] = "Y"
    output["namedescriptionmatchpercent"] = 85
    family = " / ".join(sorted(c.replace("_", " ") for c in name_cats))
    output["namedescriptionreasoning"] = (
        f"Y: {family} named by the user is visible in the image; "
        f"wording differs but the asset family matches."
    )


_BIN_EVIDENCE_RE = re.compile(
    r"\b(?:dust\s?-?bins?|bins?|waste|trash|garbage|litter|refuse|recycl\w*|swing\s?lids?|pedal)\b"
    r"|\b(?:openings?|lids?|holes?|slots?)\b[^.;]{0,60}\b(?:wet|dry)\b"
    r"|\b(?:wet|dry)\b[^.;]{0,60}\b(?:openings?|lids?|holes?|slots?)\b"
)
_BIN_ENCLOSURE_RE = re.compile(r"\b(?:cabinet|unit|enclosure|station|box)s?\b")
# The tag sits on some other object — the name check's "tag is on a different object" rejection.
_TAG_ON_OTHER_OBJECT_RE = re.compile(
    r"\b(?:desk|table|counter|floor|wall|shel(?:f|ves)|pedestal|printer|chair|bench|rack|partition"
    r"|stand|balance|trolley|monitor|keyboard)s?\b"
)


def _tag_position_text(output: dict) -> str:
    position = output.get("barcodeposition")
    if isinstance(position, dict):
        position = position.get("position")
    return normalize_classification_value(position)


def rescue_waste_bin_name(
    output: dict,
    asset_name: str,
    gemini_output: dict | None = None,
) -> bool:
    """Turn a false N into Y when the waste bins named are built into what the photo shows.

    Customer 1158 registers steel swing-lid bins ("STEEL DUST BIN SWING 8X24"); the photos show
    them built into a wooden cabinet. Phase 1 labels the unit "Cabinet" in some runs and "Waste bin
    cabinet" in others, so the name check failed the same record at random: "the image analysis
    describes a cabinet, not a steel dust bin" — beside its own "circular metal opening on top,
    labeled `WET`". 8 of the 17 readable-photo waste-bin rejections in the 2026-07/09 logs.

    Only for a waste-bin name (utils.helpers.names_waste_bin), and only when the TAGGED object is
    what carries the bins: bin evidence in the detected-asset label, or the label is a cabinet /
    unit / enclosure / station / box and a description sentence about it names bins, waste,
    recycling, a swing or pedal lid, or openings marked WET / DRY. A desk with a dustbin mentioned
    beside it does not qualify — those records, whose tag was on the desk, rightly stay N. Not added
    to _PRODUCT_CATEGORY_PATTERNS: that would arm Gate B and the subcategory checks for bin
    registrations, and a photo labelled "Cabinet" would start failing them.
    """
    from asset_validator.utils.helpers import names_waste_bin

    if output.get("error") or output.get("namedescriptionmatch") != "N":
        return False
    name = str(asset_name or "").strip()
    if not names_waste_bin(name):
        return False
    name_brands = extract_brand_keys(name)
    photo_brands = _asset_photo_brand_keys(output, gemini_output)
    if name_brands and photo_brands and not (name_brands & photo_brands):
        return False

    # A tag on the desk the dustbin stands on is a genuine rejection, even when the dustbin is the
    # detected asset: customer 1158's "Dustbin" whose tag "is placed on the wooden surface of the desk".
    tag_at = _tag_position_text(output)
    if (tag_at and _TAG_ON_OTHER_OBJECT_RE.search(tag_at)
            and not (_BIN_EVIDENCE_RE.search(tag_at) or _BIN_ENCLOSURE_RE.search(tag_at))):
        return False

    label = normalize_classification_value(output.get("detectedAsset"))
    if not label:
        return False
    carries_bins = bool(_BIN_EVIDENCE_RE.search(label))
    if not carries_bins:
        enclosure = _BIN_ENCLOSURE_RE.search(label)
        if enclosure:
            word = enclosure.group(0).rstrip("s")
            analysis = strip_negated_clauses(str(output.get("imageAnalysis") or ""))
            for sentence in _SENTENCE_BREAK.split(analysis):
                lowered = sentence.lower()
                if re.search(r"\b" + re.escape(word) + r"s?\b", lowered) and _BIN_EVIDENCE_RE.search(lowered):
                    carries_bins = True
                    break
    if not carries_bins:
        return False

    output["namedescriptionmatch"] = "Y"
    output["namedescriptionmatchpercent"] = 85
    output["namedescriptionreasoning"] = (
        "Y: the dust bins named are built into the unit in the photo."
    )
    return True


_SIZE_SPEC_RE = re.compile(
    r"[-–—]?\s*size\b.*$"
    r"|\b\d[\d\s.,/x×*-]*\s*(?:mm|cm|mtr|metre|meter|inch|inches|in|ft|feet)\b.*$",
    re.IGNORECASE,
)


# Sentences that name the primary asset — superseded once identity is corrected.
_PRIMACY_CLAIM_RE = re.compile(
    r"\b(?:primary|main|tagged)\s+(?:tagged\s+)?asset\b|\bassetis\b",
    re.IGNORECASE,
)


def clean_asset_label(name: str, *, max_words: int = 5) -> str:
    """Readable asset label from a declared name — drops trailing dimension specs."""
    text = _SIZE_SPEC_RE.sub("", str(name or "")).strip(" -–—,;:")
    words = text.split()
    return " ".join(words[:max_words]) if words else ""


def reconcile_detected_asset_with_confirmed_name(
    output: dict,
    user_asset_name: str,
    gemini_output: dict | None = None,
) -> None:
    """
    Adopt the user's asset as the primary one once the name check has confirmed it.

    A photo of a tagged desk almost always contains a monitor, and vision keeps naming
    the monitor as primary. When namedescriptionmatch is Y the named object has already
    been confirmed present, so leaving detectedAsset pointing at a different product
    family makes the checks contradict each other: the name passes while make/model
    reports a "product type mismatch" and cost prices the wrong object.

    Only runs on a confirmed Y, and only when the two families genuinely disagree.
    """
    if output.get("error") or output.get("namedescriptionmatch") != "Y":
        return

    name = str(user_asset_name or "").strip()
    if not name:
        return
    # form_factor_context=False: this rewrite keeps the reading it has always had. A "Vostro 3470
    # Desktop" record approved against a photo of its monitor is still rewritten to the named
    # desktop, so cost and make/model price the right object. For the same reason it decides on the
    # eleven families of before 2026-11-02, read as before, whatever PRODUCT_TYPES_V2_ENABLED says
    # (6.1): which object is priced is not the product-type list's to change.
    name_cats = _detect_product_categories_v1(name, form_factor_context=False)
    if not name_cats:
        return

    detected = str(output.get("detectedAsset") or "").strip()
    detected_cats = _detect_product_categories_v1(detected, form_factor_context=False)
    # No reading, or the families already agree — nothing to correct.
    if not detected_cats or (name_cats & detected_cats):
        return

    # Prefer vision's own words for the object over echoing the user's name back. With the head-noun
    # reading on (6.1), a listed object must name the asset's family by that reading too, or the rules
    # after this one would read the new label as something else: the old list took "Desk fan" and
    # "Money counter" for tables (AssetIds 2720266, 286213, "Security Desk" and "Printer Table With
    # Storage"). Then the name itself is the label.
    head_cats = _detect_product_categories(name, form_factor_context=False) if product_types_v2() else None
    label = ""
    for item in _visible_asset_items(output, gemini_output):
        if not _detect_product_categories_v1(item, form_factor_context=False) & name_cats:
            continue
        if head_cats is not None and not _detect_product_categories(item, form_factor_context=False) & head_cats:
            continue
        label = " ".join(item.split()[:5])
        break
    if not label:
        label = clean_asset_label(name)
    if not label:
        return

    output["detectedAsset"] = label
    output["identity_corrected_from"] = detected

    # imageAnalysis is fed verbatim into the cost and make/model prompts. Left as-is it
    # would still announce the monitor as primary and they would price the wrong object.
    # Drop the superseded primacy claim first — keeping it leaves the field asserting two
    # different primary assets in one string.
    analysis = str(output.get("imageAnalysis") or "").strip()
    kept = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", analysis)
        if sentence.strip() and not _PRIMACY_CLAIM_RE.search(sentence)
    ]
    note = (
        f"Primary tagged asset: {label}. "
        f"The {detected} is a secondary object resting on or near it."
    )
    output["imageAnalysis"] = " ".join([note, *kept]).strip()


# Equipment and furniture that "mobile" describes as movable rather than as a phone.
_MOBILE_NOT_A_PHONE = (
    r"(?:crane|rack|racking|trolley|stand|van|shelving|shelf|shelves|storage|tower|generator"
    r"|toilet|cabinet|pedestal|table|compactor|scaffold|scaffolding|platform|ladder|light"
    r"|lighting|pump|cart|workstation|x ?ray|xray|medical|clinic|dispensary|canteen|kitchen"
    r"|substation|dg|compressor|welding|screen|partition|container|office|cabin|crusher|plant"
    r"|bin|barricade|chair|desk|fan|cooler|projector|podium|whiteboard|board|bed|stretcher"
    r"|hoist|lift|elevator|conveyor|tank|bowser)s?\b"
)

_PRODUCT_CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
    "tv": re.compile(r"\b(?:tv|television|led tv|smart tv|flat screen tv)s?\b"),
    "monitor": re.compile(
        r"\b(?:monitor|computer monitor|desktop monitor|lcd monitor|led monitor|display monitor)s?\b"
    ),
    "laptop": re.compile(
        r"\b(?:laptop|notebook|thinkpad|macbook|chromebook|latitude|pavilion|elitebook|probook|ideapad|vivobook|victus|omen|inspiron|vostro|xps|envy|spectre|zenbook|aspire|swift|nitro|rog|tuf|predator)s?\b"
    ),
    "printer": re.compile(r"\b(?:printer|mfp|multifunction)s?\b"),
    "chair": re.compile(r"\b(?:chair|armchair|office chair|task chair|cafeteria chair|plastic chair|stool)s?(?:\b|\d)"),
    # Furniture families — declared subcategories are routinely "tables", "desk", "almirah".
    "table": re.compile(
        r"\b(?:table|desk|workstation top|counter|worktop|writing table|conference table"
        r"|dining table|reception table|side table|coffee table|meeting table)s?\b"
    ),
    "cabinet": re.compile(
        r"\b(?:cabinet|almirah|cupboard|wardrobe|locker|storage unit|filing cabinet"
        r"|book ?case|book ?shelf|shelving|rack unit)s?\b"
    ),
    # Single-seat upholstered seating is described either way: AssetId 2818594, registered
    # "Sofa / Single Seater Sofa with leatherite", was described as a "lounge chair" and failed
    # make/model as sofa-vs-chair on 2026-09-09. These chair names are also sofa-type seating, so
    # they match BOTH families; an office, task or plastic chair against a sofa still conflicts.
    "sofa": re.compile(
        r"\b(?:sofa|couch|settee|recliner|lounge seat|lounge chair|armchair|arm chair"
        r"|easy chair|club chair|accent chair|sofa chair|wing chair)s?\b"
    ),
    "fire_extinguisher": re.compile(
        r"\b(?:fire extinguisher|fire extinguishers|abc extinguisher|co2 extinguisher)s?\b"
    ),
    # "Mobile" alone is how registers name a phone ("Mobile", "Samsung Mobile"), but followed
    # by equipment it means movable: a "Mobile Crane" record was read as a phone claim and failed
    # against a photo of a crane.
    "phone": re.compile(
        r"\b(?:phone|smartphone|mobile(?!\s+" + _MOBILE_NOT_A_PHONE + r")|mobile phone|cellphone"
        r"|iphone|oneplus|nord|galaxy|pixel|redmi)s?\b"
    ),
    "mouse": re.compile(r"\b(?:mouse|optical mouse|wireless mouse)s?\b"),
}

_ALIGNMENT_STOP_TOKENS = frozenset({
    "type", "the", "and", "or", "for", "with", "a", "an", "of", "in", "on", "to",
})


def _detect_product_categories(text: str, *, form_factor_context: bool = True) -> set[str]:
    """Known product types named in `text` (docs 6.1).

    With PRODUCT_TYPES_V2_ENABLED (the default), the families of 2026-11-02 read by the noun that names the thing
    (`_v2_reading`); off, the eleven families of before, a family word anywhere (`_detect_product_categories_v1`).
    `form_factor_context=False` keeps the reading used before laptop product lines were checked
    against desktop/vehicle wording.
    """
    if product_types_v2():
        return set(_v2_reading(text, form_factor_context=form_factor_context).families)
    return _detect_product_categories_v1(text, form_factor_context=form_factor_context)


def _detect_product_categories_v1(text: str, *, form_factor_context: bool = True) -> set[str]:
    """The eleven families of before 2026-11-02, a family word anywhere in the text (docs 6.1).

    `form_factor_context=False` keeps the reading used before laptop product lines were checked
    against desktop/vehicle wording. Only reconcile_detected_asset_with_confirmed_name uses it, and it
    decides on this list whatever PRODUCT_TYPES_V2_ENABLED says, so that rewrite behaves as it did.
    """
    norm = normalize_classification_value(text)
    if not norm:
        return set()
    found: set[str] = set()
    for category, pattern in _PRODUCT_CATEGORY_PATTERNS.items():
        if pattern.search(norm):
            found.add(category)
    if re.search(r"\btv\b", norm):
        found.add("tv")
    for token in norm.split():
        if re.fullmatch(r"tv\w*", token) or token in ("tv", "tvs"):
            found.add("tv")
    if re.search(r"\blatitude\b", norm):
        found.add("laptop")
    if (
        form_factor_context
        and "laptop" in found
        and not _LAPTOP_ONLY_WORDS.search(norm)
        and _NOT_A_LAPTOP_CONTEXT.search(norm)
    ):
        found.discard("laptop")
    return found


# Laptop product lines that are ALSO sold as desktops, or share a name with a car. They count
# as a laptop only when nothing in the same text says otherwise: AssetId 2290143,
# "Vostro 3470 Desktop", was read as a laptop claim, and a "Maruti Swift" company car would be
# read as an Acer Swift. Words that can only mean a laptop always win.
_LAPTOP_ONLY_WORDS = re.compile(
    r"\b(?:laptop|notebook|thinkpad|macbook|chromebook|latitude|elitebook|probook|ideapad"
    r"|vivobook|spectre|zenbook)s?\b"
)
_NOT_A_LAPTOP_CONTEXT = re.compile(
    r"\b(?:desktop(?!\s+(?:monitor|screen|display))|cpu|tower|sff|small form factor"
    r"|all in one|all-in-one|aio|mini pc|micro tower|car|cars|hatchback|sedan|suv|vehicle"
    r"|maruti|suzuki|dzire)\b"
)


# ── Product types read by the head noun (PRODUCT_TYPES_V2_ENABLED; docs 6.1; owner, 2026-11-02) ──────────────────────
#
# A family word names the asset only as the noun its phrase is about. Followed inside its own noun phrase (up to three
# words, before a connector such as "with", "on", "and") by a noun that names another object, it is a modifier and
# names nothing: "HP laptop power ADAPTER" is a charger, "Laptop KEYBOARD" a keyboard, "printer TABLE" a table, "desk
# PHONE" a phone. A part of the thing ("laptop screen", "chair back", "fire extinguisher cylinder") keeps it. Texts are
# read phrase by phrase: a comma, full stop, semicolon or bracket ends a phrase before punctuation is erased, and "/",
# "&" and "+" join two items ("Keyboard & Mouse" names both; "tablet/capsule sorter" is a sorter).
#
# The families were chosen on the 15-29 Oct logs and the 59,450 master rows (work/typelist, 2 Nov 2026). A family is a
# word that names the asset as its head noun, and its words must cover how the photo describes it, because Gate B fails
# a registered type the photo text never names (6.1). Left out on purpose: desktop (real desktops are described as
# "electronic device" and all-in-ones as monitors), fan, pedestal, rack, shelf, whiteboard, trolley, bag, stand,
# mount, lamp, light, panel, unit, system, machine, box, station, tower, and every industrial type (pump, motor, panel,
# tank, vessel): a heavy machine is photographed by its motor (owner, 2026-11-01).

_V2_PATTERNS: dict[str, re.Pattern[str]] = {family: re.compile(pattern) for family, pattern in {
    "tv": r"\b(?:(?:led|lcd|oled|qled|smart|android|flat screen) tvs?|televisions?|tvs?)\b",
    "monitor": r"\b(?:computer monitor|desktop monitor|lcd monitor|led monitor|display monitor|monitor)s?\b",
    "laptop": (
        r"\b(?:laptop|notebook|thinkpad|thinkbook|macbook|chromebook|latitude|pavilion|elitebook|probook|ideapad"
        r"|vivobook|expertbook|travelmate|victus|omen|inspiron|vostro|xps|envy|spectre|zenbook|aspire|swift|nitro|rog"
        r"|tuf|predator)s?\b"
    ),
    "printer": r"\b(?:printer|mfp|multifunction)s?\b",
    "chair": (
        r"\b(?:office chair|task chair|cafeteria chair|plastic chair|wheel chair|armchair|wheelchair|chair|stool)s?\b"
    ),
    "table": (
        r"\b(?:writing table|conference table|dining table|reception table|side table|coffee table|meeting table"
        r"|workstation top|table|desk|counter|worktop)s?\b"
    ),
    # "safe" names a safe only as the head of its phrase ("Godrej safe", "safe locker"); _v2_sense_removed.
    "cabinet": (
        r"\b(?:filing cabinet|storage unit|rack unit|book ?case|book ?shelf|cabinet|almirah|cupboard|wardrobe"
        r"|locker|shelving|safe)s?\b"
    ),
    "sofa": (
        r"\b(?:lounge seat|lounge chair|arm chair|easy chair|club chair|accent chair|sofa chair|wing chair|sofa|couch"
        r"|settee|recliner|armchair)s?\b"
    ),
    # Fire balls are how 27 genuine extinguishers that passed are labelled (Flipkart, Oct 2026).
    "fire_extinguisher": (
        r"\b(?:fire extinguishers?|abc extinguishers?|co2 extinguishers?|fire (?:safety|suppression) balls?"
        r"|(?:fire )?extinguishing balls?|fire ?balls?|extinguishers?)\b"
    ),
    "phone": (
        r"\b(?:mobile phone|cordless phone|smartphone|cellphone|telephone|landline|iphone|phone|mobile|oneplus|nord"
        r"|galaxy|pixel|redmi)s?\b"
    ),
    "mouse": r"\b(?:optical mouse|wireless mouse|mouse|mice)\b",
    # UPS, inverter and battery are one family: 368 registrations carry a UPS and its batteries together, and
    # separate families made "UPS / 10KVA with batteries" contradict itself.
    "power_backup": (
        r"\b(?:uninterruptible power suppl(?:y|ies)|online ups|battery banks?|lead acid|ups|inverters?"
        r"|batter(?:y|ies)|smf)\b"
    ),
    "power_bank": r"\b(?:power ?banks?|portable batter(?:y|ies))\b",
    "charger": (
        r"\b(?:power adapt[eo]rs?|ac adapt[eo]rs?|acdc adapt[eo]rs?|power suppl(?:y|ies)|power bricks?|chargers?"
        r"|smps)\b"
    ),
    "keyboard": r"\bkey ?boards?\b",
    "ac": (
        r"\b(?:air conditioners?|air conditioning|air condition|(?:split|window|cassette|ductable|tower) acs?"
        r"|split units?|aircon|acs?)\b"
    ),
    "refrigerator": (
        r"\b(?:deep freezers?|visi ?coolers?|bottle coolers?|refrigeration units?|refrigerators?|fridges?|freezers?)\b"
    ),
    "water_dispenser": (
        r"\b(?:water dispensers?|water coolers?|water purifiers?|ro (?:water )?purifiers?|aquaguard)\b"
    ),
    "projector": r"\bprojectors?\b",
    "camera": r"\b(?:cctv|cameras?|webcams?)\b",
    "network": (
        r"\b(?:(?:network|ethernet|poe|managed|gigabit) switch(?:es)?|(?:wireless )?access points?"
        r"|(?:network|server) racks?|(?:network|rack|server) cabinets?|patch panels?|routers?|firewalls?|modems?)\b"
    ),
    "tablet": r"\b(?:tablet pcs?|galaxy tabs?|lenovo tabs?|redmi pads?|ipads?|tablets?)\b",
    "stabilizer": r"\bstabili[sz]ers?\b",
    "microwave": r"\b(?:microwave ovens?|microwaves?)\b",
    "scanner": r"\b(?:barcode readers?|scanners?)\b",
    "server": r"\bservers?\b",
    "speaker": r"\b(?:loud ?speakers?|sound ?bars?|subwoofers?|speakers?)\b",
    "docking_station": r"\bdocking stations?\b",
}.items()}

# The families the rules knew before 2026-11-02. The name rescue lifts on these only (6.1).
_LEGACY_FAMILIES = frozenset(_PRODUCT_CATEGORY_PATTERNS)

# Nouns naming another object. After a family word, inside its phrase, they make that word a modifier.
_V2_OBJECT_HEADS = frozenset("""
adapter adaptor charger supply brick bag backpack sleeve pouch case cover skin stand riser holder mount bracket arm
tray trolley cart table desk cabinet cupboard locker rack racking shelf keyboard mouse battery cartridge toner ink
ribbon paper cable cord wire remote lamp light fan clock calendar cushion pillow mat pad booth pod cabin sign signage
symbol icon sticker label logo graphic poster picture image diagram number box carton packaging lease lock guard
protector shield organizer organiser divider partition tester machine vibrator feeder coater press grinder centrifuge
sterilizer autoclave scale balance shaker analyzer analyser mill roller stacker sorter reactor digester dryer furnace
alarm refrigerator fridge freezer sink washer dishwasher phone telephone printer scanner pedestal pedest pallet
vessel lifter booster foam weld welding truck tanker ladder screen chair sofa bench bed compartment indicator level
slot operated powered backup dt handheld computer pc tab tablet tv television station camera monitor rejection
shorting counter counting deduster punch inspection liner refill jacket gimbal tripod tennis coating drilling laser
compression room area closet environment space zone corner hall lobby facility mechanism mechanizim nvr dvr recorder
feed footage cutter welder kit accessory part spare repair service servicing amc maintenance software
licence license insurance rent rental drive board bottle grille grill controller switch dock docking cleaner speaker
duct ducting pipe piping panel db distribution work works installation cabling wiring laying commissioning charges
""".split())

# Parts and own words: after these the family word still names the thing itself.
_V2_PARTS: dict[str, frozenset[str]] = {family: frozenset(words.split()) for family, words in {
    "tv": "screen display panel bezel set remote",
    "monitor": "screen display bezel panel base computer",
    "laptop": "screen display lid casing chassis body base bottom hinge bezel panel camera computer pc tablet port",
    "printer": "tray panel lid display scanner copier fax photocopier label",
    "chair": "back seat base leg arm armrest wheel castor frame sofa cushion",
    "table": "top leg surface edge frame drawer base counter",
    "cabinet": "door drawer shelf handle body system unit locker rack racking",
    "sofa": "chair set bench cushion",
    "fire_extinguisher": "ball cylinder body hose nozzle",
    "phone": "screen display handset camera",
    "mouse": "keyboard combo",
    "power_backup": "battery bank pack unit system cabinet rack stand enclosure backup terminal",
    "charger": "cable cord brick unit plug panel",
    "keyboard": "key mouse combo",
    "ac": "unit indoor outdoor system machine",
    "refrigerator": "door unit compartment",
    "water_dispenser": "unit machine",
    "projector": "lens unit",
    "camera": "dome bullet housing lens system unit",
    "network": "port rack cabinet panel module",
    "tablet": "screen display pc computer",
    "stabilizer": "unit",
    "microwave": "oven",
    "scanner": "unit gun lid glass bed",
    "server": "unit system chassis tower",
    "speaker": "system unit",
    "it": "screen display lid base unit tower system set panel chassis body casing port",
}.items()}

# Heads that make a modifier of one family only: "printer STORAGE", a "TV UNIT" or "TV CONSOLE" is furniture.
_V2_OWN_MODIFIER_HEADS: dict[str, frozenset[str]] = {
    "printer": frozenset({"cartridge", "toner", "ink", "ribbon", "paper", "drum", "spool", "storage"}),
    "tv": frozenset({"unit", "console"}),
    "laptop": frozenset({"storage", "storag"}),
}

# The word in FRONT of a family word that gives it another sense.
_V2_NOT_AFTER: dict[str, frozenset[str]] = {family: frozenset(words.split()) for family, words in {
    "table": "turn rotary rotating indexing lift lifting scissor tilting hydraulic vibrating vibratory vibro",
    "cabinet": (
        "control electrical electric server network rack panel biosafety laminar fume humidity stability drying"
        " heating cooling hose battery ups mcb lt ht vfd plc switch distribution flow relay telecom dcs pcc mcc apfc"
        " starter junction meter incubator"
    ),
    "phone": "mega head micro ear xylo saxo gramo",
    "power_backup": (
        "close set pick mock follow start make warm clean line sign round push pull roll mix hook check lock grown"
        " lay vfd drive frequency servo portable"
    ),
    "charger": "alf3 aif3 alumina ore coke bath crust pot furnace material raw screw portable uninterruptible",
    "camera": "built in front rear back selfie laptop phone mobile integrated inbuilt its",
    "stabilizer": "image gimbal camera handheld mobile phone",
    "scanner": "ct mri ultrasound ultrasonic pet body baggage luggage ray xray",
    "server": "air",
    "projector": "profile",
}.items()}
# ...and only in front of "counter": a counting device, a pharma "tablet counter" or a counter balance, not a table.
_V2_COUNTER_NOT_AFTER = frozenset(
    "note currency particle colony cell token coin money bill people geiger production digital hour tablet counting"
    " cash footfall visitor frequency pulse revolution rpm not".split()
)
_V2_COUCH_NOT_AFTER = frozenset("examination exam medical patient treatment hospital".split())
# "it is safe to use": a verb or adverb in front of "safe" makes it no safe.
_V2_SAFE_NOT_AFTER = frozenset(
    "is are be was were appears appear seems look looks keep kept stay remain remains not very quite food child"
    " dishwasher microwave oven user environmentally eco power".split()
)
_V2_SAFE_COMPANIONS = frozenset("locker box cabinet almirah door drawer shelf".split())
# "ac" is alternating current before these words ("AC adapter", "AC motor", "AC 415V") and after these.
_V2_AC_NOT_BEFORE = frozenset(
    "adapter adaptor power input outlet socket dc motor drive supply cord plug voltage line mains current cable arc"
    " generator induction servo synchronous coupling contactor relay capacitor".split()
)
_V2_AC_NOT_AFTER = frozenset("dc input output v vac volt volts hv lv kv ph phase".split())
_V2_VOLTAGE_RE = re.compile(r"\d+(?:v|vac|kv|kva|kw|ph|hz)")
# Words that end a noun phrase: the modifier check never looks past them.
_V2_CONNECTORS = frozenset("""
with without on in inside into onto upon for near featuring showing under beneath underneath beside besides next and
or nor but cum also alongside along via from by at behind above below over against between around within across
through towards toward to of the a an this that these those its it his her their our your which who whose where while
when is are was were be been being has have had having can could may might will would should shall must do does did
not no as such than like used using including includes include included containing contains holding holds attached
mounted placed plugged connected displaying displays labeled labelled marked located sitting sits resting rests
standing stands stored hanging hangs lying lies kept set installed fixed visible appears appear appearing seems shows
shown show bearing bears features faces facing leaning leans supports supporting carrying carries reads reading
""".split())
# Brand and product-line words: evidence of their family only when nothing else in their phrase, or for a
# registration nothing else in the record, names another electronic device: the same makers sell those too ("Redmi
# TV", "TV / OnePlus", "Laptops / Samsung Galaxy Book", "Monitor / HP Omen"). Against furniture or anything else they
# still count: "CHAIRS5000 / pavilion hp" contradicts itself.
_V2_WEAK_WORDS: dict[str, frozenset[str]] = {
    "phone": frozenset({"oneplus", "nord", "galaxy", "pixel", "redmi"}),
    "laptop": frozenset({"pavilion", "victus", "omen", "inspiron", "vostro", "xps", "envy", "aspire", "swift",
                         "nitro", "rog", "tuf", "predator"}),
    # A tablet is furniture's when the record or phrase is furniture: "SS Office Tablet", "PODIUM TABLET" (a table
    # typed as a tablet), "CHAIRS / Tablet" (a tablet-arm chair).
    "tablet": frozenset({"tablet", "tablets"}),
}
# ...and a product line counts only beside its maker's name: "Kent Galaxy" is a water purifier, "UPS / Galaxy" an APC
# UPS, "Royal swift" a purifier, but "Samsung Galaxy A14", "pavilion hp" and "Acer Swift 3" are what they say.
_V2_LINE_MAKERS = {
    "galaxy": "samsung", "pavilion": "hp", "victus": "hp", "omen": "hp", "envy": "hp", "inspiron": "dell",
    "vostro": "dell", "xps": "dell", "aspire": "acer", "swift": "acer", "nitro": "acer", "predator": "acer",
    "rog": "asus", "tuf": "asus",
    # "pixels" of a photo and NORD gear motors are no phones: the post-hoc text of an unusable photo says "95% of
    # pixels one tone" (3.4). Both words are aliases of their maker in the brand list, so the maker must be named by
    # another word (_v2_maker_brands).
    "pixel": "google", "nord": "oneplus",
}


def _v2_maker_brands(text: str) -> set[str]:
    """The brands a text names by a word that is not one of the product lines above."""
    lines = r"\b(?:" + "|".join(sorted(_V2_LINE_MAKERS)) + r")s?\b"
    return extract_brand_keys(re.sub(lines, " ", str(text or ""), flags=re.IGNORECASE))
_V2_FURNITURE_WORD_RE = re.compile(
    r"\b(?:ss|wooden|wood|steel|podium|cubical|cubicle|workstation|slide|modular|laminate|office)\b"
)
_V2_ELECTRONICS = frozenset({
    "tv", "monitor", "laptop", "printer", "phone", "mouse", "keyboard", "tablet", "scanner", "projector", "camera",
    "network", "server", "speaker", "charger", "power_bank", "docking_station",
})
# One kind of device that customers and photos name either way, never a conflict: a 2-in-1 or rugged tablet is a laptop
# or a tablet, a Galaxy Tab sits under "Mobile", a handheld terminal is a phone or a scanner ("Point Mobile"), a water
# cooler a refrigerator, a server and its rack are network gear. Used by every conflict test (families_disjoint); the
# alignments that lift an N still need a shared family. A printer and a scanner are companions, not kinds: an HP
# scanner registered "Printer" (AssetId 2684610, whose master data suggests "Scanner") is a genuine N.
_V2_ADJACENT = frozenset(frozenset(pair) for pair in (
    ("laptop", "tablet"), ("phone", "tablet"), ("phone", "scanner"),
    ("refrigerator", "water_dispenser"), ("server", "network"),
))
# Companions a customer files under the device they serve. Between the customer's own fields (Gate A) they are no
# contradiction: "Laptop / Lenovo ThinkBook Charger", "CCTV / SMPS", "CCTV / POE switch", "Air Conditioner /
# Stabilizer", "Server / UPS", "Television / Speaker", "Mini Docking Station / ThinkPad", "Printer / HP Scanner".
# Against the photo they are what they are: a power bank is no charger (AssetId 4411007).
_V2_COMPANIONS = frozenset(
    {frozenset(("charger", device)) for device in _V2_ELECTRONICS - {"charger"} | {"power_backup"}}
    | {frozenset(("stabilizer", device)) for device in
       ("ac", "refrigerator", "tv", "server", "network", "microwave", "water_dispenser", "power_backup")}
    | {frozenset(pair) for pair in (
        ("power_backup", "server"), ("power_backup", "network"), ("power_backup", "camera"), ("speaker", "tv"),
        ("speaker", "projector"), ("camera", "network"), ("docking_station", "laptop"), ("printer", "scanner"),
        ("docking_station", "tablet"), ("keyboard", "laptop"), ("keyboard", "tablet"), ("mouse", "laptop"),
        ("keyboard", "mouse"))}
)
# An inverter in an AC or refrigerator is its compressor drive, not a power backup ("1.5 TR inverter", "3 star Non
# Inverter", "AC / Daikin inverter").
_V2_APPLIANCE_CONTEXT = re.compile(
    r"\b(?:acs?|air ?condition\w*|split|window|cassette|ductable|tons?|tr|star|btu|vrf|vrv|non|refrigerators?"
    r"|fridges?|freezers?|washing|compressors?)\b"
)
_V2_GALAXY_COMPANY = re.compile(
    r"galaxy (?:industrial|industries|equipment|engineers|enterprises|pvt|ltd|tech|infra|electricals?|sivtek)\b"
)
_V2_MOBILE_MOVABLE = re.compile(_MOBILE_NOT_A_PHONE)
_V2_SLASH = "slashsep"
_V2_FITTED_WITH = frozenset({"with", "having"})
_V2_ASIDE_RE = re.compile(r"[(\[{]([^()\[\]{}]*)[)\]}]")
_V2_SEGMENT_BREAK = re.compile(r"[;:|()\[\]{}<>!?\n\r\t]+|(?<!\d)[.,]|[.,](?!\d)|\s[-–—]+\s*|[-–—]+\s")
_V2_REPLACEMENTS = (
    (re.compile(r"\bac\s*/\s*dc\b", re.IGNORECASE), " acdc "),
    (re.compile(r"\ba\s*/\s*c\b", re.IGNORECASE), " ac "),
    (re.compile(r"\bm\s*/\s*c\b", re.IGNORECASE), " machine "),
    (re.compile(r"\bt\s*\.\s*v\b\.?", re.IGNORECASE), " tv "),
    (re.compile(r"\bw\s*/\s*(?=\w)", re.IGNORECASE), " with "),
    # "Bio Safety Cabinet" is the laboratory cabinet "Biosafety Cabinet"; "bio" alone is no other sense ("Bio Locker").
    (re.compile(r"\bbio[\s-]*safety\b", re.IGNORECASE), " biosafety "),
)
# "TVS" is the brand (printers, scanners, cash counters: 56 master rows), "TVs" the plural.
_V2_TVS_RE = re.compile(r"\btvs\b", re.IGNORECASE)
_V2_TV_QUALIFIERS = frozenset("led lcd oled qled smart android flat branded other two three four several multiple many".split())
# Customer 1201 ends unrelated items with "- Camera" ("9URack- Camera", "SmartcomTexecom - Camera", "PIR motion senser
# - Camera 14"): after an item that names no camera and no brand, the suffix is a project label, not the type.
_V2_CAMERA_SUFFIX_RE = re.compile(r"^(?P<before>.*\S)\s*-\s*camera(?:\s*\d+)?\s*$", re.IGNORECASE)
_V2_CAMERA_WORD_RE = re.compile(r"cctv|camera|cam\b|dome|bullet|ptz|webcam|turret", re.IGNORECASE)
_V2_PROJECT_ITEM_RE = re.compile(r"rack|panel|pannel|siren|dialer|disk|switch|nvr|dvr|sens[eo]r|detector|pir\b",
                                 re.IGNORECASE)


class _TypeHit(NamedTuple):
    family: str
    words: tuple[str, ...]
    segment: int
    start: int
    end: int          # index of the last word of the match
    head: bool        # names the asset: not a modifier of another object's noun
    weak: bool        # a brand or product-line word


class _TypeReading(NamedTuple):
    families: frozenset[str]   # what the text names, after the context rules
    strong: frozenset[str]     # named by a word that is not a brand or product-line word
    weak_only: frozenset[str]  # named only by brand or product-line words
    served: frozenset[str]     # families of device words in front of another object ("laptop" in "laptop charger")
    inverter_only: bool        # power backup named by "inverter" alone
    laptop_only: bool          # a word that only means a laptop names one
    makers: tuple = ()         # (family, makers) a product line names only beside one of those makers' names


def _v2_prepare(text: str) -> str:
    def tvs(match: re.Match) -> str:
        before = match.string[max(0, match.start() - 24):match.start()].lower().split()
        if match.group(0) == "TVs" or (before and before[-1] in _V2_TV_QUALIFIERS):
            return match.group(0)
        return " tvsbrand "

    def camera_suffix(field: str) -> str:
        found = _V2_CAMERA_SUFFIX_RE.match(field)
        if not found:
            return field
        before = found.group("before")
        if _V2_CAMERA_WORD_RE.search(before) or extract_brand_keys(before):
            return field
        run_together = " " not in before.strip() and len(before.strip()) >= 12
        return before if run_together or _V2_PROJECT_ITEM_RE.search(before) else field

    text = ";".join(camera_suffix(field) for field in text.split(";"))
    for pattern, replacement in _V2_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return _V2_TVS_RE.sub(tvs, text)


_V2_CACHE_MAX_TEXT = 2000


def _v2_cached(fn):
    """lru_cache (4,096 texts) for texts of up to _V2_CACHE_MAX_TEXT characters; a longer text is read without the
    cache, so a long request field holds no memory after its request."""
    cached = functools.lru_cache(maxsize=4096)(fn)

    @functools.wraps(fn)
    def wrapper(text, *args):
        if isinstance(text, str) and len(text) > _V2_CACHE_MAX_TEXT:
            return fn(text, *args)
        return cached(text, *args)

    wrapper.cache_clear = cached.cache_clear
    wrapper.cache_info = cached.cache_info
    return wrapper


@_v2_cached
def _v2_segments(text: str) -> tuple[tuple[str, ...], ...]:
    """The text as phrases of words: split at punctuation BEFORE it is erased (6.1). "/" stays a word of its own;
    "&" and "+" read as "and"; a cost band glued to a word is split off ("EXTINGUISHERS5000"). An aside in brackets is
    read on its own and taken out of its phrase: "power over Ethernet (PoE) switch", "Dining Chair (box)"."""
    if not text or not text.strip():
        return ()
    prepared = _v2_prepare(text)
    asides = _V2_ASIDE_RE.findall(prepared)
    prepared = ";".join([_V2_ASIDE_RE.sub(" ", prepared), *asides])
    segments = []
    for piece in _V2_SEGMENT_BREAK.split(prepared):
        if not piece or not piece.strip():
            continue
        lowered = piece.casefold().replace("/", f" {_V2_SLASH} ")
        lowered = re.sub(r"[&+]", " and ", lowered)
        lowered = re.sub(r"[^\w\s]|_", " ", lowered)
        lowered = re.sub(r"\b([a-z]{4,})(\d+)\b", r"\1 \2", lowered)
        lowered = re.sub(r"\btv(\d)", r"tv \1", lowered)
        words = tuple(lowered.split())
        if words:
            segments.append(words)
    return tuple(segments)


def _v2_singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("es") and word[:-2] in _V2_OBJECT_HEADS:
        return word[:-2]      # boxes, switches, benches
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


# An ordinal starts a location, not a compound: "Speaker Phone 1st Cabin" is a phone in the first cabin.
_V2_ORDINAL_RE = re.compile(r"\d+(?:st|nd|rd|th)")


def _v2_following(words: tuple[str, ...], index: int) -> list[str]:
    """Up to three words from `index`, stopping at a connector, a "/" or an ordinal."""
    out = []
    for word in words[index:index + 3]:
        if word in _V2_CONNECTORS or word == _V2_SLASH or _V2_ORDINAL_RE.fullmatch(word):
            break
        out.append(word)
    return out


def _v2_is_modifier(family: str, words: tuple[str, ...], end: int) -> bool:
    """The family word ending at `end` stands in front of another object's noun inside its phrase."""
    parts = _V2_PARTS.get(family, frozenset())
    own = _V2_OWN_MODIFIER_HEADS.get(family, frozenset())
    pattern = _V2_PATTERNS.get(family)
    for word in _v2_following(words, end + 1):
        base = _v2_singular(word)
        if word in parts or base in parts or (pattern is not None and pattern.fullmatch(word)):
            continue
        if word in own or base in own or word in _V2_OBJECT_HEADS or base in _V2_OBJECT_HEADS:
            return True
    # "tablet/capsule SORTER", "TV/monitor STAND": a "/" shares the next item's noun. "Keyboard/Mouse" does not.
    if end + 1 < len(words) and words[end + 1] == _V2_SLASH:
        item = _v2_following(words, end + 2)
        if 2 <= len(item) <= 3:
            base = _v2_singular(item[-1])
            if (base in _V2_OBJECT_HEADS or base in own) and base not in parts:
                return True
    return False


def _v2_safe_is_head(words: tuple[str, ...], start: int, end: int) -> bool:
    """"safe" is a safe only as the head of its phrase: "Godrej safe", "safe locker", "a metal safe with a keypad";
    never "SAFE PRO" or "Safe ABC6KG" (extinguisher brands), "safe deposit", "it is safe to use", "Exide Power Safe"."""
    if start > 0 and words[start - 1] in _V2_SAFE_NOT_AFTER:
        return False
    for word in _v2_following(words, end + 1):
        if not (word in _V2_SAFE_COMPANIONS or _v2_singular(word) in _V2_SAFE_COMPANIONS):
            return False      # "Safe ABC6KG", "SAFE PRO-6KG": an extinguisher brand
    return True


def _v2_sense_removed(family: str, words: tuple[str, ...], start: int, end: int) -> bool:
    """The family word is used in another sense here (6.1)."""
    previous = words[start - 1] if start > 0 else ""
    following = words[end + 1] if end + 1 < len(words) else ""
    first = words[start]
    # A machine word takes the table away only from the word "table": "turn table", but a "Hydraulic Desk" is a desk.
    if previous in _V2_NOT_AFTER.get(family, ()) and (family != "table" or words[end].startswith("table")):
        return True
    if family == "table" and words[end].startswith("counter") and previous in _V2_COUNTER_NOT_AFTER:
        return True
    if family == "sofa" and words[end].startswith("couch") and previous in _V2_COUCH_NOT_AFTER:
        return True
    if family == "cabinet" and words[end] in ("safe", "safes"):
        return not _v2_safe_is_head(words, start, end)
    if family == "phone":
        if first == "mobile" and len(words) > end + 1 and _V2_MOBILE_MOVABLE.match(" ".join(words[end + 1:end + 3])):
            return True       # "Mobile Crane", "mobile pedestal": movable
        if first == "galaxy" and _V2_GALAXY_COMPANY.match(" ".join(words[start:start + 2])):
            return True       # "Galaxy Industrial Pvt Ltd"
    if family == "ac" and first in ("ac", "acs"):
        if following in _V2_AC_NOT_BEFORE or _V2_VOLTAGE_RE.fullmatch(following):
            return True       # "AC adapter", "AC motor", "AC 415V"
        if previous in _V2_AC_NOT_AFTER or _V2_VOLTAGE_RE.fullmatch(previous):
            return True       # "Input 100-240V AC"
    return False


@_v2_cached
def _v2_hits(text: str) -> tuple[_TypeHit, ...]:
    """Every family word in the text, read in its phrase."""
    hits = []
    for index, words in enumerate(_v2_segments(text)):
        joined = " ".join(words)
        for family, pattern in _V2_PATTERNS.items():
            for match in pattern.finditer(joined):
                start = joined.count(" ", 0, match.start())
                matched = tuple(match.group(0).split())
                end = start + len(matched) - 1
                if _v2_sense_removed(family, words, start, end):
                    continue
                weak_words = _V2_WEAK_WORDS.get(family, ())
                weak = len(matched) == 1 and (matched[0] in weak_words or _v2_singular(matched[0]) in weak_words)
                head = not (_v2_is_modifier(family, words, end) or _v2_fitted_to_object(words, start))
                hits.append(_TypeHit(family, matched, index, start, end, head, weak))
    return tuple(hits)


# Hosts that keep what they are sold with, though they head a phrase of their own (_V2_OBJECT_HEADS).
_V2_SOLD_WITH_HOSTS = frozenset({"computer", "computers", "pc", "pcs", "station", "stations", "workstation",
                                 "workstations"})


def _v2_fitted_to_object(words: tuple[str, ...], start: int) -> bool:
    """The family word is fitted to another object named just before it: "Weighing Balance WITH Printer", "12 U rack
    WITH internal power supply". A thing with no family of its own keeps what it is sold with ("Desktop with Monitor",
    "UPS 10KVA with batteries"), and so does a family's own item ("Laptop with charger" names both, as before). So do
    a computer, a PC and a work station ("Microtower PC with MONITOR", "Work station with pre lam table top")."""
    i = start - 1
    while i >= 0 and start - i <= 3 and words[i] not in _V2_CONNECTORS:
        i -= 1
    if i < 0 or words[i] not in _V2_FITTED_WITH:
        return False
    while i >= 0 and words[i] in _V2_CONNECTORS:
        i -= 1
    if i < 0:
        return False
    host = words[i]
    if host in _V2_SOLD_WITH_HOSTS:
        return False
    return (_v2_singular(host) in _V2_OBJECT_HEADS or host in _V2_OBJECT_HEADS) and not any(
        pattern.fullmatch(host) for pattern in _V2_PATTERNS.values())


# A speaker phone or conference phone is both a phone and a speaker ("Jabra speaker phone", "Polycom conference phone"
# for a Speaker registration).
_V2_SPEAKERPHONE_RE = re.compile(r"\b(?:speaker|conference|conferencing) ?phones?\b")


@_v2_cached
def _v2_reading_cached(text: str, form_factor_context: bool) -> _TypeReading:
    hits = _v2_hits(text)
    heads = [hit for hit in hits if hit.head]
    whole = " ".join(" ".join(words) for words in _v2_segments(text))
    brands = _v2_maker_brands(text)
    families = {hit.family for hit in heads}
    strong = {hit.family for hit in heads if not hit.weak}
    weak_only = families - strong
    makers: dict[str, set[str]] = {}
    for family in weak_only:
        weak_heads = [hit for hit in heads if hit.family == family]
        segments = {hit.segment for hit in weak_heads}
        if family == "tablet":
            yields_to = _FURNITURE_FAMILIES
            if _V2_FURNITURE_WORD_RE.search(whole):
                families.discard(family)  # "SS Office Tablet", "PODIUM TABLET"
                continue
        else:
            yields_to = _V2_ELECTRONICS
        if any(hit.segment in segments and hit.family != family and hit.family in yields_to and not hit.weak
               for hit in heads):
            families.discard(family)      # "Redmi TV", "Pixel Edge projector", "tablet chair"
            continue
        needed = {_V2_LINE_MAKERS.get(hit.words[0], _V2_LINE_MAKERS.get(_v2_singular(hit.words[0])))
                  for hit in weak_heads}
        # form_factor_context=False is the product-line reading of before (the identity rewrite): no maker needed.
        if form_factor_context and None not in needed and not needed & brands:
            families.discard(family)      # "Kent Galaxy", "Royal swift": no maker, no product line
            makers[family] = needed
    backup = [hit for hit in heads if hit.family == "power_backup"]
    inverter_only = bool(backup) and all(hit.words[0].startswith("inverter") for hit in backup)
    if inverter_only and _V2_APPLIANCE_CONTEXT.search(whole):
        families.discard("power_backup")
    laptop_only = any(hit.family == "laptop" and _LAPTOP_ONLY_WORDS.fullmatch(" ".join(hit.words)) for hit in heads)
    if form_factor_context and "laptop" in families and not laptop_only and _NOT_A_LAPTOP_CONTEXT.search(whole):
        families.discard("laptop")
    if "phone" in families and _V2_SPEAKERPHONE_RE.search(whole):
        families.add("speaker")
    served = {hit.family for hit in hits if not hit.head}
    return _TypeReading(frozenset(families), frozenset(strong), frozenset(weak_only), frozenset(served),
                        inverter_only, laptop_only, tuple(sorted((k, frozenset(v)) for k, v in makers.items())))


def _v2_reading(text: Any, *, form_factor_context: bool = True) -> _TypeReading:
    """The families a text names, read by the head noun (6.1)."""
    return _v2_reading_cached(str(text or ""), bool(form_factor_context))


def _v2_user_field_readings(req_body: dict) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(families, served devices) per classification field, each read in the context of the whole registration.

    As before 2026-11-02, a laptop line in a desktop, CPU or car registration is no laptop, and a trusted category
    (6.6) counts only as context that takes a reading away. Read the same way: an inverter in an AC or refrigerator
    registration is no power backup ("AC / Daikin inverter"), and a brand or product-line word is no evidence when the
    registration names another electronic device ("TV / Redmi", "Laptops / Samsung Galaxy Book").
    """
    makemodel = str(req_body.get("makemodel") or req_body.get("makemodelname") or "").strip()
    texts = {
        "category": str(req_body.get("categoryname") or ""),
        "subcategory": str(req_body.get("subcategoryname") or ""),
        "makemodel": makemodel,
    }
    readings = {field: _v2_reading(text) for field, text in texts.items()}
    families = {field: set(reading.families) for field, reading in readings.items()}
    served = {field: set(reading.served) for field, reading in readings.items()}
    if industrial_record(req_body):
        # Industrial records are never judged by the everyday families of 2026-11-02 (owner, 2026-11-01): a "Camera"
        # inspection system on a production line, a bottle "Stabilizer" mechanism, a "UPS DB PANEL".
        for types in (*families.values(), *served.values()):
            types &= _LEGACY_FAMILIES
    whole = " ".join(" ".join(words) for words in _v2_segments(_join_texts(*texts.values())))
    judged = ("subcategory", "makemodel") if category_trusted() else tuple(texts)
    if _NOT_A_LAPTOP_CONTEXT.search(whole) and not any(readings[field].laptop_only for field in judged):
        for types in families.values():
            types.discard("laptop")
    if _V2_APPLIANCE_CONTEXT.search(whole):
        for field, reading in readings.items():
            if reading.inverter_only:
                families[field].discard("power_backup")
    brands = _v2_maker_brands(_join_texts(*texts.values()))
    for field, reading in readings.items():
        for family, needed in reading.makers:
            if needed & brands:
                families[field].add(family)   # "Samsung" in one field, "Galaxy A14" in another
    strong = set().union(*(reading.strong for reading in readings.values()))
    for field, reading in readings.items():
        for family in reading.weak_only:
            yields_to = _FURNITURE_FAMILIES if family == "tablet" else _V2_ELECTRONICS
            if (strong & yields_to) - {family}:
                families[field].discard(family)
    return families, served


def industrial_record(record: dict | None) -> bool:
    """The record is classed industrial by the customer's own category and asset class (helpers.asset_scope, docs
    6.7), as every rule kept for everyday records classes it: plant, machinery, P&M, civil works, installed plant, and
    heavy plant equipment the record names wherever it is filed. asset_scope never raises; a record it cannot class is
    industrial, so it keeps the families of before 2026-11-02 (fail safe)."""
    from asset_validator.utils.helpers import asset_scope

    return asset_scope(record or {}) == "industrial"


def scoped_families(families, record: dict | None) -> set[str]:
    """The families a rule may judge this record by: for an industrial record (5.6), only those of before 2026-11-02.
    The reading of before has no others, so with PRODUCT_TYPES_V2_ENABLED off nothing changes."""
    families = set(families or ())
    return families & _LEGACY_FAMILIES if product_types_v2() and industrial_record(record) else families


def _v2_related(a, b, pairs) -> bool:
    return any(frozenset((x, y)) in pairs for x in a for y in b)


def families_disjoint(a, b) -> bool:
    """Both sides name a product family and none is shared: the conflict test of 6.1.

    With PRODUCT_TYPES_V2_ENABLED, adjacent kinds of one device (a laptop and a tablet, a server and its rack) are no
    conflict either. The families of before 2026-11-02 have no adjacent kinds, so their test is unchanged.
    """
    if not (a and b) or set(a) & set(b):
        return False
    return not (product_types_v2() and _v2_related(a, b, _V2_ADJACENT))


def _asset_label_text(output: dict | None, gemini_output: dict | None = None) -> str:
    """What names the tagged asset itself: the detected-asset label and the phase-1 identity candidates, never the
    description, which also names whatever else is in frame (6.1)."""
    if not output:
        return ""
    parts = [output.get("detectedAsset")]
    reasoning = (gemini_output or {}).get("inspection_reasoning")
    if isinstance(reasoning, dict):
        parts += [c.get("label") for c in reasoning.get("identity_candidates") or [] if isinstance(c, dict)]
    return _join_texts(*parts)


def families_ruled_out(families, photo_families, label_families=None) -> bool:
    """The photo rules these product families out (6.1): it names a product type and shares none of them.

    With PRODUCT_TYPES_V2_ENABLED, a family added on 2026-11-02 is ruled out only by what the asset's own label names
    (`label_families`): the description may support it but never rule it out ("a water cooler is visible in the
    background" of a bench photo, "two fire extinguishers are mounted below" a wall rack). The families of before are
    ruled out by the label or the description, as before. Off, or without a label, the whole photo decides, as before.
    """
    families = set(families or ())
    named = set(photo_families or ())
    if not families_disjoint(families, named):
        return False
    if label_families is None or not product_types_v2():
        return True
    against = set(label_families)
    if families & _LEGACY_FAMILIES:
        against |= named & _LEGACY_FAMILIES
    return families_disjoint(families, against)


def _v2_fields_conflict(a: set, a_served: set, b: set, b_served: set) -> bool:
    """Two of the customer's own fields contradict each other: disjoint families, and neither is an accessory or a
    companion of the device the other names ("Laptop" / "HP laptop charger 65W", "CCTV" / "SMPS")."""
    return (families_disjoint(a, b)
            and not (set(a) & set(b_served) or set(a_served) & set(b))
            and not _v2_related(a, b, _V2_COMPANIONS))


def _v2_types_conflict(a: _TypeReading, b: _TypeReading) -> bool:
    return _v2_fields_conflict(a.families, a.served, b.families, b.served)


def _v2_modifier_words(text: str) -> set[str]:
    """The family words that stand in front of another object's noun ("laptop" in "HP laptop power adapter")."""
    return {word for hit in _v2_hits(str(text or "")) if not hit.head for word in hit.words}


def _v2_registered_word_in_frame(values: list, frame_text: str) -> bool:
    """A registered family word, inflected, is in the frame as the noun of its phrase (6.1, Gate B).

    "Patient Monitor" is supported by "patient monitoring system"; "Laptop keyboard" registers a keyboard, so the
    "laptops" of a frame vouch for nothing, nor does the "printer" of a "printer table". A word of several words keeps
    them all ("water dispenser", not every "dispenser"), unless its last word alone names the family ("office chair").
    """
    targets: list[tuple[str, tuple[str, ...]]] = []
    for value in values:
        for hit in _v2_hits(str(value or "")):
            if not hit.head:
                continue
            last = hit.words[-1]
            last = re.sub(r"s$", "", last) if len(last) > 3 else last
            pattern = _V2_PATTERNS[hit.family]
            words = (last,) if (len(hit.words) == 1 or pattern.fullmatch(hit.words[-1])) else (*hit.words[:-1], last)
            targets.append((hit.family, words))
    if not targets:
        return False
    for words in _v2_segments(str(frame_text or "")):
        for family, target in targets:
            size = len(target)
            for i in range(len(words) - size + 1):
                if words[i:i + size - 1] != target[:-1]:
                    continue
                if not re.fullmatch(re.escape(target[-1]) + _INFLECTION, words[i + size - 1]):
                    continue
                if _v2_sense_removed(family, words, i, i + size - 1) or _v2_is_modifier(family, words, i + size - 1):
                    continue
                return True
    return False


def _v2_names_it_device(text: str) -> bool:
    """An IT device word names the asset as the noun of its phrase (5.5): "Laptop bag", "Printer cartridge" and a
    pharma "TABLET COATING M/C" name none, and "10 PCS" is a quantity. A word of a family is read with that family's
    parts ("Printer Label Brother PT-P900W" is a label printer); computer, PC, CPU and desktop with the IT parts."""
    for words in _v2_segments(str(text or "")):
        joined = " ".join(words)
        for match in _IT_DEVICE_RE.finditer(joined):
            start = joined.count(" ", 0, match.start())
            matched = match.group(0).split()
            end = start + len(matched) - 1
            if matched[-1] == "pcs" and start > 0 and re.fullmatch(r"\d[\w]*|nos?|qty|x", words[start - 1]):
                continue
            family = next((key for key, pattern in _V2_PATTERNS.items() if pattern.fullmatch(match.group(0))), "it")
            if not _v2_is_modifier(family, words, end):
                return True
    return False


# Kinds of one family that the rescues never take for each other (owner's hand-check, 3 Nov 2026; docs 6.1). A safe is
# the same kind only as a safe: a safe, cash locker or digital safe, never a storage cabinet, a locker cabinet, an
# almirah or a cupboard ("Storage cabinet" and "Locker Cabinert of 16G construction with key" on a metal safe were
# lifted by the name rescue). A bare "locker" is either: "Safilo Bio-2 locker" is a safe, a bank of staff "lockers" is
# not. So is a "safe locker": customer 1063's SAFE LOCKER subcategory holds safes and staff locker cabinets alike. A
# staff, shoe, tiffin, steel or key locker is a storage locker. Network devices are told apart: a firewall, a switch, a
# wireless device (router, modem, access point), a patch panel and a rack (AssetId 1728350, a Cisco switch photographed
# for "Fire Wall Installation" / Firewall, and 4403975, access points photographed as a network rack, were lifted by
# the same-family rescue). A fire ball is no cylinder extinguisher: pending the owner's decision the rescues keep them
# apart, read from the whole text (ball, spherical vs cylinder, hose, nozzle, CO2, powder, foam); a text naming both or
# neither is either. The gates are unchanged: a SAFE LOCKER subcategory still fits a safe photo, and a vault names no
# family.
_V2_SAFE_LOCKER_BEFORE = frozenset({"cash", "digital", "electronic"})
_V2_STORAGE_LOCKER_BEFORE = frozenset({"staff", "storage", "employee", "employees", "shoe", "shoes", "tiffin", "tiffen",
                                       "apron", "canteen", "ss", "ms", "wooden", "steel", "pvc", "key", "bedside",
                                       "gym"})


def _v2_rescue_kinds(family: str, words: tuple[str, ...], hit: _TypeHit) -> set[str]:
    word = _v2_singular(hit.words[-1])
    if family == "cabinet":
        if word == "safe":
            return {"safe"}
        if word != "locker":
            return {"storage"}
        previous = words[hit.start - 1] if hit.start > 0 else ""
        following = words[hit.end + 1] if hit.end + 1 < len(words) else ""
        if previous in _V2_SAFE_LOCKER_BEFORE:
            return {"safe"}
        if previous in _V2_STORAGE_LOCKER_BEFORE or following.startswith("cabine"):    # "Locker Cabinert"
            return {"storage"}
        return {"safe", "storage"}
    if family == "network":
        if word in ("firewall", "switch"):
            return {word}
        if word in ("router", "modem", "point"):
            return {"wireless"}
        if word == "panel":
            return {"patch panel"}
        if word in ("rack", "cabinet"):
            return {"rack"}
        return {"network"}
    if family == "fire_extinguisher":
        return {"ball"} if any(w.startswith("ball") for w in hit.words) else {"ball", "cylinder"}
    return {family}


_V2_KIND_FAMILIES = frozenset({"cabinet", "network", "fire_extinguisher"})
_V2_BALL_RE = re.compile(r"\b(?:balls?|spherical|spheres?)\b")
_V2_CYLINDER_RE = re.compile(
    r"\b(?:cylinders?|cylindrical|hoses?|nozzles?|gauges?|co2|carbon dioxide|powder|dcp|abc|foam)\b")


def _v2_kinds(text: str, family: str) -> set[str]:
    """The kinds of `family` that the text names as the noun of a phrase. For a fire extinguisher, the whole text
    decides: ball words and no cylinder words make a ball, the reverse a cylinder; both or neither leave either."""
    segments = _v2_segments(str(text or ""))
    kinds = {kind for hit in _v2_hits(str(text or "")) if hit.head and hit.family == family
             for kind in _v2_rescue_kinds(family, segments[hit.segment], hit)}
    if family == "fire_extinguisher" and kinds:
        whole = " ".join(" ".join(words) for words in segments)
        ball, cylinder = bool(_V2_BALL_RE.search(whole)), bool(_V2_CYLINDER_RE.search(whole))
        if ball != cylinder:
            kinds = {"ball"} if ball else {"cylinder"}
    return kinds


def same_kind_families(shared, text_a: str, text_b: str) -> set[str]:
    """The shared families both texts name as the same kind (6.1, the name and same-family rescues): a family whose
    kinds the two texts name apart is no evidence that one is the other. Off, or for an industrial record, every
    shared family counts, as before."""
    shared = set(shared or ())
    if not product_types_v2():
        return shared
    kept = set()
    for family in shared:
        if family in _V2_KIND_FAMILIES:
            left, right = _v2_kinds(text_a, family), _v2_kinds(text_b, family)
            if left and right and not left & right:
                continue
        kept.add(family)
    return kept


def model_conflicts_with_subcategory(user_model: str, user_subcategory: str) -> bool:
    """True when user Model product type clearly differs from user Subcategory.

    Read by the head noun (6.1), an accessory under the device it serves is no conflict: "Laptop charger 65W" under
    "Laptop" and "Battery 12V 7AH" under "UPS" stay in the prompt.
    """
    if product_types_v2():
        return _v2_types_conflict(_v2_reading(user_model), _v2_reading(user_subcategory))
    model_cats = _detect_product_categories(user_model)
    sub_cats = _detect_product_categories(user_subcategory)
    if not model_cats or not sub_cats:
        return False
    return not (model_cats & sub_cats)


def resolve_user_model_for_subcat_check(user_model: str, user_subcategory: str) -> str:
    """Omit Model from subcat check when it belongs to a different product type than Subcategory."""
    model = str(user_model or "").strip()
    if model and model_conflicts_with_subcategory(model, user_subcategory):
        return ""
    return model


def _meaningful_alignment_tokens(text: str) -> set[str]:
    norm = normalize_classification_value(text)
    if not norm:
        return set()
    tokens: set[str] = set()
    for token in norm.split():
        if token in _ALIGNMENT_STOP_TOKENS:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        tokens.add(token)
    return tokens


def _classification_tokens_align_with_vision(
    user_subcategory: str,
    user_makemodel: str,
    detected_text: str,
    *,
    min_overlap: int = 2,
) -> bool:
    """Semantic token overlap for equipment not in product taxonomy (e.g. fire extinguisher)."""
    user_tokens = _meaningful_alignment_tokens(
        f"{user_subcategory} {user_makemodel}".strip()
    )
    detected_tokens = _meaningful_alignment_tokens(detected_text)
    if not user_tokens or not detected_tokens:
        return False

    overlap = set(user_tokens & detected_tokens)
    for user_token in user_tokens:
        for detected_token in detected_tokens:
            if len(user_token) >= 4 and len(detected_token) >= 4:
                if user_token in detected_token or detected_token in user_token:
                    overlap.add(user_token)
                    overlap.add(detected_token)
    return len(overlap) >= min_overlap


def _subcategory_aligns_with_vision(
    user_subcategory: str,
    detected_text: str,
    user_makemodel: str = "",
    *,
    same_kind: bool = False,
) -> bool:
    """`same_kind` (the same-family rescue): a family the two sides name as different kinds does not align (6.1): a
    Firewall subcategory on a Cisco network switch. The gates leave it off."""
    sub = normalize_classification_value(user_subcategory)
    if not sub or not detected_text:
        return False
    sub_cats = _detect_product_categories(sub)
    detected_cats = _detect_product_categories(detected_text)
    # Adjacent kinds of one device (6.1) are left to the word comparison below, as an untyped side is.
    if families_disjoint(sub_cats, detected_cats):
        return False
    if sub_cats and detected_cats and (sub_cats & detected_cats):
        return not same_kind or bool(same_kind_families(sub_cats & detected_cats, sub, detected_text))
    detected_norm = normalize_classification_value(detected_text)
    if sub in detected_norm or detected_norm in sub:
        return True
    return _classification_tokens_align_with_vision(
        user_subcategory, user_makemodel, detected_text
    )


def reconcile_subcatmodel_with_vision(output: dict, req_body: dict) -> None:
    """
    Upgrade false N when user Subcategory/Make-Model align with committed vision.
    Image-based check — ignores asset name and stale Model fields.
    Refuses upgrade when Gate A (internal types) or Gate B (unsupported type) would fail —
    gates that leave a trusted category out (docs 6.6), so it never blocks an upgrade either.
    """
    if output.get("error") or output.get("subcatmodelmatch") != "N":
        return

    detected_text = _vision_detection_text(output, None)
    user_sub = str(req_body.get("subcategoryname") or "").strip()
    user_mm = str(req_body.get("makemodel") or req_body.get("makemodelname") or "").strip()
    if not detected_text or not user_sub:
        return

    # Gate A / B — never upgrade junk or unsupported known types
    if _internal_classification_types_conflict(req_body):
        return
    if _known_user_types_unsupported_by_vision(req_body, detected_text, output):
        return


    frame_text = _join_texts(detected_text, _visible_asset_text(output))
    if not _subcategory_aligns_with_vision(user_sub, frame_text, user_mm, same_kind=True):
        return

    if user_mm and _product_category_conflict(user_mm, detected_text):
        return

    detected_cats = _detect_product_categories(detected_text)
    mm_cats = _detect_product_categories(user_mm) if user_mm else set()
    if families_ruled_out(mm_cats, detected_cats, _detect_product_categories(_asset_label_text(output))):
        return

    # Stale Model is ignored for upgrade — never fail reconcile solely on Model.
    output["subcatmodelmatch"] = "Y"
    output["subcatmodelmatchpercent"] = 80
    output["subcatmodelreasoning"] = _build_subcatmodel_pass_reasoning(req_body, output)


def _subcategory_product_type_conflicts_with_vision(
    user_subcategory: str, detected_text: str, req_body: dict | None = None, label_text: str | None = None
) -> bool:
    """True when user subcategory product type does not match vision (e.g. laptop vs chair).

    Read by the head noun (6.1), the registered word counted when inflected vouches here as it does for Gate B, so a
    family that only a scene word supplies cannot fail what Gate B would accept: "Patient Monitor" against a "patient
    monitoring system ... beside a UPS".
    """
    v2 = product_types_v2()
    mm_cats: set = set()
    if v2 and req_body is not None:
        # Read in its registration, as Gate A and Gate B read it (6.1): context, brand words, industrial records.
        fields = _user_field_product_types(req_body)
        sub_cats, mm_cats = fields["subcategory"], fields["makemodel"]
    else:
        sub_cats = _detect_product_categories(user_subcategory)
    detected_cats = _detect_product_categories(detected_text)
    label_cats = _detect_product_categories(label_text) if label_text is not None else None
    if not families_ruled_out(sub_cats, detected_cats, label_cats):
        return False
    # A companion filed under the device it serves ("CCTV" / "POE switch", "Server" / "UPS") photographed as the
    # companion its make/model names: no conflict, when the reading of before would not fail it either (6.1).
    if (v2 and mm_cats and _v2_related(sub_cats, mm_cats, _V2_COMPANIONS)
            and not families_ruled_out(mm_cats, detected_cats, label_cats)):
        with _reading_of_before():
            if not (_subcategory_product_type_conflicts_with_vision(user_subcategory, detected_text, req_body,
                                                                    label_text)
                    or _gate_b_unsupported(req_body, detected_text)):
                return False
    return not (v2 and _v2_registered_word_in_frame([user_subcategory], detected_text))


def _classnumber_product_type_conflicts_with_vision(
    classnumber: str, detected_text: str
) -> bool:
    """True when classnumber product type does not match vision (e.g. LAPTOP vs chair)."""
    class_cats = _detect_product_categories(classnumber)
    detected_cats = _detect_product_categories(detected_text)
    if not class_cats or not detected_cats:
        return False
    return not (class_cats & detected_cats)


def _build_classnumber_vision_conflict_reasoning(
    classnumber: str,
    output: dict,
    detected_text: str,
    *,
    max_words: int = 25,
) -> str:
    """Concise N verdict when user classnumber product type conflicts with vision."""
    class_label = str(classnumber or "").strip() or "user classnumber"
    asset_label = _vision_asset_label(output, detected_text)
    message = (
        f"N: classnumber {class_label} conflicts with {asset_label} "
        f"in image — product type mismatch."
    )
    words = message.split()
    if len(words) > max_words:
        message = " ".join(words[:max_words])
    return message


def _product_category_conflict(name_text: str, detected_text: str) -> bool:
    """True when name and vision imply incompatible product categories (e.g. TV vs monitor)."""
    return _families_conflict(_detect_product_categories(name_text), _detect_product_categories(detected_text))


def _families_conflict(name_cats: set[str], detected_cats: set[str]) -> bool:
    """The listed pairs of incompatible families, one direction each (6.1). The families of 2026-11-02 take part
    only through the chair clause: no pair was added for them."""
    if not name_cats or not detected_cats:
        return False
    if name_cats & detected_cats:
        return False
    incompatible = (
        ("tv" in name_cats and "monitor" in detected_cats)
        or ("monitor" in name_cats and "tv" in detected_cats)
        or ("laptop" in name_cats and "tv" in detected_cats)
        or ("tv" in name_cats and "laptop" in detected_cats)
        or ("laptop" in name_cats and "monitor" in detected_cats and "laptop" not in detected_cats)
        or ("printer" in name_cats and "monitor" in detected_cats)
        or ("chair" in name_cats and detected_cats - {"chair"})
    )
    return bool(incompatible)


def enforce_makemodel_vs_subcategory_contradiction(
    output: dict,
    req_body: dict,
) -> None:
    """
    Downgrade subcatmodelmatch on internal type contradictions or vision type conflicts.

    Gate A runs even without vision text. Brand-only mismatches do not fail.
    Stale Model is excluded from internal pairwise checks, and a trusted category (docs 6.6)
    from every check here.
    """
    if output.get("error") or output.get("subcatmodelmatch") != "Y":
        return

    # Gate A — internal known-type contradictions
    conflict = _find_internal_type_conflict(req_body)
    if conflict:
        left_field, left_val, right_field, right_val = conflict
        left_label = " ".join((left_val or left_field).split()[:3])
        right_label = " ".join((right_val or right_field).split()[:3])
        _downgrade_subcatmodel_with_note(
            output,
            "",
            20,
            reasoning=(
                f"N: {left_field} {left_label} conflicts with {right_field} {right_label}"
                f" — product type mismatch."
            ),
        )
        return

    user_sub = str(req_body.get("subcategoryname") or "").strip()
    user_mm = str(req_body.get("makemodel") or req_body.get("makemodelname") or "").strip()
    detected_text = _with_furniture_under_devices(
        _vision_detection_text(output, None), req_body, output.get("detectedAsset") or ""
    )
    if not detected_text:
        return

    # Gate B safety net (in case earlier enforcers were skipped)
    if _known_user_types_unsupported_by_vision(req_body, detected_text, output):
        claim = _user_claim_product_types(req_body)
        claim_label = _family_words(claim)
        asset_label = _vision_asset_label(output, detected_text)
        _downgrade_subcatmodel_with_note(
            output,
            "",
            20,
            reasoning=(
                f"N: classification {claim_label} conflicts with {asset_label}"
                f" — product type mismatch."
            ),
        )
        return

    detected_cats = _detect_product_categories(detected_text)
    # Read make/model within the whole registration, as Gate A and Gate B do, so a laptop line
    # registered under a desktop subcategory is not a laptop here either.
    mm_cats = _user_field_product_types(req_body)["makemodel"] if user_mm else set()

    label_text = _asset_label_text(output)

    def _mm_conflicts_with_vision() -> bool:
        return families_ruled_out(mm_cats, detected_cats, _detect_product_categories(label_text))

    def _companion_kept_by_before() -> bool:
        """A companion filed under its device ("Mini Docking Station" / "ThinkPad") that the reading of before would
        not fail either (6.1)."""
        if not (product_types_v2() and _v2_related(_user_field_product_types(req_body)["subcategory"], mm_cats,
                                                   _V2_COMPANIONS)):
            return False
        with _reading_of_before():
            before = _user_field_product_types(req_body)["makemodel"] if user_mm else set()
            return not _gate_b_unsupported(req_body, detected_text, output) and not families_ruled_out(
                before, _detect_product_categories(detected_text), _detect_product_categories(label_text))

    if user_sub and _subcategory_aligns_with_vision(user_sub, detected_text, user_mm):
        if _mm_conflicts_with_vision() and not _companion_kept_by_before():
            mm_label = " ".join(user_mm.split()[:3])
            asset_label = _vision_asset_label(output, detected_text)
            _downgrade_subcatmodel_with_note(
                output,
                "",
                20,
                reasoning=(
                    f"N: makemodel {mm_label} conflicts with {asset_label}"
                    f" — product type mismatch."
                ),
            )
        return

    if user_sub and _subcategory_product_type_conflicts_with_vision(user_sub, detected_text, req_body, label_text):
        sub_label = " ".join(user_sub.split()[:3])
        asset_label = _vision_asset_label(output, detected_text)
        _downgrade_subcatmodel_with_note(
            output,
            "",
            20,
            reasoning=(
                f"N: subcategory {sub_label} conflicts with {asset_label}"
                f" — product type mismatch."
            ),
        )
        return

    if _mm_conflicts_with_vision():
        mm_label = " ".join(user_mm.split()[:3])
        asset_label = _vision_asset_label(output, detected_text)
        _downgrade_subcatmodel_with_note(
            output,
            "",
            20,
            reasoning=(
                f"N: makemodel {mm_label} conflicts with {asset_label}"
                f" — product type mismatch."
            ),
        )


def enforce_classification_match_percent_bounds(output: dict) -> None:
    """Clamp category/subcategory/model match percents to matched/failed ranges."""
    pairs = (
        ("categorymatch", "categorymatchpercent"),
        ("subcategorymatch", "subcategorymatchpercent"),
        ("makemodelmatch", "makemodelmatchpercent"),
        ("modelmatch", "modelmatchpercent"),
    )
    for match_key, pct_key in pairs:
        match_val = output.get(match_key)
        pct = output.get(pct_key)
        if match_val is None:
            output[pct_key] = None
            continue
        if not isinstance(pct, (int, float)):
            if match_val == "matched":
                output[pct_key] = 85
            elif match_val == "failed":
                output[pct_key] = 15
            continue
        pct_int = int(pct)
        if match_val == "matched":
            output[pct_key] = min(100, max(85, pct_int))
        elif match_val == "failed":
            output[pct_key] = min(40, max(10, pct_int))
