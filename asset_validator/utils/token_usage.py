"""Per-request LLM token usage collection (structured logs only)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

_request_usage_local = threading.local()


@dataclass
class TokenUsageRecord:
    phase: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    has_images: bool = False
    # Cache hits are a SUBSET of prompt_tokens, billed at a reduced rate.
    cached_tokens: int = 0


@dataclass
class RequestTokenUsage:
    records: list[TokenUsageRecord] = field(default_factory=list)

    def add(
        self,
        *,
        phase: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int | None = None,
        has_images: bool = False,
        cached_tokens: int = 0,
    ) -> None:
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens
        self.records.append(
            TokenUsageRecord(
                phase=phase,
                model=model or "",
                prompt_tokens=max(prompt_tokens, 0),
                completion_tokens=max(completion_tokens, 0),
                total_tokens=max(total_tokens, 0),
                has_images=has_images,
                cached_tokens=max(cached_tokens, 0),
            )
        )

    def totals(self) -> dict[str, int]:
        prompt = sum(r.prompt_tokens for r in self.records)
        cached = sum(r.cached_tokens for r in self.records)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": sum(r.completion_tokens for r in self.records),
            "total_tokens": sum(r.total_tokens for r in self.records),
            "llm_calls": len(self.records),
            "cached_tokens": cached,
            # Input actually billed at full rate.
            "uncached_prompt_tokens": max(prompt - cached, 0),
            "cache_hit_percent": round(cached / prompt * 100, 1) if prompt else 0.0,
        }

    def by_phase(self) -> dict[str, dict[str, int]]:
        phases: dict[str, dict[str, int]] = {}
        for record in self.records:
            bucket = phases.setdefault(
                record.phase,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "calls": 0,
                    "cached_tokens": 0,
                },
            )
            bucket["prompt_tokens"] += record.prompt_tokens
            bucket["completion_tokens"] += record.completion_tokens
            bucket["total_tokens"] += record.total_tokens
            bucket["cached_tokens"] += record.cached_tokens
            bucket["calls"] += 1
        return phases


def start_request_usage() -> None:
    _request_usage_local.usage = RequestTokenUsage()


def get_request_usage() -> RequestTokenUsage | None:
    return getattr(_request_usage_local, "usage", None)


def bind_request_usage(usage: RequestTokenUsage | None) -> None:
    """Attach the request's collector to the CURRENT thread.

    The collector is thread-local, so a worker thread starts with none and record_usage
    silently drops everything it records — the call happens and is billed, but never
    shows up in the request's token totals. Call this at the top of a worker with the
    collector captured on the request thread. Safe to share: add() only appends.
    """
    if usage is not None:
        _request_usage_local.usage = usage


def clear_request_usage() -> None:
    if hasattr(_request_usage_local, "usage"):
        del _request_usage_local.usage


def extract_gemini_usage(response: Any) -> dict[str, int]:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return {}
    prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
    completion = int(getattr(meta, "candidates_token_count", 0) or 0)
    total = int(getattr(meta, "total_token_count", 0) or 0)
    if total <= 0:
        total = prompt + completion
    # Implicit/explicit context cache hits; absent (None) when nothing was cached.
    cached = int(getattr(meta, "cached_content_token_count", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
    }


def extract_gpt_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion = int(getattr(usage, "completion_tokens", 0) or 0)
    total = int(getattr(usage, "total_tokens", 0) or 0)
    if total <= 0:
        total = prompt + completion
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
    }


def record_usage(
    phase: str,
    model: str,
    usage: dict[str, int],
    *,
    has_images: bool = False,
) -> None:
    """Record token usage for the active request; no-op if no collector is active."""
    collector = get_request_usage()
    if collector is None or not usage:
        return
    collector.add(
        phase=phase,
        model=model,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens"),
        has_images=has_images,
        cached_tokens=usage.get("cached_tokens", 0),
    )
