"""Pinecone master data lookup — embeddings and filtered vector search."""

import logging
import os
import threading
import time
from datetime import datetime

from google.genai import types
from pinecone import Pinecone

from asset_validator.services.gemini_service import get_genai_client
from asset_validator.utils.token_usage import record_usage

_pinecone_index = None
_pinecone_lock = threading.Lock()


class PineconeRateLimiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.minute_calls = 0
        self.hourly_calls = 0
        self.daily_calls = 0
        self.last_reset_minute = None
        self.last_reset_hour = None
        self.last_reset_day = None
        self.max_per_minute = int(os.getenv("PINECONE_MAX_CALLS_PER_MINUTE", "50"))
        self.max_per_hour = int(os.getenv("PINECONE_MAX_CALLS_PER_HOUR", "500"))
        self.max_per_day = int(os.getenv("PINECONE_MAX_CALLS_PER_DAY", "5000"))

    def check_and_record(self):
        with self.lock:
            now = datetime.now()
            if self.last_reset_day is None:
                self.last_reset_day = now.day
                self.last_reset_hour = now.hour
                self.last_reset_minute = now.minute
            if now.day != self.last_reset_day:
                self.daily_calls = 0
                self.last_reset_day = now.day
            if now.hour != self.last_reset_hour:
                self.hourly_calls = 0
                self.last_reset_hour = now.hour
            if now.minute != self.last_reset_minute:
                self.minute_calls = 0
                self.last_reset_minute = now.minute
            if self.minute_calls >= self.max_per_minute:
                raise Exception(f"PINECONE RATE LIMIT: {self.max_per_minute}/min exceeded")
            if self.hourly_calls >= self.max_per_hour:
                raise Exception(f"PINECONE RATE LIMIT: {self.max_per_hour}/hour exceeded")
            if self.daily_calls >= self.max_per_day:
                raise Exception(f"PINECONE RATE LIMIT: {self.max_per_day}/day exceeded")
            self.minute_calls += 1
            self.hourly_calls += 1
            self.daily_calls += 1


_rate_limiter = PineconeRateLimiter()


def get_pinecone_index():
    global _pinecone_index
    with _pinecone_lock:
        if _pinecone_index is None:
            api_key = os.getenv("PINECONE_API_KEY")
            index_name = os.getenv("PINECONE_INDEX_NAME", "masterdata")
            if not api_key:
                raise ValueError("PINECONE_API_KEY environment variable not set")
            pc = Pinecone(api_key=api_key)
            _pinecone_index = pc.Index(index_name)
            logging.info(f"Pinecone index '{index_name}' connected")
    return _pinecone_index


def _normalize_embedding_model(model_name: str) -> str:
    if model_name.startswith("models/"):
        return model_name[len("models/") :]
    return model_name


def generate_embedding(text: str, request_id: str = "") -> list:
    try:
        model_name = _normalize_embedding_model(
            os.getenv("EMBEDDING_MODEL_NAME", "text-embedding-004")
        )
        dimension = int(os.getenv("EMBEDDING_DIMENSION", "768"))
        start = time.time()
        client = get_genai_client()
        result = client.models.embed_content(
            model=model_name,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=dimension),
        )
        elapsed_ms = (time.time() - start) * 1000
        embedding = result.embeddings[0].values
        billable = getattr(result, "billable_character_count", None)
        if billable is None:
            meta = getattr(result, "metadata", None)
            if meta is not None:
                billable = getattr(meta, "billable_character_count", None)
        usage: dict[str, int] = {}
        if billable is not None:
            usage["prompt_tokens"] = int(billable)
            usage["total_tokens"] = int(billable)
        else:
            usage["prompt_tokens"] = len(text)
            usage["total_tokens"] = len(text)
        record_usage("embedding", model_name, usage, has_images=False)
        logging.info(
            f"[{request_id}] Embedding in {elapsed_ms:.0f}ms "
            f"(dims={len(embedding)}, text_len={len(text)})"
        )
        return embedding
    except Exception as e:
        logging.error(f"[{request_id}] Embedding generation failed: {e}")
        return []


def _metadata_id(value) -> int | None:
    """An id from a candidate's index metadata, or None when the row carries none.

    Pinecone returns every metadata number as a float (12.0), so helpers.to_int_or_none — which
    strips the point and reads 12.0 as 120 — cannot be used here. Company-level rows carry null
    levels; a missing, null or non-numeric value is no id.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return int(number) if number.is_integer() else None


def _scope_filter(customer_id, asset_class_id, category_id, filled: dict | None = None) -> dict:
    """The record's own customer, asset class and category (docs 6.6).

    With PINECONE_ASSET_CLASS_SCOPE_ENABLED off, or without an asset class id, this is exactly the
    filter deployed before: customer, and category when the request names one. `filled` narrows the
    lookup for a blank field to the field the customer did fill (docs 6.5): {"subcategoryid": 1064}
    adds one $eq clause to the same $and.
    """
    from asset_validator.core.config import PINECONE_ASSET_CLASS_SCOPE_ENABLED

    clauses = [{"customerid": {"$eq": customer_id}}]
    if PINECONE_ASSET_CLASS_SCOPE_ENABLED and asset_class_id is not None:
        clauses.append({"assetclassid": {"$eq": asset_class_id}})
    if category_id is not None:
        clauses.append({"categoryid": {"$eq": category_id}})
    for key, wanted in (filled or {}).items():
        clauses.append({key: {"$eq": wanted}})
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _same_category_name(candidate: dict, category_name: str) -> bool:
    """The row's category, by name, is the record's (docs 6.6).

    Judged by the same comparison the category status used before 2026-11-01, so a spelling variant
    ("IT Equipments" against "IT Equipment") is the same category and keeps its rows (review, 1 Nov 2026). A
    row without a category name cannot be shown to be in scope.
    """
    from asset_validator.utils.classification_match import evaluate_classification_field

    return any(
        evaluate_classification_field(category_name, candidate.get(key), field="category")[0] == "matched"
        for key in ("categoryname", "category")
    )


def _drop_out_of_scope_candidates(
    candidates: list, customer_id, asset_class_id, category_id, request_id: str = "",
    category_name: str | None = None,
) -> list:
    """No suggestion may come from another customer, asset class or category (docs 6.6).

    The filter already asks the index for the record's own scope; this holds even if the index
    returns a row outside it. Only a level the request names is compared, so a request without an
    asset class id keeps every asset class the filter returned, as before. A row whose metadata lacks
    a level the request names cannot be shown to be in scope, so it is dropped. A request without a
    category id is still held to its category by NAME (owner, 2026-11-01): the index cannot filter on
    a name reliably, so rows whose category name differs are dropped here.
    """
    from asset_validator.core.config import PINECONE_ASSET_CLASS_SCOPE_ENABLED, PINECONE_CATEGORY_SCOPE_ENABLED

    if not PINECONE_ASSET_CLASS_SCOPE_ENABLED:
        if not PINECONE_CATEGORY_SCOPE_ENABLED:
            return candidates
        # The customer and the category are fixed filters whatever the asset-class switch says (owner, 2026-11-02):
        # only the asset class is left uncompared.
        asset_class_id = None
    scope = {
        key: wanted
        for key, wanted in (("customerid", customer_id), ("assetclassid", asset_class_id),
                            ("categoryid", category_id))
        if wanted is not None
    }
    by_name = category_id is None and bool(str(category_name or "").strip())
    kept = [c for c in candidates
            if all(_metadata_id(c.get(key)) == wanted for key, wanted in scope.items())
            and (not by_name or _same_category_name(c, category_name))]
    if by_name:
        scope["categoryname"] = category_name
    if len(kept) != len(candidates):
        logging.warning(
            f"[{request_id}] Pinecone returned {len(candidates) - len(kept)} candidate(s) outside "
            f"the record's scope {scope}; dropped"
        )
    return kept


def _drop_outside_filled(candidates: list, filled: dict | None, request_id: str = "") -> list:
    """A blank field's suggestion comes from inside the field the customer did fill (docs 6.5).

    The filter already asks the index for it; this holds even if the index returns another row, and,
    unlike the scope drop, whatever PINECONE_ASSET_CLASS_SCOPE_ENABLED says: a row of another
    subcategory would look like a certain match for the wrong one. Ids are compared float-safely.
    """
    if not filled:
        return candidates
    kept = [c for c in candidates
            if all(_metadata_id(c.get(key)) == wanted for key, wanted in filled.items())]
    if len(kept) != len(candidates):
        logging.warning(
            f"[{request_id}] Pinecone returned {len(candidates) - len(kept)} candidate(s) outside "
            f"the filled field {filled}; dropped"
        )
    return kept


def query_pinecone_with_embedding(
    embedding: list,
    customer_id: int,
    category_id: int = None,
    top_k: int = 5,
    request_id: str = "",
    asset_class_id: int = None,
    category_name: str | None = None,
    filled_filter: dict | None = None,
    stats: dict | None = None,
) -> list:
    """`filled_filter` narrows a blank field's lookup to the field the customer filled (docs 6.5). `stats`, when
    given, receives "returned": how many rows the index sent before any drop. Fewer than top_k means every row the
    filter allows came back, so none was hidden past the cut."""
    if not embedding:
        return []
    from asset_validator.core.config import PINECONE_CATEGORY_SCOPE_ENABLED

    # Without a category, by id or by name, there is no fixed category to hold the lookup to (owner, 2026-11-02; docs
    # 6.6): no query is made, so no read units are spent, and the record is treated as having no master data.
    if PINECONE_CATEGORY_SCOPE_ENABLED and category_id is None and not str(category_name or "").strip():
        logging.warning(f"[{request_id}] Record has no category id or name: master data not searched (docs 6.6)")
        return []
    try:
        _rate_limiter.check_and_record()
        metadata_filter = _scope_filter(customer_id, asset_class_id, category_id, filled_filter)
        index = get_pinecone_index()
        start = time.time()
        results = index.query(
            vector=embedding,
            top_k=top_k,
            filter=metadata_filter,
            include_metadata=True,
        )
        elapsed_ms = (time.time() - start) * 1000
        matches = results.get("matches", [])
        if stats is not None:
            stats["returned"] = len(matches)
        logging.info(
            f"[{request_id}] Pinecone query: {len(matches)} candidates in {elapsed_ms:.0f}ms"
        )
        candidates = []
        for match in matches:
            meta = match.get("metadata", {})
            meta["_score"] = match.get("score", 0)
            candidates.append(meta)
        candidates = _drop_out_of_scope_candidates(
            candidates, customer_id, asset_class_id, category_id, request_id, category_name=category_name
        )
        return _drop_outside_filled(candidates, filled_filter, request_id)
    except Exception as e:
        logging.error(f"[{request_id}] Pinecone query failed: {e}")
        return []


def query_pinecone_masterdata(
    search_text: str,
    customer_id: int,
    category_id: int = None,
    top_k: int = 5,
    request_id: str = "",
    prefetched_embedding: list = None,
    asset_class_id: int = None,
    category_name: str | None = None,
    filled_filter: dict | None = None,
    stats: dict | None = None,
) -> tuple:
    embed_ms = 0.0
    query_ms = 0.0
    try:
        embedding = prefetched_embedding
        if embedding:
            logging.info(f"[{request_id}] Using prefetched embedding")
        else:
            embed_start = time.time()
            embedding = generate_embedding(search_text, request_id)
            embed_ms = (time.time() - embed_start) * 1000
        if not embedding:
            return [], embed_ms, query_ms
        query_start = time.time()
        candidates = query_pinecone_with_embedding(
            embedding=embedding,
            customer_id=customer_id,
            category_id=category_id,
            top_k=top_k,
            request_id=request_id,
            asset_class_id=asset_class_id,
            category_name=category_name,
            filled_filter=filled_filter,
            stats=stats,
        )
        query_ms = (time.time() - query_start) * 1000
        return candidates, embed_ms, query_ms
    except Exception as e:
        logging.error(f"[{request_id}] Pinecone master data query failed: {e}")
        return [], embed_ms, query_ms


def pinecone_auto_select(candidates: list) -> dict:
    top = candidates[0]
    score = float(top.get("_score", 0))
    return {
        "recommendedcategory": top.get("category"),
        "recommendedsubcategory": top.get("subcategory"),
        "recommendedmakemodel": top.get("makemodel"),
        "recommendedmodel": top.get("model"),
        "selection_reasoning": f"Auto-selected top match (score={score:.3f})",
    }
