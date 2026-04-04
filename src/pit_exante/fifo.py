"""FIFO engine for tax lot tracking."""

from __future__ import annotations

import re
from collections import defaultdict, deque
from datetime import date
from decimal import Decimal

from .models import FifoLot, TaxEvent


class FifoEngine:
    """Stateful FIFO engine tracking lots per (account_id, symbol).

    Supports both long and short positions. Short positions are used for
    forex/derivative instruments where you can sell before buying.
    """

    def __init__(self) -> None:
        self._queues: dict[tuple[str, str], deque[FifoLot]] = defaultdict(deque)
        self._short_queues: dict[tuple[str, str], deque[FifoLot]] = defaultdict(deque)

    def _key(self, account_id: str, symbol: str) -> tuple[str, str]:
        return (account_id, symbol)

    def has_long_position(self, account_id: str, symbol: str) -> bool:
        key = self._key(account_id, symbol)
        return bool(self._queues[key])

    def has_short_position(self, account_id: str, symbol: str) -> bool:
        key = self._key(account_id, symbol)
        return bool(self._short_queues[key])

    def buy(
        self,
        account_id: str,
        symbol: str,
        buy_date: date,
        quantity: Decimal,
        price_per_unit: Decimal,
        currency: str,
        commission: Decimal,
        nbp_rate: Decimal,
    ) -> None:
        """Add a new lot to the FIFO queue."""
        commission_per_unit = commission / quantity if quantity else Decimal("0")
        lot = FifoLot(
            date=buy_date,
            quantity=quantity,
            price_per_unit=price_per_unit,
            currency=currency,
            commission_per_unit=commission_per_unit,
            nbp_rate=nbp_rate,
        )
        self._queues[self._key(account_id, symbol)].append(lot)

    def sell(
        self,
        account_id: str,
        symbol: str,
        sell_date: date,
        quantity: Decimal,
        sell_price: Decimal,
        currency: str,
        sell_commission: Decimal,
        nbp_rate_sell: Decimal,
    ) -> TaxEvent:
        """Remove lots from FIFO queue and calculate capital gain/loss.

        Returns a TaxEvent with income and cost in both original currency and PLN.
        """
        key = self._key(account_id, symbol)
        queue = self._queues[key]

        remaining = abs(quantity)
        cost_pln = Decimal("0")
        cost_original = Decimal("0")
        consumed_lots: list[str] = []

        while remaining > 0 and queue:
            lot = queue[0]
            if lot.quantity <= remaining:
                # Consume entire lot
                cost_pln += lot.quantity * (lot.price_per_unit + lot.commission_per_unit) * lot.nbp_rate
                cost_original += lot.quantity * (lot.price_per_unit + lot.commission_per_unit)
                consumed_lots.append(f"{lot.date}:{lot.quantity}@{lot.price_per_unit}")
                remaining -= lot.quantity
                queue.popleft()
            else:
                # Partial consumption
                cost_pln += remaining * (lot.price_per_unit + lot.commission_per_unit) * lot.nbp_rate
                cost_original += remaining * (lot.price_per_unit + lot.commission_per_unit)
                consumed_lots.append(f"{lot.date}:{remaining}@{lot.price_per_unit}")
                lot.quantity -= remaining
                remaining = Decimal("0")

        if remaining > 0:
            raise ValueError(
                f"FIFO underflow: trying to sell {abs(quantity)} of {symbol} "
                f"on {account_id} but only had {abs(quantity) - remaining} in queue"
            )

        # Sell income
        sell_qty = abs(quantity)
        income_original = sell_qty * sell_price
        income_pln = income_original * nbp_rate_sell

        # Add sell commission to cost
        sell_commission_pln = abs(sell_commission) * nbp_rate_sell
        cost_pln += sell_commission_pln
        cost_original += abs(sell_commission)

        return TaxEvent(
            date=sell_date,
            symbol=symbol,
            account_id=account_id,
            event_type="sell",
            income_original=income_original,
            cost_original=cost_original,
            income_pln=income_pln,
            cost_pln=cost_pln,
            currency=currency,
            nbp_rate=nbp_rate_sell,
            details=f"SELL {sell_qty} {symbol} @ {sell_price} {currency}, FIFO lots: {'; '.join(consumed_lots)}",
        )

    def sell_short(
        self,
        account_id: str,
        symbol: str,
        sell_date: date,
        quantity: Decimal,
        sell_price: Decimal,
        currency: str,
        commission: Decimal,
        nbp_rate: Decimal,
    ) -> None:
        """Open a short position (sell without owning — for forex/derivatives)."""
        commission_per_unit = commission / quantity if quantity else Decimal("0")
        lot = FifoLot(
            date=sell_date,
            quantity=quantity,
            price_per_unit=sell_price,
            currency=currency,
            commission_per_unit=commission_per_unit,
            nbp_rate=nbp_rate,
        )
        self._short_queues[self._key(account_id, symbol)].append(lot)

    def buy_to_close(
        self,
        account_id: str,
        symbol: str,
        buy_date: date,
        quantity: Decimal,
        buy_price: Decimal,
        currency: str,
        commission: Decimal,
        nbp_rate_buy: Decimal,
    ) -> TaxEvent:
        """Close a short position by buying back (FIFO on short lots).

        For short positions: income = sell price × qty (when short was opened),
        cost = buy price × qty (when closing).
        """
        key = self._key(account_id, symbol)
        queue = self._short_queues[key]

        remaining = abs(quantity)
        income_pln = Decimal("0")
        income_original = Decimal("0")
        consumed_lots: list[str] = []

        while remaining > 0 and queue:
            lot = queue[0]
            if lot.quantity <= remaining:
                income_pln += lot.quantity * lot.price_per_unit * lot.nbp_rate
                income_original += lot.quantity * lot.price_per_unit
                consumed_lots.append(f"{lot.date}:{lot.quantity}@{lot.price_per_unit}")
                remaining -= lot.quantity
                queue.popleft()
            else:
                income_pln += remaining * lot.price_per_unit * lot.nbp_rate
                income_original += remaining * lot.price_per_unit
                consumed_lots.append(f"{lot.date}:{remaining}@{lot.price_per_unit}")
                lot.quantity -= remaining
                remaining = Decimal("0")

        if remaining > 0:
            raise ValueError(
                f"Short FIFO underflow: trying to close {abs(quantity)} of {symbol} "
                f"on {account_id} but only had {abs(quantity) - remaining} short"
            )

        buy_qty = abs(quantity)
        cost_original = buy_qty * buy_price + abs(commission)
        cost_pln = buy_qty * buy_price * nbp_rate_buy + abs(commission) * nbp_rate_buy

        return TaxEvent(
            date=buy_date,
            symbol=symbol,
            account_id=account_id,
            event_type="sell",
            income_original=income_original,
            cost_original=cost_original,
            income_pln=income_pln,
            cost_pln=cost_pln,
            currency=currency,
            nbp_rate=nbp_rate_buy,
            details=f"Close short {buy_qty} {symbol} @ {buy_price} {currency}, short lots: {'; '.join(consumed_lots)}",
        )

    def apply_split(
        self,
        account_id: str,
        symbol: str,
        new_for_old: int,
        old_shares: int,
    ) -> None:
        """Apply a stock split (e.g., 2-for-1).

        Adjusts quantity and price per unit so total cost stays constant.
        """
        key = self._key(account_id, symbol)
        queue = self._queues[key]

        ratio = Decimal(str(new_for_old)) / Decimal(str(old_shares))
        for lot in queue:
            lot.quantity *= ratio
            lot.price_per_unit /= ratio
            lot.commission_per_unit /= ratio

    def apply_reverse_split(
        self,
        account_id: str,
        symbol: str,
        reverse_date: date,
        old_quantity: Decimal,
        new_quantity: Decimal,
        fractional_cash: Decimal | None,
        currency: str,
        nbp_rate: Decimal,
    ) -> list[TaxEvent]:
        """Apply a reverse split (e.g., REMX 1-for-3).

        Removes old lots, creates new lot with adjusted cost basis.
        If there's fractional cash payment, creates a TaxEvent for it.
        """
        key = self._key(account_id, symbol)
        queue = self._queues[key]

        # Pop all existing lots and compute total cost basis
        total_cost_pln = Decimal("0")
        total_cost_original = Decimal("0")
        old_lots: list[FifoLot] = []

        consumed = Decimal("0")
        while consumed < old_quantity and queue:
            lot = queue.popleft()
            old_lots.append(lot)
            total_cost_pln += lot.quantity * (lot.price_per_unit + lot.commission_per_unit) * lot.nbp_rate
            total_cost_original += lot.quantity * (lot.price_per_unit + lot.commission_per_unit)
            consumed += lot.quantity

        events: list[TaxEvent] = []

        fraction_for_new_shares = new_quantity / old_quantity

        # Average NBP rate from old lots
        if old_lots:
            weighted_rate = sum(
                lot.quantity * lot.nbp_rate for lot in old_lots
            ) / sum(lot.quantity for lot in old_lots)
        else:
            weighted_rate = nbp_rate

        new_lot = FifoLot(
            date=old_lots[0].date if old_lots else reverse_date,
            quantity=new_quantity,
            price_per_unit=total_cost_original / new_quantity,
            currency=currency,
            commission_per_unit=Decimal("0"),  # commission already baked into price
            nbp_rate=weighted_rate,
        )
        queue.append(new_lot)

        # Fractional cash payment = taxable event
        if fractional_cash and fractional_cash > 0:
            # Fractional shares = old_quantity - (new_quantity * old_quantity/new_quantity)
            # Actually: the cash represents the fraction that didn't make a whole share
            fractional_shares = old_quantity - new_quantity * (old_quantity / new_quantity)
            # Simpler: cost proportional to cash vs total value
            fractional_cost_pln = total_cost_pln * (Decimal("1") - fraction_for_new_shares)
            fractional_cost_original = total_cost_original * (Decimal("1") - fraction_for_new_shares)

            # Actually, for REMX 1:3: 5 shares → 1 whole + 0.67 fractional
            # Fractional cost = (0.67/5) * total_cost = cost of 0.67 shares
            # But simpler: cash / total_value * total_cost
            fractional_income_pln = fractional_cash * nbp_rate

            events.append(TaxEvent(
                date=reverse_date,
                symbol=symbol,
                account_id=account_id,
                event_type="fractional_cash",
                income_original=fractional_cash,
                cost_original=fractional_cost_original,
                income_pln=fractional_income_pln,
                cost_pln=fractional_cost_pln,
                currency=currency,
                nbp_rate=nbp_rate,
                details=f"Reverse split {symbol} fractional share payment: {fractional_cash} {currency}",
            ))

        return events

    def get_positions(self) -> dict[tuple[str, str], list[FifoLot]]:
        """Return current positions (for open position reporting)."""
        return {k: list(v) for k, v in self._queues.items() if v}

    @staticmethod
    def parse_split_ratio(comment: str) -> tuple[int, int]:
        """Parse split ratio from comment like 'Stock split 2 for 1' or 'Stock Split 1 for 3'.

        Returns (new, old) — e.g., (2, 1) for 2-for-1 split.
        """
        m = re.search(r"(\d+)\s+for\s+(\d+)", comment, re.IGNORECASE)
        if not m:
            raise ValueError(f"Cannot parse split ratio from: {comment}")
        return int(m.group(1)), int(m.group(2))
