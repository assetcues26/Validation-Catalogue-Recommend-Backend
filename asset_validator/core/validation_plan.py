"""Validation plan derived from request check flags."""

from __future__ import annotations

from dataclasses import dataclass

from asset_validator.core.constants import CHECK_FLAGS


@dataclass(frozen=True)
class ValidationPlan:
    """Enabled checks only — false flags are never registered."""

    enabled: frozenset[str]

    @classmethod
    def from_flags(cls, flags: dict[str, bool]) -> ValidationPlan:
        enabled = frozenset(
            name for name in CHECK_FLAGS if flags.get(name)
        )
        return cls(enabled=enabled)

    def is_enabled(self, check_id: str) -> bool:
        return check_id in self.enabled

    def requires_asset_image(self) -> bool:
        return bool(self.enabled)

    def requires_barcode_image(self) -> bool:
        return bool(self.enabled)

    def requires_pinecone(self) -> bool:
        return "assetmakemodelmatch" in self.enabled

    def requires_ai(self, taggingai: bool) -> bool:
        """True when TaggingAI is on and at least one check flag is enabled."""
        return taggingai and bool(self.enabled)
