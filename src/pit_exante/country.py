"""Derive dividend source country from instrument symbol and currency."""

from __future__ import annotations

_EXCHANGE_COUNTRY: dict[str, str] = {
    "NYSE": "US",
    "NASDAQ": "US",
    "ARCA": "US",
    "BATS": "US",
    "TMX": "CA",
    "SOMX": "SE",
}


def derive_country(
    symbol: str,
    currency: str | None = None,
    overrides: dict[str, str] | None = None,
) -> str:
    """Derive ISO country code for a dividend-paying instrument.

    Priority:
    1. Manual override (if provided)
    2. Currency heuristic (CAD on US exchange → CA)
    3. Exchange suffix mapping
    """
    if overrides and symbol in overrides:
        return overrides[symbol]

    exchange = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    country = _EXCHANGE_COUNTRY.get(exchange, "??")

    if country == "US" and currency == "CAD":
        country = "CA"

    return country
