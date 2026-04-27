"""Parse Exante transactions JSON into Transaction objects."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import BARE_CURRENCIES, Transaction

# Last-resort suffix fallback for symbol-leg rows that have no TRADE cash leg
# in the dataset (none in current data). Only trust mono-currency exchanges.
# .TMX intentionally excluded — Toronto lists both CAD-class (LUN, U.UN) and
# USD-class (U/U, BTCQ.U) instruments; the cash-leg map is authoritative.
_EXCHANGE_CURRENCY: dict[str, str] = {
    ".NYSE": "USD",
    ".NASDAQ": "USD",
    ".ARCA": "USD",
    ".BATS": "USD",
    ".SOMX": "SEK",
}


def _build_orderid_currency_map(raw: list[dict]) -> dict[str, str]:
    """Map orderId → settlement currency, derived from TRADE cash legs.

    Each TRADE order in Exante has paired rows with the same orderId: the
    instrument leg (asset = symbolId) and the cash leg (asset is a bare
    currency). The cash leg's asset is the empirical settlement currency.

    AUTOCONVERSION rows (broker-internal forex bridging EUR↔USD↔CAD↔SEK
    around the trade) share the same orderId but are NOT the settlement
    currency, so we filter to operationType == "TRADE" only.
    """
    mapping: dict[str, str] = {}
    for r in raw:
        if r.get("operationType") != "TRADE":
            continue
        oid = r.get("orderId")
        asset = r.get("asset")
        if oid and asset in BARE_CURRENCIES:
            mapping[oid] = asset
    return mapping


def _derive_currency(
    asset: str,
    symbol_id: str | None,
    orderid_currency_map: dict[str, str] | None = None,
    order_id: str | None = None,
) -> str:
    """Derive settlement currency.

    Hierarchy:
    1. asset is itself a bare currency → asset (e.g. DIVIDEND in USD)
    2. asset is forex pair (.FX) → quote currency
    3. order_id is in cash-leg map → that currency (primary truth)
    4. asset's exchange suffix → suffix table fallback
    5. symbol_id's exchange suffix → suffix table fallback
    6. default "USD"
    """
    if asset in BARE_CURRENCIES:
        return asset

    # Forex: EUR/USD.E.FX → settlement in USD
    if asset.endswith(".FX"):
        # Extract quote currency: EUR/USD.E.FX → USD
        parts = asset.split("/")
        if len(parts) == 2:
            return parts[1].split(".")[0]
        return "USD"

    # Primary: cash leg of the same TRADE order
    if orderid_currency_map and order_id and order_id in orderid_currency_map:
        return orderid_currency_map[order_id]

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

    orderid_currency_map = _build_orderid_currency_map(raw)

    transactions: list[Transaction] = []
    for r in raw:
        asset = r["asset"]
        symbol_id = r.get("symbolId")
        order_id = r.get("orderId")
        currency = _derive_currency(asset, symbol_id, orderid_currency_map, order_id)

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
    return t.transaction_price is not None and t.asset not in BARE_CURRENCIES
