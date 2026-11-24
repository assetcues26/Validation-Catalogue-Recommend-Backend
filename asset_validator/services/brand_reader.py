"""Read the manufacturer marking off an asset photo, shown nothing but the pixels.

WHY THIS EXISTS. makemodelmatch compares two STRINGS — what the customer typed against the
makemodel on the master-data row. Neither side is the photograph, so when an asset carries
no legible maker's marking there is nothing that can settle the comparison, and a "failed"
is a disagreement between two records rather than a finding about the asset. AssetId
2302965 is the case: a laser coding machine shrink-wrapped on a pallet, no nameplate
anywhere, rejected because the customer's makemodel "IJP Printer" did not equal master
data's "Markem Image" while category and subcategory both matched at 100% and 99%.

WHY IT IS A SEPARATE CALL. The obvious implementation — ask the main phase-1 vision call
for the brand alongside everything else — was built and measured, and it regressed the
civil assets it was meant to sit beside. Adding the field made the model dwell on where
the tag was mounted, and AssetId 2294700 (a compound wall whose tag sits on a fire hydrant
cabinet) went from passing to being rejected as "the tag is on a fire hydrant, not the
compound wall":

    baseline phase-1 prompt        15 / 15 pass
    with the brand field added      4 / 15 pass

The shared vision prompt is load-bearing and sensitive to additions. So the question is
asked on its own, of a call shown only the image, and only for a record whose make/model
has ALREADY failed — roughly one record in forty, so it costs nothing on the common path.

The call is deliberately name-free for the same reason view_classifier is: the declared
record leaks into what the model reports seeing, and a brand invented from the customer's
own makemodel field would make this check confirm whatever it was asked about.
"""

from __future__ import annotations

import logging

# The call could not be made, or its answer was unusable. NOT the same as "no brand":
# unknown must leave the existing comparison standing.
UNKNOWN = None
# The image was read and carries no legible manufacturer marking.
NO_BRAND = ""


def read_visible_brand(
    model, image_b64: str, *, target_asset: str = "", request_id: str = "", retries: int | None = None
) -> str | None:
    """Return the legible maker's marking, NO_BRAND if there is none, UNKNOWN on failure.

    `target_asset` names which object in the frame to inspect. It is the system's OWN
    detectedAsset, never the customer's declared make/model — the call must be able to find
    the asset among the other things on the desk without being told what brand to expect.
    Measured need: AssetId 2294686 is a desk with a Cisco IP phone standing on it, and
    without a target the call answered "Cisco" for the desk.

    `retries` caps the attempts (None = the default); the brand-conflict confirmation uses one.
    """
    from asset_validator.prompts.loader import load_prompt
    from asset_validator.services.llm_provider import call_llm_with_retry

    if not model or not image_b64:
        return UNKNOWN
    prompt = load_prompt("visible_brand.md").replace(
        "{target_asset}", str(target_asset or "the main asset in this photograph").strip()
    )
    try:
        result = call_llm_with_retry(
            model,
            prompt,
            images=[{"mime_type": "image/jpeg", "data": image_b64}],
            max_tokens=200,
            retries=retries,
            response_mime_type="application/json",
            usage_label="visible_brand",
        )
    except Exception as e:
        logging.warning(f"[{request_id}] Visible-brand call raised: {e}")
        return UNKNOWN
    if not isinstance(result, dict) or "brand" not in result:
        logging.warning(f"[{request_id}] Visible-brand unusable: {result!r:.200}")
        return UNKNOWN

    brand = result.get("brand")
    # The model writes the string "null"/"none" often enough that it must be handled.
    text = "" if brand is None else str(brand).strip()
    if text.lower() in ("null", "none", "n/a", "na", "-", "unknown"):
        text = ""
    logging.info(
        f"[{request_id}] Visible brand: {text or '(none legible)'} — "
        f"{str(result.get('reason') or '')[:80]}"
    )
    return text


class BrandConflictConfirmer:
    """Settles a brand conflict on the full-size uploads before a rule fails on it (docs 6.3).

    WHY. Every brand rule — the name (6.1), the make/model (6.0), the IT identity (5.5) — takes
    the photo's brand from the phase-1 description, which is written from copies shrunk to
    896/768 px. A small logo can be misread there. AssetId 3824091, a Voltas split AC whose two
    photos were swapped (so the asset sat in the slot shrunk to 768 px), was described as Hitachi
    and failed against its own Voltas record; its full-size upload reads VOLTAS on every run.

    HOW. A rule calls it with the record's brand keys, and only after it has found a conflict.
    The uploads are then read once for the request, name-free, one attempt each, at up to
    TAG_REREAD_MAX_EDGE, the object named by the system's own label with every brand word removed
    (the label can carry the very brand being checked). The answer is the set of brands read when
    every brand read is among the calling rule's AND every field of the record that names a brand
    (`declared`: the asset name; make/model with model) names one of them. Anything else —
    nothing legible, a different or unlisted brand on any upload, a read that failed, a record
    whose fields disagree with what was read — is the empty set, and the conflict stands exactly
    as before. So it can only drop a brand failure, and only on positive evidence from a call
    that never saw the record.

    Why every field: 2715676 names Voltas while its make/model says Haier, on a Haier unit. Had the
    description misread the logo as Voltas, the name rule would see no conflict, and confirming
    only the make/model's Haier would pass a record whose name carries the wrong brand.

    Reading stops at the first upload that can no longer confirm anything (a failed read, or a
    brand none of the record's fields name), so a hung call costs one read, not two.
    """

    def __init__(self, model, photos, target_label, *, declared=(), reader=None, request_id: str = ""):
        # `photos`: zero-arg callables returning a base64 JPEG, so nothing is decoded until a
        # conflict needs it. `target_label`: a zero-arg callable, read at that moment too.
        # `declared`: the brand keys of each record field group that names a brand.
        self._model = model
        self._photos = list(photos)
        self._target_label = target_label
        self._declared = [set(group) for group in declared if group]
        self._reader = reader or read_visible_brand
        self._request_id = request_id
        self._reads: list[str | None] | None = None
        self.confirmed_brands: set[str] = set()

    def _read_all(self) -> list[str | None]:
        if self._reads is None:
            from asset_validator.utils.classification_match import extract_brand_keys, strip_brand_words

            target = strip_brand_words(self._target_label())
            named = set().union(*self._declared) if self._declared else None
            reads: list[str | None] = []
            for photo in self._photos:
                image = photo()
                text = (
                    self._reader(self._model, image, target_asset=target,
                                 request_id=self._request_id, retries=1)
                    if image else UNKNOWN
                )
                reads.append(text)
                if text is UNKNOWN:
                    break                   # a failed read already means the conflict stands
                if str(text).strip():
                    keys = extract_brand_keys(str(text))
                    if not keys or (named is not None and not keys <= named):
                        break               # a brand the record never names: nothing to confirm
            self._reads = reads
        return self._reads

    def __call__(self, record_brands) -> set[str]:
        from asset_validator.utils.classification_match import extract_brand_keys

        wanted = set(record_brands or ())
        if not wanted or not self._photos:
            return set()
        reads = self._read_all()
        confirmed: set[str] = set()
        why = "conflict stands"
        for text in reads:
            if text is UNKNOWN:
                confirmed, why = set(), "conflict stands (a read failed)"
                break
            if not str(text).strip():
                continue                    # nothing legible on this upload
            keys = extract_brand_keys(str(text))
            if not keys or not keys <= wanted:
                confirmed = set()           # an unlisted or different brand: the conflict stands
                break
            confirmed |= keys
        if confirmed and any(not (group & confirmed) for group in self._declared):
            confirmed, why = set(), "conflict stands (another field of the record names a different brand)"
        if confirmed:
            why = "misread, conflict dropped"
            self.confirmed_brands |= confirmed
        logging.info(
            f"[{self._request_id}] Brand conflict, record {sorted(wanted)}: full-size reads {reads} -> {why}"
        )
        return confirmed
