"""Tests for dividend country derivation and reporting."""

import pytest
from decimal import Decimal
from datetime import date
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante.models import DividendEvent

# Will be implemented in pit_exante.country
# from pit_exante.country import derive_country, resolve_country


# ---------------------------------------------------------------------------
# Unit tests: derive_country(symbol, currency) → ISO country code
# ---------------------------------------------------------------------------

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
        # Even though CAD would suggest CA, override says US
        overrides = {"WEIRD.NYSE": "US"}
        assert derive_country("WEIRD.NYSE", currency="CAD", overrides=overrides) == "US"


# ---------------------------------------------------------------------------
# Integration: all real dividend symbols get a known country (not '??')
# ---------------------------------------------------------------------------

class TestAllDividendSymbolsHaveCountry:
    """Every symbol that pays dividends in real data gets a country."""

    EXPECTED_COUNTRIES = {
        "CCJ.NYSE": "CA",
        "COPX.ARCA": "US",
        "ECH.BATS": "US",
        "EWJV.NASDAQ": "US",
        "GDXJ.ARCA": "US",
        "GOOG.NASDAQ": "US",
        "LUN.TMX": "CA",
        "MA.NYSE": "US",
        "MOS.NYSE": "US",
        "NGE.ARCA": "US",
        "PLD.NYSE": "US",
        "QCOM.NASDAQ": "US",
        "REMX.ARCA": "US",
        "SDEM.ARCA": "US",
        "SILJ.ARCA": "US",
        "SMH.NASDAQ": "US",
        "UNH.NYSE": "US",
        "URNJ.NASDAQ": "US",
        "VIG.ARCA": "US",
        "VRT.NYSE": "US",
        "XLE.ARCA": "US",
    }

    # Currency as seen in real transactions (needed for CCJ)
    DIVIDEND_CURRENCIES = {
        "CCJ.NYSE": "CAD",
        "LUN.TMX": "CAD",
    }

    def test_all_symbols_resolved(self):
        from pit_exante.country import derive_country
        for symbol, expected in self.EXPECTED_COUNTRIES.items():
            currency = self.DIVIDEND_CURRENCIES.get(symbol)
            result = derive_country(symbol, currency=currency)
            assert result == expected, f"{symbol}: expected {expected}, got {result}"

    def test_no_unknown_countries(self):
        from pit_exante.country import derive_country
        for symbol in self.EXPECTED_COUNTRIES:
            currency = self.DIVIDEND_CURRENCIES.get(symbol)
            result = derive_country(symbol, currency=currency)
            assert result != "??", f"{symbol} has unknown country"


# ---------------------------------------------------------------------------
# Integration: DividendEvent has country field
# ---------------------------------------------------------------------------

class TestDividendEventCountry:
    """DividendEvent model has country field."""

    def test_dividend_event_has_country(self):
        ev = DividendEvent(
            date=date(2024, 1, 1),
            symbol="GOOG.NASDAQ",
            account_id="ACC001",
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


# ---------------------------------------------------------------------------
# Integration: calculator populates country on all dividend events
# ---------------------------------------------------------------------------

class TestCalculatorPopulatesCountry:
    """Calculator sets country on every DividendEvent."""

    @pytest.fixture(scope="class")
    def results(self):
        from pit_exante.calculator import calculate
        transactions_path = Path(__file__).parent.parent / "data" / "transactions.json"
        reports, _ = calculate(transactions_path)
        return reports

    def test_all_dividend_events_have_country(self, results):
        for report in results:
            for ev in report.dividend_events:
                assert hasattr(ev, "country"), f"DividendEvent for {ev.symbol} missing country"
                assert ev.country != "", f"DividendEvent for {ev.symbol} has empty country"

    def test_no_unknown_countries_in_output(self, results):
        unknowns = []
        for report in results:
            for ev in report.dividend_events:
                if ev.country == "??":
                    unknowns.append(f"{ev.date} {ev.symbol}")
        assert unknowns == [], f"Unknown countries: {unknowns}"

    def test_lun_tmx_is_canada(self, results):
        for report in results:
            for ev in report.dividend_events:
                if ev.symbol == "LUN.TMX":
                    assert ev.country == "CA"
                    return
        pytest.fail("No LUN.TMX dividend found")

    def test_ccj_nyse_is_canada(self, results):
        for report in results:
            for ev in report.dividend_events:
                if ev.symbol == "CCJ.NYSE":
                    assert ev.country == "CA"
                    return
        pytest.fail("No CCJ.NYSE dividend found")

    def test_goog_is_us(self, results):
        for report in results:
            for ev in report.dividend_events:
                if ev.symbol == "GOOG.NASDAQ":
                    assert ev.country == "US"
                    return
        pytest.fail("No GOOG.NASDAQ dividend found")


# ---------------------------------------------------------------------------
# Report: dividends grouped by country
# ---------------------------------------------------------------------------

class TestReportGroupsByCountry:
    """Report output groups dividends per country for PIT/ZG."""

    @pytest.fixture(scope="class")
    def report_text(self):
        from pit_exante.calculator import calculate
        from pit_exante.report import generate_year_report
        transactions_path = Path(__file__).parent.parent / "data" / "transactions.json"
        reports, _ = calculate(transactions_path)
        # 2024 has both US and CA dividends
        report_2024 = next(r for r in reports if r.year == 2024)
        return generate_year_report(report_2024)

    def test_us_section_present(self, report_text):
        assert "USA" in report_text or "US" in report_text

    def test_ca_section_present(self, report_text):
        assert "Kanada" in report_text or "CA" in report_text

    def test_country_sections_have_subtotals(self, report_text):
        # Each country section should have its own subtotal
        # At minimum, we should see the country name appear in a section header
        lines = report_text.split("\n")
        country_headers = [l for l in lines if "PIT-ZG" in l or "Kraj:" in l]
        assert len(country_headers) >= 1
