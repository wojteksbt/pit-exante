"""Tests for classifier module."""

import pytest
from decimal import Decimal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante.classifier import classify
from pit_exante.models import TaxCategory, Transaction


def _make_txn(op_type: str, amount: str = "1.0") -> Transaction:
    return Transaction(
        uuid="test", timestamp=0, value_date=None, account_id="X",
        symbol_id="TEST", operation_type=op_type, sum=Decimal(amount),
        transaction_price=None, asset="TEST", currency="USD",
        order_id=None, parent_uuid=None, comment=None, id=0,
    )


class TestClassifyOperationTypes:
    """All 14 operation types map to correct categories."""

    def test_trade_buy(self):
        assert classify(_make_txn("TRADE", "10.0")) == TaxCategory.BUY

    def test_trade_sell(self):
        assert classify(_make_txn("TRADE", "-10.0")) == TaxCategory.SELL

    def test_commission(self):
        assert classify(_make_txn("COMMISSION")) == TaxCategory.COMMISSION

    def test_dividend(self):
        assert classify(_make_txn("DIVIDEND")) == TaxCategory.DIVIDEND

    def test_tax(self):
        assert classify(_make_txn("TAX")) == TaxCategory.TAX_WITHHELD

    def test_us_tax(self):
        assert classify(_make_txn("US TAX")) == TaxCategory.TAX_WITHHELD

    def test_stock_split(self):
        assert classify(_make_txn("STOCK SPLIT")) == TaxCategory.SPLIT

    def test_corporate_action(self):
        assert classify(_make_txn("CORPORATE ACTION")) == TaxCategory.CORPORATE_ACTION

    def test_rollover_cost(self):
        assert classify(_make_txn("ROLLOVER", "-5.0")) == TaxCategory.ROLLOVER_COST

    def test_rollover_income(self):
        assert classify(_make_txn("ROLLOVER", "5.0")) == TaxCategory.ROLLOVER_INCOME

    def test_special_fee(self):
        assert classify(_make_txn("SPECIAL FEE")) == TaxCategory.FEE

    def test_excess_margin_fee(self):
        assert classify(_make_txn("EXCESS MARGIN FEE")) == TaxCategory.FEE

    def test_autoconversion_skip(self):
        assert classify(_make_txn("AUTOCONVERSION")) == TaxCategory.SKIP

    def test_funding_skip(self):
        assert classify(_make_txn("FUNDING/WITHDRAWAL")) == TaxCategory.SKIP

    def test_subaccount_transfer_skip(self):
        assert classify(_make_txn("SUBACCOUNT TRANSFER")) == TaxCategory.SKIP

    def test_balance_writeoff_skip(self):
        assert classify(_make_txn("BALANCE WRITE-OFF")) == TaxCategory.SKIP

    def test_unknown_skip(self):
        assert classify(_make_txn("SOMETHING_NEW")) == TaxCategory.SKIP


class TestClassifyEdgeCases:
    def test_trade_zero_sum_is_sell(self):
        # sum=0 is not > 0, so classified as SELL
        assert classify(_make_txn("TRADE", "0")) == TaxCategory.SELL

    def test_rollover_zero_is_income(self):
        # sum=0 is not < 0, so classified as ROLLOVER_INCOME
        assert classify(_make_txn("ROLLOVER", "0")) == TaxCategory.ROLLOVER_INCOME
