"""Map Exante symbolType to InstrumentKind for PIT-8C/PIT-38 classification.

Exante /md/3.0/symbols/{id} returns symbolType field with values like STOCK,
CFD, FUTURE, OPTION etc. We map these to two tax categories (SECURITY vs
DERIVATIVE) which determine which PIT-8C position (23-24 or 27-28) and PIT-38
row (1 or 3) the income/cost flows into.

FX is intentionally NOT in EXANTE_TYPE_TO_KIND. Manual currency exchange at
broker (EUR/USD.E.FX with sum=0 P&L, only commission booked) is handled
directly in calculator.py — the resulting fee event gets kind=SECURITY because
it's a broker commission, not a forex derivative trade.
"""

from __future__ import annotations

from .models import (
    InstrumentKind,
    UnknownInstrumentError,
    UnknownTypeError,
)

EXANTE_TYPE_TO_KIND: dict[str, InstrumentKind] = {
    "STOCK": InstrumentKind.SECURITY,
    "BOND": InstrumentKind.SECURITY,
    "FUND": InstrumentKind.SECURITY,
    "CFD": InstrumentKind.DERIVATIVE,
    "FUTURE": InstrumentKind.DERIVATIVE,
    "OPTION": InstrumentKind.DERIVATIVE,
}


def get_symbol_type(
    symbol_id: str,
    symbols: dict[str, dict],
    overrides: dict[str, str],
) -> str:
    """Return symbolType for symbol_id. Metadata wins over override if both present."""
    if symbol_id in symbols:
        return symbols[symbol_id]["symbolType"]
    if symbol_id in overrides:
        return overrides[symbol_id]
    raise UnknownInstrumentError(
        f"Symbol {symbol_id!r} not in metadata (data/symbols.json) nor overrides "
        f"(config/symbol_overrides.json). Run download_transactions.py to refresh "
        f"metadata, or add manually to overrides."
    )


def classify(
    symbol_id: str,
    symbols: dict[str, dict],
    overrides: dict[str, str],
) -> InstrumentKind:
    """Classify symbol as SECURITY or DERIVATIVE for PIT-8C/PIT-38 mapping."""
    symbol_type = get_symbol_type(symbol_id, symbols, overrides)
    if symbol_type not in EXANTE_TYPE_TO_KIND:
        raise UnknownTypeError(symbol_type, symbol_id)
    return EXANTE_TYPE_TO_KIND[symbol_type]
