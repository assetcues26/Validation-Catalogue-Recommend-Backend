"""Market adaptation for rendered cost prompts.

The cost prompts carry a handful of worked examples anchored to Indian prices
("~₹1,800-2,800 for an entry CCTV"). Those anchors are the most useful part of
the prompt inside India and actively harmful outside it — quoting a rupee figure
to a model pricing in dirhams invites it to convert rather than price locally.

Rather than rewriting the tuned India text, the anchor-bearing lines are swapped
for market-neutral equivalents **only when the market is not the default**. For
INR this module is a pass-through, so the India prompt stays byte-for-byte what
it has always been.
"""

from __future__ import annotations

from asset_validator.core.market import MarketProfile

# (India-anchored line, market-neutral replacement). The replacement keeps the
# tier lesson — which is the transferable part — and drops the currency figures.
_ANCHOR_SUBSTITUTIONS: tuple[tuple[str, str], ...] = (
    # The opening line of the text-only cost prompts — the builder production uses once
    # phase 1 has identified the asset. It said INDIAN market for every market, and it is
    # the first and most salient instruction the model reads.
    (
        'Analyze the asset image for the INDIAN market with maximum accuracy — **image evidence only**.',
        'Analyze the asset image for its {currency} price in {market} with maximum accuracy — **image evidence only**.',
    ),
    (
        'Analyze the asset for the INDIAN market with maximum accuracy — use **vision context and confirmed AssetName/Description** together.',
        'Analyze the asset for its {currency} price in {market} with maximum accuracy — use **vision context and confirmed AssetName/Description** together.',
    ),
    (
        "=== COST / DATE (INDIAN market — INR only) ===",
        "=== COST / DATE ({market} market — {currency} only) ===",
    ),
    (
        "   - Historical acquisition priced with today's used/resale values "
        "(e.g. 2019 MacBook at ₹45k instead of ~₹1.5L–2.2L new 2019)",
        "   - Historical acquisition priced with today's used/resale values instead of "
        "that year's NEW price in {market}",
    ),
    (
        "   - Small fixed bullet/dome CCTV priced at ₹10,000+ when range should be "
        "~₹1,800–2,800 (entry tier)",
        "   - Small fixed bullet/dome CCTV priced at an enterprise/PTZ level when it "
        "belongs in the ENTRY tier for {market}",
    ),
    (
        "   - Large premium wall fans priced at ₹2,000–4,000 when range should be "
        "~₹6,500–9,000 (premium tier)",
        "   - Large premium wall fans priced at small-domestic-desk-fan level when they "
        "belong in the PREMIUM tier for {market}",
    ),
    (
        "  - Small fixed bullet/dome CCTV (IR ring, no PTZ) → entry CCTV tier "
        "(low thousands INR new India), not mid/enterprise",
        "  - Small fixed bullet/dome CCTV (IR ring, no PTZ) → entry CCTV tier at "
        "{market} entry-tier prices, not mid/enterprise",
    ),
    (
        "  - Large wall fan with metal blades and speed controls on motor head → "
        "premium wall-fan tier (high thousands), not domestic ₹3k class",
        "  - Large wall fan with metal blades and speed controls on motor head → "
        "premium wall-fan tier at {market} prices, not the domestic desk-fan class",
    ),
)


def adapt_prompt_for_market(text: str, market: MarketProfile | None) -> str:
    """Swap India price anchors for market-neutral wording outside India.

    Returns `text` unchanged for the default market — the India prompt is never
    routed through a substitution table.
    """
    if market is None or market.is_default:
        return text
    for anchored, neutral in _ANCHOR_SUBSTITUTIONS:
        if anchored in text:
            text = text.replace(
                anchored,
                neutral.format(market=market.market, currency=market.currency),
            )
    return text


def unadapted_anchor_count(text: str) -> int:
    """How many India anchors are still present — used by the tests to prove the
    substitution table has not drifted from the prompt files."""
    return sum(1 for anchored, _ in _ANCHOR_SUBSTITUTIONS if anchored in text)
