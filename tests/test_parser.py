"""Tests for parser module."""

import pytest
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante.parser import parse_transactions, is_instrument_trade, _derive_currency
from pit_exante.models import Transaction

TRANSACTIONS_PATH = Path(__file__).parent.parent / "data" / "transactions.json"


class TestParseTransactions:
    """Tests using real transaction data."""

    @pytest.fixture(scope="class")
    def transactions(self):
        return parse_transactions(TRANSACTIONS_PATH)

    def test_total_count(self, transactions):
        assert len(transactions) == 631

    def test_sorted_chronologically(self, transactions):
        for i in range(1, len(transactions)):
            assert transactions[i].timestamp >= transactions[i - 1].timestamp

    def test_operation_type_counts(self, transactions):
        counts = Counter(t.operation_type for t in transactions)
        assert counts["TRADE"] == 254
        assert counts["COMMISSION"] == 129
        assert counts["AUTOCONVERSION"] == 74
        assert counts["DIVIDEND"] == 71
        assert counts["US TAX"] == 42
        assert counts["TAX"] == 37
        assert counts["FUNDING/WITHDRAWAL"] == 7
        assert counts["ROLLOVER"] == 5
        assert counts["CORPORATE ACTION"] == 3
        assert counts["SPECIAL FEE"] == 3
        assert counts["SUBACCOUNT TRANSFER"] == 2
        assert counts["STOCK SPLIT"] == 2
        assert counts["BALANCE WRITE-OFF"] == 1
        assert counts["EXCESS MARGIN FEE"] == 1

    def test_all_transactions_have_required_fields(self, transactions):
        for t in transactions:
            assert t.uuid
            assert t.timestamp > 0
            assert t.account_id
            assert t.operation_type
            assert t.asset
            assert t.currency

    def test_decimal_types(self, transactions):
        for t in transactions:
            assert isinstance(t.sum, Decimal)
            if t.transaction_price is not None:
                assert isinstance(t.transaction_price, Decimal)

    def test_accounts_are_exante(self, transactions):
        accounts = {t.account_id for t in transactions}
        assert accounts <= {"ACC001.001", "ACC001.002"}

    def test_currencies_present(self, transactions):
        currencies = {t.currency for t in transactions}
        assert "USD" in currencies
        assert "CAD" in currencies
        assert "SEK" in currencies
        assert "EUR" in currencies


class TestDeriveCurrency:
    def test_nyse_is_usd(self):
        assert _derive_currency("PHYS.ARCA", "PHYS.ARCA") == "USD"

    def test_nasdaq_is_usd(self):
        assert _derive_currency("GOOG.NASDAQ", "GOOG.NASDAQ") == "USD"

    def test_arca_is_usd(self):
        assert _derive_currency("GDXJ.ARCA", "GDXJ.ARCA") == "USD"

    def test_bats_is_usd(self):
        assert _derive_currency("ECH.BATS", "ECH.BATS") == "USD"

    def test_tmx_is_cad(self):
        assert _derive_currency("LUN.TMX", "LUN.TMX") == "CAD"

    def test_somx_is_sek(self):
        assert _derive_currency("SINCH.SOMX", "SINCH.SOMX") == "SEK"

    def test_forex_quote_currency(self):
        assert _derive_currency("EUR/USD.E.FX", None) == "USD"

    def test_bare_usd(self):
        assert _derive_currency("USD", None) == "USD"

    def test_bare_eur(self):
        assert _derive_currency("EUR", None) == "EUR"

    def test_bare_pln(self):
        assert _derive_currency("PLN", None) == "PLN"

    def test_fallback_uses_symbol_id(self):
        assert _derive_currency("UNKNOWN", "PHYS.ARCA") == "USD"

    def test_fallback_default_usd(self):
        assert _derive_currency("UNKNOWN", None) == "USD"


class TestIsInstrumentTrade:
    def _make_trade(self, asset, price=Decimal("10"), op="TRADE"):
        return Transaction(
            uuid="test", timestamp=0, value_date=None, account_id="X",
            symbol_id=asset, operation_type=op, sum=Decimal("1"),
            transaction_price=price, asset=asset, currency="USD",
            order_id=None, parent_uuid=None, comment=None, id=0,
        )

    def test_instrument_trade_true(self):
        t = self._make_trade("PHYS.ARCA")
        assert is_instrument_trade(t) is True

    def test_cash_leg_false(self):
        t = self._make_trade("USD", price=None)
        assert is_instrument_trade(t) is False

    def test_bare_currency_with_price_false(self):
        t = self._make_trade("USD", price=Decimal("1"))
        assert is_instrument_trade(t) is False

    def test_non_trade_false(self):
        t = self._make_trade("PHYS.ARCA", op="COMMISSION")
        assert is_instrument_trade(t) is False
