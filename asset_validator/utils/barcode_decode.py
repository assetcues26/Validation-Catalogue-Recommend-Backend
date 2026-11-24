"""Deterministic QR/barcode decoding of the tag label.

Purely additive to the vision-based tag read: every function here returns an
empty result on any failure, so a decode miss leaves the existing detection path
byte-for-byte unchanged. A decoded symbol carries its own checksum/ECC, so it is
either correct or absent — it cannot misread a digit the way OCR can.
"""

from __future__ import annotations

import base64
import io
import logging
import os

# Soft import: a missing or broken native wheel must degrade to vision-only,
# never take down the function app at import time.
try:
    import zxingcpp as _zxing
except Exception as _import_error:  # pragma: no cover - depends on deployment image
    _zxing = None
    logging.warning(
        f"Barcode decoding unavailable, falling back to vision-only tag read: {_import_error}"
    )


# Symbologies whose checksum/ECC is strong enough to trust without a second opinion.
# Anything outside this set is ignored rather than fed into tag resolution.
TRUSTED_FORMATS = frozenset({
    "QRCode",
    "MicroQRCode",
    "RMQRCode",
    "DataMatrix",
    "Aztec",
    "PDF417",
    "Code128",
    "Code93",
    "EAN13",
    "EAN8",
    "UPCA",
    "UPCE",
    "ITF",
})


def barcode_decode_enabled() -> bool:
    """Kill-switch. Decoding is on unless explicitly disabled."""
    return os.getenv("BARCODE_DECODE_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def barcode_enhance_enabled() -> bool:
    """Kill-switch for the retry ladder below. On unless explicitly disabled."""
    return os.getenv("BARCODE_DECODE_ENHANCE_ENABLED", "true").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _enhanced_variants(image):
    """Cheapest-first retries for a label the plain read could not decode.

    zxing already tries 90/270 rotation, downscaling and inversion by default, so nothing here
    repeats those. What it does NOT do is help a SMALL symbol in a large frame, or one flattened
    by glare: the plant tags are a few hundred pixels of QR inside a 720p photo.

    Measured on the 13 tag failures of the 250-record preprod run (2026-09-22), by whether the
    DECLARED tag was decoded -- a QR carries its own ECC, so a hit is exact, never a near miss:
      upscale2 + autocontrast  5/13
      grayscale + sharpen      +1  (G152)
      upscale2 + GlobalHistogram +1  (G398)
    Seven of the thirteen in total. Of the rest, two decode to a DIFFERENT number than declared
    (a real mismatch, and they must keep failing) and four decode nothing under any variant.
    """
    from PIL import Image, ImageFilter, ImageOps

    grey = image.convert("L")

    def upscaled(img, factor=2):
        return img.resize((img.width * factor, img.height * factor), Image.LANCZOS)

    # (variant image, read kwargs)
    yield upscaled(ImageOps.autocontrast(grey)).convert("RGB"), {}
    yield grey.filter(ImageFilter.SHARPEN).convert("RGB"), {}
    yield upscaled(image.convert("RGB")), {"binarizer": _zxing.Binarizer.GlobalHistogram}

    # Last: stretch the contrast of each REGION rather than the frame. The variants above
    # autocontrast the whole photo, which is dominated by a pale enclosure or wall, so a tag
    # covering ~5% of the frame barely moves the histogram -- and upscaling a low-contrast patch
    # only yields a bigger low-contrast patch. The tags are etched brown-on-grey, not printed
    # black-on-white, so local contrast is the thing that matters. Recovers G068 and G487.
    for tile in _tiles(image):
        yield tile, {}


def _tiles(image, grid: int = 2, overlap: float = 0.5):
    """Half-frame windows at 50% overlap, each locally contrast-stretched and upscaled.

    Overlapping matters: a tag straddling a tile boundary is cut in half in every window that
    touches it. 3x3 windows over a half-size tile = 9 reads, ~0.7s to a hit and ~1.6s to give up.
    """
    from PIL import Image, ImageOps

    width, height = image.size
    tile_w, tile_h = width // grid, height // grid
    step_x, step_y = max(int(tile_w * (1 - overlap)), 1), max(int(tile_h * (1 - overlap)), 1)
    for top in range(0, max(height - tile_h, 0) + 1, step_y):
        for left in range(0, max(width - tile_w, 0) + 1, step_x):
            crop = image.crop((left, top, left + tile_w, top + tile_h)).convert("L")
            crop = ImageOps.autocontrast(crop)
            yield crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS).convert("RGB")


def _has_trusted(results) -> bool:
    """Whether any decoded symbol is one decode_codes_from_image will keep."""
    return any(
        _normalize_format(getattr(r, "format", "")) in _TRUSTED_FORMATS_NORMALIZED
        and (getattr(r, "text", "") or "").strip()
        for r in results or []
    )


def _read_symbols(image, request_id: str = "", label: str = "barcode"):
    """Plain read, then the enhancement ladder only if it found no TRUSTED symbol.

    A decode that succeeds first time costs exactly what it did before; the ladder is paid only
    on the ~12% of photos the plain read misses. A symbol the caller discards (Code 39, Codabar)
    does not count as a hit: on a label carrying one beside a QR code, the QR would otherwise
    never be retried. When the ladder finds nothing trusted, the plain read is returned so the
    caller still logs what it ignored.
    """
    plain = _zxing.read_barcodes(image.convert("RGB"))
    if _has_trusted(plain) or not barcode_enhance_enabled():
        return plain

    for index, (variant, kwargs) in enumerate(_enhanced_variants(image), start=1):
        try:
            results = _zxing.read_barcodes(variant, **kwargs)
        except Exception as e:  # a variant must never break the decode path
            logging.warning(f"[{request_id}] Decode retry {index} failed on {label}: {e}")
            continue
        if _has_trusted(results):
            logging.info(f"[{request_id}] Decoded {label} on retry {index} after a plain miss")
            return results
    return plain


def decoder_available() -> bool:
    return _zxing is not None


def _normalize_format(value) -> str:
    """Canonical symbology name, tolerant of zxing-cpp version differences.

    Across versions the format renders as "QRCode", "QR Code", or
    "BarcodeFormat.QRCode". Matching the raw string against the whitelist would
    silently reject every symbol on a version we did not test — the feature would
    look installed and quietly do nothing.
    """
    name = str(value or "")
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    for ch in (" ", "_", "-"):
        name = name.replace(ch, "")
    return name.upper()


_TRUSTED_FORMATS_NORMALIZED = frozenset(_normalize_format(f) for f in TRUSTED_FORMATS)


def _classify(text: str, user_tagnumber) -> str:
    """Label a decoded payload so it slots into the existing code-type vocabulary."""
    val = str(text).strip()
    user = str(user_tagnumber).strip() if user_tagnumber else ""
    if user and val == user:
        return "asset_tag"
    if val.isdigit() and 4 <= len(val) <= 20:
        return "asset_tag"
    return "barcode"


def decode_codes_from_image(
    b64_string: str | None,
    user_tagnumber=None,
    request_id: str = "",
    label: str = "barcode",
) -> list[dict]:
    """Decode every readable symbol in an image. Returns [] on any failure.

    Runs against the ORIGINAL upload bytes rather than the LLM-bound copy: the
    resize to 512-768px measurably costs decode range on small labels.
    """
    if not b64_string or _zxing is None or not barcode_decode_enabled():
        return []

    try:
        raw = base64.b64decode(b64_string)
        with io.BytesIO(raw) as buffer:
            from PIL import Image

            with Image.open(buffer) as image:
                results = _read_symbols(image, request_id, label)
    except Exception as e:
        logging.warning(f"[{request_id}] Barcode decode failed on {label} image: {e}")
        return []

    codes: list[dict] = []
    seen: set[str] = set()
    for result in results or []:
        text = (getattr(result, "text", "") or "").strip()
        if not text or text in seen:
            continue
        fmt = _normalize_format(getattr(result, "format", ""))
        if fmt not in _TRUSTED_FORMATS_NORMALIZED:
            logging.info(f"[{request_id}] Ignoring untrusted symbology {fmt!r} on {label}")
            continue
        seen.add(text)
        codes.append({
            "type": _classify(text, user_tagnumber),
            "value": text,
            "format": fmt,
            "source": "decoded",
            "image": label,
        })

    if codes:
        logging.info(
            f"[{request_id}] Decoded {len(codes)} symbol(s) from {label} image: "
            + ", ".join(f"{c['format']}={c['value']}" for c in codes)
        )
    return codes


def decode_codes_from_images(
    barcode_b64: str | None,
    asset_b64: str | None,
    user_tagnumber=None,
    request_id: str = "",
) -> list[dict]:
    """Decode both uploads, barcode image first, de-duplicated by value."""
    if _zxing is None or not barcode_decode_enabled():
        return []

    codes = decode_codes_from_image(barcode_b64, user_tagnumber, request_id, "barcode")
    seen = {c["value"] for c in codes}
    for entry in decode_codes_from_image(asset_b64, user_tagnumber, request_id, "asset"):
        if entry["value"] not in seen:
            seen.add(entry["value"])
            codes.append(entry)
    return codes


def decoded_values(codes: list[dict] | None) -> list[str]:
    """Ordered, de-duplicated payloads of decoded symbols only."""
    values: list[str] = []
    seen: set[str] = set()
    for entry in codes or []:
        if entry.get("source") != "decoded":
            continue
        val = str(entry.get("value", "")).strip()
        if val and val not in seen:
            seen.add(val)
            values.append(val)
    return values
