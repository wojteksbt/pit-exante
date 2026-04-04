"""Classify transactions into tax categories."""

from __future__ import annotations

from .models import TaxCategory, Transaction


def classify(t: Transaction) -> TaxCategory:
    """Map operation type to tax category."""
    match t.operation_type:
        case "TRADE":
            return TaxCategory.BUY if t.sum > 0 else TaxCategory.SELL
        case "COMMISSION":
            return TaxCategory.COMMISSION
        case "DIVIDEND":
            return TaxCategory.DIVIDEND
        case "TAX" | "US TAX":
            return TaxCategory.TAX_WITHHELD
        case "STOCK SPLIT":
            return TaxCategory.SPLIT
        case "CORPORATE ACTION":
            return TaxCategory.CORPORATE_ACTION
        case "ROLLOVER":
            return TaxCategory.ROLLOVER_COST if t.sum < 0 else TaxCategory.ROLLOVER_INCOME
        case "SPECIAL FEE" | "EXCESS MARGIN FEE":
            return TaxCategory.FEE
        case _:
            return TaxCategory.SKIP
