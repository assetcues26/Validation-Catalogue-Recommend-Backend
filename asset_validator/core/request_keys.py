"""Case-insensitive request key normalization."""

from __future__ import annotations

from typing import Any

# Compact key (lowercase, no underscores) -> canonical internal field name.
_COMPACT_TO_CANONICAL: dict[str, str] = {
    "taggingai": "taggingai",
    "tagnumbercheck": "tagnumbercheck",
    "assetnamedescriptionmatch": "assetnamedescriptionmatch",
    "assetmakemodelmatch": "assetmakemodelmatch",
    "acquisitiondatecostmatch": "acquisitiondatecostmatch",
    "assetcondition": "assetcondition",
    "assetid": "assetid",
    "assettaggingdetailid": "assettaggingdetailid",
    "customerid": "customerid",
    "companyid": "companyid",
    "assetclassid": "assetclassid",
    "assetclassname": "assetclassname",
    "classnumber": "classnumber",
    "categoryid": "categoryid",
    "categoryname": "categoryname",
    "subcategoryid": "subcategoryid",
    "subcategoryname": "subcategoryname",
    "makemodelid": "makemodelid",
    "makemodelname": "makemodelname",
    "makemodel": "makemodel",
    "modelid": "modelid",
    "model": "model",
    "currency": "currency",
    "assetname": "assetname",
    "description": "description",
    "tagnumber": "tagnumber",
    "assetnumber": "assetnumber",
    "assetimage": "assetimage",
    "barcodeimage": "barcodeimage",
    "company": "company",
    "cost": "cost",
    "acquisitioncost": "cost",
    "acquisitiondate": "acquisitiondate",
}


def _compact_key(key: str) -> str:
    """Normalize key for lookup: lowercase, no underscores."""
    return key.lower().replace("_", "")


def normalize_request_keys(data: dict[str, Any]) -> dict[str, Any]:
    """
    Map request JSON keys to canonical lowercase internal names.

    Accepts any casing and optional underscores, e.g.:
    AssetImage, assetimage, ASSETIMAGE, asset_image -> assetimage
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            continue
        compact = _compact_key(key)
        canonical = _COMPACT_TO_CANONICAL.get(compact, compact)
        result[canonical] = value
    return result
