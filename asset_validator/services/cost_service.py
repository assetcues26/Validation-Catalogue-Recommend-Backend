"""Cost validation — Gemini image-derived market range with L/U percent tolerance."""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date

from asset_validator.core.config import (
    COST_EQUIPMENT_MAJOR_FACTOR,
    COST_FURNITURE_MAJOR_FACTOR,
    COST_MAJOR_FACTOR,
    COST_MIN_VALUATION_CONFIDENCE,
    COST_PLACEHOLDER_MAX,
    COST_REFINE_MAX_CONFIDENCE,
    COST_TRUST_DECLARED_WHEN_UNSURE,
    COST_USER_TOLERANCE,
    currency_inr_rate,
)
from asset_validator.prompts.cost_date import DEFAULT_CURRENCY
from asset_validator.utils.date_utils import acquisition_invalid_reason


@dataclass
class CostDateResult:
    identified_model: str | None = None
    predicted_cost: float | None = None
    cost_lower_bound: float | None = None
    cost_upper_bound: float | None = None
    cost_acceptance_lower: float | None = None
    cost_acceptance_upper: float | None = None
    cost_range: str | None = None
    currency: str | None = None
    model_release_year: int | None = None
    model_end_year: int | None = None
    asset_class_detected: str | None = None
    cost_check: str | None = None
    # "market" when bounds came from the model, "declared" when they fall back to the
    # user's cost ± COST_USER_TOLERANCE because the model could not price the asset.
    cost_bounds_source: str = "market"
    acquisition_date_valid: bool | None = None
    acquisition_date_invalid_reason: str | None = None
    cost_reasoning: str | None = None
    release_year_reasoning: str | None = None


_GENERIC_MODEL_FRAGMENTS = frozenset({
    "pro", "air", "book", "plus", "max", "mini", "lite", "ultra",
    "pavilion", "latitude", "thinkpad", "ideapad", "inspiron", "elitebook",
})

_GENERIC_IDENTIFIED_MODEL_PHRASES = (
    "bullet camera",
    "security camera",
    "surveillance camera",
    "cctv camera",
    "cctv",
    "wall fan",
    "high speed wall fan",
    "high speed fan",
    "desk fan",
    "table fan",
    "ceiling fan",
)

_PRODUCT_LINE_TOKENS = (
    "turbo", "force", "series", "model", "hd", "4k", "pro", "plus",
    "elite", "pavilion", "thinkpad", "latitude", "inspiron",
)

# Furniture / assembled office fixtures — photo cannot pin cost or model year.
_FURNITURE_POSITIVE_PHRASES = (
    "furniture",
    "office furniture",
    "office_chair",
    "office chair",
    "work station",
    "workstation",
    "cubicle",
    "partition",
    "almirah",
    "wardrobe",
    "cupboard",
    "cabinet",
    "shelving",
    "modular desk",
    "modular furniture",
    "conference table",
    "meeting table",
    "side table",
    "coffee table",
    "dining table",
)

_FURNITURE_POSITIVE_TOKENS = (
    "furniture",
    "desk",
    "table",
    "cabinet",
    "cupboard",
    "modular",
    "cubicle",
    "partition",
    "almirah",
    "wardrobe",
    "shelf",
    "shelving",
    "sofa",
    "bench",
    "chair",
    "workstation",
)

# IT / electronics — never treat as low-priceability furniture (incl. PC "workstation").
_IT_EXCLUDE_TOKENS = (
    "laptop",
    "notebook",
    "desktop",
    "computer",
    "monitor",
    "server",
    "pc ",
    " pc",
    "cpu",
    "macbook",
    "imac",
    "thin client",
)

# "desk fan" / "table fan" contain furniture tokens but are appliances.
_FAN_EXCLUDE_PHRASES = (
    "desk fan",
    "table fan",
    "ceiling fan",
    "wall fan",
    "pedestal fan",
)


def _parse_positive_amount(value) -> float | None:
    if value is None:
        return None
    try:
        amount = float(value)
        # A reply of "1e400" parses as infinity, and one above 1e18 overflows its band (x6) to infinity: no price.
        return amount if 0 < amount <= 1e18 else None
    except (TypeError, ValueError):
        return None


def _parse_release_year(value) -> int | None:
    if value is None:
        return None
    try:
        year = int(value)
        if 1900 <= year <= 2100:
            return year
    except (TypeError, ValueError):
        pass
    return None


def _parse_confidence(gemini_out: dict) -> float | None:
    val_conf = gemini_out.get("valuation_confidence")
    if val_conf is None:
        reasoning = gemini_out.get("inspection_reasoning")
        if isinstance(reasoning, dict):
            val_conf = reasoning.get("valuation_confidence")
    if val_conf is None:
        return None
    try:
        conf = float(val_conf)
        if 0.0 <= conf <= 1.0:
            return conf
    except (TypeError, ValueError):
        pass
    return None


def compute_user_acceptance_bounds(
    market_lower: float,
    market_upper: float,
    *,
    tolerance: float = COST_USER_TOLERANCE,
) -> tuple[float, float]:
    """Percent-tolerance band: L × (1 − tolerance) .. U × (1 + tolerance).

    Retained for callers that want the narrow band explicitly; the pipeline now uses
    compute_major_acceptance_bounds so only gross discrepancies fail.
    """
    accept_min = round(market_lower * (1 - tolerance), 2)
    accept_max = round(market_upper * (1 + tolerance), 2)
    return accept_min, accept_max


def format_cost_range(lower: float, upper: float) -> str:
    """Public alias — keeps cost_range formatting identical across all call sites."""
    return _format_cost_range(lower, upper)


def compute_declared_fallback_bounds(
    user_cost: float,
    *,
    tolerance: float = COST_USER_TOLERANCE,
) -> tuple[float, float]:
    """Band around the DECLARED cost: cost × (1 ∓ tolerance).

    Used only when the model returned no usable market range. It is not a valuation —
    it exists so the response shows a concrete band instead of nulls. Because it is
    centred on the declared cost, cost_check against it is PASS by construction.
    """
    return (
        round(user_cost * (1 - tolerance), 2),
        round(user_cost * (1 + tolerance), 2),
    )


def compute_major_acceptance_bounds(
    market_lower: float,
    market_upper: float,
    *,
    factor: float = COST_MAJOR_FACTOR,
) -> tuple[float, float]:
    """Major-discrepancy band: L/factor .. U×factor.

    Catches order-of-magnitude errors (a ₹5,000 laptop) while tolerating the spread a
    photo genuinely cannot resolve — spec variants, regional pricing, bundled fitting.
    """
    safe_factor = factor if factor > 0 else COST_MAJOR_FACTOR
    accept_min = round(market_lower / safe_factor, 2)
    accept_max = round(market_upper * safe_factor, 2)
    return accept_min, accept_max


def compute_furniture_acceptance_bounds(
    market_lower: float,
    market_upper: float,
    *,
    factor: float = COST_FURNITURE_MAJOR_FACTOR,
) -> tuple[float, float]:
    """Major-discrepancy band: L/factor .. U×factor for hard-to-price furniture."""
    safe_factor = factor if factor > 0 else COST_FURNITURE_MAJOR_FACTOR
    accept_min = round(market_lower / safe_factor, 2)
    accept_max = round(market_upper * safe_factor, 2)
    return accept_min, accept_max


def compute_equipment_acceptance_bounds(
    market_lower: float,
    market_upper: float,
    *,
    factor: float = COST_EQUIPMENT_MAJOR_FACTOR,
) -> tuple[float, float]:
    """Major-discrepancy band: L/factor .. U×factor for industrial process equipment."""
    safe_factor = factor if factor > 0 else COST_EQUIPMENT_MAJOR_FACTOR
    accept_min = round(market_lower / safe_factor, 2)
    accept_max = round(market_upper * safe_factor, 2)
    return accept_min, accept_max


def _format_cost_range(lower: float, upper: float) -> str:
    if lower == int(lower) and upper == int(upper):
        return f"{int(lower)}-{int(upper)}"
    return f"{lower}-{upper}"


def _normalize_market_bounds(
    lower: float | None,
    upper: float | None,
) -> tuple[float | None, float | None]:
    """Return validated (L, U) or (None, None) when bounds are unusable."""
    if lower is None or upper is None:
        return None, None
    if lower <= 0 or upper <= 0:
        return None, None
    if lower > upper:
        lower, upper = upper, lower
    return lower, upper


def is_placeholder_cost(declared) -> bool:
    """A declared cost that is no cost: zero, negative, or at most COST_PLACEHOLDER_MAX (7.8).

    Registers carry Rs 1 where the cost is unknown. The band check fails it everywhere the
    model priced the asset, but the classes whose cost is never judged — "Land and Building",
    bulk vessels, installed systems — passed it: customer 1158 filed pedestals and switches
    under Land and Building and 186 Rs 1 records passed in a week. Same rule, every tier.
    """
    if not isinstance(declared, (int, float)) or isinstance(declared, bool):
        return False
    return declared <= max(float(COST_PLACEHOLDER_MAX), 0.0)


def _resolve_model_end_year(
    gemini_out: dict,
    *,
    acquisition_date: date | None = None,
    request_id: str = "",
) -> int | None:
    """Return LLM-provided end-of-sales year only — never infer from visible era or acquisition."""
    del acquisition_date, request_id
    return _parse_release_year(gemini_out.get("model_end_year"))


def _is_model_name_too_short(model: str) -> bool:
    stripped = model.strip()
    if len(stripped) < 8:
        return True
    words = stripped.split()
    if len(words) == 1 and stripped.lower() in _GENERIC_MODEL_FRAGMENTS:
        return True
    return False


def _enrich_model_from_context(
    fragment: str,
    detected_asset: str,
    image_analysis: str,
) -> str | None:
    for source in (detected_asset, image_analysis):
        if not source or not isinstance(source, str):
            continue
        source = source.strip()
        if len(source) < len(fragment):
            continue
        if fragment.lower() in source.lower() and len(source) > len(fragment):
            return source
    return None


def is_generic_identified_model(model: str | None) -> bool:
    """True when the model name is a class label without a specific product line."""
    if not model or not isinstance(model, str):
        return True
    normalized = model.strip().lower()
    if not normalized:
        return True
    if not any(phrase in normalized for phrase in _GENERIC_IDENTIFIED_MODEL_PHRASES):
        return False
    return not any(token in normalized for token in _PRODUCT_LINE_TOKENS)


# A word that names a generation: 3050, G8, G1iR, MF244dw, M1, 9300.
_MODEL_TOKEN_RE = re.compile(r"[A-Za-z]*\d[A-Za-z0-9-]*")
# Words with a digit that name no generation: a CPU, a size, a unit, a year. "Laptop i5 8GB",
# "Macbook purchased in 2019" and a 14-inch screen must not corroborate a guessed model.
_NOT_A_GENERATION_RE = re.compile(
    r"^(?:i[3579]|r[3579]|ryzen\d*|\d+(?:gb|tb|mb|ghz|mhz|hz|w|kw|kva|v|a|ah|mm|cm|in|inch|rpm|hp)"
    r"|(?:19|20)\d{2}|\d{1,2})$"
)


def model_identity_corroborated(identified_model: str | None, *texts: str) -> bool:
    """Whether the generation the cost reply named is written in the photo or the register.

    The reply names a model for every record, including a generic tower it priced as "a Dell
    OptiPlex 3040", and its release / end-of-sales years belong to that generation. They are
    evidence about THIS asset only when the generation is named with a number — 3050, G8,
    G1iR, MF244dw — that the photo showed or the register wrote; otherwise they are a pricing
    assumption. "DESKTOP" acquired in 2011 and priced as an OptiPlex 3040 is not acquired
    before its release; "Dell Optiplex 3040" acquired in 2011 is (customer 1009, 2026-10).
    """
    if is_generic_identified_model(identified_model):
        return False
    tokens = {
        t.lower() for t in _MODEL_TOKEN_RE.findall(str(identified_model))
        if len(t) >= 2 and not _NOT_A_GENERATION_RE.match(t.lower())
    }
    if not tokens:
        return False
    written = set(re.findall(r"[a-z0-9-]+", " ".join(t or "" for t in texts).lower()))
    return bool(tokens & written)


def model_years_not_evidence(
    reason: str | None,
    acquisition: date,
    result: "CostDateResult",
    gemini_out: dict,
    *,
    photo_text: str,
    record_text: str,
) -> str | None:
    """Why a release / end-year verdict on the acquisition date is not evidence, or None.

    Three limits of the pricing reply's lifecycle knowledge (docs 7.7):
    - the generation it named is not written in the photo or the register, so the years are an
      assumption made to price the asset;
    - "already discontinued" for an acquisition after MODEL_LIFECYCLE_KNOWLEDGE_YEAR: the model
      cannot know what was still sold then. A 2026 HP ProBook 4 G1iR was "discontinued" in 2016,
      2023 and 2024 on three runs (customer 1278, 2026-10-22);
    - a year of channel stock after the last production year (an HP ProBook 440 G8 bought in
      March 2024 against an end year of 2023).
    A date before the release year of a generation the register itself names stays a failure.
    """
    from asset_validator.core.config import (
        ACQUISITION_DATE_EVIDENCE_GATE_ENABLED,
        MODEL_END_YEAR_STOCK_MARGIN_YEARS,
        MODEL_LIFECYCLE_KNOWLEDGE_YEAR,
    )

    if not ACQUISITION_DATE_EVIDENCE_GATE_ENABLED:
        return None
    if reason not in ("before_release", "after_discontinuation"):
        return None
    if not model_identity_corroborated(gemini_out.get("identified_model"), photo_text, record_text):
        return "the generation the reply priced is not named in the photo or the register"
    if reason == "after_discontinuation":
        if acquisition.year > MODEL_LIFECYCLE_KNOWLEDGE_YEAR:
            return (
                f"acquired in {acquisition.year}, after the pricing model's lifecycle "
                f"knowledge ends ({MODEL_LIFECYCLE_KNOWLEDGE_YEAR})"
            )
        if result.model_end_year is not None and (
            acquisition.year <= result.model_end_year + MODEL_END_YEAR_STOCK_MARGIN_YEARS
        ):
            return (
                f"acquired in {acquisition.year}, within {MODEL_END_YEAR_STOCK_MARGIN_YEARS} "
                f"year(s) of stock after the last production year {result.model_end_year}"
            )
    return None


def _blob_has_it_exclude(blob: str) -> bool:
    if any(phrase in blob for phrase in _FAN_EXCLUDE_PHRASES):
        return True
    padded = f" {blob} "
    for token in _IT_EXCLUDE_TOKENS:
        if token in blob or token in padded:
            return True
    # bare "pc" as whole word
    if blob == "pc" or blob.startswith("pc ") or blob.endswith(" pc") or " pc " in padded:
        return True
    return False


def _blob_has_furniture_signal(blob: str) -> bool:
    if any(phrase in blob for phrase in _FURNITURE_POSITIVE_PHRASES):
        return True
    return any(token in blob for token in _FURNITURE_POSITIVE_TOKENS)


def is_low_priceability_furniture(
    *,
    asset_class_detected: str | None = None,
    detected_asset: str = "",
    image_analysis: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_asset_name: str = "",
) -> bool:
    """
    True for assembled/custom furniture where a single photo cannot pin cost or model year.

    Excludes IT workstations (laptop/desktop/PC) and desk/table fans.

    IT exclude uses primary identity fields only — background monitors/PCs/keyboards
    mentioned in image_analysis must not reclassify a desk/table as catalog IT.
    Fan appliances still use the full blob (including image_analysis).
    """
    parts = [
        asset_class_detected or "",
        detected_asset or "",
        image_analysis or "",
        user_category or "",
        user_subcategory or "",
        user_asset_name or "",
    ]
    blob = " ".join(p.strip().lower() for p in parts if p and isinstance(p, str)).strip()
    if not blob:
        return False

    # Desk/table fans are appliances — check full scene text.
    if any(phrase in blob for phrase in _FAN_EXCLUDE_PHRASES):
        return False

    identity_parts = [
        asset_class_detected or "",
        detected_asset or "",
        user_category or "",
        user_subcategory or "",
        user_asset_name or "",
    ]
    identity_blob = " ".join(
        p.strip().lower() for p in identity_parts if p and isinstance(p, str)
    ).strip()
    # Background IT clutter in image_analysis must not block furniture desks.
    if _blob_has_it_exclude(identity_blob if identity_blob else blob):
        return False

    class_norm = (asset_class_detected or "").strip().lower()
    if class_norm in ("furniture", "office_chair") or class_norm.startswith("furniture"):
        return True

    return _blob_has_furniture_signal(blob)


# ── Cost-check category policy ─────────────────────────────────────────────
# Some classes cannot be validated from a photo. Land, buildings and intangibles
# have no photographable market price at all (SKIP). Installed building-services
# systems (HVAC, lifts, electrical/plumbing/fire networks) are only ever partly
# visible in one frame, so a visual valuation is unreliable (SOFTEN — never
# reject on cost, but still surface an estimate).
#
# Matching is word-boundary based against the master-data classification fields
# (category / sub-category / asset-class / make-model) only — NOT the free-text
# asset name/description — to keep false positives low. Add your own category
# names to these lists as needed.

# Intangibles have no physical asset; the photo shows the device they run on (7.1).
_COST_INTANGIBLE_TERMS = (
    "software", "license", "licence", "goodwill", "patent", "trademark",
    "copyright", "intangible",
)

_COST_SKIP_TERMS = (
    # Land & building / civil structures
    "land", "building", "civil", "shed", "boundary wall", "compound wall",
    "road", "drainage", "culvert", "pavement",
    # Leasehold / interior fit-outs (precise — bare "interior" over-matches decor items)
    "leasehold improvement", "interior fit-out", "interior fitout",
    "interior finishing", "interior work", "fit-out", "fitout",
    "false ceiling", "partition", "flooring",
    # Intangibles (no physical asset)
    *_COST_INTANGIBLE_TERMS,
    # Capital work-in-progress
    "capital work", "work in progress", "cwip", "under construction",
)

_COST_SOFTEN_TERMS = (
    # Electrical installation
    "electrical installation", "electrical fitting", "wiring", "cabling",
    "distribution board", "electrification",
    # HVAC / central cooling
    "hvac", "central air", "chiller", "cooling tower", "ducting", "air handling",
    # Vertical transport — installed only. Bare "lift" leaks mobile scissor/boom
    # /man lifts (photo-priceable plant), so match the installed variants precisely.
    "elevator", "escalator", "passenger lift", "goods lift",
    "capsule lift", "service lift", "lift installation",
    # Plumbing / water / effluent
    "plumbing", "sanitary", "water supply", "sewage", "effluent",
    "treatment plant", "stp", "etp", "wtp",
    # Fire systems (networks — not standalone extinguishers)
    "fire fighting", "firefighting", "sprinkler", "hydrant", "fire detection",
    # Heavy electrical infrastructure. Bare "solar" leaks solar water heaters/
    # lamps/panels (photo-priceable), so match installed solar plants precisely.
    "solar power plant", "solar power system", "solar plant", "rooftop solar",
    "dg set", "diesel generator", "transformer", "substation",
    "ht panel", "lt panel",
    # Passive network infrastructure
    "structured cabling", "network infrastructure",
)


# Industrial process equipment: photo-identifiable as a class, but NOT photo-priceable.
# What sets the price — duty point, head/flow, materials of construction, fabrication
# spec, tube count — is invisible in a single frame, and the model consistently prices
# the premium variant. Unlike the "system" soften classes these are NOT force-passed:
# they keep a real (wide) band so an order-of-magnitude error still fails.
_COST_EQUIPMENT_TERMS = (
    # Rotating / fluid handling
    "pump", "compressor", "blower", "centrifuge", "agitator", "gearbox", "turbine",
    # Thermal
    "heat exchanger", "boiler", "condenser", "evaporator", "radiator",
    # Packaging / process lines
    "sealing machine", "packing machine", "packaging machine", "filling machine",
    "labelling machine", "labeling machine", "wrapping machine", "conveyor",
    # Industrial marking / coding
    "inkjet printer", "coding machine", "laser marking",
)


def _compile_term_patterns(terms: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    # \b...s?\b matches the term and its simple plural without substring bleed.
    return tuple(re.compile(r"\b" + re.escape(t) + r"s?\b") for t in terms)


_COST_SKIP_PATTERNS = _compile_term_patterns(_COST_SKIP_TERMS)
_COST_INTANGIBLE_PATTERNS = _compile_term_patterns(_COST_INTANGIBLE_TERMS)
_COST_SOFTEN_PATTERNS = _compile_term_patterns(_COST_SOFTEN_TERMS)
_COST_EQUIPMENT_PATTERNS = _compile_term_patterns(_COST_EQUIPMENT_TERMS)

_COST_POLICY_FIELDS = (
    "categoryname",
    "subcategoryname",
    "assetclassname",
    "makemodel",
    "makemodelname",
)

# Cleared when the cost check is skipped — only the cost-specific value fields.
_COST_VALUE_KEYS = (
    "cost_lower_bound",
    "cost_upper_bound",
    "cost_acceptance_lower",
    "cost_acceptance_upper",
    "cost_range",
)

# ONE principled rule: soften bulk vessels of >= 1 kilolitre (1000 L), however the
# capacity is written — KL, m3/m³, or litres. These are large fabricated vessels/tanks/
# mixers/blenders whose cost scales with capacity, material and fabrication a photo
# cannot convey, and the model under-values them. We deliberately do NOT extend to
# power (kVA), weight (ton) or flow (KLD): those units are ambiguous and usually
# photo-priceable ("5 Ton AC", "10 kVA UPS", "5 kg scale"). The magnitude threshold
# doubles as a false-positive filter — small model codes like "5 L" fall below it, and
# each unit's trailing \b blocks lookalikes (litre vs "lumen"/"lb", KL vs "KLD"/"KLM").
_LARGE_CAPACITY_LITRES = 1000.0
# A FLOW RATE is not a capacity. "Scrubber Pump-PP-3 M3/hr" declares 3 cubic metres per
# HOUR of throughput, not a 3 KL vessel — and process-equipment names carry these constantly
# ("45 M3/hr", "150 M3/hr", "3 M3/hr"). Without this guard every such pump was read as a bulk
# vessel, which routed it to the 'capacity' soften class and forced cost_check to PASS
# regardless of the declared figure (a Rs.50,000 pump declared at Rs.1 passed).
# Appended to every volume unit below, so a rate suffix never counts as declared capacity.
_RATE_SUFFIX = (
    r"(?!\s*(?:/|\bper\b)\s*"
    r"(?:h|hr|hrs|hour|hours|min|mins|minute|minutes|sec|secs|second|seconds|day|days)\b)"
)

_VOLUME_PATTERNS = (
    (
        re.compile(
            r"(\d+(?:\.\d+)?)\s*(?:kl|kilo-?\s?litres?)\b" + _RATE_SUFFIX,
            re.IGNORECASE,
        ),
        1000.0,
    ),
    (
        re.compile(
            r"(\d+(?:\.\d+)?)\s*(?:m3|m³|cbm|cu\.?\s?m|cubic\s?met(?:re|er)s?)\b"
            + _RATE_SUFFIX,
            re.IGNORECASE,
        ),
        1000.0,
    ),
    (
        re.compile(
            r"(\d+(?:\.\d+)?)\s*(?:l|ltr|litres?|liters?)\b" + _RATE_SUFFIX,
            re.IGNORECASE,
        ),
        1.0,
    ),
)

_COST_CAPACITY_FIELDS = ("assetname", "description", "makemodel", "makemodelname")


def _has_large_capacity(req_body: dict) -> bool:
    """True when a bulk volume capacity (>= 1 KL, written as KL / m3 / litres) is declared."""
    text = " ".join(str(req_body.get(field) or "") for field in _COST_CAPACITY_FIELDS)
    for pattern, litres_per_unit in _VOLUME_PATTERNS:
        for match in pattern.finditer(text):
            try:
                if float(match.group(1)) * litres_per_unit >= _LARGE_CAPACITY_LITRES:
                    return True
            except ValueError:
                continue
    return False


def _is_low_priceability_furniture_asset(req_body: dict, output: dict | None) -> bool:
    """Furniture (desks/workstations/cabinets/chairs) via the SAME detector that already
    selects the furniture acceptance band — so this adds no new match surface."""
    out = output or {}
    # Fold make/model into the name signal so "office workstation" / "Modular Cabinet"
    # count too. The detector's IT-exclude still runs on these, so a "Computer Table"
    # stays out — the safe direction.
    name_signal = " ".join(
        str(req_body.get(field) or "")
        for field in ("assetname", "makemodel", "makemodelname")
    ).strip()
    return is_low_priceability_furniture(
        detected_asset=str(out.get("detectedAsset") or ""),
        image_analysis=str(out.get("imageAnalysis") or ""),
        user_category=str(req_body.get("categoryname") or ""),
        user_subcategory=str(req_body.get("subcategoryname") or ""),
        user_asset_name=name_signal,
    )


def _installed_plant_cost_soften(req_body: dict) -> bool:
    from asset_validator.core.config import INSTALLED_PLANT_COST_SOFTEN_ENABLED
    from asset_validator.utils.helpers import is_installed_plant

    return INSTALLED_PLANT_COST_SOFTEN_ENABLED and is_installed_plant(req_body, include_name=False)


def _resolve_soften_reason(req_body: dict, output: dict | None = None) -> str | None:
    """Return the soften sub-type, or None if the asset is not a soften class.

    - 'system'    → installed building-services (HVAC, lifts, electrical, fire, DG,
                    solar plants): only partly visible in one frame — a photo cannot
                    bound the cost, so it is never rejected on cost.
    - 'capacity'  → bulk fabricated vessels >= 1 KL: cost scales with capacity/material
                    the photo cannot convey; the model under-values them.
    - 'furniture' → assembled/custom furniture (desks, chairs, cabinets): a photo cannot
                    pin the exact cost, but CAN catch an order-of-magnitude error.
    - 'equipment' → industrial process equipment (pumps, heat exchangers, compressors):
                    same reasoning as furniture — the duty spec that sets the price is
                    invisible in one frame, so judge against a wide band, never force PASS.

    Assumes skip classes are handled first (skip takes priority in the caller).
    """
    blob = _cost_policy_blob(req_body)
    if blob and any(pattern.search(blob) for pattern in _COST_SOFTEN_PATTERNS):
        return "system"
    # Installed industrial plant is the same case as installed building services: never seen
    # whole, never bought off a shelf. Customer 1299's "Air Slide System For Pot" (₹14.4 lakh,
    # capitalised per pot) was priced anywhere from ₹20 to ₹15 lakh by one model and ₹1.5–5.5
    # lakh by the other. Classification fields only, like the terms above.
    if _installed_plant_cost_soften(req_body):
        return "system"
    if _has_large_capacity(req_body):
        return "capacity"
    if _is_low_priceability_furniture_asset(req_body, output):
        return "furniture"
    if blob and any(pattern.search(blob) for pattern in _COST_EQUIPMENT_PATTERNS):
        return "equipment"
    return None


# Combined hardware/software class labels are the standard bucket for laptops, desktops,
# monitors and tablets — physical, priceable assets. Without this, the intangibles term
# "software" matched "Computer Hardware and Software" and silently skipped the cost check
# for every asset in that class. Neutralised before matching; a genuine intangible reads
# "Software Licence" or "SAP Software" and never carries "hardware".
_HARDWARE_AND_SOFTWARE_RE = re.compile(
    r"\bhardwares?\s*(?:and|&|/|\+|,)\s*softwares?\b"
    r"|\bsoftwares?\s*(?:and|&|/|\+|,)\s*hardwares?\b",
    re.IGNORECASE,
)


def _cost_policy_blob(req_body: dict) -> str:
    """Lower-cased text the category patterns are matched against."""
    blob = " ".join(
        str(req_body.get(field) or "").strip().lower()
        for field in _COST_POLICY_FIELDS
    ).strip()
    return _HARDWARE_AND_SOFTWARE_RE.sub(" computer equipment ", blob)


# A photo label that names a place or a structure is not one item a price can be put on: the "Server/Electrical
# Area" and "UPS Server Room" photos of Flipkart civil-works records must never be priced as a server (docs 7.1).
_COST_PHOTO_PLACE_RE = re.compile(
    r"\b(?:rooms?|areas?|panels?|boards?|installations?|works?|sites?|buildings?|walls?|floors?|ceilings?"
    r"|structures?|sheds?|civil|plants?|lines?|spaces?|premises|interiors?|setups?|yards?)\b",
    re.IGNORECASE,
)


# Product types of 6.1 that are IT devices, so a product line the device words miss counts too ("14 inch MacBook
# Pro Apple M3 Pro" reads as a laptop).
_COST_DEVICE_FAMILIES = frozenset(
    {"laptop", "desktop", "monitor", "printer", "scanner", "phone", "tablet", "server", "network", "projector"}
)


def _photo_cost_override(output: dict | None, req_body: dict | None = None) -> str | None:
    """How a skip class is priced from its photo, or None (docs 7.1, COST_SKIP_YIELDS_TO_PHOTO_ENABLED).

    The photo decides, not the record (the owner, 2026-11-03). When the photo shows one item a price can
    be put on (an HP laptop, a chair, a fire extinguisher), that item is priced and the declared cost is
    judged against it:
    - "photo_item": the record names the same kind of item (a MacBook Pro filed under Computer Software);
    - "photo_other_item": it does not, so the message says what the photo shows (AssetId 40524: "MIXING
      MEZZANINE - PHASE 1" under Buildings, USD 76,777, photographed as an HP laptop).
    A photo of a place or a structure (a building, a server room, an electrical area, a panel) is not
    priced; the record then gets the band around its declared cost (apply_cost_category_policy).
    Decided once per request and kept for the final cost policy.
    """
    from asset_validator.core.config import COST_SKIP_YIELDS_TO_PHOTO_ENABLED

    if not COST_SKIP_YIELDS_TO_PHOTO_ENABLED or not output:
        return None
    if "_cost_photo_override" in output:
        return output["_cost_photo_override"] or None
    try:
        reason = _photo_cost_override_reason(output, req_body or {})
    except Exception as exc:
        # A fault keeps the skip and its declared band.
        logging.warning(f"Cost photo override skipped, the never-priced class stands: {exc}")
        reason = ""
    output["_cost_photo_override"] = reason
    return reason or None


def _photo_cost_override_reason(output: dict, req_body: dict) -> str:
    detected = str(output.get("detectedAsset") or "").strip()
    if not detected or _COST_PHOTO_PLACE_RE.search(detected):
        return ""
    # An installed system or a bulk vessel is never rejected on cost, whatever part of it the photo shows: a
    # "Fire Alarm & Sprinkler System" under Leasehold Improvements photographed as a fire extinguisher.
    if _resolve_soften_reason(req_body, None) in ("system", "capacity"):
        return ""
    from asset_validator.utils.classification_match import (
        _IT_DEVICE_RE,
        _detect_product_categories,
        normalize_classification_value,
    )

    def device(text: str, types: set) -> bool:
        return bool(_IT_DEVICE_RE.search(normalize_classification_value(text))) or bool(types & _COST_DEVICE_FAMILIES)

    photo_types = set(_detect_product_categories(detected))
    photo_is_device = device(detected, photo_types)
    if not photo_is_device and not photo_types:
        return ""
    record_text = " ".join(
        str(req_body.get(field) or "") for field in ("assetname", "description", "subcategoryname", "makemodel", "model")
    ).strip()
    record_types = set(_detect_product_categories(record_text)) if record_text else set()
    if photo_types & record_types:
        return "photo_item"
    if photo_is_device and record_text and device(record_text, record_types) and not (photo_types and record_types):
        return "photo_item"
    # An intangible is photographed on the device it runs on: a licence on a laptop screen is not priced as the
    # laptop. It is priced only when the record names that device (a MacBook Pro under Computer Software).
    if any(pattern.search(_cost_policy_blob(req_body)) for pattern in _COST_INTANGIBLE_PATTERNS):
        return ""
    return "photo_other_item"


def resolve_cost_category_policy(req_body: dict, output: dict | None = None) -> str:
    """Return 'skip', 'soften', or 'normal'.

    Priority: skip (never-appraisable classes) > soften (installed systems,
    large-capacity fabricated equipment, or low-priceability furniture) > normal.
    A skip class is normal when its photo can be priced instead (_photo_cost_override).
    """
    blob = _cost_policy_blob(req_body)
    if blob and any(pattern.search(blob) for pattern in _COST_SKIP_PATTERNS):
        if _photo_cost_override(output, req_body):
            return "normal"
        return "skip"
    if _resolve_soften_reason(req_body, output) is not None:
        return "soften"
    return "normal"


def apply_cost_category_policy(
    output: dict,
    req_body: dict,
    *,
    cost_check_enabled: bool,
) -> str:
    """Override the cost outcome for classes a photo cannot value.

    Runs after the normal cost logic — behaviour is untouched for every other
    class. Returns the policy applied ('skip' | 'soften' | 'normal').

    - skip     → Land & Building / intangibles / CWIP: cost_check PASS, no cost values.
    - soften   → installed systems / bulk-capacity vessels: cost_check PASS (never
                 reject), market estimate kept, band = declared ±25%.
    - soften (furniture / equipment) → judged against a WIDE band (L/factor .. U*factor):
                 gross order-of-magnitude errors still FAIL, normal spread PASSes.

    A zero or negative declared cost is exempt from both overrides: it is a data defect,
    not a valuation-difficulty problem, so the FAIL from the cost decision stands.
    """
    if not cost_check_enabled:
        return "normal"
    policy = resolve_cost_category_policy(req_body, output)
    if policy == "normal" and output.get("_cost_photo_override") == "photo_other_item":
        # The message then says what the photo shows instead of "the expected range for this asset".
        output["_cost_priced_from_photo"] = str(output.get("detectedAsset") or "").strip()
    declared = output.get("cost")
    # Zero, negative, or a placeholder (7.8): a data defect no policy may excuse.
    declared_invalid = is_placeholder_cost(declared)
    if policy == "skip":
        for key in _COST_VALUE_KEYS:
            output[key] = None
        output["cost_check"] = "FAIL" if declared_invalid else "PASS"
        from asset_validator.core.config import COST_SKIP_YIELDS_TO_PHOTO_ENABLED

        if COST_SKIP_YIELDS_TO_PHOTO_ENABLED and not declared_invalid and isinstance(declared, (int, float)):
            # A photo of the building itself cannot be priced: return the band around the declared cost
            # (±25%, as for installed systems) instead of an empty range (the owner, 2026-11-03).
            accept_min, accept_max = compute_declared_fallback_bounds(declared)
            output["cost_acceptance_lower"] = accept_min
            output["cost_acceptance_upper"] = accept_max
            output["cost_range"] = format_cost_range(accept_min, accept_max)
    elif policy == "soften":
        if declared_invalid:
            # Leave the market bounds untouched so they are surfaced as the
            # recommended range for the reviewer.
            output["cost_check"] = "FAIL"
            return policy
        reason = _resolve_soften_reason(req_body, output)
        market_lower, market_upper = _normalize_market_bounds(
            output.get("cost_lower_bound"), output.get("cost_upper_bound")
        )
        if (
            reason in ("furniture", "equipment")
            and market_lower is not None
            and market_upper is not None
            and isinstance(declared, (int, float))
        ):
            # Furniture is photo-assessable for GROSS errors: a chair is not Rs.10 when
            # the market is Rs.700-1100. Judge the declared cost against the WIDE furniture
            # band (L/factor .. U*factor) instead of forcing PASS — genuine model over/
            # under-valuation still passes, only order-of-magnitude discrepancies fail.
            bounds_fn = (
                compute_furniture_acceptance_bounds
                if reason == "furniture"
                else compute_equipment_acceptance_bounds
            )
            accept_min, accept_max = bounds_fn(market_lower, market_upper)
            output["cost_acceptance_lower"] = accept_min
            output["cost_acceptance_upper"] = accept_max
            output["cost_range"] = format_cost_range(accept_min, accept_max)
            output["cost_check"] = (
                "PASS" if accept_min <= declared <= accept_max else "FAIL"
            )
        else:
            # Installed building-services systems, bulk-capacity vessels, or furniture the
            # model could not price at all: a single photo cannot bound the cost, so never
            # reject on it. Present a clean symmetric band around the declared cost (±25%),
            # not the widened market band — an asymmetric range reads as suspicious.
            output["cost_check"] = "PASS"
            if declared is not None:
                accept_min, accept_max = compute_declared_fallback_bounds(declared)
                output["cost_acceptance_lower"] = accept_min
                output["cost_acceptance_upper"] = accept_max
                output["cost_range"] = format_cost_range(accept_min, accept_max)
    return policy


_COST_ROUND_KEYS = (
    "cost",
    "cost_lower_bound",
    "cost_upper_bound",
    "cost_acceptance_lower",
    "cost_acceptance_upper",
    "predicted_cost",
)


def round_cost_fields(output: dict) -> None:
    """Round every monetary field to whole rupees (no decimals) and set cost_range.

    cost_range shows the tight MARKET band (cost_lower_bound-cost_upper_bound) when the
    declared cost lands inside it (cost_lower_bound <= cost <= cost_upper_bound); otherwise
    it shows the wider ACCEPTANCE band (the range that actually decides pass/fail).
    """
    for key in _COST_ROUND_KEYS:
        value = output.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, float) and not math.isfinite(value):
            output[key] = None  # int(round(inf)) raised, an HTTP 500 after every check had run
            continue
        if isinstance(value, (int, float)):
            output[key] = int(round(value))
    cost = output.get("cost")
    market_lower = output.get("cost_lower_bound")
    market_upper = output.get("cost_upper_bound")
    accept_lower = output.get("cost_acceptance_lower")
    accept_upper = output.get("cost_acceptance_upper")
    if (
        all(isinstance(v, int) for v in (cost, market_lower, market_upper))
        and (cost <= 0 or market_lower <= cost <= market_upper)
    ):
        # Show the market band when the declared cost agrees with it, OR when the declared
        # cost is invalid (<= 0) — in that case the market band is the recommended range.
        output["cost_range"] = f"{market_lower}-{market_upper}"
    elif isinstance(accept_lower, int) and isinstance(accept_upper, int):
        output["cost_range"] = f"{accept_lower}-{accept_upper}"
    # else: leave cost_range as-is (e.g. None for a skipped cost check)


def should_refine_cost(gemini_out: dict) -> bool:
    """Trigger image-only cost re-pass when tier or model identification is weak."""
    if not gemini_out or gemini_out.get("error"):
        return False

    confidence = _parse_confidence(gemini_out)
    if confidence is not None and confidence < COST_REFINE_MAX_CONFIDENCE:
        return True

    visual_tier = gemini_out.get("visual_tier")
    if not isinstance(visual_tier, str) or not visual_tier.strip():
        return True

    if is_generic_identified_model(gemini_out.get("identified_model")):
        return True

    return False


def parse_visible_model_years(output: dict) -> tuple[int | None, int | None]:
    """Read Phase 1 visible hardware era bounds from pipeline output."""
    return (
        _parse_release_year(output.get("visibleModelYearMin")),
        _parse_release_year(output.get("visibleModelYearMax")),
    )


def apply_cost_date_from_gemini(
    gemini_out: dict,
    *,
    detected_asset: str,
    image_analysis: str,
    user_cost: float | None,
    acquisition_date: date | None,
    request_id: str,
    visible_model_year_min: int | None = None,
    visible_model_year_max: int | None = None,
    today: date | None = None,
    user_category: str = "",
    user_subcategory: str = "",
    user_asset_name: str = "",
    currency: str = DEFAULT_CURRENCY,
    priced_natively: bool = False,
    user_description: str = "",
    user_makemodel: str = "",
    user_model: str = "",
) -> CostDateResult:
    """Parse Gemini market range, compute acceptance band, validate declared cost.

    Two pricing modes:
    - `priced_natively` (a request Currency was supplied): the prompt asked for that
      market's prices in that currency, so the model's range is used as-is.
    - otherwise (legacy customer-currency map): the model priced in INR and the range
      is translated via a static FX rate.
    Either way the acceptance band, the PASS/FAIL comparison, and the output all end
    up in one currency.
    """
    del visible_model_year_min

    result = CostDateResult()
    result.currency = currency or DEFAULT_CURRENCY
    # INR per 1 unit of the customer currency; 1.0 for INR (no-op).
    # When the prompt was rendered for the customer's own market, the model already
    # quoted that market's prices in that currency, so converting would be wrong
    # twice over — an FX-converted Indian price is not a local price.
    _rate = 1.0 if priced_natively else currency_inr_rate(result.currency)

    def _to_currency(value):
        if value is None or _rate == 1.0:
            return value
        return round(value / _rate, 2)

    cost_reasoning = gemini_out.get("cost_reasoning")
    if isinstance(cost_reasoning, str) and cost_reasoning.strip():
        result.cost_reasoning = cost_reasoning.strip()
    release_year_reasoning = gemini_out.get("release_year_reasoning")
    if isinstance(release_year_reasoning, str) and release_year_reasoning.strip():
        result.release_year_reasoning = release_year_reasoning.strip()

    result.identified_model = gemini_out.get("identified_model")
    if isinstance(result.identified_model, str):
        result.identified_model = result.identified_model.strip() or None
        if result.identified_model and _is_model_name_too_short(result.identified_model):
            enriched = _enrich_model_from_context(
                result.identified_model, detected_asset, image_analysis
            )
            if enriched:
                logging.info(
                    f"[{request_id}] Enriched identified_model "
                    f"'{result.identified_model}' → '{enriched}'"
                )
                result.identified_model = enriched

    asset_class = gemini_out.get("asset_class_detected")
    if isinstance(asset_class, str):
        result.asset_class_detected = asset_class.strip() or None

    low_priceability = is_low_priceability_furniture(
        asset_class_detected=result.asset_class_detected,
        detected_asset=detected_asset,
        image_analysis=image_analysis,
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_asset_name=user_asset_name,
    )

    result.model_release_year = _parse_release_year(gemini_out.get("model_release_year"))
    result.model_end_year = _resolve_model_end_year(
        gemini_out,
        acquisition_date=acquisition_date,
        request_id=request_id,
    )
    del visible_model_year_max

    market_lower, market_upper = _normalize_market_bounds(
        _parse_positive_amount(gemini_out.get("cost_lower_bound")),
        _parse_positive_amount(gemini_out.get("cost_upper_bound")),
    )
    # Translate the model's INR range into the customer currency before any band math,
    # so the declared cost (already in that currency) is compared like-for-like.
    market_lower = _to_currency(market_lower)
    market_upper = _to_currency(market_upper)

    if market_lower is not None and market_upper is not None:
        result.cost_lower_bound = market_lower
        result.cost_upper_bound = market_upper

        predicted = _to_currency(_parse_positive_amount(gemini_out.get("predicted_cost")))
        if predicted is None:
            predicted = round((market_lower + market_upper) / 2, 2)
        result.predicted_cost = predicted

        if low_priceability:
            accept_min, accept_max = compute_furniture_acceptance_bounds(
                market_lower, market_upper
            )
        else:
            accept_min, accept_max = compute_major_acceptance_bounds(
                market_lower, market_upper
            )
        result.cost_acceptance_lower = accept_min
        result.cost_acceptance_upper = accept_max
        # cost_range mirrors the acceptance band that actually decides PASS/FAIL,
        # not the tighter market band, so the displayed range matches the verdict.
        result.cost_range = _format_cost_range(accept_min, accept_max)
    else:
        result.predicted_cost = _to_currency(_parse_positive_amount(gemini_out.get("predicted_cost")))
        # No market estimate from the model. Leave the market bounds null — inventing them
        # would claim a valuation that never happened — but still return a usable band
        # around the declared cost so the response is not a row of nulls.
        result.cost_lower_bound = None
        result.cost_upper_bound = None
        if user_cost is not None:
            accept_min, accept_max = compute_declared_fallback_bounds(user_cost)
            result.cost_acceptance_lower = accept_min
            result.cost_acceptance_upper = accept_max
            result.cost_range = _format_cost_range(accept_min, accept_max)
            result.cost_bounds_source = "declared"
        else:
            result.cost_range = None
            result.cost_acceptance_lower = None
            result.cost_acceptance_upper = None

    confidence = _parse_confidence(gemini_out)
    low_confidence = (
        confidence is not None and confidence < COST_MIN_VALUATION_CONFIDENCE
    )

    if is_placeholder_cost(user_cost):
        # A zero, negative or placeholder declared cost is invalid — always fail so it is
        # flagged (7.8). The market bounds (cost_lower_bound/upper_bound) are surfaced as the
        # recommended range for the reviewer.
        result.cost_check = "FAIL"
        logging.info(f"[{request_id}] Cost FAIL: declared cost {user_cost} is not a cost")
    elif user_cost is not None:
        # A declared-derived band would always contain the declared cost, so it must not
        # decide the verdict — the trust flag does.
        have_bounds = (
            result.cost_bounds_source == "market"
            and result.cost_acceptance_lower is not None
            and result.cost_acceptance_upper is not None
        )
        if have_bounds:
            if result.cost_acceptance_lower <= user_cost <= result.cost_acceptance_upper:
                result.cost_check = "PASS"
            else:
                result.cost_check = "FAIL"
                logging.info(
                    f"[{request_id}] Cost major discrepancy"
                    f"{' (furniture)' if low_priceability else ''}: declared={user_cost} "
                    f"outside [{result.cost_acceptance_lower}, "
                    f"{result.cost_acceptance_upper}]"
                )
        elif COST_TRUST_DECLARED_WHEN_UNSURE:
            # No usable market range — the model could not price this asset. That is not
            # evidence the declared cost is wrong, so do not fail the record on it.
            result.cost_check = "PASS"
            logging.info(
                f"[{request_id}] Cost PASS (no market bounds; trusting declared cost)"
            )
        else:
            result.cost_check = "FAIL"

        if low_confidence:
            # Kept as a log signal only. A low-confidence valuation used to hard-FAIL, which
            # rejected correct records whenever the photo was simply hard to price.
            logging.info(
                f"[{request_id}] Low valuation_confidence {confidence} "
                f"(below {COST_MIN_VALUATION_CONFIDENCE}) — cost_check "
                f"{result.cost_check} decided on the major-discrepancy band"
            )

    if acquisition_date is not None:
        if low_priceability:
            reference_today = today or date.today()
            if acquisition_date > reference_today:
                result.acquisition_date_valid = False
                result.acquisition_date_invalid_reason = "future_date"
            else:
                result.acquisition_date_valid = True
                result.acquisition_date_invalid_reason = None
        elif result.model_release_year is not None:
            invalid_reason = acquisition_invalid_reason(
                acquisition_date,
                result.model_release_year,
                model_end_year=result.model_end_year,
                today=today,
            )
            not_evidence = model_years_not_evidence(
                invalid_reason,
                acquisition_date,
                result,
                gemini_out,
                photo_text=f"{detected_asset} {image_analysis}",
                record_text=" ".join(
                    (user_asset_name, user_description, user_makemodel, user_model)
                ),
            )
            if not_evidence:
                logging.info(
                    f"[{request_id}] Acquisition date not judged '{invalid_reason}' on the "
                    f"reply's model years: {not_evidence}"
                )
                # The response carries no year that was not trusted: both years of a
                # generation nobody named, or an end year past what the model can know.
                if "not named" in not_evidence:
                    result.model_release_year = None
                    result.model_end_year = None
                elif "knowledge ends" in not_evidence:
                    result.model_end_year = None
                invalid_reason = None
            result.acquisition_date_invalid_reason = invalid_reason
            result.acquisition_date_valid = invalid_reason is None
        elif COST_TRUST_DECLARED_WHEN_UNSURE:
            # No release year identified — only a future date is provably wrong.
            reference_today = today or date.today()
            if acquisition_date > reference_today:
                result.acquisition_date_valid = False
                result.acquisition_date_invalid_reason = "future_date"
            else:
                result.acquisition_date_valid = True
                result.acquisition_date_invalid_reason = None
        else:
            result.acquisition_date_valid = False
            result.acquisition_date_invalid_reason = "invalid_release_year"

    return result


# ── Everyday band (docs 7.1, the owner 2026-11-03) ─────────────────────────────
# Everyday assets are judged against L/COST_EVERYDAY_MAJOR_FACTOR .. U×COST_EVERYDAY_MAJOR_FACTOR (4 by default)
# instead of today's L/F .. U×F (F=6 on pre-prod): a USD 1,000 laptop priced 500-1,000 and declared at USD 6,000
# passed at 6. Enterprise IT gear keeps today's band, since the model underprices it (a Ruckus ICX switch at
# Rs 1.4 lakh priced 15-40k, a FortiGate 600E priced as a 60E). A cost today's band accepts and this one does not
# is valued once more before it fails. COST_EVERYDAY_BAND_MODE: "watch" only logs, "on" applies, "off" never runs.

# Enterprise IT gear, read in the asset name, description, subcategory, make/model, model and the photo's label.
# A bare "switch" is a light switch as often as a network one, so a switch counts only with a network word or a
# port count beside it (a Ruckus or Cisco switch counts by its vendor).
_ENTERPRISE_GEAR_RE = re.compile(
    r"\b(?:"
    r"(?:network|ethernet|lan|poe|managed|unmanaged|core|access|distribution|aggregation|gigabit|"
    r"l2|l3|layer\s?[23]|san|fc|fibre\s?channel|stackable|\d+\s?-?\s?ports?)\s+switch(?:es)?"
    r"|switch(?:es)?\s*[-(,]?\s*(?:network|ethernet|poe|managed|\d+\s?-?\s?ports?)"
    r"|access\s?points?|routers?|firewalls?|forti\s?gate|fortinet"
    r"|servers?|power\s?edge|proliant"
    r"|nas|san|storage\s+(?:arrays?|systems?|servers?|appliances?)|tape\s+(?:library|libraries|drives?|autoloaders?)"
    r"|(?<!-)ups|uninterrupt\w*\s+power\s+suppl\w*"
    r"|racks?|racking"
    r"|nvr|dvr|network\s+video\s+recorders?|digital\s+video\s+recorders?"
    r"|ruckus|icx|cisco|meraki|juniper|aruba|hpe|dell\s?emc|netapp|vertiv"
    r")\b",
    re.IGNORECASE,
)

_EVERYDAY_BAND_RECORD_FIELDS = ("assetname", "description", "subcategoryname", "makemodel", "makemodelname", "model")

# Output fields the everyday band may change in "on" mode; all are put back if anything in it fails.
_EVERYDAY_BAND_KEYS = ("cost_check", "cost_acceptance_lower", "cost_acceptance_upper", "cost_range")


def is_enterprise_gear(req_body: dict | None, output: dict | None = None) -> bool:
    """Whether the record or the photo's label names enterprise IT gear, which keeps today's band (docs 7.1)."""
    body = req_body or {}
    texts = [str(body.get(field) or "") for field in _EVERYDAY_BAND_RECORD_FIELDS]
    texts.append(str((output or {}).get("detectedAsset") or ""))
    return any(_ENTERPRISE_GEAR_RE.search(text) for text in texts if text)


def everyday_band_mode() -> str:
    """COST_EVERYDAY_BAND_MODE, read when called so a test can change it; anything but watch or on is off."""
    from asset_validator.core import config

    mode = str(getattr(config, "COST_EVERYDAY_BAND_MODE", "off") or "off").strip().lower()
    return mode if mode in ("watch", "on") else "off"


def _valid_amount(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value > 0


def everyday_band_bounds(market_lower: float, market_upper: float) -> tuple[float, float] | None:
    """L/COST_EVERYDAY_MAJOR_FACTOR .. U×COST_EVERYDAY_MAJOR_FACTOR, never wider than today's band (docs 7.1).

    None when the factor is below 1: such a band would leave out part of the market range itself.
    """
    from asset_validator.core import config

    factor = float(getattr(config, "COST_EVERYDAY_MAJOR_FACTOR", 4.0))
    if not factor >= 1:
        return None
    today_min, today_max = compute_major_acceptance_bounds(market_lower, market_upper)
    return (
        max(round(market_lower / factor, 2), today_min),
        min(round(market_upper * factor, 2), today_max),
    )


def everyday_band_ineligible(
    output: dict,
    valuation: dict | None,
    req_body: dict | None,
    *,
    user_cost,
    cost_check_enabled: bool = True,
    has_asset_image: bool = True,
    user_category: str = "",
    user_subcategory: str = "",
    user_asset_name: str = "",
) -> str | None:
    """Why the everyday band does not apply to this record, or None when it does (docs 7.1).

    Applies only when the cost check ran on an asset photo with a valuation that has no error; the record is
    everyday (helpers.asset_scope); its cost policy is normal and it is not furniture-like, by the record and
    the photo or by the cost reply's own class; it is not enterprise gear; the market range L-U is two amounts
    above 0; the declared cost is an amount and not a placeholder; and the output holds today's major band
    built from L-U with the verdict that band gives (so a fallback band or a verdict decided elsewhere is
    never touched). Read-only: `output` and `req_body` are not changed.
    """
    from asset_validator.utils.helpers import asset_scope

    body = req_body or {}
    if not cost_check_enabled:
        return "cost_check_disabled"
    if not has_asset_image:
        return "no_asset_image"
    if not isinstance(valuation, dict) or valuation.get("error"):
        return "valuation_error"
    if asset_scope(body) != "everyday":
        return "scope"
    # The furniture tests read the photo's description; without one the final cost policy could still find
    # furniture after this band.
    if not str(output.get("detectedAsset") or "").strip():
        return "no_photo_description"
    # A copy, so the photo decision resolve_cost_category_policy caches stays out of the output in watch mode.
    if resolve_cost_category_policy(body, dict(output)) != "normal":
        return "policy"
    asset_class = valuation.get("asset_class_detected")
    if is_low_priceability_furniture(
        asset_class_detected=(asset_class.strip() or None) if isinstance(asset_class, str) else None,
        detected_asset=str(output.get("detectedAsset") or ""),
        image_analysis=str(output.get("imageAnalysis") or ""),
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_asset_name=user_asset_name,
    ):
        return "furniture"
    if is_enterprise_gear(body, output):
        return "enterprise"
    market_lower, market_upper = output.get("cost_lower_bound"), output.get("cost_upper_bound")
    if not (_valid_amount(market_lower) and _valid_amount(market_upper)) or market_lower > market_upper:
        return "no_market_range"
    if not isinstance(user_cost, (int, float)) or isinstance(user_cost, bool) or user_cost != user_cost:
        return "no_declared_cost"
    if is_placeholder_cost(user_cost):
        return "placeholder_cost"
    today = compute_major_acceptance_bounds(market_lower, market_upper)
    if (output.get("cost_acceptance_lower"), output.get("cost_acceptance_upper")) != today:
        return "not_major_band"
    if output.get("cost_check") != ("PASS" if today[0] <= user_cost <= today[1] else "FAIL"):
        return "verdict_elsewhere"
    return None


def _second_valuation_band(reply, rate: float) -> tuple[float, float, float, float] | None:
    """(L2, U2, band low, band high) of the second valuation, or None when it gives no usable range."""
    if not isinstance(reply, dict) or reply.get("error"):
        return None
    raw = (reply.get("cost_lower_bound"), reply.get("cost_upper_bound"))
    if any(isinstance(value, bool) for value in raw):
        return None
    lower, upper = _normalize_market_bounds(_parse_positive_amount(raw[0]), _parse_positive_amount(raw[1]))
    # An infinite bound ("1e400") is no range; it must never confirm a failure.
    if lower is None or upper is None or not (math.isfinite(lower) and math.isfinite(upper)):
        return None
    if rate != 1.0:
        lower, upper = round(lower / rate, 2), round(upper / rate, 2)
    bounds = everyday_band_bounds(lower, upper)
    if bounds is None:
        return None
    return lower, upper, bounds[0], bounds[1]


def apply_everyday_cost_band(
    output: dict,
    valuation: dict | None,
    req_body: dict | None,
    *,
    user_cost,
    second_valuation,
    cost_check_enabled: bool = True,
    has_asset_image: bool = True,
    priced_natively: bool = False,
    request_id: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_asset_name: str = "",
) -> dict | None:
    """The everyday band (docs 7.1), after apply_cost_date_from_gemini's result is in `output`.

    For an eligible record (everyday_band_ineligible) the outcome is:
    - "inside": the declared cost lies in the everyday band;
    - "already_failing": it lies outside today's band;
    - "new_failure_candidate": today's band accepts it and the everyday band does not. `second_valuation()` is
      called once; "confirmed" when its reply gives a range whose own everyday band leaves the cost out too,
      otherwise "rescued" (an error, a timeout, no reply, no range, or a band that accepts the cost).
    "watch" changes no response field. "on" shows the everyday band in the acceptance fields and cost_range of
    every eligible record and fails a confirmed candidate; a rescued one keeps today's band and its PASS.
    "off" does nothing. Either mode records what happened in output["_decision_path"]["cost_everyday_band"].
    Any exception keeps today's outcome, puts back every field it changed and logs a warning.
    """
    mode = everyday_band_mode()
    if mode == "off":
        return None
    before = {key: output[key] for key in _EVERYDAY_BAND_KEYS if key in output}
    try:
        record = _everyday_cost_band(
            output,
            valuation,
            req_body,
            mode=mode,
            user_cost=user_cost,
            second_valuation=second_valuation,
            cost_check_enabled=cost_check_enabled,
            has_asset_image=has_asset_image,
            priced_natively=priced_natively,
            request_id=request_id,
            user_category=user_category,
            user_subcategory=user_subcategory,
            user_asset_name=user_asset_name,
        )
    except Exception as exc:
        for key in _EVERYDAY_BAND_KEYS:
            if key in before:
                output[key] = before[key]
            else:
                output.pop(key, None)
        logging.warning(f"[{request_id}] Everyday cost band failed, today's band stands: {exc}")
        output.setdefault("_decision_path", {})["cost_everyday_band"] = {
            "mode": mode,
            "outcome": "error",
            "error": f"{type(exc).__name__}: {exc}"[:160],
        }
        return None
    if record is not None:
        output.setdefault("_decision_path", {})["cost_everyday_band"] = record
        logging.info(
            f"[{request_id}] Everyday cost band ({mode}, {record['outcome']}): declared={record['declared']} "
            f"market={record['L']}-{record['U']} band={record['band']} second={record['second']} "
            f"cost_check={output.get('cost_check')}"
        )
    return record


def _everyday_cost_band(
    output: dict,
    valuation: dict | None,
    req_body: dict | None,
    *,
    mode: str,
    user_cost,
    second_valuation,
    cost_check_enabled: bool,
    has_asset_image: bool,
    priced_natively: bool,
    request_id: str,
    user_category: str,
    user_subcategory: str,
    user_asset_name: str,
) -> dict | None:
    from asset_validator.core import config

    if everyday_band_ineligible(
        output,
        valuation,
        req_body,
        user_cost=user_cost,
        cost_check_enabled=cost_check_enabled,
        has_asset_image=has_asset_image,
        user_category=user_category,
        user_subcategory=user_subcategory,
        user_asset_name=user_asset_name,
    ):
        return None
    market_lower, market_upper = output["cost_lower_bound"], output["cost_upper_bound"]
    band = everyday_band_bounds(market_lower, market_upper)
    if band is None:
        return None
    today = (output["cost_acceptance_lower"], output["cost_acceptance_upper"])
    record = {
        "mode": mode,
        "factor": float(getattr(config, "COST_EVERYDAY_MAJOR_FACTOR", 4.0)),
        "L": market_lower,
        "U": market_upper,
        "band": [band[0], band[1]],
        "declared": user_cost,
        "outcome": None,
        "second": None,
    }
    if band[0] <= user_cost <= band[1]:
        record["outcome"] = "inside"
    elif not today[0] <= user_cost <= today[1]:
        record["outcome"] = "already_failing"
    else:
        record["outcome"] = "new_failure_candidate"
        try:
            reply = second_valuation()
        except Exception as exc:
            logging.warning(f"[{request_id}] Second cost valuation failed: {exc}")
            reply = None
        rate = 1.0 if priced_natively else currency_inr_rate(output.get("currency") or DEFAULT_CURRENCY)
        second = _second_valuation_band(reply, rate)
        if second is None:
            record["second"] = {"L": None, "U": None, "band": None, "outcome": "rescued"}
        else:
            lower2, upper2, low2, high2 = second
            record["second"] = {
                "L": lower2,
                "U": upper2,
                "band": [low2, high2],
                "outcome": "rescued" if low2 <= user_cost <= high2 else "confirmed",
            }

    if mode == "on":
        rescued = record["second"] is not None and record["second"]["outcome"] == "rescued"
        if not rescued:
            # The band that decides is the one the response shows; round_cost_fields still shows L-U itself
            # when the declared cost lies inside it.
            output["cost_acceptance_lower"] = band[0]
            output["cost_acceptance_upper"] = band[1]
            output["cost_range"] = format_cost_range(band[0], band[1])
        if record["outcome"] == "new_failure_candidate" and not rescued:
            output["cost_check"] = "FAIL"
    return record
