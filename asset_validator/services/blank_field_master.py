"""What master data says about a field the customer left blank (docs 6.5; owner, 2026-11-02).

WHY THIS EXISTS. The owner, 2 Nov 2026: "search the detected asset under that category under the same customer id and
recommend them", and "if you are flipping the result make sure you got the best match in master data; many times the
user fills all data correctly when this is blank". A blank subcategory or make/model had nothing to compare, so the
record passed and master data's pick was thrown away on the way out (2.2): matrix case 04 (AssetId 999100004), a
12V 65AH Quanta battery whose make/model id is filed only under BATTERIES5000, passed with its subcategory blank.

So a blank field is looked up with what the photo shows, inside the customer's category and, once the make/model check
approved it, the filled field's own id (pipeline_service._blank_field_lookup_filter). Then:
- a pass shows master data's best match for the blank field, only where the photo supports it (`recommendation`);
- the classification fails only on a certain match, with that row as the correction (`certain_row`).

Certain means no competitor could hide and the evidence names one row:
1. Every row the filter allows came back (fewer than top_k), and all of them are inspected, not only those the
   selection was shown.
2. A blank subcategory beside a filled make/model: master data files that make/model id under one subcategory.
3. A blank make/model: exactly one make/model is named, word for word, by the photo text; it carries a rating or model
   number; no more specific variant the photo cannot confirm exists; it sits under one subcategory; and the
   selection picked it too (the caller checks the pick). With both fields blank, the photo must also support that
   subcategory, and no equal or more specific make/model may sit under another one (cost brackets such as
   BATTERIES / BATTERIES5000 / BATTERIES<5000 cannot be told apart from a photo).

Everything is a word test on the rows the one lookup returned: no extra call or query. A brand alone ("HP", "VOLTAS"
in customer 1278's brand-level catalogue) is not an identity: that is the strict variant, kept while the owner decides
whether a brand alone may fail a record (HANDOFF, 2 Nov 2026).
"""

from __future__ import annotations

import re
from typing import NamedTuple

from asset_validator.utils.classification_match import (
    _detect_product_categories,
    _join_texts,
    extract_brand_keys,
    families_disjoint,
    families_ruled_out,
)
from asset_validator.utils.helpers import normalize_user_description

# "65 AH" and "65AH" are one word ("65ah"); "4.5 kg" is "4p5kg", so a decimal survives the split.
_UNITS = (
    "ah|v|kva|va|w|kw|mah|ton|tons|tr|hp|inch|inches|in|mm|cm|gb|tb|ltr|l|kg|kgs|mtr|ft|amp|amps|cc"
    "|lph|cfm|ml|mp|ch|seater|step|steps|tier|hz|ltrs|litre|liter"
)
_DECIMAL_RE = re.compile(r"(\d)\.(\d)")
_NOT_A_WORD_RE = re.compile(r"[^a-z0-9]+")
_WORD_GLUED_TO_NUMBER_RE = re.compile(r"([a-z]{3,})(\d)")       # "amaron12v" -> "amaron 12v"
# "12v100ah" -> "12v 100ah", but a decimal stays whole: split at its "p", "4.5 kg" read as "4p" and "5kg" would let
# a 5 kg row pass for a photo of a 4.5 kg extinguisher.
_RATING_GLUED_TO_NUMBER_RE = re.compile(r"(\d(?!p\d)[a-z]{1,3})(\d)")
_NUMBER_THEN_UNIT_RE = re.compile(r"\b(\d+(?:p\d+)?)\s+(" + _UNITS + r")\b")

_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "for", "with", "without", "in", "on", "to", "by", "at", "from", "make",
    "made", "model", "type", "series", "set", "unit", "no", "nos", "pc", "pcs", "piece", "item", "qty", "size",
    "new", "old", "is", "are", "its", "it", "this", "that", "be", "has", "have", "visible", "image", "photo",
})
# A make/model that names nothing is never a suggestion, a competitor or a certain match: customer 1063 alone has 320
# "NA" rows.
_PLACEHOLDERS = frozenset({
    "na", "n a", "nil", "none", "null", "other", "others", "misc", "miscellaneous", "general", "generic", "local",
    "assembled", "standard", "normal", "default", "unknown", "test", "testing", "dummy", "-", "",
})
# Colours, materials and sizes describe a thing; they do not say which subcategory it is filed under.
_DESCRIPTIVE_WORDS = frozenset({
    "black", "white", "grey", "gray", "silver", "blue", "red", "green", "brown", "yellow", "orange", "steel", "metal",
    "wooden", "wood", "plastic", "glass", "mesh", "ss", "ms", "office", "big", "small", "large", "medium", "mini",
    "heavy", "duty", "light", "colour", "color", "colored", "coloured", "top", "side", "front", "back",
})


def _split(text) -> list[str]:
    text = _DECIMAL_RE.sub(r"\1p\2", str(text or "").casefold())
    text = _NOT_A_WORD_RE.sub(" ", text)
    text = _WORD_GLUED_TO_NUMBER_RE.sub(r"\1 \2", text)
    text = _RATING_GLUED_TO_NUMBER_RE.sub(r"\1 \2", text)
    return _NUMBER_THEN_UNIT_RE.sub(r"\1\2", text).split()


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss") and not any(c.isdigit() for c in word):
        return word[:-1]
    return word


def words(text) -> list[str]:
    """The words a make/model and the photo text are compared in: casefolded, decimals kept, a number joined to its
    unit, a word split from a number glued to it, plurals made singular."""
    return [_singular(word) for word in _split(text)]


def identity_words(makemodel) -> frozenset[str]:
    """The words that say which product a make/model is: stop words and single letters left out."""
    return frozenset(word for word in words(makemodel) if word not in _STOP_WORDS and len(word) > 1)


def is_placeholder(makemodel) -> bool:
    """NA, N/A, Others, Assembled, Local, "-" and the like name nothing; nor does a make/model of stop words."""
    return " ".join(_split(makemodel)) in _PLACEHOLDERS or not identity_words(makemodel)


def is_identity(makemodel) -> bool:
    """A make/model that names one product: it carries a rating or model number ("12V 65AH", "Latitude 5420", "G10"),
    or it is a known brand ("HP", "VOLTAS").

    The owner, 2 Nov 2026: "if we find that thing HP in master data under the same category then fail and show
    recommendations". In customer 1278's catalogue "HP" is a make/model under DESKTOP, LAPTOPS, SCANNERS and SERVER;
    the other checks still hold it back where that leaves more than one subcategory. A type never names one product
    ("Office chair", "Wall mounted fan", "signs"). BLANK_FIELD_BRAND_IS_IDENTITY_ENABLED off is the strict variant:
    a rating or model number only.
    """
    from asset_validator.core.config import BLANK_FIELD_BRAND_IS_IDENTITY_ENABLED

    if any(any(c.isdigit() for c in word) for word in identity_words(makemodel)):
        return True
    return BLANK_FIELD_BRAND_IS_IDENTITY_ENABLED and bool(extract_brand_keys(str(makemodel or "")))


def covered(row: dict, seen) -> bool:
    """Every word of the row's make/model is in `seen`, and the make/model names something."""
    makemodel = (row or {}).get("makemodel")
    return not is_placeholder(makemodel) and identity_words(makemodel) <= set(seen)


def product_types(text) -> set[str]:
    """The known product families (6.1) a text names."""
    return _detect_product_categories(str(text)) if str(text or "").strip() else set()


class PhotoTypes(NamedTuple):
    """The photo's product families (6.1): all that its label and description name, and those its asset label names."""
    named: frozenset
    label: frozenset


def _named(photo_types) -> set:
    return set(photo_types.named if isinstance(photo_types, PhotoTypes) else photo_types or ())


def fits_photo(row: dict, photo_types) -> bool:
    """The row's product type does not contradict the photo's: the families of 6.1, read from the row's subcategory
    and make/model as 6.2 reads them. An untyped row or photo fits. With the photo's PhotoTypes, a family added on
    2026-11-02 is ruled out only by the asset label, the families of before by label or description (families_ruled_out)."""
    row_types = product_types(_join_texts(row.get("subcategory"), row.get("makemodel")))
    if isinstance(photo_types, PhotoTypes):
        return not families_ruled_out(row_types, photo_types.named, photo_types.label)
    return not families_disjoint(row_types, photo_types)


def same_name(a, b) -> bool:
    """The same name after strip, casefold and whitespace collapse. Never the fuzzy comparison of 6.6, which scores
    "BATTERIES" inside "BATTERIES5000" at 0.92."""
    left = " ".join(str(a or "").casefold().split())
    return bool(left) and left == " ".join(str(b or "").casefold().split())


# The asset label ends at the first line break of photo_text; the photo description follows it.
_LABEL_END = "\n"


def photo_text(output: dict) -> str:
    """What the photo shows: the asset label, then the full photo description on a line of its own. The word
    comparisons read both; photo_product_types also reads the label on its own."""
    label = str(output.get("detectedAsset") or "").strip()
    analysis = str(output.get("imageAnalysis") or "").strip()
    return f"{label}{_LABEL_END}{analysis}" if analysis else label


def photo_product_types(photo_text) -> PhotoTypes:
    """The photo's product families (6.1): those of label and description together, and those of the asset label
    alone (the text before the first line break). A water cooler "visible in the background" of a bench photo names a
    family that rules out no bench row (AssetId 1867481), while the table a labelled printer stands on still counts."""
    text = str(photo_text or "")
    return PhotoTypes(frozenset(product_types(text)), frozenset(product_types(text.split(_LABEL_END, 1)[0])))


def confirmed_name_text(output: dict, req_body: dict) -> str:
    """The asset name and description, once the name check confirmed them; otherwise nothing."""
    if output.get("namedescriptionmatch") != "Y":
        return ""
    body = req_body or {}
    return f"{body.get('assetname') or ''} {normalize_user_description(body.get('description'))}".strip()


def _id(value) -> int | None:
    """A positive id, read float-safely: the index returns every metadata number as a float (12.0)."""
    from asset_validator.services.pinecone_service import _metadata_id

    parsed = _metadata_id(value)
    return parsed if parsed and parsed > 0 else None


def agrees_with_filled(row: dict, req_body: dict, *, subcategory_blank: bool, makemodel_blank: bool) -> bool:
    """The row is the field the customer did fill: by its id when the request sends one, else by its exact name."""
    body = req_body or {}
    filled = []
    if not subcategory_blank:
        filled.append(("subcategoryid", "subcategory", body.get("subcategoryname")))
    if not makemodel_blank:
        filled.append(("makemodelid", "makemodel", body.get("makemodel") or body.get("makemodelname")))
    for id_key, name_key, name in filled:
        wanted = _id(body.get(id_key))
        if wanted is not None:
            if _id(row.get(id_key)) != wanted:
                return False
        elif not same_name(row.get(name_key), name):
            return False
    return True


def _names_another_brand(text, shown_text) -> bool:
    """The text names a brand, the photo names another, and none of the text's (6.1's brand list)."""
    own = extract_brand_keys(str(text or ""))
    shown = extract_brand_keys(str(shown_text or ""), prose=True)
    return bool(own and shown and not (own & shown))


def _supports_subcategory(row: dict, seen: set[str], photo_types: set[str]) -> bool:
    """A word of the row's subcategory, or its product family, is in what the photo shows."""
    subcategory = row.get("subcategory")
    own = {word for word in words(subcategory)
           if word not in _STOP_WORDS and word not in _DESCRIPTIVE_WORDS and len(word) > 1 and not word.isdigit()}
    return bool(own & seen) or bool(product_types(subcategory) & _named(photo_types))


def _is_variant(row: dict, chosen: frozenset[str], seen: set[str]) -> bool:
    """A more specific version of the chosen make/model that the photo cannot confirm: all of its words and more
    ("Hp 430 G5" beside "Hp"), or, when the chosen one carries a rating, the same numbers and a word the photo does
    not show ("Safex Co2 4.5kg" beside "co2 fire extinguisher 4.5 kg")."""
    makemodel = row.get("makemodel")
    if is_placeholder(makemodel):
        return False
    own = identity_words(makemodel)
    if own > chosen:
        return True
    ratings = {word for word in chosen if any(c.isdigit() for c in word)}
    return bool(ratings) and ratings <= own and bool(own - seen) and (own & seen) <= chosen


def _sort_key(row: dict):
    makemodel_id = _id(row.get("makemodelid"))
    return (makemodel_id is None, makemodel_id or 0)


def _listed(rows: list, limit: int = 4) -> str:
    return " | ".join(sorted({str(row.get("makemodel") or "") for row in rows})[:limit])


def _hierarchy_row(pool: list, photo_types: set[str]) -> tuple[dict | None, str]:
    """A blank subcategory beside a filled make/model: every returned row carries that make/model id, so the row is
    certain when they all sit under one subcategory. In the 2026-11-01 snapshot every (customer, make/model id) pair is
    unique, so one row decides."""
    subcategories = {_id(row.get("subcategoryid")) for row in pool}
    if None in subcategories:
        return None, "a returned row carries no subcategory id"
    if len(subcategories) != 1:
        return None, "master data files the make/model under several subcategories"
    if any(is_placeholder(row.get("makemodel")) for row in pool):
        return None, "the make/model is a placeholder"
    if not all(fits_photo(row, photo_types) for row in pool):
        return None, "the make/model's product type does not fit the photo"
    return sorted(pool, key=_sort_key)[0], "master data files the make/model under one subcategory"


def _identity_row(pool: list, seen: set[str], named: set[str], photo_types: set[str], *,
                  both_blank: bool) -> tuple[dict | None, str]:
    """A blank make/model: one make/model named word for word by the photo, with nothing that could also be it."""
    fit = [row for row in pool if fits_photo(row, photo_types)]
    # Competitors are every row the photo, or the confirmed name, names in full; only the photo can make one certain.
    competitors = [row for row in fit if covered(row, seen) or covered(row, named)]
    shown = [row for row in fit if covered(row, seen) and is_identity(row.get("makemodel"))]
    if not shown:
        return None, "no make/model with a rating or model number is named in full by the photo"
    word_sets = {identity_words(row.get("makemodel")) for row in competitors}
    widest = [ws for ws in word_sets if not any(ws < other for other in word_sets)]
    if len(widest) > 1:
        rivals = [row for row in competitors if identity_words(row.get("makemodel")) in widest]
        return None, "several make/models fit the photo: " + _listed(rivals)
    chosen = widest[0]
    best = sorted((row for row in shown if identity_words(row.get("makemodel")) == chosen), key=_sort_key)
    if not best:
        return None, "the most specific make/model the photo names has no rating or model number"
    variants = [row for row in pool if not covered(row, seen) and _is_variant(row, chosen, seen)]
    if variants:
        return None, "a more specific make/model the photo cannot confirm: " + _listed(variants)
    subcategories = {_id(row.get("subcategoryid")) for row in best}
    if None in subcategories or len(subcategories) != 1:
        return None, "the make/model sits under several subcategories"
    row = best[0]
    if both_blank:
        if not _supports_subcategory(row, seen, photo_types):
            return None, f"the photo does not support the subcategory {row.get('subcategory')}"
        rivals = [other for other in pool
                  if _id(other.get("subcategoryid")) != _id(row.get("subcategoryid"))
                  and not is_placeholder(other.get("makemodel"))
                  and identity_words(other.get("makemodel")) >= chosen]
        if rivals:
            return None, "an equal or more specific make/model sits under another subcategory: " + _listed(rivals)
    return row, "one make/model fits the photo: " + " ".join(sorted(chosen))


def certain_row(pool: list, *, photo_text: str, name_text: str, subcategory_blank: bool, makemodel_blank: bool,
                complete: bool) -> tuple[dict | None, str]:
    """The row master data is certain the blank field should be, or (None, why not).

    `pool` is every row the lookup returned, before the photo filter: a competitor counts wherever it sits. `complete`
    says fewer rows came back than were asked for. The caller has checked that the make/model check approved the
    filled field, the name did not fail and the lookup was narrowed to the filled field's id (or, with both blank,
    held to the category), and, for a make/model, checks that the selection picked this row too.
    """
    if not pool:
        return None, "the lookup returned no rows"
    if not complete:
        return None, "the lookup returned as many rows as it asked for, so a competitor may sit past the cut"
    photo_types = photo_product_types(photo_text)
    if subcategory_blank and not makemodel_blank:
        return _hierarchy_row(pool, photo_types)
    return _identity_row(pool, set(words(photo_text)), set(words(name_text)), photo_types,
                         both_blank=subcategory_blank)


def most_relatable(pool: list, pick: dict | None, *, photo_text: str, name_text: str, req_body: dict,
                   subcategory_blank: bool, makemodel_blank: bool) -> dict | None:
    """The returned row that names the asset most closely, not simply the one returned or picked first.

    The owner, 2 Nov 2026: "while selecting HP don't just take what you receive first, check the most relatable with
    the asset and then recommend". Of the rows that agree with every filled field and fit the photo's type, the one
    whose make/model the photo (and a confirmed name) names in full with the most words wins: "HP ProBook 440 G8" over
    "HP" for a photo of an HP ProBook 440 G8; a "440 G9" row the photo does not show never does. Ties keep the
    selection's pick, so its spelling of a duplicate stands, then the index's ranking. With no such row the pick is
    returned and `recommendation` holds back what the photo does not support.
    """
    seen = set(words(photo_text)) | set(words(name_text))
    photo_types = photo_product_types(photo_text)
    shown = [row for row in pool or []
             if agrees_with_filled(row, req_body, subcategory_blank=subcategory_blank, makemodel_blank=makemodel_blank)
             and fits_photo(row, photo_types) and covered(row, seen)]
    if not shown:
        return pick
    pick = pick or {}
    pick_words = identity_words(pick.get("makemodel"))

    def rank(row):
        row_words = identity_words(row.get("makemodel"))
        is_pick = row_words == pick_words and _id(row.get("subcategoryid")) == _id(pick.get("subcategoryid"))
        return -len(row_words), not is_pick, -float(row.get("_score") or 0)

    best = sorted(shown, key=rank)[0]
    if identity_words(best.get("makemodel")) == pick_words and _id(best.get("subcategoryid")) == _id(
            pick.get("subcategoryid")):
        return pick
    return best


def recommendation(row: dict, *, photo_text: str, name_text: str, subcategory_blank: bool,
                   makemodel_blank: bool) -> tuple[dict, str]:
    """The row's values for the fields left blank, each only where the photo supports it, and why any was held back.

    The caller has checked that the name did not fail and that the row agrees with every filled field. Without the
    word test on the make/model, about 45 of 119 suggestions on 320 real records were visibly wrong: iPads offered
    "Bar Code Scanner" and "Wifi", a ThinkCentre "Cooling Tower Pump", fire extinguishers "Wall Fan".
    """
    photo_types = photo_product_types(photo_text)
    if not fits_photo(row, photo_types):
        return {}, "the row's product type does not fit the photo"
    seen = set(words(photo_text))
    values: dict = {}
    held = []
    if subcategory_blank:
        subcategory = str(row.get("subcategory") or "").strip()
        if _names_another_brand(subcategory, photo_text):
            held.append("the subcategory names another brand")
        elif not _supports_subcategory(row, seen, photo_types):
            held.append("the photo does not support the subcategory")
        elif subcategory:
            values["recommendedsubcategory"] = subcategory
    if makemodel_blank:
        makemodel = str(row.get("makemodel") or "").strip()
        if is_placeholder(makemodel):
            held.append("the make/model is a placeholder")
        elif _names_another_brand(f"{row.get('subcategory') or ''} {makemodel}", photo_text):
            held.append("the make/model names another brand")
        elif not covered(row, seen | set(words(name_text))):
            held.append("the photo does not show every word of the make/model")
        else:
            values["recommendedmakemodel"] = makemodel
    # Everything follows the hierarchy (owner, 2 Nov 2026): a make/model is never suggested without its own
    # subcategory, so with both fields blank it is shown only beside the row's subcategory.
    if subcategory_blank and "recommendedmakemodel" in values and "recommendedsubcategory" not in values:
        values.pop("recommendedmakemodel")
        held.append("a make/model is never suggested without its own subcategory")
    return values, "; ".join(held)
