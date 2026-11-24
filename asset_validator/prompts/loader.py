"""Load and cache prompt templates from prompts/md/."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "md"
_SECTION_MARKER = re.compile(r"<!--\s*section:\s*(\w+)\s*-->", re.IGNORECASE)

# Per-asset data blocks are wrapped in these markers so they can be relocated to the
# end of a prompt (turning the static instructions into a cacheable prefix) without
# rewording anything. See hoist_variable_context.
VARCTX_START = "[[VARCTX_START]]"
VARCTX_END = "[[VARCTX_END]]"
_VARCTX_BLOCK = re.compile(
    r"[ \t]*\[\[VARCTX_START\]\][ \t]*\n(.*?)\n[ \t]*\[\[VARCTX_END\]\][ \t]*\n?",
    re.DOTALL,
)
_VARCTX_MARKER_LINE = re.compile(r"[ \t]*\[\[VARCTX_(?:START|END)\]\][ \t]*\n?")


def _hot_reload_enabled() -> bool:
    return os.getenv("PROMPT_HOT_RELOAD", "false").strip().lower() == "true"


def prompt_cache_hoist_enabled() -> bool:
    """When true, hoist per-asset data blocks to the prompt tail for prefix caching.

    Defaults to true: the static instructions form a cache-friendly prefix and the
    hoist is byte-safe (blocks carry their own headers, no rewording). Set the env var
    PROMPT_CACHE_HOIST=false to opt out.
    """
    return os.getenv("PROMPT_CACHE_HOIST", "true").strip().lower() == "true"


def hoist_variable_context(text: str, *, enabled: bool | None = None) -> str:
    """Relocate VARCTX-marked blocks to the end so the static instructions form a
    cache-friendly prefix. With hoisting disabled, only the markers are stripped and
    the text is byte-identical to the un-marked original (regression-safe default).
    """
    if enabled is None:
        enabled = prompt_cache_hoist_enabled()

    if not enabled:
        return _VARCTX_MARKER_LINE.sub("", text)

    blocks: list[str] = []

    def _collect(match: re.Match) -> str:
        blocks.append(match.group(1))
        return ""

    body = _VARCTX_BLOCK.sub(_collect, text)
    if not blocks:
        return body
    # Blocks carry their own headers ("**INPUT DATA:**", "=== DECLARED ... ==="),
    # so append them verbatim — no new wording introduced.
    suffix = "\n".join(block.strip("\n") for block in blocks)
    return f"{body.rstrip()}\n\n{suffix}\n"


def clear_prompt_cache() -> None:
    """Clear cached prompt reads (used when PROMPT_HOT_RELOAD=true)."""
    _read_file.cache_clear()
    _parse_barcode_sections.cache_clear()
    load_barcode_section.cache_clear()


def _read_file_uncached(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _read_file(name: str) -> str:
    return _read_file_uncached(name)


def load_prompt(name: str, **fmt) -> str:
    """Load a markdown prompt file; apply str.format when fmt kwargs are provided."""
    if _hot_reload_enabled():
        clear_prompt_cache()
    text = _read_file(name)
    if fmt:
        return text.format(**fmt)
    return text


@lru_cache(maxsize=None)
def _parse_barcode_sections() -> dict[str, str]:
    raw = _read_file("barcode.md")
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines(keepends=True):
        match = _SECTION_MARKER.match(line.strip())
        if match:
            current = match.group(1).lower()
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return {key: "".join(lines).strip() for key, lines in sections.items()}


@lru_cache(maxsize=None)
def load_barcode_section(section: str) -> str:
    """Return rules, phase1, or retry section from barcode.md."""
    if _hot_reload_enabled():
        clear_prompt_cache()
    key = section.lower()
    sections = _parse_barcode_sections()
    if key not in sections:
        raise KeyError(f"Unknown barcode section '{section}'; available: {sorted(sections)}")
    body = sections[key]
    if key in ("phase1", "retry"):
        rules = sections.get("rules", "")
        body = body.replace("{barcode_rules}", rules)
    return body


def load_output_schema() -> str:
    """Global JSON output instruction appended to merged prompts."""
    text = load_prompt("output_schema.md").strip()
    return f"\n{text}"
