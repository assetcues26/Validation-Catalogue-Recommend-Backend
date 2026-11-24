"""Request / response contracts for Validation and Catalogue Recommend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ValidationCatalogueRequest:
    asset_image_bytes: bytes
    barcode_image_bytes: bytes
    customerid: int
    asset_name: str | None = None
    description: str | None = None
    tag_number: str | None = None
    categoryid: int | None = None
    assetclassid: int | None = None
    source: str = "multipart"  # multipart | json

    def user_asset_name(self) -> str:
        return str(self.asset_name or "").strip()

    def user_description(self) -> str:
        return str(self.description or "").strip()

    def user_tag_number(self) -> str:
        return str(self.tag_number or "").strip()

    def has_name_or_description(self) -> bool:
        return bool(self.user_asset_name() or self.user_description())

    def has_tag_number(self) -> bool:
        return bool(self.user_tag_number())


RESPONSE_KEYS = (
    # User-provided echoes
    "user_asset_name",
    "user_description",
    "user_tag_number",
    "customerid",
    "categoryid",
    "assetclassid",
    # Stage A — LLM generation (Additional Asset)
    "imageAnalysis",
    "detectedAsset",
    "detected_description",
    "condition",
    "decodedtagnumber",
    "detectedtagnumber",
    "tagnumbersource",
    "decodedtagnumbers",
    "detectedtagnumbers",
    "barcodereadability",
    "barcodeposition",
    "tag_number_status",
    "multiplebarcodesdetected",
    # Stage B — matching (validation)
    "namedescriptionmatch",
    "namedescriptionmatchpercent",
    "namedescriptionreasoning",
    "detectedtagnumbermatch",
    "detectedtagnumbermatchpercent",
    # Stage C — Pinecone / catalogue (Additional Asset)
    "assetclassname",
    "categoryname",
    "subcategoryid",
    "subcategoryname",
    "makemodelid",
    "makemodel",
    "recommendedcategory",
    "recommendedsubcategory",
    "recommendedmakemodel",
    "recommendedmodel",
    "recommendation_confidence",
    "selection_reasoning",
    # Meta
    "request_id",
    "llm_provider",
    "timings_ms",
    "llm_usage",
    "error",
    "matching_ran",
    "pinecone_mode",
)


def empty_response(
    request_id: str,
    *,
    customerid=None,
    categoryid=None,
    assetclassid=None,
    user_asset_name: str | None = None,
    user_description: str | None = None,
    user_tag_number: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {k: None for k in RESPONSE_KEYS}
    body["request_id"] = request_id
    body["customerid"] = customerid
    body["categoryid"] = categoryid
    body["assetclassid"] = assetclassid
    body["user_asset_name"] = user_asset_name or None
    body["user_description"] = user_description or None
    body["user_tag_number"] = user_tag_number or None
    body["llm_provider"] = "gemini"
    body["timings_ms"] = {}
    body["llm_usage"] = None
    body["error"] = None
    body["matching_ran"] = {"name_description": False, "tag": False}
    body["pinecone_mode"] = None
    return body


def to_external_response(internal: dict[str, Any]) -> dict[str, Any]:
    """Public PascalCase API: user fields + LLM generation + match + Pinecone join."""
    return {
        # User-provided
        "UserAssetName": internal.get("user_asset_name"),
        "UserDescription": internal.get("user_description"),
        "UserTagNumber": internal.get("user_tag_number"),
        "CustomerId": internal.get("customerid"),
        "CategoryId": internal.get("categoryid"),
        "AssetClassId": internal.get("assetclassid"),
        # LLM generation (Additional Asset Phase 1)
        "ImageAnalysis": internal.get("imageAnalysis"),
        "DetectedAsset": internal.get("detectedAsset"),
        "Description": internal.get("detected_description"),
        "AssetCondition": internal.get("condition"),
        "TagNumber": {
            "decodedtagnumber": internal.get("decodedtagnumber"),
            "detectedtagnumber": internal.get("detectedtagnumber"),
            "tagnumbersource": internal.get("tagnumbersource"),
            "barcodereadability": internal.get("barcodereadability"),
            "barcodeposition": internal.get("barcodeposition"),
        },
        # Matching (validation)
        "NameDescriptionMatch": internal.get("namedescriptionmatch"),
        "NameDescriptionMatchPercent": internal.get("namedescriptionmatchpercent"),
        "NameDescriptionReasoning": internal.get("namedescriptionreasoning"),
        "DetectedTagNumberMatch": internal.get("detectedtagnumbermatch"),
        "DetectedTagNumberMatchPercent": internal.get("detectedtagnumbermatchpercent"),
        "MatchingRan": internal.get("matching_ran"),
        # Catalogue / Pinecone join (Additional Asset)
        "AssetClassName": internal.get("assetclassname"),
        "CategoryName": internal.get("categoryname"),
        "SubCategoryId": internal.get("subcategoryid"),
        "SubCategoryName": internal.get("subcategoryname"),
        "MakeModelId": internal.get("makemodelid"),
        "MakeModel": internal.get("makemodel"),
        "PineconeMode": internal.get("pinecone_mode"),
        # Meta
        "request_id": internal.get("request_id"),
        "llm_provider": internal.get("llm_provider"),
        "timings_ms": internal.get("timings_ms"),
        "llm_usage": internal.get("llm_usage"),
        "error": internal.get("error"),
    }
