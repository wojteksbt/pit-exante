"""Tests for report module — three-section PIT-38 output mapping."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pit_exante.calculator import calculate
from pit_exante.report import generate_year_report

TRANSACTIONS_PATH = ROOT / "data" / "transactions.json"


@pytest.fixture(scope="module")
def report_2025():
    """2025 = year with VIG.US (CFD) — exercises both papiery and pochodne sections."""
    if not TRANSACTIONS_PATH.exists():
        pytest.skip("data/transactions.json not present")
    reports, _ = calculate(TRANSACTIONS_PATH)
    r = next((r for r in reports if r.year == 2025), None)
    if r is None:
        pytest.skip("No 2025 report")
    return generate_year_report(r), r


class TestSectionHeaders:
    def test_papiery_section_header_present(self, report_2025):
        text, _ = report_2025
        assert "Papiery wartościowe" in text

    def test_pochodne_section_header_present(self, report_2025):
        text, _ = report_2025
        assert "Instrumenty pochodne" in text

    def test_dividends_section_header_present(self, report_2025):
        text, _ = report_2025
        assert "Dywidendy zagraniczne" in text


class TestPIT38Labels:
    def test_papiery_maps_to_wiersz_1(self, report_2025):
        text, _ = report_2025
        assert "wiersz 1" in text
        assert "poz. 23" in text or "poz. 23-24" in text

    def test_pochodne_maps_to_wiersz_3(self, report_2025):
        text, _ = report_2025
        assert "wiersz 3" in text
        assert "poz. 27" in text or "poz. 27-28" in text

    def test_dividends_maps_to_sekcja_g(self, report_2025):
        text, _ = report_2025
        assert "sekcja G" in text or "PIT/ZG" in text


class TestSectionSums:
    def test_papiery_income_in_text(self, report_2025):
        text, r = report_2025
        # papiery_wart_income should appear formatted with grosze
        formatted = f"{r.papiery_wart_income:,.2f}"
        assert formatted in text, f"Expected papiery income {formatted} in report"

    def test_pochodne_income_in_text(self, report_2025):
        text, r = report_2025
        formatted = f"{r.pochodne_income:,.2f}"
        assert formatted in text, f"Expected pochodne income {formatted} in report"

    def test_pit38_total_in_text(self, report_2025):
        text, r = report_2025
        formatted = f"{r.pit38_income:,.2f}"
        # Total appears in summary section
        assert formatted in text


class TestVigUsInPochodneSection:
    """VIG.US (CFD) entries should appear in the pochodne block, not papiery."""

    def test_vig_us_appears_in_report(self, report_2025):
        text, _ = report_2025
        assert "VIG.US" in text

    def test_pochodne_section_contains_vig_us(self, report_2025):
        text, _ = report_2025
        # Find pochodne section start, then check VIG.US appears before next major
        # section. Sections separated by ═══ headers.
        idx_pochodne = text.find("Instrumenty pochodne")
        assert idx_pochodne != -1
        # next section starts with ═══════ followed by " Dywidendy" or similar
        idx_next = text.find("Dywidendy zagraniczne", idx_pochodne)
        assert idx_next != -1
        section = text[idx_pochodne:idx_next]
        assert "VIG.US" in section, "VIG.US must be inside pochodne section"


class TestPerRowDeductSumsToCountryAggregate:
    """Guardrail: suma kolumny "Do odliczenia" per kraj musi równać się
    aggregate `cd.tax_to_deduct_pln` (= wartość filingowa).

    Historyczny bug (kwiecień 2026): per-row zawsze cap-clampował, country
    branch używał "no cap clamping" gdy WHT ≤ UPO+0.1pp → dla USA 50.12
    vs aggregate 50.24 PLN. Filing był poprawny (używa aggregate), ale
    tabela display'owa wprowadzała w błąd.
    """

    def _parse_country_table_deducts(self, text: str, country_name: str) -> list[Decimal]:
        """Wyciągnij wartości z ostatniej kolumny (Do odliczenia) z tabeli kraju."""
        idx = text.find(f"Kraj: {country_name}")
        if idx == -1:
            return []
        # Sekcja kończy się następnym "Kraj:" albo końcem
        end = text.find("Kraj:", idx + 1)
        if end == -1:
            end = len(text)
        section = text[idx:end]
        deducts: list[Decimal] = []
        for line in section.splitlines():
            line = line.strip()
            # Wiersze danych zaczynają się od daty YYYY-MM-DD
            if len(line) >= 10 and line[:4].isdigit() and line[4] == "-":
                # Ostatnia liczba w linii to "Do odliczenia"
                tokens = line.split()
                deducts.append(Decimal(tokens[-1]))
        return deducts

    def test_usa_per_row_sum_equals_aggregate(self, report_2025):
        text, r = report_2025
        if "US" not in r.dividends_by_country:
            return  # no USA dividends in this year
        cd = r.dividends_by_country["US"]
        rows = self._parse_country_table_deducts(text, "USA (US)")
        assert len(rows) > 0, "Expected at least one USA dividend row"
        per_row_sum = sum(rows, Decimal("0"))
        assert per_row_sum == cd.tax_to_deduct_pln, (
            f"Per-row sum {per_row_sum} != country aggregate {cd.tax_to_deduct_pln}. "
            f"Display branch (cap clamp vs no-cap) musi być spójny z calculator."
        )

    def test_canada_per_row_sum_equals_aggregate(self, report_2025):
        text, r = report_2025
        if "CA" not in r.dividends_by_country:
            return
        cd = r.dividends_by_country["CA"]
        rows = self._parse_country_table_deducts(text, "Kanada (CA)")
        assert len(rows) > 0, "Expected at least one Canada dividend row"
        per_row_sum = sum(rows, Decimal("0"))
        assert per_row_sum == cd.tax_to_deduct_pln, (
            f"Per-row sum {per_row_sum} != country aggregate {cd.tax_to_deduct_pln}"
        )

    def test_all_countries_per_row_sum_equals_aggregate(self, report_2025):
        text, r = report_2025
        country_names = {"US": "USA (US)", "CA": "Kanada (CA)", "SE": "Szwecja (SE)"}
        for code, cd in r.dividends_by_country.items():
            label = country_names.get(code, f"{code} ({code})")
            rows = self._parse_country_table_deducts(text, label)
            if not rows:
                continue
            per_row_sum = sum(rows, Decimal("0"))
            assert per_row_sum == cd.tax_to_deduct_pln, (
                f"{code}: per-row {per_row_sum} != aggregate {cd.tax_to_deduct_pln}"
            )
