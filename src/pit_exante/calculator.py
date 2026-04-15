"""Orchestrator: parse → classify → fifo → aggregate."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from .classifier import classify
from .country import derive_country
from .fifo import FifoEngine
from .models import (
    BARE_CURRENCIES,
    TAX_RATE,
    DividendEvent,
    TaxCategory,
    TaxEvent,
    Transaction,
    YearReport,
    to_pln,
)
from .nbp import get_rate, save_cache_if_dirty
from .parser import is_instrument_trade, parse_transactions


def _normalize_account(account_id: str) -> str:
    """Normalize subaccount to main account for FIFO purposes.

    Exante subaccounts (e.g., ACC001.001, ACC001.002) share a single FIFO pool.
    The rozliczenie Exante confirms combined FIFO across subaccounts.
    """
    # Strip subaccount suffix: ACC001.001 → ACC001
    parts = account_id.rsplit(".", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return account_id


def _effective_date(t: Transaction) -> date:
    """Get effective date for a transaction, falling back to timestamp if valueDate is None."""
    if t.value_date is not None:
        return t.value_date
    return _timestamp_date(t)


# Polish timezone: CET (UTC+1) / CEST (UTC+2). Using CET as conservative
# default — the 1h difference vs CEST doesn't cross midnight for any
# Exante transaction in the dataset (market hours are daytime).
_TZ_POLAND = timezone(timedelta(hours=1))


def _timestamp_date(t: Transaction) -> date:
    """Get date from timestamp (execution/recording date) in Polish time.

    Exante rozliczenia use timestamp date for dividends and tax events,
    not the valueDate (payment date). This affects year assignment and NBP rates.
    Uses Polish timezone (CET) — PIT is filed with Polish tax authority.
    """
    return datetime.fromtimestamp(t.timestamp / 1000, tz=_TZ_POLAND).date()


def _build_commission_map(transactions: list[Transaction]) -> dict[str, Decimal]:
    """Map orderId → total commission amount (absolute value)."""
    commissions: dict[str, Decimal] = defaultdict(Decimal)
    for t in transactions:
        if t.operation_type == "COMMISSION" and t.order_id:
            commissions[t.order_id] += abs(t.sum)
    return dict(commissions)


def _build_settlement_value_map(transactions: list[Transaction]) -> dict[str, Decimal]:
    """Map orderId → actual settlement value from cash leg.

    Exante cash legs contain the actual settlement amount which may differ
    slightly from qty × transactionPrice due to execution price precision.

    Only populated for single-fill orders. Multi-fill orders (partial executions)
    share cash legs across fills, making per-fill attribution impossible.
    """
    # Count instrument fills per order
    fills_per_order: dict[str, int] = defaultdict(int)
    for t in transactions:
        if (t.operation_type == "TRADE" and t.order_id
                and t.transaction_price is not None
                and t.asset not in BARE_CURRENCIES):
            fills_per_order[t.order_id] += 1

    # Only build settlement map for single-fill orders
    values: dict[str, Decimal] = {}
    for t in transactions:
        if (t.operation_type == "TRADE" and t.order_id
                and t.asset in BARE_CURRENCIES
                and fills_per_order.get(t.order_id, 0) == 1):
            values[t.order_id] = values.get(t.order_id, Decimal("0")) + abs(t.sum)
    return values


def _build_execution_date_map(transactions: list[Transaction]) -> dict[str, date]:
    """Map orderId → execution date (from COMMISSION valueDate).

    Exante COMMISSION valueDate is the trade execution date (T+0),
    while TRADE valueDate is the settlement date (T+2 for US stocks).
    Polish tax law requires NBP rate from the day preceding the transaction,
    and Exante rozliczenia use the execution date for this purpose.
    """
    exec_dates: dict[str, date] = {}
    for t in transactions:
        if t.operation_type == "COMMISSION" and t.order_id and t.value_date:
            if t.order_id not in exec_dates:
                exec_dates[t.order_id] = t.value_date
    return exec_dates


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


def _match_tax_by_timestamp(
    tax_txn: Transaction,
    tax_amount: Decimal,
    tax_pln: Decimal,
    dividend_txns_by_symbol: dict[str, list[tuple[int, DividendEvent]]],
    max_delta_ms: int = 60_000,
    symbol_override: str | None = None,
) -> bool:
    """Match a TAX entry to a dividend by timestamp proximity + symbol.

    Exante emits TAX and DIVIDEND with nearly identical timestamps (within ms)
    but different valueDates. Returns True if matched.
    tax_amount/tax_pln can be negative for refunds.
    """
    symbol = symbol_override or tax_txn.symbol_id
    if not symbol or symbol not in dividend_txns_by_symbol:
        return False

    best_div = None
    best_delta = float("inf")
    for ts, div_event in dividend_txns_by_symbol[symbol]:
        delta = abs(ts - tax_txn.timestamp)
        if delta < best_delta and delta < max_delta_ms:
            best_delta = delta
            best_div = div_event

    if best_div is not None:
        best_div.tax_withheld += tax_amount
        best_div.tax_withheld_pln += tax_pln
        return True
    return False


def calculate(transactions_path: str | Path) -> tuple[list[YearReport], dict]:
    """Process all transactions and generate yearly tax reports.

    Returns (reports, open_positions) where open_positions is the FIFO state
    at the end of processing.
    """
    transactions = parse_transactions(transactions_path)
    commission_map = _build_commission_map(transactions)
    execution_date_map = _build_execution_date_map(transactions)
    settlement_value_map = _build_settlement_value_map(transactions)

    fifo = FifoEngine()
    tax_events: list[TaxEvent] = []
    dividend_events: list[DividendEvent] = []

    # Index dividends by uuid for TAX linkage
    dividend_by_uuid: dict[str, DividendEvent] = {}
    # Index dividends by (symbol, date) for US TAX linkage
    dividend_by_symbol_date: dict[tuple[str, str], list[DividendEvent]] = defaultdict(list)
    # Index dividend transactions by timestamp for deferred TAX matching
    dividend_txns_by_symbol: dict[str, list[tuple[int, DividendEvent]]] = defaultdict(list)
    # Collect unlinked TAX entries for deferred matching
    unlinked_tax_entries: list[Transaction] = []
    # Map TAX uuid → DividendEvent for rollback chain following
    tax_to_dividend_map: dict[str, DividendEvent] = {}

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

                # EUR/USD.E.FX — manual currency exchange at broker, not forex trading.
                # Economically identical to AUTOCONVERSION (which is SKIP per art. 24c).
                # Exante reports these as TRADE but P&L is zero (spread only).
                # Only the commission (0.01 USD/trade) is booked as cost.
                if t.asset.endswith(".FX"):
                    exec_date = execution_date_map.get(t.order_id) if t.order_id else None
                    tx_date = exec_date or _effective_date(t)
                    commission = commission_map.get(t.order_id, Decimal("0")) if t.order_id else Decimal("0")
                    if commission > 0:
                        nbp_rate = get_rate(t.currency, tx_date)
                        tax_events.append(TaxEvent(
                            date=tx_date, symbol=t.symbol_id, account_id=t.account_id,
                            event_type="fee", income_original=Decimal("0"),
                            cost_original=commission, income_pln=Decimal("0"),
                            cost_pln=to_pln(commission, nbp_rate), currency=t.currency,
                            nbp_rate=nbp_rate, details=f"FX exchange commission: {t.symbol_id}",
                        ))
                    continue

                fifo_acct = _normalize_account(t.account_id)
                # Use execution date (from COMMISSION) for NBP rate, not settlement date
                exec_date = execution_date_map.get(t.order_id) if t.order_id else None
                tx_date = exec_date or _effective_date(t)

                commission = commission_map.get(t.order_id, Decimal("0")) if t.order_id else Decimal("0")
                nbp_rate = get_rate(t.currency, tx_date)

                # Use settlement value from cash leg for accurate price
                # But NOT for CFDs where cash leg is just P&L differential
                settlement = settlement_value_map.get(t.order_id)
                notional = t.sum * t.transaction_price
                if settlement and settlement > notional * Decimal("0.5"):
                    effective_price = settlement / t.sum
                else:
                    effective_price = t.transaction_price

                if fifo.has_short_position(fifo_acct, t.symbol_id):
                    event = fifo.buy_to_close(
                        account_id=fifo_acct,
                        symbol=t.symbol_id,
                        buy_date=tx_date,
                        quantity=t.sum,
                        buy_price=effective_price,
                        currency=t.currency,
                        commission=commission,
                        nbp_rate_buy=nbp_rate,
                    )
                    tax_events.append(event)
                else:
                    fifo.buy(
                        account_id=fifo_acct,
                        symbol=t.symbol_id,
                        buy_date=tx_date,
                        quantity=t.sum,
                        price_per_unit=effective_price,
                        currency=t.currency,
                        commission=commission,
                        nbp_rate=nbp_rate,
                    )

            case TaxCategory.SELL:
                if not is_instrument_trade(t):
                    continue  # Skip cash legs

                assert t.symbol_id is not None
                assert t.transaction_price is not None

                # EUR/USD.E.FX — manual currency exchange at broker, not forex trading.
                # Economically identical to AUTOCONVERSION (which is SKIP per art. 24c).
                # Exante reports these as TRADE but P&L is zero (spread only).
                # Only the commission (0.01 USD/trade) is booked as cost.
                if t.asset.endswith(".FX"):
                    exec_date = execution_date_map.get(t.order_id) if t.order_id else None
                    tx_date = exec_date or _effective_date(t)
                    commission = commission_map.get(t.order_id, Decimal("0")) if t.order_id else Decimal("0")
                    if commission > 0:
                        nbp_rate = get_rate(t.currency, tx_date)
                        tax_events.append(TaxEvent(
                            date=tx_date, symbol=t.symbol_id, account_id=t.account_id,
                            event_type="fee", income_original=Decimal("0"),
                            cost_original=commission, income_pln=Decimal("0"),
                            cost_pln=to_pln(commission, nbp_rate), currency=t.currency,
                            nbp_rate=nbp_rate, details=f"FX exchange commission: {t.symbol_id}",
                        ))
                    continue

                fifo_acct = _normalize_account(t.account_id)
                # Use execution date (from COMMISSION) for NBP rate, not settlement date
                exec_date = execution_date_map.get(t.order_id) if t.order_id else None
                tx_date = exec_date or _effective_date(t)

                commission = commission_map.get(t.order_id, Decimal("0")) if t.order_id else Decimal("0")
                nbp_rate = get_rate(t.currency, tx_date)

                # Use settlement value from cash leg for accurate price
                # But NOT for CFDs where cash leg is just P&L differential
                settlement = settlement_value_map.get(t.order_id)
                notional = abs(t.sum) * t.transaction_price
                if settlement and settlement > notional * Decimal("0.5"):
                    effective_price = settlement / abs(t.sum)
                else:
                    effective_price = t.transaction_price

                if not fifo.has_long_position(fifo_acct, t.symbol_id):
                    fifo.sell_short(
                        account_id=fifo_acct,
                        symbol=t.symbol_id,
                        sell_date=tx_date,
                        quantity=abs(t.sum),
                        sell_price=effective_price,
                        currency=t.currency,
                        commission=commission,
                        nbp_rate=nbp_rate,
                    )
                else:
                    event = fifo.sell(
                        account_id=fifo_acct,
                        symbol=t.symbol_id,
                        sell_date=tx_date,
                        quantity=t.sum,
                        sell_price=effective_price,
                        currency=t.currency,
                        sell_commission=commission,
                        nbp_rate_sell=nbp_rate,
                    )
                    tax_events.append(event)

            case TaxCategory.DIVIDEND:
                tx_date = _timestamp_date(t)
                nbp_rate = get_rate(t.currency, tx_date)
                symbol = t.symbol_id or t.asset

                div_event = DividendEvent(
                    date=tx_date,
                    symbol=symbol,
                    account_id=t.account_id,
                    gross_amount=t.sum,
                    gross_amount_pln=to_pln(t.sum, nbp_rate),
                    tax_withheld=Decimal("0"),
                    tax_withheld_pln=Decimal("0"),
                    currency=t.currency,
                    nbp_rate=nbp_rate,
                    comment=t.comment or "",
                    country=derive_country(symbol, currency=t.currency),
                )
                dividend_events.append(div_event)
                dividend_by_uuid[t.uuid] = div_event
                dividend_by_symbol_date[(symbol, tx_date.isoformat())].append(div_event)
                dividend_txns_by_symbol[symbol].append((t.timestamp, div_event))

            case TaxCategory.TAX_WITHHELD:
                tx_date = _timestamp_date(t)
                tax_amount = abs(t.sum)
                nbp_rate = get_rate(t.currency, tx_date)
                tax_pln = to_pln(tax_amount, nbp_rate)

                if t.parent_uuid and t.parent_uuid in dividend_by_uuid:
                    # TAX linked by parentUuid → DIVIDEND
                    div = dividend_by_uuid[t.parent_uuid]
                    if t.sum > 0:
                        div.tax_withheld -= tax_amount
                        div.tax_withheld_pln -= tax_pln
                    else:
                        div.tax_withheld += tax_amount
                        div.tax_withheld_pln += tax_pln
                    tax_to_dividend_map[t.uuid] = div
                elif t.parent_uuid and t.parent_uuid in tax_to_dividend_map:
                    # Rollback: parentUuid → TAX → DIVIDEND (chain following)
                    div = tax_to_dividend_map[t.parent_uuid]
                    div.tax_withheld -= tax_amount
                    div.tax_withheld_pln -= tax_pln
                elif t.operation_type == "TAX" and not t.parent_uuid and t.symbol_id:
                    # TAX without parentUuid — match by timestamp proximity + symbol
                    matched = _match_tax_by_timestamp(
                        t, tax_amount, tax_pln, dividend_txns_by_symbol
                    )
                    if not matched:
                        unlinked_tax_entries.append(t)
                elif t.comment:
                    # US TAX: parse comment for symbol, match by timestamp proximity
                    symbol = _parse_dividend_symbol_from_comment(t.comment)
                    if symbol and symbol in dividend_txns_by_symbol:
                        sign_amount = tax_amount if t.sum < 0 else -tax_amount
                        sign_pln = tax_pln if t.sum < 0 else -tax_pln
                        matched = _match_tax_by_timestamp(
                            t, sign_amount, sign_pln,
                            dividend_txns_by_symbol,
                            max_delta_ms=120_000,
                            symbol_override=symbol,
                        )
                        if matched:
                            pass  # linked via timestamp
                        elif t.sum < 0:
                            key = (symbol, tx_date.isoformat())
                            matching_divs = dividend_by_symbol_date.get(key, [])
                            if matching_divs:
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
                                    tax_withheld_pln=to_pln(-t.sum, nbp_rate),
                                    currency=t.currency,
                                    nbp_rate=nbp_rate,
                                    comment=t.comment,
                                    country=derive_country(symbol, currency=t.currency),
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
                                    country=derive_country(symbol, currency=t.currency),
                                )
                            dividend_events.append(div_event)
                    else:
                        # Recalculation without symbol — US TAX by operation type
                        div_event = DividendEvent(
                            date=tx_date,
                            symbol="US_TAX_RECALC",
                            account_id=t.account_id,
                            gross_amount=Decimal("0"),
                            gross_amount_pln=Decimal("0"),
                            tax_withheld=-t.sum if t.sum > 0 else tax_amount,
                            tax_withheld_pln=to_pln(-t.sum, nbp_rate) if t.sum > 0 else tax_pln,
                            currency=t.currency,
                            nbp_rate=nbp_rate,
                            comment=t.comment or "",
                            country="US",
                        )
                        dividend_events.append(div_event)

            case TaxCategory.SPLIT:
                assert t.symbol_id is not None
                assert t.comment is not None
                tx_date = _effective_date(t)
                fifo_acct = _normalize_account(t.account_id)

                # Process both split transactions together
                key = (t.symbol_id, tx_date.isoformat())
                split_txns = stock_splits.get(key, [t])
                for st in split_txns:
                    processed_uuids.add(st.uuid)

                new_for_old, old_shares = FifoEngine.parse_split_ratio(t.comment)
                fifo.apply_split(
                    account_id=fifo_acct,
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
                    elif ct.asset in BARE_CURRENCIES:
                        fractional_cash = ct

                if removal and addition:
                    removal_date = _effective_date(removal)
                    fifo_acct_removal = _normalize_account(removal.account_id)
                    nbp_rate = get_rate(removal.currency, removal_date)
                    cash_amount = Decimal(str(fractional_cash.sum)) if fractional_cash else None
                    cash_nbp = get_rate(
                        fractional_cash.currency if fractional_cash else removal.currency,
                        _effective_date(fractional_cash) if fractional_cash else removal_date
                    ) if fractional_cash else nbp_rate

                    # Parse split ratio from comment (e.g., "Stock Split 1 for 3" → ratio=3)
                    FifoEngine.parse_split_ratio(removal.comment or addition.comment or "")

                    events = fifo.apply_reverse_split(
                        account_id=fifo_acct_removal,
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
                    cost_pln=to_pln(abs(t.sum), nbp_rate),
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
                    income_pln=to_pln(t.sum, nbp_rate),
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
                    cost_pln=to_pln(abs(t.sum), nbp_rate),
                    currency=t.currency,
                    nbp_rate=nbp_rate,
                    details=f"Fee: {t.sum} {t.currency} — {t.comment or ''}",
                )
                tax_events.append(event)

            case TaxCategory.SKIP:
                pass

    # Deferred TAX matching: retry unlinked entries now that all dividends exist
    for t in unlinked_tax_entries:
        tx_date = _timestamp_date(t)
        tax_amount = abs(t.sum)
        nbp_rate = get_rate(t.currency, tx_date)
        tax_pln = to_pln(tax_amount, nbp_rate)
        if not _match_tax_by_timestamp(t, tax_amount, tax_pln, dividend_txns_by_symbol):
            # Still unlinked — create standalone entry
            symbol = t.symbol_id or "TAX_UNLINKED"
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
                comment=t.comment or f"Unlinked TAX for {symbol}",
                country=derive_country(symbol, currency=t.currency),
            )
            dividend_events.append(div_event)

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
            (report.pit38_profit_loss * TAX_RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
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
            (report.dividends_income_pln * TAX_RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP),
        )
        report.dividends_tax_to_pay_pln = max(
            Decimal("0"),
            report.dividends_tax_due_pln - report.dividends_tax_paid_pln,
        )

        reports.append(report)

    return reports
