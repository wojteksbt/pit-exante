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
