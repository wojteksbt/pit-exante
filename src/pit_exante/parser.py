"""Parse Exante transactions JSON into Transaction objects."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import Transaction

# Exchange suffixes → settlement currency
_EXCHANGE_CURRENCY: dict[str, str] = {
    ".NYSE": "USD",
    ".NASDAQ": "USD",
    ".ARCA": "USD",
    ".BATS": "USD",
    ".TMX": "CAD",
    ".SOMX": "SEK",
}

_BARE_CURRENCIES = {"USD", "EUR", "CAD", "SEK", "PLN"}


def _derive_currency(asset: str, symbol_id: str | None) -> str:
    """Derive settlement currency from asset string."""
    if asset in _BARE_CURRENCIES:
        return asset

    # Forex: EUR/USD.E.FX → settlement in USD
    if asset.endswith(".FX"):
        # Extract quote currency: EUR/USD.E.FX → USD
        parts = asset.split("/")
        if len(parts) == 2:
            return parts[1].split(".")[0]
        return "USD"

    for suffix, currency in _EXCHANGE_CURRENCY.items():
        if asset.endswith(suffix):
            return currency

    # Fallback: try symbolId
    if symbol_id:
        for suffix, currency in _EXCHANGE_CURRENCY.items():
            if symbol_id.endswith(suffix):
                return currency

    return "USD"  # default


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def parse_transactions(path: str | Path) -> list[Transaction]:
    """Load and parse transactions from JSON file.

    Returns all transactions sorted chronologically.
    """
    with open(path) as f:
        raw = json.load(f)

    transactions: list[Transaction] = []
    for r in raw:
        asset = r["asset"]
        symbol_id = r.get("symbolId")
        currency = _derive_currency(asset, symbol_id)

        t = Transaction(
            uuid=r["uuid"],
            timestamp=r["timestamp"],
            value_date=_parse_date(r.get("valueDate")),
            account_id=r["accountId"],
            symbol_id=symbol_id,
            operation_type=r["operationType"],
            sum=Decimal(str(r["sum"])),
            transaction_price=Decimal(str(r["transactionPrice"])) if r.get("transactionPrice") is not None else None,
            asset=asset,
            currency=currency,
            order_id=r.get("orderId"),
            parent_uuid=r.get("parentUuid"),
            comment=r.get("comment"),
            id=r["id"],
        )
        transactions.append(t)

    transactions.sort(key=lambda t: (t.timestamp, t.id))
    return transactions


def is_instrument_trade(t: Transaction) -> bool:
    """Check if a TRADE transaction is the instrument leg (not the cash leg).

    Instrument legs have: transactionPrice set AND asset matches symbolId.
    Cash legs have: asset is a bare currency (USD, EUR, etc.) and no transactionPrice.
    """
    if t.operation_type != "TRADE":
        return False
    return t.transaction_price is not None and t.asset not in _BARE_CURRENCIES
