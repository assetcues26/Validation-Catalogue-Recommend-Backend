"""Request / response contracts for Additional Asset."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdditionalAssetRequest:
    asset_image_bytes: bytes
    barcode_image_bytes: bytes
    customerid: int
    categoryid: int | None = None
    assetclassid: int | None = None
    source: str = "multipart"  # multipart | json


@dataclass
class StepLog:
    phase: str
    detail: dict[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None


# Internal pipeline keys (LLM / join). Mapped to PascalCase at HTTP boundary.
RESPONSE_KEYS = (
    "imageAnalysis",
    "detectedAsset",
    "detected_description",
    "condition",
    "decodedtagnumber",
    "detectedtagnumber",
    "tagnumbersource",
    "customerid",
    "assetclassid",
    "assetclassname",
    "categoryid",
    "categoryname",
    "subcategoryid",
    "subcategoryname",
    "makemodelid",
    "makemodel",
    "request_id",
    "llm_provider",
    "timings_ms",
    "llm_usage",
    "error",
)


def empty_response(request_id: str, *, customerid=None, categoryid=None, assetclassid=None) -> dict[str, Any]:
    body: dict[str, Any] = {k: None for k in RESPONSE_KEYS}
    body["request_id"] = request_id
    body["customerid"] = customerid
    body["categoryid"] = categoryid
    body["assetclassid"] = assetclassid
    body["llm_provider"] = "gemini"
    body["timings_ms"] = {}
    body["llm_usage"] = None
    body["error"] = None
    return body


def to_external_response(internal: dict[str, Any]) -> dict[str, Any]:
    """Map internal pipeline keys to the public PascalCase API contract."""
    return {
        "ImageAnalysis": internal.get("imageAnalysis"),
        "AssetName": internal.get("detectedAsset"),
        "Description": internal.get("detected_description"),
        "AssetClassName": internal.get("assetclassname"),
        "MakeModel": internal.get("makemodel"),
        "CategoryName": internal.get("categoryname"),
        "SubCategoryName": internal.get("subcategoryname"),
        "MakeModelId": internal.get("makemodelid"),
        "CategoryId": internal.get("categoryid"),
        "CustomerId": internal.get("customerid"),
        "AssetClassId": internal.get("assetclassid"),
        "SubCategoryId": internal.get("subcategoryid"),
        "AssetCondition": internal.get("condition"),
        "TagNumber": {
            "decodedtagnumber": internal.get("decodedtagnumber"),
            "detectedtagnumber": internal.get("detectedtagnumber"),
            "tagnumbersource": internal.get("tagnumbersource"),
        },
        "request_id": internal.get("request_id"),
        "llm_provider": internal.get("llm_provider"),
        "timings_ms": internal.get("timings_ms"),
        "llm_usage": internal.get("llm_usage"),
        "error": internal.get("error"),
    }
