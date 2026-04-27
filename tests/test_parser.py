"""Unit tests for parser module (pure logic, no data dependencies)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante.parser import (
    _build_orderid_currency_map,
    _derive_currency,
    is_instrument_trade,
)
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

    def test_cash_leg_overrides_suffix_for_dual_listed(self):
        # U/U.TMX is USD-class on TSX (Sprott Physical Uranium Trust USD class).
        # Suffix table has no .TMX entry; cash-leg map is the only source.
        ccy_map = {"order-uu": "USD"}
        assert _derive_currency("U/U.TMX", "U/U.TMX", ccy_map, "order-uu") == "USD"

    def test_cash_leg_lookup_returns_cad_for_lun_tmx(self):
        # LUN.TMX trades settle in CAD per cash leg.
        ccy_map = {"order-lun": "CAD"}
        assert _derive_currency("LUN.TMX", "LUN.TMX", ccy_map, "order-lun") == "CAD"

    def test_cash_leg_miss_falls_back_to_suffix(self):
        # Unknown orderId → fall back to suffix table; .NASDAQ → USD.
        assert _derive_currency("GOOG.NASDAQ", "GOOG.NASDAQ", {"other": "EUR"}, "missing") == "USD"

    def test_tmx_without_cash_leg_falls_through_to_default(self):
        # .TMX no longer in suffix table; without cash-leg map hit, default USD.
        # (This is acceptable because every TRADE in real data has a cash leg —
        # this case is theoretical.)
        assert _derive_currency("U/U.TMX", "U/U.TMX") == "USD"


class TestBuildOrderidCurrencyMap:
    def test_trade_cash_leg_recorded(self):
        raw = [
            {"operationType": "TRADE", "orderId": "o1", "asset": "U/U.TMX", "symbolId": "U/U.TMX"},
            {"operationType": "TRADE", "orderId": "o1", "asset": "USD", "symbolId": "U/U.TMX"},
        ]
        assert _build_orderid_currency_map(raw) == {"o1": "USD"}

    def test_autoconversion_does_not_pollute_map(self):
        # AUTOCONVERSION shares orderId with TRADE but is broker-internal forex,
        # not the settlement currency. Must be filtered out.
        raw = [
            {"operationType": "TRADE", "orderId": "o1", "asset": "LUN.TMX", "symbolId": "LUN.TMX"},
            {"operationType": "TRADE", "orderId": "o1", "asset": "CAD", "symbolId": "LUN.TMX"},
            {"operationType": "AUTOCONVERSION", "orderId": "o1", "asset": "USD", "symbolId": None},
            {"operationType": "AUTOCONVERSION", "orderId": "o1", "asset": "CAD", "symbolId": None},
        ]
        assert _build_orderid_currency_map(raw) == {"o1": "CAD"}

    def test_commission_does_not_pollute_map(self):
        # COMMISSION shares orderId — should not contribute to map (the cash
        # leg of TRADE is the truth; commission is incidental).
        raw = [
            {"operationType": "TRADE", "orderId": "o1", "asset": "GOOG.NASDAQ", "symbolId": "GOOG.NASDAQ"},
            {"operationType": "TRADE", "orderId": "o1", "asset": "USD", "symbolId": "GOOG.NASDAQ"},
            {"operationType": "COMMISSION", "orderId": "o1", "asset": "USD", "symbolId": "GOOG.NASDAQ"},
        ]
        assert _build_orderid_currency_map(raw) == {"o1": "USD"}

    def test_skips_rows_without_orderid(self):
        raw = [
            {"operationType": "TRADE", "orderId": None, "asset": "USD", "symbolId": None},
            {"operationType": "DIVIDEND", "orderId": None, "asset": "USD", "symbolId": "GOOG.NASDAQ"},
        ]
        assert _build_orderid_currency_map(raw) == {}

    def test_skips_non_bare_assets(self):
        # The instrument-leg row (asset == symbolId) should not contribute.
        raw = [
            {"operationType": "TRADE", "orderId": "o1", "asset": "GOOG.NASDAQ", "symbolId": "GOOG.NASDAQ"},
        ]
        assert _build_orderid_currency_map(raw) == {}


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
