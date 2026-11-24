"""Load and cache prompt templates from prompts/md/ (ported from main)."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from additional_asset.core import config

PROMPTS_DIR = Path(__file__).parent / "md"
_SECTION_MARKER = re.compile(r"<!--\s*section:\s*(\w+)\s*-->", re.IGNORECASE)
VARCTX_START = "[[VARCTX_START]]"
VARCTX_END = "[[VARCTX_END]]"
_VARCTX_BLOCK = re.compile(
    r"[ \t]*\[\[VARCTX_START\]\][ \t]*\n(.*?)\n[ \t]*\[\[VARCTX_END\]\][ \t]*\n?",
    re.DOTALL,
)
_VARCTX_MARKER_LINE = re.compile(r"[ \t]*\[\[VARCTX_(?:START|END)\]\][ \t]*\n?")


def hoist_variable_context(text: str, *, enabled: bool | None = None) -> str:
    if enabled is None:
        enabled = config.prompt_cache_hoist()
    if not enabled:
        return _VARCTX_MARKER_LINE.sub("", text)
    blocks: list[str] = []

    def _collect(match: re.Match) -> str:
        blocks.append(match.group(1))
        return ""

    body = _VARCTX_BLOCK.sub(_collect, text)
    if not blocks:
        return body
    suffix = "\n".join(block.strip("\n") for block in blocks)
    return f"{body.rstrip()}\n\n{suffix}\n"


def clear_prompt_cache() -> None:
    _read_file.cache_clear()
    _parse_barcode_sections.cache_clear()
    load_barcode_section.cache_clear()


@lru_cache(maxsize=None)
def _read_file(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def load_prompt(name: str, **fmt) -> str:
    if config.prompt_hot_reload():
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
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    return {k: "".join(v).strip() for k, v in sections.items()}


@lru_cache(maxsize=None)
def load_barcode_section(name: str) -> str:
    if config.prompt_hot_reload():
        clear_prompt_cache()
    sections = _parse_barcode_sections()
    key = name.lower()
    if key not in sections:
        raise KeyError(f"Unknown barcode section: {name}")
    return sections[key]
