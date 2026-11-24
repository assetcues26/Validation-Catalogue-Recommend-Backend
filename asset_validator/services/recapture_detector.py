"""Name-free detection of re-photographed uploads: a photo of a screen or a printout.

WHY THIS EXISTS. A tagger who does not have the physical asset in front of them can open an
existing picture of it — on a phone, a laptop, or a printed page — photograph that, and upload
the result. Every check then runs on a real-looking photo of the right asset, and the record
passes although nobody saw the asset. That is the one thing an asset-tagging audit exists to
prevent, and nothing in the pipeline asked about it.

WHY A SEPARATE CALL. The question could have been added to an existing prompt, and both
candidates were ruled out:

- the phase-1 vision prompt is shown the declared asset name, which leaks into what the model
  reports seeing, and it is sensitive to additions: adding a single field to it once took
  AssetId 2294700 (a compound wall) from 15/15 passing to 4/15;
- the blind-view prompt (view_classifier.py) already decides blank and tag-only photos, and a
  change to it could shift those decisions.

So it is asked on its own, of a call shown nothing but the pixels, run in parallel with blind
view so it adds no latency. All provided photos go in one call, answered per photo.

WHY NOT PIXELS. Moiré detection on the image itself was considered and rejected on our own data:
mesh dustbins, Aeron mesh chairs and chain-link fencing are genuine photos whose fine repeating
texture aliases exactly like moiré on a screen.

The call only REPORTS. Whether a finding changes the response is decided in
validation_service.apply_recapture_policy, behind RECAPTURE_ENFORCE.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass

# The visual evidence the prompt may name. Anything else the model writes is dropped, so a
# detection always rests on a cue that means "flat picture", never on free text.
CUES = frozenset({
    "screen_bezel",
    "device_ui",
    "moire_pixel_grid",
    "screen_glow",
    "print_edge",
    "halftone_dots",
    "glossy_glare",
    "held_in_hand",
    "picture_in_scene",
    "second_timestamp",
})


@dataclass(frozen=True)
class RecaptureFinding:
    recaptured: bool
    cues: tuple[str, ...]
    confidence: float
    reason: str

    def as_dict(self) -> dict:
        return {
            "recaptured": self.recaptured,
            "cues": list(self.cues),
            "confidence": self.confidence,
            "reason": self.reason,
        }


def _parse_confidence(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(number, 0.0), 1.0)


def _parse_recaptured(value) -> bool:
    # Only an explicit yes counts. A missing or malformed answer is not a detection.
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def detect_recapture(
    model, slots: dict[str, str | None], *, request_id: str = ""
) -> dict[str, RecaptureFinding]:
    """Ask, per provided photo, whether it is a re-photograph.

    Returns {slot: finding} for every photo the model answered for. Any failure — the call
    raising, an error payload, an unusable shape — returns {} for the whole call, which callers
    treat as "unknown" and never as "not recaptured".
    """
    from asset_validator.prompts.loader import load_prompt
    from asset_validator.services.llm_provider import call_llm_with_retry

    ordered = [(slot, b64) for slot, b64 in slots.items() if b64]
    if not model or not ordered:
        return {}
    try:
        result = call_llm_with_retry(
            model,
            load_prompt("recapture.md"),
            images=[{"mime_type": "image/jpeg", "data": b64} for _, b64 in ordered],
            max_tokens=400,
            response_mime_type="application/json",
            usage_label="recapture",
        )
    except Exception as e:
        logging.warning(f"[{request_id}] Recapture call raised: {e}")
        return {}
    photos = (result or {}).get("photos") if isinstance(result, dict) else None
    if not isinstance(photos, list):
        logging.warning(f"[{request_id}] Recapture result unusable: {result!r:.200}")
        return {}

    findings: dict[str, RecaptureFinding] = {}
    for entry in photos:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not 1 <= index <= len(ordered):
            continue
        slot = ordered[index - 1][0]
        raw_cues = entry.get("cues") if isinstance(entry.get("cues"), list) else []
        cues = tuple(dict.fromkeys(
            str(c).strip().lower() for c in raw_cues if str(c).strip().lower() in CUES
        ))
        findings[slot] = RecaptureFinding(
            recaptured=_parse_recaptured(entry.get("recaptured")),
            cues=cues,
            confidence=_parse_confidence(entry.get("confidence")),
            reason=str(entry.get("reason") or "").strip()[:200],
        )

    for slot, f in findings.items():
        logging.info(
            f"[{request_id}] Recapture {slot}: recaptured={f.recaptured} "
            f"confidence={f.confidence:.2f} cues={list(f.cues)} — {f.reason[:120]}"
        )
    return findings


class RecaptureDetection:
    """Run detect_recapture in the background, alongside blind view and the main checks.

    Started before the main vision call and joined after it, so it adds no latency of its own.
    A timeout or failure yields {}, and callers fall back to their previous behaviour.
    """

    def __init__(self, model, slots: dict[str, str | None], *, request_id: str = ""):
        from asset_validator.utils.token_usage import bind_request_usage, get_request_usage

        self._request_id = request_id
        self._started = time.time()
        usage = get_request_usage()          # captured here, on the request thread

        def _run() -> dict[str, RecaptureFinding]:
            bind_request_usage(usage)        # otherwise this call's tokens go unrecorded
            return detect_recapture(model, slots, request_id=request_id)

        self._executor = ThreadPoolExecutor(max_workers=1)
        self._future = self._executor.submit(_run)

    def result(self, timeout: float) -> dict[str, RecaptureFinding]:
        try:
            remaining = max(self._started + timeout - time.time(), 0.0)
            return self._future.result(timeout=remaining)
        except FutureTimeout:
            logging.warning(f"[{self._request_id}] Recapture detection timed out")
            return {}
        except Exception as e:
            logging.warning(f"[{self._request_id}] Recapture detection failed: {e}")
            return {}
        finally:
            self._executor.shutdown(wait=False)
