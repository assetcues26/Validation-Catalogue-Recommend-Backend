"""Cost prediction and release-year extraction prompts.

Defaults to the India/INR market. When the request carries a Currency, the market
name, currency code and worked examples are all swapped for that market, so the
model prices natively there instead of pricing in India and converting.
"""

from datetime import date

from asset_validator.core.market import MarketProfile, resolve_market
from asset_validator.prompts.loader import hoist_variable_context, load_prompt
from asset_validator.prompts.market_text import adapt_prompt_for_market

DEFAULT_CURRENCY = "INR"


def _market_or_default(market: MarketProfile | None) -> MarketProfile:
    return market if market is not None else resolve_market(DEFAULT_CURRENCY)


def _cost_calibration_block(market: MarketProfile | None = None) -> str:
    """Pricing rubric for the target market.

    The worked examples carry India price anchors, so a non-India market gets a
    numberless variant instead — a stale anchor in the wrong currency is worse
    than no anchor. For INR the rendered text is byte-identical to the original.
    """
    profile = _market_or_default(market)
    examples_file = (
        "cost_examples_india.md" if profile.is_default else "cost_examples_generic.md"
    )
    examples = load_prompt(examples_file)
    if not profile.is_default:
        examples = examples.format(market=profile.market)
    return load_prompt(
        "cost_calibration.md",
        market=profile.market,
        market_examples=examples,
    )


def load_cost_output_schema(market: MarketProfile | None = None) -> str:
    """Cost JSON schema block, with the pricing market filled in."""
    return load_prompt("output_schema_cost.md", market=_market_or_default(market).market)


def _cost_self_check_block(market: MarketProfile | None = None) -> str:
    """Correction rules that used to arrive as a second LLM call. Empty in two-pass mode.

    Its worked corrections are India-priced, so they are dropped outside India for
    the same reason the calibration examples are.
    """
    from asset_validator.core.config import cost_single_pass

    if not cost_single_pass():
        return ""
    if not _market_or_default(market).is_default:
        return ""
    return load_prompt("cost_self_check.md")


def _waste_bin_cost_note(*fields: str) -> str:
    """Price the bin, not the cabinet it is built into — only for a waste-bin record.

    Customer 1158's "STEEL DUST BIN SWING 8X24" (₹980) and "Ss Dustbin" (₹765) were priced as
    "a standard wooden waste segregation cabinet" at ₹7,500–25,000 and failed cost. Empty for every
    other record, so their prompts stay byte-identical.
    """
    from asset_validator.core.config import WASTE_BIN_COST_NOTE_ENABLED
    from asset_validator.utils.helpers import names_waste_bin

    if not WASTE_BIN_COST_NOTE_ENABLED or not names_waste_bin(*fields):
        return ""
    return load_prompt("cost_waste_bin.md")


def build_declared_record_block(
    *,
    user_asset_name: str = "",
    user_description: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
) -> str:
    """Confirmed customer master data for pricing.

    Carries what a photo cannot show — stated dimensions, seat counts, capacity — which
    is what separates a 2700mm reception counter from a generic desk.
    """
    return load_prompt(
        "declared_record.md",
        user_asset_name=user_asset_name.strip() or "(empty)",
        user_description=user_description.strip() or "(empty)",
        user_category=user_category.strip() or "(empty)",
        user_subcategory=user_subcategory.strip() or "(empty)",
        user_makemodel=user_makemodel.strip() or "(empty)",
        user_model=user_model.strip() or "(empty)",
    )


def build_cost_date_extension(
    *,
    acquisition_date: date | None = None,
    declared_cost: str = "",
    user_asset_name: str = "",
    user_description: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
    use_user_identity: bool = False,
    market: MarketProfile | None = None,
) -> str:
    """Cost/date section for acquisition check — image-only or identity-assisted."""
    acq_display = (
        acquisition_date.strftime("%Y-%m-%d")
        if acquisition_date
        else "(not provided)"
    )
    template = "cost_date_with_identity.md" if use_user_identity else "cost_date.md"
    today_display = date.today().strftime("%Y-%m-%d")
    kwargs: dict = {
        "acquisition_date": acq_display,
        "current_date": today_display,
        "cost_calibration": _cost_calibration_block(market),
        "declared_cost": declared_cost or "(not provided)",
        "market": _market_or_default(market).market,
        "currency": _market_or_default(market).currency,
    }
    if use_user_identity:
        kwargs["declared_record"] = build_declared_record_block(
            user_asset_name=user_asset_name,
            user_description=user_description,
            user_category=user_category,
            user_subcategory=user_subcategory,
            user_makemodel=user_makemodel,
            user_model=user_model,
        )
    text = load_prompt(template, **kwargs) + _waste_bin_cost_note(user_asset_name, user_description)
    return adapt_prompt_for_market(text, market)


def build_cost_date_prompt(
    detected_asset: str,
    image_analysis: str,
    acquisition_date: date | None = None,
    visible_model_year_min: int | None = None,
    visible_model_year_max: int | None = None,
    *,
    use_user_identity: bool = False,
    user_asset_name: str = "",
    user_description: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
    market: MarketProfile | None = None,
) -> str:
    acq_display = (
        acquisition_date.strftime("%Y-%m-%d")
        if acquisition_date
        else "(not provided)"
    )
    if visible_model_year_min is not None or visible_model_year_max is not None:
        era_display = f"{visible_model_year_min or '?'} – {visible_model_year_max or '?'}"
    else:
        era_display = "(not estimated)"
    today_display = date.today().strftime("%Y-%m-%d")
    kwargs: dict = {
        "detected_asset": detected_asset or "(unknown)",
        "image_analysis": image_analysis or "(unknown)",
        "visible_era": era_display,
        "acquisition_date": acq_display,
        "current_date": today_display,
        "cost_calibration": _cost_calibration_block(market),
        "market": _market_or_default(market).market,
        "currency": _market_or_default(market).currency,
    }
    if use_user_identity:
        kwargs["declared_record"] = build_declared_record_block(
            user_asset_name=user_asset_name,
            user_description=user_description,
            user_category=user_category,
            user_subcategory=user_subcategory,
            user_makemodel=user_makemodel,
            user_model=user_model,
        )
        template = "cost_date_standalone_with_identity.md"
    else:
        template = "cost_date_standalone.md"
    text = load_prompt(template, **kwargs)
    text += _waste_bin_cost_note(user_asset_name, user_description)
    # Appended BEFORE hoisting so it stays in the static prefix, ahead of the per-asset block.
    text += _cost_self_check_block(market)
    return hoist_variable_context(adapt_prompt_for_market(text, market))


def build_cost_price_refine_prompt(
    *,
    detected_asset: str,
    image_analysis: str,
    pass1_identified_model: str = "",
    pass1_visual_tier: str = "",
    pass1_predicted_cost=None,
    pass1_confidence=None,
    acquisition_date: date | None = None,
    visible_model_year_min: int | None = None,
    visible_model_year_max: int | None = None,
    user_asset_name: str = "",
    user_description: str = "",
    user_category: str = "",
    user_subcategory: str = "",
    user_makemodel: str = "",
    user_model: str = "",
    use_user_identity: bool = False,
    market: MarketProfile | None = None,
) -> str:
    """Second-pass cost prompt — correct tier when pass 1 is weak."""
    acq_display = (
        acquisition_date.strftime("%Y-%m-%d")
        if acquisition_date
        else "(not provided)"
    )
    if visible_model_year_min is not None or visible_model_year_max is not None:
        era_display = f"{visible_model_year_min or '?'} – {visible_model_year_max or '?'}"
    else:
        era_display = "(not estimated)"

    if use_user_identity:
        identity_context = load_prompt(
            "cost_price_refine_identity.md",
            declared_record=build_declared_record_block(
                user_asset_name=user_asset_name,
                user_description=user_description,
                user_category=user_category,
                user_subcategory=user_subcategory,
                user_makemodel=user_makemodel,
                user_model=user_model,
            ),
        )
        identity_mode_label = " with confirmed AssetName/Description"
        identity_context_note = " and confirmed AssetName/Description"
    else:
        identity_context = ""
        identity_mode_label = " only"
        identity_context_note = " — do NOT use request name, description, or declared cost"

    today_display = date.today().strftime("%Y-%m-%d")
    text = load_prompt(
        "cost_price_refine.md",
        detected_asset=detected_asset or "(unknown)",
        image_analysis=image_analysis or "(unknown)",
        visible_era=era_display,
        acquisition_date=acq_display,
        current_date=today_display,
        pass1_identified_model=pass1_identified_model or "(none)",
        pass1_visual_tier=pass1_visual_tier or "(none)",
        pass1_predicted_cost=(
            pass1_predicted_cost if pass1_predicted_cost is not None else "(none)"
        ),
        pass1_confidence=(
            pass1_confidence if pass1_confidence is not None else "(none)"
        ),
        cost_calibration=_cost_calibration_block(market),
        market=_market_or_default(market).market,
        currency=_market_or_default(market).currency,
        identity_context=identity_context,
        identity_mode_label=identity_mode_label,
        identity_context_note=identity_context_note,
    )
    text += _waste_bin_cost_note(user_asset_name, user_description)
    return hoist_variable_context(adapt_prompt_for_market(text, market))
