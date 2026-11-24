"""FastAPI entry — Validation and Catalogue Recommend.

HTTP shell only. Business logic is wired from:
  - additional_asset/  (Additional Asset FastAPI — generation + Pinecone)
  - asset_validator/   (validation-ai-backend — name/description + tag match)
  - validation_catalogue/services/pipeline.py (orchestration only)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from additional_asset.core import config
from additional_asset.utils.helpers import to_int_or_none
from validation_catalogue.core.models import ValidationCatalogueRequest, to_external_response
from validation_catalogue.core.payload_guards import (
    MAX_ASSET_NAME_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_IMAGE_BYTES,
    MAX_REQUEST_BODY_BYTES,
    MAX_TAG_NUMBER_CHARS,
    decode_and_validate_image_bytes,
    estimate_b64_decoded_size,
    require_content_length_ok,
    sanitize_text,
    validate_positive_id,
)
from validation_catalogue.services.pipeline import run_validation_catalogue

load_dotenv()


def configure_logging() -> None:
    """Make StepLogger / Gemini errors visible under uvicorn --reload."""
    level_name = config.log_level()
    level = getattr(logging, level_name, logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(level)
        handler.setFormatter(fmt)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setLevel(level)
            if not handler.formatter:
                handler.setFormatter(fmt)

    for name in (
        "validation_catalogue",
        "additional_asset",
        "asset_validator",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
    ):
        log = logging.getLogger(name)
        log.setLevel(level)
        log.propagate = True


configure_logging()

app = FastAPI(
    title="validation-catalogue",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.on_event("startup")
def _on_startup() -> None:
    # Uvicorn may reconfigure logging after import; re-apply so console stays useful.
    configure_logging()
    logging.getLogger("validation_catalogue").info(
        "validation-catalogue logging ready level=%s debug_file=%s "
        "max_body=%s max_image=%s",
        config.log_level(),
        config.debug_log_to_file(),
        MAX_REQUEST_BODY_BYTES,
        MAX_IMAGE_BYTES,
    )


# CORS: never pair allow_credentials=True with wildcard origins.
_cors_raw = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()]
_cors_wildcard = _cors_raw == ["*"] or not _cors_raw
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_wildcard else _cors_raw,
    allow_credentials=False if _cors_wildcard else True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)


def _error(request_id: str, code: str, detail: str, status: int = 400) -> JSONResponse:
    # Keep client-facing detail short; never echo raw bodies or secrets.
    safe_detail = str(detail or "")[:500]
    body = {
        "request_id": request_id,
        "error": code,
        "detail": safe_detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(content=body, status_code=status)


def _decode_b64_image(value: Any, field: str) -> bytes:
    if value is None:
        raise ValueError(f"Missing required field: {field}")
    if isinstance(value, bytes):
        return decode_and_validate_image_bytes(value, field=field)
    if isinstance(value, bytearray):
        return decode_and_validate_image_bytes(bytes(value), field=field)
    if not isinstance(value, str):
        raise ValueError(f"Invalid type for {field}: expected base64 string")

    text = value.strip()
    if not text:
        raise ValueError(f"Missing required field: {field}")
    if "," in text and text.lower().startswith("data:"):
        # data:[<mediatype>][;base64],<data>
        header, text = text.split(",", 1)
        if ";base64" not in header.lower():
            raise ValueError(f"Invalid data-URL for {field}: expected base64")

    # Reject pathological base64 before allocating decoded buffer.
    est = estimate_b64_decoded_size(text)
    if est > MAX_IMAGE_BYTES:
        raise ValueError(
            f"{field} exceeds maximum size of {MAX_IMAGE_BYTES} bytes "
            f"(estimated decoded size {est})"
        )
    # Cap encoded length roughly as well (base64 expands ~4/3).
    if len(text) > (MAX_IMAGE_BYTES * 4) // 3 + 1024:
        raise ValueError(f"{field} base64 payload is too large")

    try:
        raw = base64.b64decode(text, validate=False)
    except Exception as exc:
        raise ValueError(f"Invalid base64 for {field}") from exc
    return decode_and_validate_image_bytes(raw, field=field)


async def _file_bytes(item: Any, *, field: str) -> bytes:
    if hasattr(item, "read"):
        data = await item.read()
        if isinstance(data, str):
            # Unexpected text upload — treat as UTF-8 bytes then validate as image.
            data = data.encode("utf-8", errors="ignore")
        return decode_and_validate_image_bytes(data or b"", field=field)
    if isinstance(item, (bytes, bytearray)):
        return decode_and_validate_image_bytes(bytes(item), field=field)
    raise ValueError(f"Expected uploaded file for {field}")


def _guard_request_size(request: Request) -> None:
    require_content_length_ok(request.headers.get("content-length"))


async def _parse_multipart(request: Request) -> ValidationCatalogueRequest:
    if not config.accept_multipart():
        raise ValueError("Multipart transport is disabled")
    _guard_request_size(request)

    form = await request.form()
    asset_file = form.get("assetimage") or form.get("AssetImage")
    barcode_file = form.get("barcodeimage") or form.get("BarcodeImage")
    if asset_file is None:
        raise ValueError("Missing required field: assetimage")
    if barcode_file is None:
        raise ValueError("Missing required field: barcodeimage")

    customerid = validate_positive_id(
        to_int_or_none(form.get("customerid") or form.get("CustomerId")),
        field="customerid",
        required=True,
    )
    categoryid = validate_positive_id(
        to_int_or_none(form.get("categoryid") or form.get("CategoryId")),
        field="categoryid",
    )
    assetclassid = validate_positive_id(
        to_int_or_none(form.get("assetclassid") or form.get("AssetClassId")),
        field="assetclassid",
    )

    return ValidationCatalogueRequest(
        asset_image_bytes=await _file_bytes(asset_file, field="assetimage"),
        barcode_image_bytes=await _file_bytes(barcode_file, field="barcodeimage"),
        customerid=customerid,  # type: ignore[arg-type]
        asset_name=sanitize_text(
            form.get("assetname") or form.get("AssetName"),
            field="AssetName",
            max_chars=MAX_ASSET_NAME_CHARS,
        ),
        description=sanitize_text(
            form.get("description") or form.get("Description"),
            field="Description",
            max_chars=MAX_DESCRIPTION_CHARS,
        ),
        tag_number=sanitize_text(
            form.get("tagnumber")
            or form.get("TagNumber")
            or form.get("barcode")
            or form.get("Barcode"),
            field="TagNumber",
            max_chars=MAX_TAG_NUMBER_CHARS,
        ),
        categoryid=categoryid,
        assetclassid=assetclassid,
        source="multipart",
    )


async def _parse_json(request: Request) -> ValidationCatalogueRequest:
    if not config.accept_json():
        raise ValueError("JSON transport is disabled")
    _guard_request_size(request)

    try:
        body = await request.json()
    except Exception as exc:
        raise ValueError(f"Invalid JSON body: {exc}") from exc
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    # Shallow key-count guard against absurd objects.
    if len(body) > 64:
        raise ValueError("JSON body has too many fields")

    def pick(*keys: str) -> Any:
        for k in keys:
            if k in body and body[k] is not None:
                return body[k]
        return None

    customerid = validate_positive_id(
        to_int_or_none(pick("customerid", "CustomerId")),
        field="customerid",
        required=True,
    )
    categoryid = validate_positive_id(
        to_int_or_none(pick("categoryid", "CategoryId")),
        field="categoryid",
    )
    assetclassid = validate_positive_id(
        to_int_or_none(pick("assetclassid", "AssetClassId")),
        field="assetclassid",
    )

    return ValidationCatalogueRequest(
        asset_image_bytes=_decode_b64_image(pick("assetimage", "AssetImage"), "assetimage"),
        barcode_image_bytes=_decode_b64_image(pick("barcodeimage", "BarcodeImage"), "barcodeimage"),
        customerid=customerid,  # type: ignore[arg-type]
        asset_name=sanitize_text(
            pick("assetname", "AssetName"),
            field="AssetName",
            max_chars=MAX_ASSET_NAME_CHARS,
        ),
        description=sanitize_text(
            pick("description", "Description"),
            field="Description",
            max_chars=MAX_DESCRIPTION_CHARS,
        ),
        tag_number=sanitize_text(
            pick("tagnumber", "TagNumber", "barcode", "Barcode"),
            field="TagNumber",
            max_chars=MAX_TAG_NUMBER_CHARS,
        ),
        categoryid=categoryid,
        assetclassid=assetclassid,
        source="json",
    )


async def _parse_request(request: Request) -> ValidationCatalogueRequest:
    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        return await _parse_multipart(request)
    if "application/json" in content_type:
        return await _parse_json(request)
    # Fallback: try JSON then multipart (same as Additional Asset)
    try:
        return await _parse_json(request)
    except ValueError:
        return await _parse_multipart(request)


@app.post("/api/validation_catalogue")
async def validation_catalogue(request: Request) -> JSONResponse:
    request_id = str(uuid.uuid4())
    content_type = (request.headers.get("content-type") or "")[:80]
    logging.info(
        "[%s] HTTP POST /api/validation_catalogue content_type=%s",
        request_id,
        content_type,
    )
    try:
        # Early reject oversized Content-Length before reading body into memory.
        _guard_request_size(request)
        parsed = await _parse_request(request)
    except ValueError as exc:
        logging.warning("[%s] payload validation failed: %s", request_id, exc)
        return _error(request_id, "PAYLOAD_VALIDATION_FAILED", str(exc), 400)
    except Exception as exc:
        logging.exception("[%s] parse failed", request_id)
        return _error(request_id, "PAYLOAD_VALIDATION_FAILED", "Unable to parse request", 400)

    logging.info(
        "[%s] parsed source=%s customerid=%s has_name=%s has_desc=%s has_tag=%s "
        "categoryid=%s assetclassid=%s asset_bytes=%s barcode_bytes=%s",
        request_id,
        parsed.source,
        parsed.customerid,
        bool(parsed.user_asset_name()),
        bool(parsed.user_description()),
        bool(parsed.user_tag_number()),
        parsed.categoryid,
        parsed.assetclassid,
        len(parsed.asset_image_bytes or b""),
        len(parsed.barcode_image_bytes or b""),
    )

    try:
        result = await asyncio.to_thread(run_validation_catalogue, parsed, request_id)
        if result.get("error") and str(result["error"]).startswith("IMAGE_"):
            status = 400
        elif result.get("error") and str(result["error"]).startswith("PHASE1_"):
            status = 502
        else:
            status = 200
        err = result.get("error")
        log_done = logging.error if err else logging.info
        usage = result.get("llm_usage") or {}
        log_done(
            "[%s] done status=%s total_ms=%s name_match=%s tag_match=%s pinecone_mode=%s "
            "llm_calls=%s by_phase=%s error=%s debug=%s",
            request_id,
            status,
            (result.get("timings_ms") or {}).get("total"),
            result.get("namedescriptionmatch"),
            result.get("detectedtagnumbermatch"),
            result.get("pinecone_mode"),
            usage.get("llm_calls"),
            list((usage.get("by_phase") or {}).keys()),
            err,
            f"debug/{request_id}.jsonl" if config.debug_log_to_file() else None,
        )
        return JSONResponse(content=to_external_response(result), status_code=status)
    except Exception as exc:
        logging.exception("[%s] unhandled", request_id)
        return _error(request_id, "INTERNAL_SERVER_ERROR", "Internal server error", 500)


@app.get("/api/health")
async def health() -> JSONResponse:
    body = {
        "status": "healthy",
        "service": "validation-catalogue",
        "model_active": config.gemini_model_name(),
        "text_model": config.gemini_text_model_name(),
        "pinecone_lookup": config.include_pinecone_lookup(),
        "pixel_gate": config.blank_image_gate_enabled(),
        "phase1_content_route": config.phase1_content_route_enabled(),
        "blind_view": config.blind_view_enabled(),
        "accept_multipart": config.accept_multipart(),
        "accept_json": config.accept_json(),
        "limits": {
            "max_request_body_bytes": MAX_REQUEST_BODY_BYTES,
            "max_image_bytes": MAX_IMAGE_BYTES,
            "max_asset_name_chars": MAX_ASSET_NAME_CHARS,
            "max_description_chars": MAX_DESCRIPTION_CHARS,
            "max_tag_number_chars": MAX_TAG_NUMBER_CHARS,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(content=body, status_code=200)
