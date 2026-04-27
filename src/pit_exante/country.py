"""Derive dividend source country and apply UPO (DTT) limits."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from .models import TAX_RATE

if TYPE_CHECKING:
    from .models import DividendEvent

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

# Tolerancja na zaokrąglenia kursów — gdy efektywna stawka WHT w walucie
# oryginalnej jest ≤ stawce UPO + tej tolerancji, traktujemy to jako "WHT
# pobrany na poziomie UPO" (np. USA 15% = UPO 15%) i nie cap-ujemy w PLN.
# Cap w PLN dawałby sztuczne straty groszowe wynikające wyłącznie z konwersji
# walutowych (różne kursy NBP dla różnych dywidend w roku).
UPO_RATE_TOLERANCE: Decimal = Decimal("0.001")  # 0.1 pp


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


def is_below_upo_threshold(country: str, events: list[DividendEvent]) -> bool:
    """Czy kraj jest w branch'u "no cap clamping" dla limitu z art. 30a ust. 9.

    True gdy efektywna stawka WHT w walucie oryginalnej (nie PLN) ≤ UPO + tolerance.
    W tym branchu country aggregate odlicza pełen c_paid (capped at 19% PL),
    a per-row deduction = min(WHT, PL 19%) — bez cap clamping w PLN.

    False gdy WHT > UPO (np. CA bez NR301 = 25%) — wtedy cap clamping w PLN
    per-row i per-country: deduct = min(c_paid, c_cap_upo).
    """
    gross_orig = sum((e.gross_amount for e in events), Decimal("0"))
    paid_orig = sum((e.tax_withheld for e in events), Decimal("0"))
    if gross_orig <= 0:
        return False
    effective_rate = paid_orig / gross_orig
    return effective_rate <= upo_rate(country) + UPO_RATE_TOLERANCE
