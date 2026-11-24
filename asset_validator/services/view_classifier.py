"""Name-free classification of each uploaded photo: full asset, tag close-up, or empty.

WHY THIS EXISTS. The main vision call is given the declared record ("Expected Asset
Name: SHREDDING MACHINE ...") as a disambiguation hint, and that hint leaks into what the
model says it SEES. Measured on one black photo carrying only the app's date stamp:

    declared "SHREDDING MACHINE"       -> Y, "a paper shredder ... black plastic top housing"
    declared "Executive Office Chair"  -> E, "completely black, no objects visible"
    declared "Split Air Conditioner"   -> E, "completely black"

Identical pixels; the only difference is the declared name. The same leak turns a tag
close-up into a whole asset: the scrubber pump's tag sticker (AssetId 2911417, whose two
upload fields are swapped) was reported as "a blue industrial centrifugal pump mounted on
a metal base frame" — a base frame that is not in that photo.

Removing the hint from the main call is not the fix: across 62 real records it changed 7,
including 4 PASS -> FAIL, because the hint genuinely helps pick the right object in a busy
frame. So the hint stays for IDENTIFICATION, and the two questions it must never influence
— is there an asset in this photo at all, and is this a whole asset or only a tag — are
asked separately, of a call that is shown nothing but the pixels.

Against ground truth established by viewing every image, this call scored 17/17 on two
consecutive runs: 7 tag close-ups, 8 whole assets (including the swapped pump photos and
the Dell laptop) and 2 empty frames (the black date-stamped photo and a plain wall).
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

FULL_ASSET = "full_asset"
# The frame is filled by the asset's OWN surface, too close to show its shape — a table
# top, a cabinet door, an equipment panel. It shows the asset, so it is not a missing
# photo. Added after AssetId 104436 ("Packaging Table"), where a close shot of the table
# top was called "empty — only a plain, light-coloured surface" and the record failed four
# checks although the tag decoded and matched. For furniture the surface IS the asset.
PARTIAL_ASSET = "partial_asset"
TAG_CLOSEUP = "tag_closeup"
# A product's retail or shipping box, with the product inside it unseen. Customer 1278 tagged
# 14 HP laptops still sealed in their cartons (2026-10-22): the carton is a whole object, so it
# was "full_asset", the phase-1 call read the laptop's model off the carton label, and 13 of the
# 14 passed the name, classification, condition and cost checks of an asset nobody had seen.
PACKAGING = "packaging"
EMPTY = "empty"
VIEWS = frozenset({FULL_ASSET, PARTIAL_ASSET, TAG_CLOSEUP, PACKAGING, EMPTY})
# Views in which a physical asset is present in the frame.
ASSET_PRESENT_VIEWS = frozenset({FULL_ASSET, PARTIAL_ASSET})


def classify_view(model, image_b64: str, *, request_id: str = "", label: str = "") -> str | None:
    """Classify one photo from its pixels alone. Returns a VIEWS value, or None on failure."""
    from asset_validator.prompts.loader import load_prompt
    from asset_validator.services.llm_provider import call_llm_with_retry

    try:
        result = call_llm_with_retry(
            model,
            load_prompt("blind_view.md"),
            images=[{"mime_type": "image/jpeg", "data": image_b64}],
            max_tokens=300,
            response_mime_type="application/json",
            usage_label="blind_view",
        )
    except Exception as e:
        logging.warning(f"[{request_id}] Blind view call raised on {label} image: {e}")
        return None
    view = str((result or {}).get("view") or "").strip().lower()
    if view not in VIEWS:
        logging.warning(
            f"[{request_id}] Blind view unusable for {label} image: {result!r:.200}"
        )
        return None
    logging.info(
        f"[{request_id}] Blind view {label}: {view} — {str(result.get('reason') or '')[:120]}"
    )
    return view


class ViewClassification:
    """Classify every provided photo in parallel with the main checks.

    Started before the main vision call and joined after it, so it adds no latency on
    its own. A slot whose call fails or times out is simply absent from the result,
    and callers fall back to their previous behaviour for that request.
    """

    def __init__(self, model, slots: dict[str, str | None], *, request_id: str = ""):
        from asset_validator.utils.token_usage import bind_request_usage, get_request_usage

        self._request_id = request_id
        self._started = time.time()
        usage = get_request_usage()          # captured here, on the request thread

        def _run(label: str, b64: str) -> str | None:
            bind_request_usage(usage)        # otherwise this call's tokens go unrecorded
            return classify_view(model, b64, request_id=request_id, label=label)

        present = {k: v for k, v in slots.items() if v}
        self._executor = ThreadPoolExecutor(max_workers=max(len(present), 1))
        self._futures = {k: self._executor.submit(_run, k, v) for k, v in present.items()}

    def result(self, timeout: float) -> dict[str, str]:
        """{slot: view} for every slot that classified successfully within timeout."""
        views: dict[str, str] = {}
        deadline = self._started + timeout
        for slot, future in self._futures.items():
            try:
                view = future.result(timeout=max(deadline - time.time(), 0.0))
            except FutureTimeout:
                logging.warning(f"[{self._request_id}] Blind view timed out on {slot} image")
                continue
            except Exception as e:
                logging.warning(f"[{self._request_id}] Blind view failed on {slot} image: {e}")
                continue
            if view:
                views[slot] = view
        self._executor.shutdown(wait=False)
        return views
