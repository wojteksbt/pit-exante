"""Unit tests for dividend country derivation (pure logic)."""

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

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


class TestUpoRate:
    """UPO (treaty) rates per country — limit z art. 30a ust. 9."""

    def test_us_is_15_percent(self):
        from pit_exante.country import upo_rate

        assert upo_rate("US") == Decimal("0.15")

    def test_ca_is_15_percent(self):
        from pit_exante.country import upo_rate

        assert upo_rate("CA") == Decimal("0.15")

    def test_se_is_15_percent(self):
        from pit_exante.country import upo_rate

        assert upo_rate("SE") == Decimal("0.15")

    def test_unknown_country_falls_back_to_19_percent(self):
        """Brak UPO → default = stawka krajowa 19% (limit nie ogranicza)."""
        from pit_exante.country import upo_rate

        assert upo_rate("??") == Decimal("0.19")
        assert upo_rate("XX") == Decimal("0.19")


class TestIsBelowUpoThreshold:
    """Country branch: WHT effective rate ≤ UPO + tolerance → no cap clamping."""

    def _ev(self, gross, paid, currency="USD"):
        return DividendEvent(
            date=date(2025, 1, 1),
            symbol="X",
            account_id="A",
            gross_amount=Decimal(gross),
            gross_amount_pln=Decimal(gross) * 4,
            tax_withheld=Decimal(paid),
            tax_withheld_pln=Decimal(paid) * 4,
            currency=currency,
            nbp_rate=Decimal("4"),
            comment="",
            country="US",
        )

    def test_usa_at_15_percent_is_below(self):
        # Effective WHT 15.00% = UPO → no cap branch
        from pit_exante.country import is_below_upo_threshold

        events = [self._ev("100.00", "15.00")]
        assert is_below_upo_threshold("US", events) is True

    def test_usa_at_14_64_percent_is_below(self):
        # Real-world 2025 USA: 13.53/92.42 ≈ 14.64% → no cap branch
        from pit_exante.country import is_below_upo_threshold

        events = [self._ev("92.42", "13.53")]
        assert is_below_upo_threshold("US", events) is True

    def test_usa_at_15_05_percent_within_tolerance(self):
        # Within 0.1pp tolerance → still no cap branch
        from pit_exante.country import is_below_upo_threshold

        events = [self._ev("100.00", "15.05")]
        assert is_below_upo_threshold("US", events) is True

    def test_usa_at_15_2_percent_exceeds_tolerance(self):
        # 15.2% > 15% + 0.1pp → cap clamping branch
        from pit_exante.country import is_below_upo_threshold

        events = [self._ev("100.00", "15.20")]
        assert is_below_upo_threshold("US", events) is False

    def test_canada_at_25_percent_is_above(self):
        # Real-world CA WHT 25% > UPO 15% → cap clamping
        from pit_exante.country import is_below_upo_threshold

        events = [self._ev("100.00", "25.00", currency="CAD")]
        assert is_below_upo_threshold("CA", events) is False

    def test_zero_gross_returns_false(self):
        # Edge case: no income → not "below threshold" (avoids div-by-zero)
        from pit_exante.country import is_below_upo_threshold

        events = [self._ev("0.00", "0.00")]
        assert is_below_upo_threshold("US", events) is False

    def test_aggregates_across_multiple_events(self):
        # Mix of rows averaging to below-UPO → no cap branch
        from pit_exante.country import is_below_upo_threshold

        events = [
            self._ev("100.00", "16.00"),  # 16% — above
            self._ev("100.00", "13.00"),  # 13% — below
        ]
        # Average 14.5% < 15% → no cap branch
        assert is_below_upo_threshold("US", events) is True
