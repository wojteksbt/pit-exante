"""Data models for PIT Exante calculator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum

ZERO = Decimal("0")
TAX_RATE = Decimal("0.19")
BARE_CURRENCIES = frozenset({"USD", "EUR", "CAD", "SEK", "PLN"})

_Q2 = Decimal("0.01")


def to_pln(amount: Decimal, rate: Decimal) -> Decimal:
    """Convert to PLN and round to grosze (Exante per-component rounding)."""
    return (amount * rate).quantize(_Q2, rounding=ROUND_HALF_UP)


class TaxCategory(Enum):
    BUY = "buy"
    SELL = "sell"
    COMMISSION = "commission"
    DIVIDEND = "dividend"
    TAX_WITHHELD = "tax_withheld"
    SPLIT = "split"
    CORPORATE_ACTION = "corporate_action"
    ROLLOVER_COST = "rollover_cost"
    ROLLOVER_INCOME = "rollover_income"
    FEE = "fee"
    SKIP = "skip"


@dataclass
class Transaction:
    uuid: str
    timestamp: int  # ms since epoch
    value_date: date | None  # from valueDate
    account_id: str  # ACC001.001 / ACC001.002
    symbol_id: str | None
    operation_type: str  # TRADE, COMMISSION, etc.
    sum: Decimal  # quantity (TRADE) or amount
    transaction_price: Decimal | None
    asset: str  # instrument or currency
    currency: str  # settlement currency (derived from asset)
    order_id: str | None
    parent_uuid: str | None
    comment: str | None
    id: int  # Exante transaction ID


@dataclass
class FifoLot:
    date: date
    quantity: Decimal
    price_per_unit: Decimal  # in original currency
    currency: str
    commission_per_unit: Decimal  # commission in currency
    nbp_rate: Decimal  # NBP rate from buy date

    @property
    def total_cost(self) -> Decimal:
        """Total cost in original currency including commission."""
        return self.quantity * (self.price_per_unit + self.commission_per_unit)

    @property
    def total_cost_pln(self) -> Decimal:
        """Total cost in PLN."""
        return self.total_cost * self.nbp_rate


@dataclass
class TaxEvent:
    date: date
    symbol: str
    account_id: str
    event_type: str  # "sell", "dividend", "rollover_cost", "rollover_income", "fee", "fractional_cash"
    income_original: Decimal  # in original currency
    cost_original: Decimal
    income_pln: Decimal
    cost_pln: Decimal
    currency: str
    nbp_rate: Decimal
    details: str  # description for report


@dataclass
class DividendEvent:
    date: date
    symbol: str
    account_id: str
    gross_amount: Decimal  # in original currency
    gross_amount_pln: Decimal
    tax_withheld: Decimal  # in original currency
    tax_withheld_pln: Decimal
    currency: str
    nbp_rate: Decimal
    comment: str
    country: str = ""


@dataclass
class YearReport:
    year: int
    # PIT-38
    pit38_income: Decimal = Decimal("0")
    pit38_cost: Decimal = Decimal("0")
    pit38_profit_loss: Decimal = Decimal("0")
    pit38_tax: Decimal = Decimal("0")
    pit38_events: list[TaxEvent] = field(default_factory=list)
    # PIT-36 / PIT-ZG
    dividends_income_pln: Decimal = Decimal("0")
    dividends_tax_paid_pln: Decimal = Decimal("0")
    dividends_tax_due_pln: Decimal = Decimal("0")
    dividends_tax_to_pay_pln: Decimal = Decimal("0")
    dividend_events: list[DividendEvent] = field(default_factory=list)
