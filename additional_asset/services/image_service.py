"""Image prepare + pixel gate (ported from main image_service patterns)."""

from __future__ import annotations

import base64
import logging
from io import BytesIO

from PIL import Image

from additional_asset.core import config


def measure_frame_coverage(image) -> dict | None:
    try:
        small = image.convert("L")
        small.thumbnail((256, 256))
        px = sorted(small.getdata())
        n = len(px)
        if not n:
            return None
        median = px[n // 2]
        return {
            "dark": sum(1 for v in px if v < config.BLANK_IMAGE_DARK_LEVEL) / n,
            "flat": sum(1 for v in px if abs(v - median) <= config.BLANK_IMAGE_FLAT_TOLERANCE) / n,
            "median": median,
        }
    except Exception:
        return None


def is_frame_unusable(image, request_id: str = "", label: str = "asset") -> str | None:
    if not config.blank_image_gate_enabled():
        return None
    cov = measure_frame_coverage(image)
    if cov is None:
        return None
    reason = None
    if cov["dark"] >= config.BLANK_IMAGE_DARK_FRACTION:
        reason = f"unlit frame ({cov['dark']:.0%} of pixels black)"
    elif cov["flat"] >= config.BLANK_IMAGE_FLAT_FRACTION:
        reason = f"flat frame ({cov['flat']:.0%} of pixels one tone, median {cov['median']})"
    if reason:
        logging.warning(
            "[%s] Unusable %s image: %s — no asset can be present",
            request_id,
            label,
            reason,
        )
    return reason


def prepare_image_bytes(
    image_bytes: bytes,
    max_edge: int,
    jpeg_quality: int,
    request_id: str = "",
    label: str = "asset",
) -> tuple[bytes, dict]:
    """Validate/resize/JPEG-compress. Returns (jpeg_bytes, meta)."""
    meta: dict = {"label": label, "input_bytes": len(image_bytes)}
    if not image_bytes:
        raise ValueError(f"Empty {label} image")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise ValueError(f"{label} image exceeds 10MB limit")

    with Image.open(BytesIO(image_bytes)) as image:
        image.load()
        meta["width"] = image.width
        meta["height"] = image.height
        meta["format"] = image.format
        cov = measure_frame_coverage(image)
        if cov:
            meta["dark"] = round(cov["dark"], 4)
            meta["flat"] = round(cov["flat"], 4)
        unusable = is_frame_unusable(image, request_id, label)
        meta["unusable"] = unusable

        needs_resize = image.width > max_edge or image.height > max_edge
        work = image.convert("RGB")
        if needs_resize:
            work.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        buf = BytesIO()
        work.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        out = buf.getvalue()
        meta["output_bytes"] = len(out)
        meta["output_width"] = work.width
        meta["output_height"] = work.height
        return out, meta


def bytes_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
