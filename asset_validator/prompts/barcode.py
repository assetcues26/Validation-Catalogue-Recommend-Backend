"""Barcode reading prompts — loaded from prompts/md/barcode.md."""

from asset_validator.prompts.loader import load_barcode_section

BARCODE_READING_RULES = load_barcode_section("rules")
PHASE1_BARCODE_SECTION = load_barcode_section("phase1")
BARCODE_RETRY_PROMPT = load_barcode_section("retry")
