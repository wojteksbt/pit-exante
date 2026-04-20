"""Unit tests for parser module (pure logic, no data dependencies)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante.parser import _derive_currency, is_instrument_trade
from pit_exante.models import Transaction


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
        assert is_instrument_trade(self._make_trade("PHYS.ARCA")) is True

    def test_cash_leg_false(self):
        assert is_instrument_trade(self._make_trade("USD", price=None)) is False

    def test_bare_currency_with_price_false(self):
        assert is_instrument_trade(self._make_trade("USD", price=Decimal("1"))) is False

    def test_non_trade_false(self):
        assert is_instrument_trade(self._make_trade("PHYS.ARCA", op="COMMISSION")) is False
