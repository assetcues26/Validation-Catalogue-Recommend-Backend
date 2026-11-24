"""Currency to pricing-market resolution.

The backend sends a currency code, not a country. Currency is a usable proxy for
the pricing market but a lossy one: EUR spans twenty countries with materially
different price levels, and USD is transacted well outside the United States. Each
entry below therefore names the DOMINANT market for that currency, which is the
best available answer until a country/region field exists. When one arrives, add it
to `resolve_market` ahead of the currency lookup rather than reworking callers.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

DEFAULT_CURRENCY = "INR"


@dataclass(frozen=True)
class MarketProfile:
    """The market an asset should be priced in, and how to say so in a prompt."""

    currency: str
    market: str
    symbol: str

    @property
    def is_default(self) -> bool:
        """True for the historical India/INR behaviour."""
        return self.currency == DEFAULT_CURRENCY


# currency -> (dominant market, symbol used when quoting amounts)
_MARKETS: dict[str, tuple[str, str]] = {
    "INR": ("India", "₹"),
    "AED": ("the United Arab Emirates", "AED "),
    "SAR": ("Saudi Arabia", "SAR "),
    "QAR": ("Qatar", "QAR "),
    "KWD": ("Kuwait", "KWD "),
    "BHD": ("Bahrain", "BHD "),
    "OMR": ("Oman", "OMR "),
    "USD": ("the United States", "$"),
    "EUR": ("the Euro area", "€"),
    "GBP": ("the United Kingdom", "£"),
    "CHF": ("Switzerland", "CHF "),
    "SEK": ("Sweden", "SEK "),
    "NOK": ("Norway", "NOK "),
    "DKK": ("Denmark", "DKK "),
    "PLN": ("Poland", "PLN "),
    "TRY": ("Türkiye", "TRY "),
    "SGD": ("Singapore", "S$"),
    "MYR": ("Malaysia", "RM"),
    "THB": ("Thailand", "฿"),
    "IDR": ("Indonesia", "Rp"),
    "PHP": ("the Philippines", "₱"),
    "VND": ("Vietnam", "₫"),
    "JPY": ("Japan", "¥"),
    "CNY": ("China", "CN¥"),
    "HKD": ("Hong Kong", "HK$"),
    "KRW": ("South Korea", "₩"),
    "AUD": ("Australia", "A$"),
    "NZD": ("New Zealand", "NZ$"),
    "CAD": ("Canada", "C$"),
    "LKR": ("Sri Lanka", "LKR "),
    "BDT": ("Bangladesh", "৳"),
    "NPR": ("Nepal", "NPR "),
    "PKR": ("Pakistan", "PKR "),
    "ZAR": ("South Africa", "R"),
    "EGP": ("Egypt", "EGP "),
    "NGN": ("Nigeria", "₦"),
    "KES": ("Kenya", "KES "),
    "TZS": ("Tanzania", "TZS "),
    "BRL": ("Brazil", "R$"),
    "MXN": ("Mexico", "MX$"),
}


def _load_market_overrides() -> dict[str, tuple[str, str]]:
    """Merge the CURRENCY_MARKET_MAP App Setting over the built-in table.

    JSON of {"<CODE>": ["<market>", "<symbol>"]} so a market can be corrected in
    production without a deploy — e.g. {"EUR": ["Germany", "€"]}.
    """
    markets = dict(_MARKETS)
    raw = os.getenv("CURRENCY_MARKET_MAP", "").strip()
    if not raw:
        return markets
    try:
        for code, value in json.loads(raw).items():
            if isinstance(value, (list, tuple)) and len(value) == 2:
                markets[str(code).strip().upper()] = (str(value[0]), str(value[1]))
    except Exception as e:
        logging.warning(f"Ignoring malformed CURRENCY_MARKET_MAP: {e}")
    return markets


def normalize_currency(value) -> str | None:
    """Return a clean ISO-4217-shaped code, or None when it is not one.

    Deliberately strict: a currency we cannot place is worse than no currency at
    all, because it would silently anchor pricing to the wrong market.
    """
    if value is None:
        return None
    code = str(value).strip().upper()
    if len(code) != 3 or not code.isalpha():
        return None
    return code


def supported_currencies() -> frozenset[str]:
    return frozenset(_load_market_overrides())


def resolve_market(currency=None, request_id: str = "") -> MarketProfile:
    """Map a currency code to the market its prices should be quoted in.

    Anything unrecognised falls back to India/INR with a warning — the historical
    behaviour — so a new or malformed code can never produce a confidently wrong
    valuation in some other market.
    """
    markets = _load_market_overrides()
    code = normalize_currency(currency)

    if code is None:
        if currency not in (None, ""):
            logging.warning(
                f"[{request_id}] Ignoring malformed currency {currency!r}; "
                f"pricing in {DEFAULT_CURRENCY}"
            )
        code = DEFAULT_CURRENCY
    elif code not in markets:
        logging.warning(
            f"[{request_id}] No pricing market known for currency {code!r}; "
            f"pricing in {DEFAULT_CURRENCY}"
        )
        code = DEFAULT_CURRENCY

    market, symbol = markets[code]
    return MarketProfile(currency=code, market=market, symbol=symbol)


def market_for_request(req_body: dict, request_id: str = "") -> tuple[MarketProfile, bool]:
    """Resolve the pricing market for one request.

    Returns (profile, price_natively). When `price_natively` is True the cost prompts are
    rendered for `profile`'s market and the model's figures are already in that currency,
    so no FX conversion is applied.

    Precedence matches core.config.resolve_currency, which picks the currency the response
    reports and the declared cost is compared in — the two must never disagree:

    1. a Currency the request actually sent: priced natively in that market, or INR and
       not native when the code cannot be placed;
    2. otherwise the customer map: priced natively in the mapped market. It used to price
       these customers with the India prompt and divide by a fixed rate, which quoted a
       Dubai customer converted rupees and "India market pricing". CUSTOMER_MAP_PRICES_NATIVELY
       restores that behaviour without a deploy;
    3. otherwise India, not native — the historical default, a byte-identical prompt.
    """
    from asset_validator.core.config import (
        CURRENCY_FROM_REQUEST_ENABLED,
        CUSTOMER_CURRENCY,
        CUSTOMER_MAP_PRICES_NATIVELY,
        request_currency_sent,
    )

    raw = req_body.get("currency")
    if request_currency_sent(raw) and CURRENCY_FROM_REQUEST_ENABLED:
        profile = resolve_market(raw, request_id)
        if profile.is_default and normalize_currency(raw) != DEFAULT_CURRENCY:
            # Unusable code — resolve_market already warned. Fall back completely.
            return profile, False
        return profile, True

    customerid = req_body.get("customerid")
    mapped = (
        CUSTOMER_CURRENCY.get(str(customerid).strip()) if customerid is not None else None
    )
    if mapped and CUSTOMER_MAP_PRICES_NATIVELY:
        profile = resolve_market(mapped, request_id)
        if not profile.is_default:
            return profile, True

    return resolve_market(DEFAULT_CURRENCY), False
