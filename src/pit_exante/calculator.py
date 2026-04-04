"""Orchestrator: parse → classify → fifo → aggregate."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .classifier import classify
from .fifo import FifoEngine
from .models import (
    DividendEvent,
    TaxCategory,
    TaxEvent,
    Transaction,
    YearReport,
)
from .nbp import get_rate, save_cache_if_dirty
from .parser import is_instrument_trade, parse_transactions


def _effective_date(t: Transaction) -> date:
    """Get effective date for a transaction, falling back to timestamp if valueDate is None."""
    if t.value_date is not None:
        return t.value_date
    # Derive from timestamp (ms)
    from datetime import datetime
    return datetime.fromtimestamp(t.timestamp / 1000).date()


def _build_commission_map(transactions: list[Transaction]) -> dict[str, Decimal]:
    """Map orderId → total commission amount (absolute value)."""
    commissions: dict[str, Decimal] = defaultdict(Decimal)
    for t in transactions:
        if t.operation_type == "COMMISSION" and t.order_id:
            commissions[t.order_id] += abs(t.sum)
    return dict(commissions)


def _parse_dividend_symbol_from_comment(comment: str) -> str | None:
    """Extract symbol from US TAX comment.

    Examples:
      '5 shares ExD 2023-06-29 PD 2023-07-10 dividend NGE.ARCA 4.45 USD ...'
      'TY2022 US TAX recalculation income Code: 06'
      'TY2025 H1 US TAX recalculation for GOOG.NASDAQ Income code:06'
    """
    # Pattern for regular US TAX: ... dividend SYMBOL amount ...
    m = re.search(r"dividend\s+(\S+)\s+\d", comment)
    if m:
        return m.group(1)

    # Pattern for recalculation: ... recalculation for SYMBOL ...
    m = re.search(r"recalculation\s+for\s+(\S+)", comment)
    if m:
        return m.group(1)

    return None


def calculate(transactions_path: str | Path) -> tuple[list[YearReport], dict]:
    """Process all transactions and generate yearly tax reports.

    Returns (reports, open_positions) where open_positions is the FIFO state
    at the end of processing.
    """
    transactions = parse_transactions(transactions_path)
    commission_map = _build_commission_map(transactions)

    fifo = FifoEngine()
    tax_events: list[TaxEvent] = []
    dividend_events: list[DividendEvent] = []

    # Index dividends by uuid for TAX linkage
    dividend_by_uuid: dict[str, DividendEvent] = {}
    # Index dividends by (symbol, date) for US TAX linkage
    dividend_by_symbol_date: dict[tuple[str, str], list[DividendEvent]] = defaultdict(list)

    # Group CORPORATE ACTION transactions by (symbol, date) for batch processing
    corporate_actions: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    # Group STOCK SPLIT transactions by (symbol, date)
    stock_splits: dict[tuple[str, str], list[Transaction]] = defaultdict(list)

    # First pass: group special transactions
    for t in transactions:
        if t.operation_type == "CORPORATE ACTION" and t.symbol_id:
            key = (t.symbol_id, t.value_date.isoformat() if t.value_date else "")
            corporate_actions[key].append(t)
        elif t.operation_type == "STOCK SPLIT" and t.symbol_id:
            key = (t.symbol_id, t.value_date.isoformat() if t.value_date else "")
            stock_splits[key].append(t)

    # Track which corporate actions / splits have been processed
    processed_uuids: set[str] = set()

    # Main processing loop — chronological
    for t in transactions:
        if t.uuid in processed_uuids:
            continue

        category = classify(t)

        match category:
            case TaxCategory.BUY:
                if not is_instrument_trade(t):
                    continue  # Skip cash legs

                assert t.symbol_id is not None
                assert t.transaction_price is not None
                tx_date = _effective_date(t)

                commission = commission_map.get(t.order_id, Decimal("0")) if t.order_id else Decimal("0")
                nbp_rate = get_rate(t.currency, tx_date)

                if fifo.has_short_position(t.account_id, t.symbol_id):
                    # Closing a short position (stock or forex)
                    event = fifo.buy_to_close(
                        account_id=t.account_id,
                        symbol=t.symbol_id,
                        buy_date=tx_date,
                        quantity=t.sum,
                        buy_price=t.transaction_price,
                        currency=t.currency,
                        commission=commission,
                        nbp_rate_buy=nbp_rate,
                    )
                    tax_events.append(event)
                else:
                    fifo.buy(
                        account_id=t.account_id,
                        symbol=t.symbol_id,
                        buy_date=tx_date,
                        quantity=t.sum,
                        price_per_unit=t.transaction_price,
                        currency=t.currency,
                        commission=commission,
                        nbp_rate=nbp_rate,
                    )

            case TaxCategory.SELL:
                if not is_instrument_trade(t):
                    continue  # Skip cash legs

                assert t.symbol_id is not None
                assert t.transaction_price is not None
                tx_date = _effective_date(t)

                commission = commission_map.get(t.order_id, Decimal("0")) if t.order_id else Decimal("0")
                nbp_rate = get_rate(t.currency, tx_date)

                if not fifo.has_long_position(t.account_id, t.symbol_id):
                    # Opening a short position (no long shares to sell)
                    fifo.sell_short(
                        account_id=t.account_id,
                        symbol=t.symbol_id,
                        sell_date=tx_date,
                        quantity=abs(t.sum),
                        sell_price=t.transaction_price,
                        currency=t.currency,
                        commission=commission,
                        nbp_rate=nbp_rate,
                    )
                else:
                    event = fifo.sell(
                        account_id=t.account_id,
                        symbol=t.symbol_id,
                        sell_date=tx_date,
                        quantity=t.sum,
                        sell_price=t.transaction_price,
                        currency=t.currency,
                        sell_commission=commission,
                        nbp_rate_sell=nbp_rate,
                    )
                    tax_events.append(event)

            case TaxCategory.DIVIDEND:
                tx_date = _effective_date(t)
                nbp_rate = get_rate(t.currency, tx_date)
                symbol = t.symbol_id or t.asset

                div_event = DividendEvent(
                    date=tx_date,
                    symbol=symbol,
                    account_id=t.account_id,
                    gross_amount=t.sum,
                    gross_amount_pln=t.sum * nbp_rate,
                    tax_withheld=Decimal("0"),
                    tax_withheld_pln=Decimal("0"),
                    currency=t.currency,
                    nbp_rate=nbp_rate,
                    comment=t.comment or "",
                )
                dividend_events.append(div_event)
                dividend_by_uuid[t.uuid] = div_event
                dividend_by_symbol_date[(symbol, tx_date.isoformat())].append(div_event)

            case TaxCategory.TAX_WITHHELD:
                tx_date = _effective_date(t)
                tax_amount = abs(t.sum)
                nbp_rate = get_rate(t.currency, tx_date)
                tax_pln = tax_amount * nbp_rate

                if t.parent_uuid and t.parent_uuid in dividend_by_uuid:
                    # TAX linked by parentUuid
                    div = dividend_by_uuid[t.parent_uuid]
                    div.tax_withheld += tax_amount
                    div.tax_withheld_pln += tax_pln
                elif t.comment:
                    # US TAX: parse comment for symbol
                    symbol = _parse_dividend_symbol_from_comment(t.comment)
                    if symbol:
                        # Try to find matching dividend
                        key = (symbol, tx_date.isoformat())
                        matching_divs = dividend_by_symbol_date.get(key, [])
                        if matching_divs:
                            # Link to first unmatched dividend of same symbol/date
                            div = matching_divs[0]
                            div.tax_withheld += tax_amount
                            div.tax_withheld_pln += tax_pln
                        else:
                            # Standalone US TAX (recalculation or no matching dividend)
                            if t.sum > 0:
                                # Positive = refund → negative tax withheld
                                div_event = DividendEvent(
                                    date=tx_date,
                                    symbol=symbol,
                                    account_id=t.account_id,
                                    gross_amount=Decimal("0"),
                                    gross_amount_pln=Decimal("0"),
                                    tax_withheld=-t.sum,
                                    tax_withheld_pln=-t.sum * nbp_rate,
                                    currency=t.currency,
                                    nbp_rate=nbp_rate,
                                    comment=t.comment,
                                )
                            else:
                                div_event = DividendEvent(
                                    date=tx_date,
                                    symbol=symbol,
                                    account_id=t.account_id,
                                    gross_amount=Decimal("0"),
                                    gross_amount_pln=Decimal("0"),
                                    tax_withheld=tax_amount,
                                    tax_withheld_pln=tax_pln,
                                    currency=t.currency,
                                    nbp_rate=nbp_rate,
                                    comment=t.comment,
                                )
                            dividend_events.append(div_event)
                    else:
                        # Recalculation without symbol
                        div_event = DividendEvent(
                            date=tx_date,
                            symbol="US_TAX_RECALC",
                            account_id=t.account_id,
                            gross_amount=Decimal("0"),
                            gross_amount_pln=Decimal("0"),
                            tax_withheld=-t.sum if t.sum > 0 else tax_amount,
                            tax_withheld_pln=-t.sum * nbp_rate if t.sum > 0 else tax_pln,
                            currency=t.currency,
                            nbp_rate=nbp_rate,
                            comment=t.comment or "",
                        )
                        dividend_events.append(div_event)

            case TaxCategory.SPLIT:
                assert t.symbol_id is not None
                assert t.comment is not None
                tx_date = _effective_date(t)

                # Process both split transactions together
                key = (t.symbol_id, tx_date.isoformat())
                split_txns = stock_splits.get(key, [t])
                for st in split_txns:
                    processed_uuids.add(st.uuid)

                new_for_old, old_shares = FifoEngine.parse_split_ratio(t.comment)
                fifo.apply_split(
                    account_id=t.account_id,
                    symbol=t.symbol_id,
                    new_for_old=new_for_old,
                    old_shares=old_shares,
                )

            case TaxCategory.CORPORATE_ACTION:
                assert t.symbol_id is not None
                tx_date = _effective_date(t)

                # Get all related corporate action transactions
                key = (t.symbol_id, tx_date.isoformat())
                ca_txns = corporate_actions.get(key, [t])
                for ct in ca_txns:
                    processed_uuids.add(ct.uuid)

                # Also check other dates for fractional cash payment (may be next day)
                for potential_key, potential_txns in corporate_actions.items():
                    if potential_key[0] == t.symbol_id and potential_key != key:
                        ca_txns = ca_txns + potential_txns
                        for ct in potential_txns:
                            processed_uuids.add(ct.uuid)

                # Parse: find removal (negative sum, instrument), addition (positive sum, instrument), cash
                removal = None
                addition = None
                fractional_cash = None

                for ct in ca_txns:
                    if ct.asset == ct.symbol_id or (ct.symbol_id and ct.asset.startswith(ct.symbol_id.split(".")[0])):
                        if ct.sum < 0:
                            removal = ct
                        elif ct.sum > 0 and ct.transaction_price is not None:
                            addition = ct
                    elif ct.asset in ("USD", "EUR", "CAD", "SEK"):
                        fractional_cash = ct

                if removal and addition:
                    removal_date = _effective_date(removal)
                    nbp_rate = get_rate(removal.currency, removal_date)
                    cash_amount = Decimal(str(fractional_cash.sum)) if fractional_cash else None
                    cash_nbp = get_rate(
                        fractional_cash.currency if fractional_cash else removal.currency,
                        _effective_date(fractional_cash) if fractional_cash else removal_date
                    ) if fractional_cash else nbp_rate

                    events = fifo.apply_reverse_split(
                        account_id=removal.account_id,
                        symbol=removal.symbol_id,
                        reverse_date=removal_date,
                        old_quantity=abs(removal.sum),
                        new_quantity=addition.sum,
                        fractional_cash=cash_amount,
                        currency=removal.currency,
                        nbp_rate=cash_nbp,
                    )
                    tax_events.extend(events)

            case TaxCategory.ROLLOVER_COST:
                tx_date = _effective_date(t)
                nbp_rate = get_rate(t.currency, tx_date)
                event = TaxEvent(
                    date=tx_date,
                    symbol=t.symbol_id or "ROLLOVER",
                    account_id=t.account_id,
                    event_type="rollover_cost",
                    income_original=Decimal("0"),
                    cost_original=abs(t.sum),
                    income_pln=Decimal("0"),
                    cost_pln=abs(t.sum) * nbp_rate,
                    currency=t.currency,
                    nbp_rate=nbp_rate,
                    details=f"Rollover cost: {t.sum} {t.currency} — {t.comment or ''}",
                )
                tax_events.append(event)

            case TaxCategory.ROLLOVER_INCOME:
                tx_date = _effective_date(t)
                nbp_rate = get_rate(t.currency, tx_date)
                event = TaxEvent(
                    date=tx_date,
                    symbol=t.symbol_id or "ROLLOVER",
                    account_id=t.account_id,
                    event_type="rollover_income",
                    income_original=t.sum,
                    cost_original=Decimal("0"),
                    income_pln=t.sum * nbp_rate,
                    cost_pln=Decimal("0"),
                    currency=t.currency,
                    nbp_rate=nbp_rate,
                    details=f"Rollover income: {t.sum} {t.currency} — {t.comment or ''}",
                )
                tax_events.append(event)

            case TaxCategory.FEE:
                tx_date = _effective_date(t)
                nbp_rate = get_rate(t.currency, tx_date)
                event = TaxEvent(
                    date=tx_date,
                    symbol=t.symbol_id or "FEE",
                    account_id=t.account_id,
                    event_type="fee",
                    income_original=Decimal("0"),
                    cost_original=abs(t.sum),
                    income_pln=Decimal("0"),
                    cost_pln=abs(t.sum) * nbp_rate,
                    currency=t.currency,
                    nbp_rate=nbp_rate,
                    details=f"Fee: {t.sum} {t.currency} — {t.comment or ''}",
                )
                tax_events.append(event)

            case TaxCategory.SKIP:
                pass

    # Persist NBP cache after all rate lookups
    save_cache_if_dirty()

    # Aggregate by year
    reports = _aggregate_by_year(tax_events, dividend_events)
    positions = fifo.get_positions()

    return reports, positions


def _aggregate_by_year(
    tax_events: list[TaxEvent],
    dividend_events: list[DividendEvent],
) -> list[YearReport]:
    """Group events by tax year and compute totals."""
    tax_by_year: dict[int, list[TaxEvent]] = defaultdict(list)
    div_by_year: dict[int, list[DividendEvent]] = defaultdict(list)
    for e in tax_events:
        tax_by_year[e.date.year].append(e)
    for e in dividend_events:
        div_by_year[e.date.year].append(e)

    all_years = sorted(set(tax_by_year) | set(div_by_year))
    reports: list[YearReport] = []
    for year in all_years:
        report = YearReport(year=year)

        # PIT-38 events
        year_tax_events = tax_by_year.get(year, [])
        report.pit38_events = year_tax_events

        report.pit38_income = sum(
            e.income_pln for e in year_tax_events
        )
        report.pit38_cost = sum(
            e.cost_pln for e in year_tax_events
        )
        report.pit38_profit_loss = report.pit38_income - report.pit38_cost
        report.pit38_tax = max(
            Decimal("0"),
            (report.pit38_profit_loss * Decimal("0.19")).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        )

        # Dividend events
        year_div_events = div_by_year.get(year, [])
        report.dividend_events = year_div_events

        report.dividends_income_pln = sum(
            e.gross_amount_pln for e in year_div_events
        )
        report.dividends_tax_paid_pln = sum(
            e.tax_withheld_pln for e in year_div_events
        )
        report.dividends_tax_due_pln = max(
            Decimal("0"),
            (report.dividends_income_pln * Decimal("0.19")).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        )
        report.dividends_tax_to_pay_pln = max(
            Decimal("0"),
            report.dividends_tax_due_pln - report.dividends_tax_paid_pln,
        )

        reports.append(report)

    return reports
