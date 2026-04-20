"""Unit tests for dividend country derivation (pure logic)."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante.models import DividendEvent


class TestDeriveCountryFromExchange:
    """Country derived from exchange suffix."""

    def test_nyse_is_us(self):
        from pit_exante.country import derive_country
        assert derive_country("GOOG.NASDAQ") == "US"

    def test_nasdaq_is_us(self):
        from pit_exante.country import derive_country
        assert derive_country("QCOM.NASDAQ") == "US"

    def test_arca_is_us(self):
        from pit_exante.country import derive_country
        assert derive_country("XLE.ARCA") == "US"

    def test_bats_is_us(self):
        from pit_exante.country import derive_country
        assert derive_country("ECH.BATS") == "US"

    def test_tmx_is_ca(self):
        from pit_exante.country import derive_country
        assert derive_country("LUN.TMX") == "CA"

    def test_somx_is_se(self):
        from pit_exante.country import derive_country
        assert derive_country("SINCH.SOMX") == "SE"


class TestDeriveCountryCurrencyOverride:
    """CAD dividend on US exchange → Canada."""

    def test_cad_on_nyse_is_canada(self):
        from pit_exante.country import derive_country
        assert derive_country("CCJ.NYSE", currency="CAD") == "CA"

    def test_usd_on_nyse_stays_us(self):
        from pit_exante.country import derive_country
        assert derive_country("GOOG.NASDAQ", currency="USD") == "US"

    def test_cad_on_tmx_stays_canada(self):
        from pit_exante.country import derive_country
        assert derive_country("LUN.TMX", currency="CAD") == "CA"

    def test_no_currency_defaults_to_exchange(self):
        from pit_exante.country import derive_country
        assert derive_country("CCJ.NYSE") == "US"


class TestDeriveCountryUnknown:
    """Unknown exchange returns '??' ."""

    def test_unknown_exchange(self):
        from pit_exante.country import derive_country
        assert derive_country("WEIRD.XYZZ") == "??"

    def test_no_dot_in_symbol(self):
        from pit_exante.country import derive_country
        assert derive_country("US_TAX_RECALC") == "??"


class TestDeriveCountryManualOverride:
    """Manual overrides take precedence over auto-detection."""

    def test_override_changes_country(self):
        from pit_exante.country import derive_country
        overrides = {"ASML.NASDAQ": "NL"}
        assert derive_country("ASML.NASDAQ", currency="USD", overrides=overrides) == "NL"

    def test_override_not_needed_passes_through(self):
        from pit_exante.country import derive_country
        overrides = {"ASML.NASDAQ": "NL"}
        assert derive_country("GOOG.NASDAQ", currency="USD", overrides=overrides) == "US"

    def test_override_beats_currency_heuristic(self):
        from pit_exante.country import derive_country
        overrides = {"WEIRD.NYSE": "US"}
        assert derive_country("WEIRD.NYSE", currency="CAD", overrides=overrides) == "US"


class TestDividendEventCountry:
    """DividendEvent model has country field."""

    def test_dividend_event_has_country(self):
        ev = DividendEvent(
            date=date(2024, 1, 1),
            symbol="GOOG.NASDAQ",
            account_id="TEST001",
            gross_amount=Decimal("10"),
            gross_amount_pln=Decimal("40"),
            tax_withheld=Decimal("1.5"),
            tax_withheld_pln=Decimal("6"),
            currency="USD",
            nbp_rate=Decimal("4.0"),
            comment="test",
            country="US",
        )
        assert ev.country == "US"
