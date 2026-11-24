"""Pinecone master-data lookup (ported/simplified from main)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from additional_asset.core import config
from additional_asset.services.gemini_client import generate_embedding

_index = None


def _metadata_id(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def _scope_filter(customer_id, asset_class_id, category_id) -> dict:
    """Always filter by customerid; add category/asset-class only when the caller provides them."""
    clauses = [{"customerid": {"$eq": customer_id}}]
    if config.PINECONE_ASSET_CLASS_SCOPE_ENABLED and asset_class_id is not None:
        clauses.append({"assetclassid": {"$eq": asset_class_id}})
    if category_id is not None:
        clauses.append({"categoryid": {"$eq": category_id}})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def get_pinecone_index():
    global _index
    if _index is not None:
        return _index
    api_key = os.getenv("PINECONE_API_KEY", "").strip()
    name = config.pinecone_index_name()
    if not api_key or not name:
        raise ValueError("PINECONE_API_KEY and PINECONE_INDEX_NAME are required for lookup")
    from pinecone import Pinecone

    pc = Pinecone(api_key=api_key)
    _index = pc.Index(name)
    return _index


def query_pinecone_masterdata(
    search_text: str,
    customer_id: int,
    category_id: int | None = None,
    *,
    asset_class_id: int | None = None,
    top_k: int | None = None,
    request_id: str = "",
) -> tuple[list[dict[str, Any]], float, float]:
    """Return (candidates, embed_ms, query_ms).

    CategoryId / AssetClassId are optional filters. When absent, search runs with
    customerid scope only (plus any ID the caller did provide).
    """
    embed_ms = 0.0
    query_ms = 0.0
    if not search_text.strip():
        return [], embed_ms, query_ms

    t0 = time.perf_counter()
    embedding = generate_embedding(search_text, request_id)
    embed_ms = (time.perf_counter() - t0) * 1000
    if not embedding:
        return [], embed_ms, query_ms

    try:
        metadata_filter = _scope_filter(customer_id, asset_class_id, category_id)
        index = get_pinecone_index()
        t1 = time.perf_counter()
        results = index.query(
            vector=embedding,
            top_k=top_k or config.PINECONE_TOP_K,
            filter=metadata_filter,
            include_metadata=True,
        )
        query_ms = (time.perf_counter() - t1) * 1000
        matches = results.get("matches", []) if isinstance(results, dict) else getattr(results, "matches", [])
        candidates: list[dict] = []
        for match in matches or []:
            if isinstance(match, dict):
                meta = dict(match.get("metadata") or {})
                meta["_score"] = match.get("score", 0)
            else:
                meta = dict(getattr(match, "metadata", None) or {})
                meta["_score"] = getattr(match, "score", 0)
            row_customer = _metadata_id(meta.get("customerid"))
            if row_customer is not None and row_customer != customer_id:
                continue
            if category_id is not None:
                row_cat = _metadata_id(meta.get("categoryid"))
                if row_cat is not None and row_cat != category_id:
                    continue
            if asset_class_id is not None and config.PINECONE_ASSET_CLASS_SCOPE_ENABLED:
                row_class = _metadata_id(meta.get("assetclassid"))
                if row_class is not None and row_class != asset_class_id:
                    continue
            candidates.append(meta)
        logging.info(
            "[%s] Pinecone query: %s candidates in %.0fms (embed %.0fms)",
            request_id,
            len(candidates),
            query_ms,
            embed_ms,
        )
        return candidates, embed_ms, query_ms
    except Exception as e:
        logging.error("[%s] Pinecone query failed: %s", request_id, e)
        return [], embed_ms, query_ms


_BLANK_MAKEMODEL = frozenset({"", "na", "n/a", "null", "none", "-", "nil", "unknown"})


def _norm_pinecone_value(value) -> str:
    return str(value or "").strip().casefold()


def makemodel_is_blank(value) -> bool:
    """True when selection intentionally omitted makemodel (null / NA / empty)."""
    return _norm_pinecone_value(value) in _BLANK_MAKEMODEL


def candidate_for_pick(candidates: list[dict], selection: dict) -> dict | None:
    """Resolve the LLM pick to a candidate row.

    - Subcategory + concrete makemodel → exact row only (no wrong-brand fallback).
    - Subcategory + blank/null makemodel → first row with that subcategory (ids for
      subcategory only; caller must clear makemodel fields).
    """
    if not candidates or not selection:
        return None

    sub = _norm_pinecone_value(selection.get("recommendedsubcategory"))
    makemodel = _norm_pinecone_value(selection.get("recommendedmakemodel"))
    if not sub and not makemodel:
        return None

    if makemodel and makemodel not in _BLANK_MAKEMODEL:
        for candidate in candidates:
            if (
                _norm_pinecone_value(candidate.get("subcategory")) == sub
                and _norm_pinecone_value(candidate.get("makemodel") or candidate.get("makemodelname"))
                == makemodel
            ):
                return candidate
        # Do NOT fall back to another brand's row when makemodel was specified.
        return None

    # Intentional null/NA makemodel: resolve subcategory id from any matching sub row.
    if sub:
        for candidate in candidates:
            if _norm_pinecone_value(candidate.get("subcategory")) == sub:
                return candidate
    return None


def ids_from_candidate(
    candidate: dict | None,
    *,
    clear_makemodel: bool = False,
) -> dict[str, Any]:
    if not candidate:
        return {
            "assetclassname": None,
            "assetclassid": None,
            "categoryname": None,
            "categoryid": None,
            "subcategoryname": None,
            "subcategoryid": None,
            "makemodel": None,
            "makemodelid": None,
        }
    ids = {
        "assetclassname": candidate.get("assetclass") or candidate.get("assetclassname"),
        "assetclassid": _metadata_id(candidate.get("assetclassid")),
        "categoryname": candidate.get("category") or candidate.get("categoryname"),
        "categoryid": _metadata_id(candidate.get("categoryid")),
        "subcategoryname": candidate.get("subcategory") or candidate.get("subcategoryname"),
        "subcategoryid": _metadata_id(candidate.get("subcategoryid")),
        "makemodel": candidate.get("makemodel") or candidate.get("makemodelname"),
        "makemodelid": _metadata_id(candidate.get("makemodelid")),
    }
    if clear_makemodel:
        ids["makemodel"] = None
        ids["makemodelid"] = None
    return ids
