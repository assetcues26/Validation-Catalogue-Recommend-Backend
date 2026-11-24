"""Image preparation and validation."""

import base64
import binascii
import logging
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from PIL import Image

from asset_validator.core.config import (
    BARCODE_JPEG_QUALITY,
    IMAGE_JPEG_QUALITY,
    TAG_REREAD_MAX_EDGE,
    effective_asset_image_max_edge,
    effective_barcode_image_max_edge,
)
from asset_validator.utils.barcode_decode import decode_codes_from_image


# Edge density of the most recent asset image per request. Keyed by request_id so
# concurrent requests cannot read each other's value; entries are popped on read.
_LAST_ASSET_DETAIL: dict[str, float | None] = {}


def take_asset_image_detail(request_id: str) -> float | None:
    """Pop the edge density measured for this request's asset image."""
    return _LAST_ASSET_DETAIL.pop(request_id, None)


def measure_image_detail(image) -> float | None:
    """Edge density of an image: how much discernible detail it carries.

    Greyscale -> 256px thumbnail -> edge filter -> mean. A black or featureless frame
    scores near zero; a photo of a real object scores several times higher. Returns None
    if it cannot be computed — this is diagnostic only and must never raise.
    """
    try:
        from PIL import ImageFilter, ImageStat

        small = image.convert("L")
        small.thumbnail((256, 256))
        return round(ImageStat.Stat(small.filter(ImageFilter.FIND_EDGES)).mean[0], 2)
    except Exception:
        return None


def measure_frame_coverage(image) -> dict | None:
    """Share of the frame that is black, and share that is one flat tone.

    Returns {"dark": fraction of pixels below BLANK_IMAGE_DARK_LEVEL,
             "flat": fraction within +-BLANK_IMAGE_FLAT_TOLERANCE of the median,
             "median": median luminance}, or None if it cannot be computed.

    SHARES, not whole-frame statistics, on purpose. The tagging app burns a white date
    stamp ("11-Sep-2026") into every photo. On a fully black frame that stamp alone lifts
    the brightest percentile to ~206 and the standard deviation to ~26, which defeated an
    earlier mean/stdev/p99 version of this gate — production AssetId 2692042, a black
    photo, passed it and the model then described a paper shredder that is not there.
    A stamp covers ~2% of the frame, so it cannot move a share that must exceed 85%.

    Deliberately NOT edge density either: that was reverted because blank and real frames
    overlap (a smooth plastic chair close up scored 7.95, a blank frame 7.73). These two
    shares do not overlap. Measured over 88 distinct real asset photos:

        real photos         dark <= 30.8%        flat <= 61.5%
        black + date stamp  dark    98.1%        flat    98.0%
        grey/white + stamp  dark     0.0%        flat 98.0-99.9%
    """
    try:
        from asset_validator.core.config import (
            BLANK_IMAGE_DARK_LEVEL,
            BLANK_IMAGE_FLAT_TOLERANCE,
        )

        small = image.convert("L")
        small.thumbnail((256, 256))
        px = sorted(small.getdata())
        n = len(px)
        if not n:
            return None
        median = px[n // 2]
        return {
            "dark": sum(1 for v in px if v < BLANK_IMAGE_DARK_LEVEL) / n,
            "flat": sum(1 for v in px if abs(v - median) <= BLANK_IMAGE_FLAT_TOLERANCE) / n,
            "median": median,
        }
    except Exception:
        return None


def is_frame_unusable(image, request_id: str = "", label: str = "asset") -> str | None:
    """Return a reason string when the frame carries no asset at all, else None.

    Two rules, both far outside the range any real photograph occupies:

      black  >= BLANK_IMAGE_DARK_FRACTION of pixels below BLANK_IMAGE_DARK_LEVEL — the
             frame is essentially unlit. Real photos peak at 30.8%, a black frame with a
             date stamp measures 98.1%. A photo of a black asset is not a black frame: the
             Dell laptop the customer reported measured 0.0%.
      flat   >= BLANK_IMAGE_FLAT_FRACTION of pixels within +-BLANK_IMAGE_FLAT_TOLERANCE of
             the median — one tone edge to edge (lens cap, blown-out white, grey card).
             Real photos peak at 61.5%, the flattest being a white-topped desk; stamped
             uniform frames measure 98.0-99.9%.

    This DOES gate imageReadability, unlike the edge-density metric beside it. That is a
    deliberate departure from the earlier "never gate a verdict" rule, justified by the
    separation above: the reverted rule failed because blank and real overlapped, and
    these do not. Set BLANK_IMAGE_GATE_ENABLED=false to disable without a deploy.
    """
    from asset_validator.core.config import (
        BLANK_IMAGE_DARK_FRACTION,
        BLANK_IMAGE_FLAT_FRACTION,
        BLANK_IMAGE_GATE_ENABLED,
    )

    if not BLANK_IMAGE_GATE_ENABLED:
        return None
    cov = measure_frame_coverage(image)
    if cov is None:
        return None
    reason = None
    if cov["dark"] >= BLANK_IMAGE_DARK_FRACTION:
        reason = f"unlit frame ({cov['dark']:.0%} of pixels black)"
    elif cov["flat"] >= BLANK_IMAGE_FLAT_FRACTION:
        reason = f"flat frame ({cov['flat']:.0%} of pixels one tone, median {cov['median']})"
    if reason:
        logging.warning(
            f"[{request_id}] Unusable {label} image: {reason} — no asset can be present"
        )
    return reason


def is_b64_frame_unusable(
    image_b64: str | None, request_id: str = "", label: str = "asset"
) -> str | None:
    """is_frame_unusable for a base64 upload; None when it cannot be decoded.

    Computed on demand for whichever photo actually SHOWS the asset. Upload fields are
    routinely swapped — the tag close-up in AssetImage, the asset in BarcodeImage — so
    gating the AssetImage field would test the wrong photo, and could fail a correct
    record whose AssetImage slot happens to hold a bad frame while the real asset photo
    sits in the other slot.
    """
    if not image_b64:
        return None
    try:
        with Image.open(BytesIO(base64.b64decode(image_b64))) as image:
            return is_frame_unusable(image, request_id, label)
    except Exception:
        return None


def log_image_detail(image, request_id: str = "", label: str = "asset") -> float | None:
    """Record the edge density of an upload. LOG ONLY — never changes a verdict.

    Exists to measure how often unusable photos arrive. Acting on this signal was tried
    and REVERTED: a smooth plastic chair photographed close up scored 7.95 while a blank
    frame scored 7.73, so the populations overlap and any threshold produces false
    "unreadable" verdicts on good photos. Blank frames are handled in the vision prompts.
    """
    from asset_validator.core.config import (
        BLANK_IMAGE_EDGE_THRESHOLD,
        BLANK_IMAGE_METRIC_ENABLED,
    )

    if not BLANK_IMAGE_METRIC_ENABLED:
        return None
    detail = measure_image_detail(image)
    if detail is None:
        return None
    if detail < BLANK_IMAGE_EDGE_THRESHOLD:
        logging.warning(
            f"[{request_id}] Low-detail {label} image: edge_density={detail} "
            f"(threshold {BLANK_IMAGE_EDGE_THRESHOLD}) — likely blank or unusable"
        )
    else:
        logging.info(f"[{request_id}] {label} image edge_density={detail}")
    return detail


def validate_barcode_image_quality(image, request_id: str = "") -> tuple[bool, str | None]:
    try:
        min_width, min_height = 200, 200
        if image.width < min_width or image.height < min_height:
            warning = (
                f"Barcode image resolution too low ({image.width}x{image.height}). "
                f"Minimum {min_width}x{min_height} required."
            )
            logging.warning(f"[{request_id}] {warning}")
            return False, warning
        if image.width < 400 and image.height < 400:
            logging.warning(
                f"[{request_id}] Barcode image may be too small "
                f"({image.width}x{image.height}). Higher resolution recommended."
            )
        return True, None
    except Exception as e:
        logging.error(f"[{request_id}] Image quality validation error: {e}")
        return True, None


def prepare_image_b64(
    b64_string: str,
    max_edge: int,
    jpeg_quality: int,
    request_id: str = "",
    is_barcode: bool = False,
) -> str:
    if not b64_string:
        return b64_string

    max_b64_size = 10 * 1024 * 1024 * 4 // 3
    if len(b64_string) > max_b64_size:
        raise ValueError("Image size exceeds 10MB limit (pre-decode validation).")

    try:
        raw_bytes = base64.b64decode(b64_string)
    except (binascii.Error, TypeError) as e:
        label = "barcodeimage" if is_barcode else "assetimage"
        raise ValueError(f"Invalid base64 string provided for '{label}'") from e

    try:
        with BytesIO(raw_bytes) as image_buffer:
            with Image.open(image_buffer) as image:
                if is_barcode:
                    is_valid, warning = validate_barcode_image_quality(image, request_id)
                    if not is_valid:
                        # Soft skip: a low-resolution tag must not sink the whole
                        # request. Keep the image and let the model mark the tag
                        # unreadable if it genuinely cannot read it.
                        logging.warning(
                            f"[{request_id}] Low-resolution barcode kept for "
                            f"best-effort read: {warning}"
                        )

                else:
                    _LAST_ASSET_DETAIL[request_id] = log_image_detail(
                        image, request_id, "asset"
                    )

                needs_resize = image.width > max_edge or image.height > max_edge
                needs_convert = image.format != "JPEG" or needs_resize
                if not needs_convert:
                    return b64_string

                if needs_resize:
                    image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)

                with BytesIO() as output_buffer:
                    if image.mode not in ("RGB", "L"):
                        image = image.convert("RGB")
                    image.save(
                        output_buffer,
                        format="JPEG",
                        quality=jpeg_quality,
                        optimize=True,
                    )
                    return base64.b64encode(output_buffer.getvalue()).decode("utf-8")
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Invalid, corrupted, or oversized image provided: {e}") from e


def split_tag_photo_for_reread(
    original_b64: str | None,
    request_id: str = "",
) -> list[str]:
    """The ORIGINAL tag photo as two overlapping halves, each within the barcode resize edge.

    The model spends the same small token budget on every image whatever its size, so a
    720x1280 tag photo shrunk to 432x768 leaves a small sticker's digits a few pixels tall.
    Cut in two along its long side first, each half keeps the upload's own resolution: on
    AssetId 1986988 the halves read 1001101602 every run, where the shrunk photo — and
    halves of the shrunk photo — read 100101002 and 1003101602. The halves overlap by a
    fifth of the long side, so a sticker on the cut line is whole in at least one.
    Returns [] when the photo cannot be decoded; the caller then uses the shrunk copy.
    """
    if not original_b64:
        return []
    try:
        with Image.open(BytesIO(base64.b64decode(original_b64))) as image:
            image = image.convert("RGB")
            width, height = image.size
            if height >= width:
                overlap = height // 10
                boxes = [(0, 0, width, height // 2 + overlap),
                         (0, height // 2 - overlap, width, height)]
            else:
                overlap = width // 10
                boxes = [(0, 0, width // 2 + overlap, height),
                         (width // 2 - overlap, 0, width, height)]
            max_edge = effective_barcode_image_max_edge()
            halves = []
            for box in boxes:
                half = image.crop(box)
                half.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                with BytesIO() as buffer:
                    half.save(buffer, format="JPEG", quality=BARCODE_JPEG_QUALITY, optimize=True)
                    halves.append(base64.b64encode(buffer.getvalue()).decode("utf-8"))
            return halves
    except Exception as e:
        logging.warning(f"[{request_id}] Could not split the tag photo for the re-read: {e}")
        return []


def tag_photo_for_focused_reread(original_b64: str | None, request_id: str = "") -> str | None:
    """One ORIGINAL photo, as the focused tag re-read sends it (docs §4.2).

    Unchanged — byte for byte — when it is a JPEG whose long edge is within TAG_REREAD_MAX_EDGE:
    that is what was measured. A larger or non-JPEG upload is shrunk to that edge and re-encoded,
    which still keeps several times the detail of the copy the phase-1 call reads. None when the
    photo cannot be decoded, so the caller can fall back to the copy it already has.
    """
    if not original_b64:
        return None
    try:
        with Image.open(BytesIO(base64.b64decode(original_b64))) as image:
            if image.format == "JPEG" and max(image.size) <= TAG_REREAD_MAX_EDGE:
                return original_b64
            image = image.convert("RGB")
            image.thumbnail((TAG_REREAD_MAX_EDGE, TAG_REREAD_MAX_EDGE), Image.Resampling.LANCZOS)
            with BytesIO() as buffer:
                image.save(buffer, format="JPEG", quality=BARCODE_JPEG_QUALITY, optimize=True)
                return base64.b64encode(buffer.getvalue()).decode("utf-8")
    except Exception as e:
        logging.warning(f"[{request_id}] Could not prepare a photo for the focused tag re-read: {e}")
        return None


def build_llm_image_parts(
    asset_b64: str | None,
    barcode_b64: str | None,
) -> list[dict[str, str]]:
    """
    Images for vision LLM calls — both upload slots when available (barcode first, asset second).
    Matches pre-prd image order; prompts classify roles when field names are swapped.
    """
    parts: list[dict[str, str]] = []
    if barcode_b64:
        parts.append({"mime_type": "image/jpeg", "data": barcode_b64})
    if asset_b64 and asset_b64 != barcode_b64:
        parts.append({"mime_type": "image/jpeg", "data": asset_b64})
    return parts


def prepare_images_parallel(
    asset_b64: str | None,
    barcode_b64: str | None,
    request_id: str = "",
) -> tuple[str | None, str | None]:
    """Prepare both images independently.

    A single unusable image is dropped (returned as None) with a warning rather
    than raising — so one bad upload never discards a perfectly good sibling image
    or sinks the whole request. The caller decides what to do when both are None.
    """
    asset_result = asset_b64
    barcode_result = barcode_b64

    def _process(b64, max_edge, quality, is_barcode, label):
        if not b64:
            return b64
        try:
            return prepare_image_b64(
                b64, max_edge, quality, request_id=request_id, is_barcode=is_barcode
            )
        except Exception as e:
            logging.warning(f"[{request_id}] Dropping unusable {label} image: {e}")
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}
        if asset_b64:
            futures["asset"] = executor.submit(
                _process,
                asset_b64,
                effective_asset_image_max_edge(),
                IMAGE_JPEG_QUALITY,
                False,
                "asset",
            )
        if barcode_b64:
            futures["barcode"] = executor.submit(
                _process,
                barcode_b64,
                effective_barcode_image_max_edge(),
                BARCODE_JPEG_QUALITY,
                True,
                "barcode",
            )
        if "asset" in futures:
            asset_result = futures["asset"].result()
        if "barcode" in futures:
            barcode_result = futures["barcode"].result()

    return asset_result, barcode_result


def prepare_images_and_decode_parallel(
    asset_b64: str | None,
    barcode_b64: str | None,
    user_tagnumber=None,
    request_id: str = "",
) -> tuple[str | None, str | None, list[dict]]:
    """Resize both images and decode their barcodes in one pass.

    Decoding reads the ORIGINAL bytes, not the resized copies, and shares the
    pool with the resize work — so it overlaps rather than adding to wall clock.
    A decode failure yields [] and the caller falls back to the vision read.
    """
    decoded: list[dict] = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        decode_future = executor.submit(
            _decode_both,
            barcode_b64,
            asset_b64,
            user_tagnumber,
            request_id,
        )
        asset_result, barcode_result = prepare_images_parallel(
            asset_b64, barcode_b64, request_id=request_id
        )
        try:
            decoded = decode_future.result()
        except Exception as e:  # pragma: no cover - decode_* already swallow errors
            logging.warning(f"[{request_id}] Barcode decode task failed: {e}")
            decoded = []

    return asset_result, barcode_result, decoded


def _decode_both(
    barcode_b64: str | None,
    asset_b64: str | None,
    user_tagnumber,
    request_id: str,
) -> list[dict]:
    """Barcode image first, then asset image, de-duplicated by payload."""
    codes = decode_codes_from_image(barcode_b64, user_tagnumber, request_id, "barcode")
    seen = {c["value"] for c in codes}
    for entry in decode_codes_from_image(asset_b64, user_tagnumber, request_id, "asset"):
        if entry["value"] not in seen:
            seen.add(entry["value"])
            codes.append(entry)
    return codes


def validate_base64_size_first(b64_string: str, max_size_mb: int = 10) -> None:
    max_b64_size = max_size_mb * 1024 * 1024 * 4 // 3
    if len(b64_string) > max_b64_size:
        raise ValueError(f"Base64 image size exceeds {max_size_mb}MB limit")


def format_barcode_position(value) -> dict:
    if isinstance(value, dict):
        position_value = value.get("position")
    else:
        position_value = value

    if isinstance(position_value, str):
        if position_value.strip() == "":
            position_value = None
    elif position_value is not None:
        position_value = str(position_value)

    return {"position": position_value if position_value is not None else None}
