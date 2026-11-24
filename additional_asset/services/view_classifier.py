"""Blind view classifier (ported from main view_classifier)."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from additional_asset.prompts.loader import load_prompt
from additional_asset.services.gemini_client import call_llm_with_retry

VIEWS = frozenset({"full_asset", "partial_asset", "tag_closeup", "packaging", "empty"})


def classify_view(model: str, image_b64: str, *, request_id: str = "", label: str = "") -> str | None:
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
        logging.warning("[%s] Blind view call raised on %s image: %s", request_id, label, e)
        return None
    if result.get("error"):
        logging.warning("[%s] Blind view error on %s: %s", request_id, label, result.get("error"))
        return None
    view = str((result or {}).get("view") or "").strip().lower()
    if view not in VIEWS:
        logging.warning("[%s] Blind view unusable for %s image: %r", request_id, label, result)
        return None
    logging.info(
        "[%s] Blind view %s: %s — %s",
        request_id,
        label,
        view,
        str(result.get("reason") or "")[:120],
    )
    return view


class ViewClassification:
    """Classify every provided photo in parallel."""

    def __init__(self, model: str, slots: dict[str, str | None], *, request_id: str = ""):
        from additional_asset.utils.token_usage import bind_request_usage, get_request_usage

        self._request_id = request_id
        self._started = time.time()
        usage = get_request_usage()  # capture on request thread

        def _run(label: str, b64: str) -> str | None:
            bind_request_usage(usage)  # otherwise worker tokens go unrecorded
            return classify_view(model, b64, request_id=request_id, label=label)

        present = {k: v for k, v in slots.items() if v}
        self._executor = ThreadPoolExecutor(max_workers=max(len(present), 1))
        self._futures = {k: self._executor.submit(_run, k, v) for k, v in present.items()}
        self._reasons: dict[str, str] = {}

    def result(self, timeout: float) -> dict[str, str]:
        views: dict[str, str] = {}
        deadline = self._started + timeout
        for slot, future in self._futures.items():
            try:
                view = future.result(timeout=max(deadline - time.time(), 0.0))
            except FutureTimeout:
                logging.warning("[%s] Blind view timed out on %s image", self._request_id, slot)
                continue
            except Exception as e:
                logging.warning("[%s] Blind view failed on %s image: %s", self._request_id, slot, e)
                continue
            if view:
                views[slot] = view
        self._executor.shutdown(wait=False)
        return views


def pick_asset_and_tag_slots(views: dict[str, str]) -> tuple[str, str, bool]:
    """Return (asset_slot, tag_slot, swapped).

    Prefer full_asset/partial_asset for asset; tag_closeup for tag.
    Falls back to upload field names when views are incomplete.
    """
    asset_like = {"full_asset", "partial_asset"}
    tag_like = {"tag_closeup"}

    asset_slot = None
    tag_slot = None
    for slot, view in views.items():
        if view in asset_like and asset_slot is None:
            asset_slot = slot
        if view in tag_like and tag_slot is None:
            tag_slot = slot

    if asset_slot is None:
        # Prefer any non-tag slot; default to upload field "asset"
        asset_slot = "asset"
        for slot, view in views.items():
            if view not in tag_like:
                asset_slot = slot
                break

    if tag_slot is None:
        for slot in ("barcode", "asset"):
            if slot != asset_slot:
                tag_slot = slot
                break
        tag_slot = tag_slot or "barcode"

    swapped = asset_slot != "asset" or tag_slot != "barcode"
    return asset_slot, tag_slot, swapped
