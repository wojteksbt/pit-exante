"""Derive dividend source country from instrument symbol and currency."""

from __future__ import annotations

from decimal import Decimal

from .models import TAX_RATE

_EXCHANGE_COUNTRY: dict[str, str] = {
    "NYSE": "US",
    "NASDAQ": "US",
    "ARCA": "US",
    "BATS": "US",
    "TMX": "CA",
    "SOMX": "SE",
}

# Stawki UPO (umów o unikaniu podwójnego opodatkowania) dla dywidend portfelowych.
# Limit z art. 30a ust. 9 ustawy o PIT — nie więcej niż stawka UPO × dochód.
_COUNTRY_UPO_RATE: dict[str, Decimal] = {
    "US": Decimal("0.15"),  # UPO PL-USA art. 11 (Dz.U. 1976 nr 31 poz. 178)
    "CA": Decimal("0.15"),  # UPO PL-Kanada art. 10 (Dz.U. 2013 poz. 1371)
    "SE": Decimal("0.15"),  # UPO PL-Szwecja art. 10
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


def upo_rate(country: str) -> Decimal:
    """Stawka UPO dla dywidendy z danego kraju.

    Default = 19% PL (TAX_RATE) gdy brak UPO — zgodnie z art. 30a ust. 9
    cap nie może przekroczyć krajowych 19% i tyle.
    """
    return _COUNTRY_UPO_RATE.get(country, TAX_RATE)
