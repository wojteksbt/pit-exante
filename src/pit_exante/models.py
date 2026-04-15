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


class InstrumentKind(Enum):
    """Tax classification per art. 30b ustawy o PIT.

    Maps to PIT-8C positions and PIT-38 rows:
    - SECURITY: PIT-8C poz. 23-24, PIT-38 wiersz 1 (akcje, ETF, obligacje, fundusze)
    - DERIVATIVE: PIT-8C poz. 27-28, PIT-38 wiersz 3 (CFD, futures, opcje)
    """

    SECURITY = "security"
    DERIVATIVE = "derivative"


class UnknownInstrumentError(KeyError):
    """Raised when symbolId is not in metadata (data/symbols.json) nor overrides."""


class UnknownTypeError(ValueError):
    """Raised when symbolType is not in EXANTE_TYPE_TO_KIND mapping."""

    def __init__(self, symbol_type: str, symbol_id: str):
        super().__init__(
            f"Unknown symbolType {symbol_type!r} for symbol {symbol_id!r}. "
            f"Add to EXANTE_TYPE_TO_KIND in symbol_metadata.py."
        )
        self.symbol_type = symbol_type
        self.symbol_id = symbol_id


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
