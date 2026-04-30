"""Tests for report module — three-section PIT-38 output mapping."""

from __future__ import annotations

import csv
import io
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pit_exante.calculator import calculate
from pit_exante.report import generate_csv, generate_year_report

TRANSACTIONS_PATH = ROOT / "data" / "transactions.json"


# Polish locale helpers — mirror src/pit_exante/report.py::_fmt convention.
# _fmt produces "70 388,62" (NBSP thousand sep, comma decimal sep).
def _pl_fmt(value) -> str:
    """Format Decimal jak _fmt w report.py: NBSP tysięczne + przecinek dziesiętny."""
    s = f"{value:,.2f}"
    return s.replace(",", "\xa0").replace(".", ",")


def _pl_to_decimal(s: str) -> Decimal:
    """Odwrotność _pl_fmt: '70 388,62' / '70\\xa0388,62' → Decimal('70388.62')."""
    return Decimal(s.replace("\xa0", "").replace(" ", "").replace(",", "."))


# Regex group capturing PL-formatted amount (digits + NBSP/space thousand sep + comma decimal).
_PL_AMOUNT_RE = r"[\d  ]+,\d{2}"


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


@pytest.fixture(scope="module")
def all_year_reports():
    """All per-year reports + their rendered text — for cross-year invariants."""
    if not TRANSACTIONS_PATH.exists():
        pytest.skip("data/transactions.json not present")
    reports, _ = calculate(TRANSACTIONS_PATH)
    return [(r.year, generate_year_report(r), r) for r in reports]


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
    """PIT-38 sekcja C — year-aware mapowanie:

    Wariant 17 (rok ≤ 2024): zagr. broker bez PIT-8C → wiersz 2 'Inne przychody' (poz. 22-23).
    Wariant 18 (rok ≥ 2025): broker (Exante = Cypr) wystawia PIT-8C → wiersz 1 (poz. 20-21,
    wstępnie wypełnione przez KAS).
    """

    def test_papiery_header_year_aware_w18(self, report_2025):
        # 2025 = wariant 18 → wiersz 1 + poz. 20-21 (pre-fill)
        text, _ = report_2025
        idx = text.find("Papiery wartościowe")
        assert idx != -1
        section_header = text[idx : idx + 200]
        assert (
            "wiersz 1" in section_header
        ), "Wariant 18: papiery → PIT-8C poz. 35/36 → PIT-38 wiersz 1 (poz. 20-21)"
        assert "poz. 20-21" in section_header

    def test_papiery_header_year_aware_w17(self, all_year_reports):
        # Wariant 17 (rok ≤ 2024): papiery → wiersz 2 (poz. 22-23)
        for year, text, _r in all_year_reports:
            if year >= 2025:
                continue
            idx = text.find("Papiery wartościowe")
            assert idx != -1
            section_header = text[idx : idx + 200]
            assert "wiersz 2" in section_header, f"Year {year}: w17 → wiersz 2"
            assert "poz. 22-23" in section_header
            return
        pytest.skip("No w17 year")

    def test_pochodne_header_year_aware_w18(self, report_2025):
        text, _ = report_2025
        idx = text.find("Instrumenty pochodne")
        assert idx != -1
        section_header = text[idx : idx + 200]
        # Wariant 18: CFD ujęte w PIT-8C poz. 35/36 razem z papierami
        assert "PIT-8C poz. 35/36" in section_header
        assert "broker łączy CFD i akcje" in section_header

    def test_pochodne_header_year_aware_w17(self, all_year_reports):
        for year, text, _r in all_year_reports:
            if year >= 2025:
                continue
            idx = text.find("Instrumenty pochodne")
            assert idx != -1
            section_header = text[idx : idx + 200]
            assert "wiersz 2" in section_header, f"Year {year}: w17 → wiersz 2"
            assert "poz. 22-23" in section_header
            return
        pytest.skip("No w17 year")

    def test_no_legacy_pit8c_position_references_in_section_headers(self, report_2025):
        """Guardrail: stare etykiety 'PIT-8C poz. 23-24' / 'poz. 27-28' były błędne.
        Dla zagranicznego brokera nie mamy PIT-8C w ogóle — referencje do jego
        pozycji wprowadzają w błąd."""
        text, _ = report_2025
        # Nagłówki sekcji obu (papiery + pochodne) — pierwsze 250 znaków po headerze
        for header in ("Papiery wartościowe", "Instrumenty pochodne"):
            idx = text.find(header)
            if idx == -1:
                continue
            chunk = text[idx : idx + 250]
            assert "PIT-8C poz. 23-24" not in chunk
            assert "PIT-8C poz. 27-28" not in chunk

    def test_dividends_maps_to_sekcja_g(self, report_2025):
        text, _ = report_2025
        assert "sekcja G" in text or "PIT/ZG" in text


class TestSectionSums:
    def test_papiery_income_in_text(self, report_2025):
        text, r = report_2025
        # papiery_wart_income should appear formatted with grosze (PL: NBSP + comma)
        formatted = _pl_fmt(r.papiery_wart_income)
        assert formatted in text, f"Expected papiery income {formatted} in report"

    def test_pochodne_income_in_text(self, report_2025):
        text, r = report_2025
        formatted = _pl_fmt(r.pochodne_income)
        assert formatted in text, f"Expected pochodne income {formatted} in report"

    def test_pit38_total_in_text(self, report_2025):
        text, r = report_2025
        formatted = _pl_fmt(r.pit38_income)
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
                deducts.append(_pl_to_decimal(tokens[-1]))
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
        assert (
            per_row_sum == cd.tax_to_deduct_pln
        ), f"Per-row sum {per_row_sum} != country aggregate {cd.tax_to_deduct_pln}"

    def test_all_countries_per_row_sum_equals_aggregate(self, report_2025):
        text, r = report_2025
        country_names = {"US": "USA (US)", "CA": "Kanada (CA)", "SE": "Szwecja (SE)"}
        for code, cd in r.dividends_by_country.items():
            label = country_names.get(code, f"{code} ({code})")
            rows = self._parse_country_table_deducts(text, label)
            if not rows:
                continue
            per_row_sum = sum(rows, Decimal("0"))
            assert (
                per_row_sum == cd.tax_to_deduct_pln
            ), f"{code}: per-row {per_row_sum} != aggregate {cd.tax_to_deduct_pln}"


class TestPerRowDeductSumsAllYears:
    """G10: rozszerzenie guardrail invariantu na WSZYSTKIE lata, nie tylko 2025.

    Bug fixed in commit f27438d (USA per-row vs aggregate) byłby też w danych
    2024 USA gdyby ktoś wcześniej go wprowadził. Test pokrywa lata 2020-2026.
    """

    def _parse_country_table_deducts(self, text: str, country_name: str) -> list[Decimal]:
        idx = text.find(f"Kraj: {country_name}")
        if idx == -1:
            return []
        end = text.find("Kraj:", idx + 1)
        if end == -1:
            end = len(text)
        section = text[idx:end]
        deducts: list[Decimal] = []
        for line in section.splitlines():
            line = line.strip()
            if len(line) >= 10 and line[:4].isdigit() and line[4] == "-":
                tokens = line.split()
                deducts.append(_pl_to_decimal(tokens[-1]))
        return deducts

    def test_per_row_sum_equals_aggregate_for_every_year(self, all_year_reports):
        country_names = {"US": "USA (US)", "CA": "Kanada (CA)", "SE": "Szwecja (SE)"}
        for year, text, r in all_year_reports:
            for code, cd in r.dividends_by_country.items():
                label = country_names.get(code, f"{code} ({code})")
                rows = self._parse_country_table_deducts(text, label)
                if not rows:
                    continue
                per_row_sum = sum(rows, Decimal("0"))
                assert (
                    per_row_sum == cd.tax_to_deduct_pln
                ), f"Year {year} country {code}: per-row {per_row_sum} != aggregate {cd.tax_to_deduct_pln}"


class TestPit38DividendPositions:
    """G5: numeracja pól PIT-38 sekcja G — przesunięcie o 2 od 2025 r."""

    def test_2020_uses_legacy_numbering(self):
        from pit_exante.report import _pit38_dividend_positions

        assert _pit38_dividend_positions(2020) == (45, 46, 47)

    def test_2024_uses_legacy_numbering(self):
        from pit_exante.report import _pit38_dividend_positions

        assert _pit38_dividend_positions(2024) == (45, 46, 47)

    def test_2025_uses_new_numbering(self):
        from pit_exante.report import _pit38_dividend_positions

        assert _pit38_dividend_positions(2025) == (47, 48, 49)

    def test_2026_uses_new_numbering(self):
        from pit_exante.report import _pit38_dividend_positions

        assert _pit38_dividend_positions(2026) == (47, 48, 49)


class TestPit38SectionCPositions:
    """Numeracja sekcji C wg wariantu formularza."""

    def test_w17_2024(self):
        from pit_exante.report import _pit38_section_c_positions

        positions = _pit38_section_c_positions(2024)
        assert positions == {
            "wiersz_2_inc": 22,
            "wiersz_2_cost": 23,
            "razem_inc": 24,
            "razem_cost": 25,
            "razem_dochod": 26,
            "razem_strata": 27,
        }

    def test_w17_2020(self):
        from pit_exante.report import _pit38_section_c_positions

        positions = _pit38_section_c_positions(2020)
        assert positions["wiersz_2_inc"] == 22
        assert positions["razem_inc"] == 24
        assert "wiersz_1_inc" not in positions
        assert "wiersz_3_inc" not in positions

    def test_w18_2025(self):
        from pit_exante.report import _pit38_section_c_positions

        positions = _pit38_section_c_positions(2025)
        assert positions == {
            "wiersz_1_inc": 20,
            "wiersz_1_cost": 21,
            "wiersz_2_inc": 22,
            "wiersz_2_cost": 23,
            "wiersz_3_inc": 24,
            "wiersz_3_cost": 25,
            "razem_inc": 26,
            "razem_cost": 27,
            "razem_dochod": 28,
            "razem_strata": 29,
        }

    def test_w18_2026(self):
        from pit_exante.report import _pit38_section_c_positions

        positions = _pit38_section_c_positions(2026)
        assert positions["wiersz_1_inc"] == 20
        assert positions["razem_strata"] == 29


class TestPit38SectionDPositions:
    """Step 4: numeracja sekcji D — w17 (28-33) vs w18 (30-35)."""

    def test_w17_2024(self):
        from pit_exante.report import _pit38_section_d_positions

        assert _pit38_section_d_positions(2024) == {
            "straty_lat": 28,
            "podstawa": 29,
            "stawka": 30,
            "podatek_dochodu": 31,
            "podatek_za_granica": 32,
            "podatek_nalezny": 33,
        }

    def test_w17_2020(self):
        from pit_exante.report import _pit38_section_d_positions

        positions = _pit38_section_d_positions(2020)
        assert positions["straty_lat"] == 28
        assert positions["podatek_nalezny"] == 33

    def test_w18_2025(self):
        from pit_exante.report import _pit38_section_d_positions

        assert _pit38_section_d_positions(2025) == {
            "straty_lat": 30,
            "podstawa": 31,
            "stawka": 32,
            "podatek_dochodu": 33,
            "podatek_za_granica": 34,
            "podatek_nalezny": 35,
        }

    def test_w18_2026(self):
        from pit_exante.report import _pit38_section_d_positions

        positions = _pit38_section_d_positions(2026)
        assert positions["straty_lat"] == 30
        assert positions["podatek_nalezny"] == 35


class TestPit38PitZGCountPosition:
    """Step 4: poz. PIT/ZG count w sekcji L — 69 (w17) vs 72 (w18)."""

    def test_w17_2020(self):
        from pit_exante.report import _pit38_pitzg_count_position

        assert _pit38_pitzg_count_position(2020) == 69

    def test_w17_2024(self):
        from pit_exante.report import _pit38_pitzg_count_position

        assert _pit38_pitzg_count_position(2024) == 69

    def test_w18_2025(self):
        from pit_exante.report import _pit38_pitzg_count_position

        assert _pit38_pitzg_count_position(2025) == 72

    def test_w18_2026(self):
        from pit_exante.report import _pit38_pitzg_count_position

        assert _pit38_pitzg_count_position(2026) == 72


class TestPit38FillingInstructions:
    """Konkretne pole-po-polu instrukcje wypełnienia PIT-38."""

    def test_section_header_present(self, report_2025):
        text, _ = report_2025
        assert "INSTRUKCJA WYPEŁNIENIA PIT-38" in text

    def test_specifies_section_a(self, report_2025):
        text, _ = report_2025
        idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
        instr = text[idx:]
        assert "SEKCJA A" in instr
        assert "poz. 6" in instr

    def test_specifies_papiery_positions_22_23(self, report_2025):
        # 2025 (w18 compare-with-prefill): wiersz 2 (poz. 22-23) is referenced
        # but instructed to remain empty (broker covered CFDs in PIT-8C wiersz 3).
        text, r = report_2025
        if not r.papiery_wart_events:
            pytest.skip("No papiery wart events in 2025")
        idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
        instr = text[idx:]
        assert "poz. 22-23" in instr
        assert "Zostaw puste" in instr

    def test_specifies_section_l_pitzg_count(self, report_2025):
        from pit_exante.report import _pit38_pitzg_count_position

        text, r = report_2025
        idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
        instr = text[idx:]
        assert "SEKCJA L" in instr
        expected_pos = _pit38_pitzg_count_position(r.year)
        assert f"poz. {expected_pos}" in instr  # 69 (w17) lub 72 (w18)
        assert "PIT/ZG" in instr

    def test_specifies_pit38_total_to_pay_position(self, report_2025):
        """PODATEK DO ZAPŁATY total: poz. 49 (2024) lub poz. 51 (2025+ shift)."""
        from pit_exante.report import _pit38_total_to_pay_position

        text, r = report_2025
        idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
        instr = text[idx:]
        expected_pos = _pit38_total_to_pay_position(r.year)
        assert f"POZYCJA {expected_pos} — PODATEK DO ZAPŁATY" in instr

    def test_2024_uses_legacy_dividend_position_47(self, all_year_reports):
        """Rok 2024 → poz. 47 dla "Różnica do zapłaty" (legacy)."""
        for year, text, r in all_year_reports:
            if year != 2024 or r.dividends_income_pln <= 0:
                continue
            idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
            instr = text[idx:]
            assert "poz. 47" in instr, "2024 should reference poz. 47 (dywidendy)"
            return
        pytest.skip("No 2024 report with dywidendy")


class TestPit38Wariant18CompareWithPrefill:
    """Wariant 18 (rok ≥ 2025) — tryb compare-with-prefill.

    US auto-pre-fillsuje poz. 20-21 z PIT-8C broker'a; tool drukuje swoje
    obliczenie papiery wartościowe jako referencję, instruuje zostawić
    wiersz 2 (poz. 22-23) puste. Sekcja D na poz. 31-35, L na poz. 72.
    """

    def test_wiersz_1_compare_block_present(self, all_year_reports):
        for year, text, r in all_year_reports:
            if year < 2025 or not (r.papiery_wart_events or r.pochodne_events):
                continue
            idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
            instr = text[idx:]
            assert "Wiersz 1 'Z PIT-8C cz. D' (poz. 20-21)" in instr
            assert "Wyliczenie kalkulatora (papiery wartościowe):" in instr
            assert "KAS wstępnie wypełnia" in instr
            return
        pytest.skip("No w18 year with sekcja C")

    def test_wiersz_2_zostaw_puste(self, all_year_reports):
        for year, text, r in all_year_reports:
            if year < 2025 or not (r.papiery_wart_events or r.pochodne_events):
                continue
            idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
            instr = text[idx:]
            assert "Wiersz 2 'Inne przychody' (poz. 22-23)" in instr
            assert "Zostaw puste" in instr
            return
        pytest.skip("No w18 year with sekcja C")

    def test_wiersz_4_razem_pre_fill(self, all_year_reports):
        for year, text, r in all_year_reports:
            if year < 2025 or not (r.papiery_wart_events or r.pochodne_events):
                continue
            idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
            instr = text[idx:]
            assert "Wiersz 4 'Razem' (poz. 26-29)" in instr
            assert "Pole wstępnie wypełnione = wiersz 1" in instr
            return
        pytest.skip("No w18 year with sekcja C")

    def test_section_d_at_poz_31_35(self, all_year_reports):
        """Wariant 18: sekcja D shifted +2 → 31 (Podstawa) … 35 (Należny)."""
        for year, text, r in all_year_reports:
            if year < 2025 or not (r.papiery_wart_events or r.pochodne_events):
                continue
            idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
            instr = text[idx:]
            assert "poz. 31 (Podstawa" in instr
            assert "poz. 33 (Podatek 19%)" in instr or "poz. 32-35:" in instr
            assert "poz. 29 (Podstawa" not in instr
            assert "poz. 30-33:" not in instr
            return
        pytest.skip("No w18 year with sekcja C")

    def test_no_pit8c_warn_banner_anywhere(self, all_year_reports):
        """After wycofanie ścieżki B: WARN o braku configu PIT-8C zlikwidowany."""
        for _year, text, _r in all_year_reports:
            assert "UWAGA wariant 18: brak config" not in text
            assert "config/pit8c/" not in text

    def test_no_diagnostyka_section(self, all_year_reports):
        """DIAGNOSTYKA tabela (path B feature) usunięta razem z importem PIT-8C."""
        for _year, text, _r in all_year_reports:
            assert "DIAGNOSTYKA — tool" not in text


class TestPitZgAttachments:
    """Per-kraj rekomendacje załączników PIT/ZG (lista + uzasadnienia)."""

    def test_section_header_present(self, report_2025):
        text, _ = report_2025
        assert "ZAŁĄCZNIKI PIT/ZG" in text

    def test_country_with_papiery_marked_required(self, all_year_reports):
        """Kraj z faktycznymi sprzedażami papierów (US exchanges) → WYMAGANY."""
        from pit_exante.country import derive_country

        for year, text, r in all_year_reports:
            us_sales = [
                e
                for e in r.papiery_wart_events
                if e.income_pln > 0 and derive_country(e.symbol, e.currency) == "US"
            ]
            if not us_sales:
                continue
            idx = text.find("ZAŁĄCZNIKI PIT/ZG")
            section = text[idx:]
            assert "STANY ZJEDNOCZONE AMERYKI" in section, f"Year {year}: USA powinno być w PIT/ZG"
            assert "WYMAGANY" in section, f"Year {year}: oznaczenie WYMAGANY brak"
            return
        pytest.skip("No year with US papiery sales")

    def test_dividend_only_country_marked_not_required(self, all_year_reports):
        """Kraj wyłącznie z dywidendami (bez papierów) → NIE WYMAGANY."""
        from pit_exante.country import derive_country

        for year, text, r in all_year_reports:
            if "CA" not in r.dividends_by_country:
                continue
            has_ca_papiery = any(
                e.income_pln > 0 and derive_country(e.symbol, e.currency) == "CA"
                for e in r.papiery_wart_events
            )
            if has_ca_papiery:
                continue
            idx = text.find("ZAŁĄCZNIKI PIT/ZG")
            section = text[idx:]
            ca_idx = section.find("KANADA")
            assert ca_idx != -1, f"Year {year}: KANADA missing from PIT/ZG section"
            window = section[ca_idx : ca_idx + 200]
            assert (
                "NIE WYMAGANY" in window
            ), f"Year {year}: KANADA z samymi dywidendami powinna być NIE WYMAGANA"
            return
        pytest.skip("No year with Canada dividends but no Canada papiery")


class TestPapieryCountryBreakdown:
    """Helper _papiery_country_breakdown — agregacja per-kraj sprzedaży papierów."""

    def test_breakdown_excludes_fees(self, report_2025):
        from pit_exante.report import _papiery_country_breakdown

        _, r = report_2025
        breakdown = _papiery_country_breakdown(r)
        sale_income_total = sum(
            (e.income_pln for e in r.papiery_wart_events if e.income_pln > 0),
            Decimal("0"),
        )
        breakdown_income_total = sum(
            (income for income, _cost in breakdown.values()),
            Decimal("0"),
        )
        assert (
            breakdown_income_total == sale_income_total
        ), "Σ przychodów per-kraj musi == Σ przychodów ze sprzedaży (bez fees)"

    def test_us_present_when_us_exchanges_traded(self, report_2025):
        from pit_exante.report import _papiery_country_breakdown

        _, r = report_2025
        breakdown = _papiery_country_breakdown(r)
        us_exchanges = (".ARCA", ".NASDAQ", ".NYSE", ".BATS")
        if any(any(ex in e.symbol for ex in us_exchanges) for e in r.papiery_wart_events if e.income_pln > 0):
            assert "US" in breakdown


class TestPit38InstructionsNumericalCorrectness:
    """Wartości pokazywane w 'INSTRUKCJA WYPEŁNIENIA PIT-38' MUSZĄ matchować
    underlying YearReport. To ta sekcja mówi userowi 'wpisz X w komórkę Y' —
    jeśli X jest złe, user wpisze złą liczbę do faktycznego PIT-38.

    Presence tests (TestPit38FillingInstructions) sprawdzają tylko że labele są.
    Te testy parsują WARTOŚCI obok pozycji i porównują z calculator output.
    """

    @staticmethod
    def _instr_section(text: str) -> str:
        idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
        assert idx != -1
        end = text.find("ZAŁĄCZNIKI PIT/ZG", idx)
        return text[idx:end] if end != -1 else text[idx:]

    @staticmethod
    def _extract_pln_after_position(section: str, position_num: int) -> Decimal | None:
        """Wyciągnij wartość PLN z linii 'poz. N (...): X PLN'.

        Pierwsze wystąpienie — dla pozycji występujących wielokrotnie (jak poz. 49
        w 2025+: raz w sekcji G dla dywidend, raz w 'POZYCJA 49 — PODATEK DO ZAPŁATY')
        zwraca tę z dywidend. Tylko ta linia ma 'PLN' bezpośrednio po wartości; linia
        'POZYCJA … — PODATEK DO ZAPŁATY' ma 'zł' i ' PLN' jest dopiero w następnej linii bez 'poz.'.
        """
        import re

        pattern = rf"poz\.\s*{position_num}\b[^\n]*?({_PL_AMOUNT_RE})\s*PLN"
        match = re.search(pattern, section)
        if not match:
            return None
        return _pl_to_decimal(match.group(1))

    def test_poz_22_w17_matches_combined_inne_income(self, all_year_reports):
        # Wariant 17 (rok ≤ 2024): poz. 22 = papiery + pochodne combined.
        # Wariant 18 nie wpisuje wartości do poz. 22 (compare-with-prefill).
        verified = 0
        for year, text, r in all_year_reports:
            if year >= 2025:
                continue
            if not (r.papiery_wart_events or r.pochodne_events):
                continue
            section = self._instr_section(text)
            value = self._extract_pln_after_position(section, 22)
            assert value is not None, f"Year {year}: poz. 22 not found in INSTRUKCJA"
            expected = (r.papiery_wart_income + r.pochodne_income).quantize(Decimal("0.01"))
            assert value == expected, f"Year {year}: poz. 22 shown {value} != combined {expected}"
            verified += 1
        if verified == 0:
            pytest.skip("No w17 year with sekcja C")

    def test_poz_23_w17_matches_combined_inne_cost(self, all_year_reports):
        verified = 0
        for year, text, r in all_year_reports:
            if year >= 2025:
                continue
            if not (r.papiery_wart_events or r.pochodne_events):
                continue
            section = self._instr_section(text)
            value = self._extract_pln_after_position(section, 23)
            assert value is not None, f"Year {year}: poz. 23 not found in INSTRUKCJA"
            expected = (r.papiery_wart_cost + r.pochodne_cost).quantize(Decimal("0.01"))
            assert value == expected, f"Year {year}: poz. 23 shown {value} != combined {expected}"
            verified += 1
        if verified == 0:
            pytest.skip("No w17 year with sekcja C")

    def test_strata_position_matches_abs_when_loss_w17(self, all_year_reports):
        """w17: strata netto → poz. 27 = wartość bezwzględna. w18 nie pokazuje
        wartości w razem (Pre-fill = wiersz 1)."""
        from pit_exante.report import _pit38_section_c_positions

        for year, text, r in all_year_reports:
            if year >= 2025:
                continue
            if not (r.papiery_wart_events or r.pochodne_events):
                continue
            inne_inc = r.papiery_wart_income + r.pochodne_income
            inne_cost = r.papiery_wart_cost + r.pochodne_cost
            net = inne_inc - inne_cost
            if net >= 0:
                continue
            section = self._instr_section(text)
            pos_c = _pit38_section_c_positions(year)
            value = self._extract_pln_after_position(section, pos_c["razem_strata"])
            assert value is not None, f"Year {year}: poz. {pos_c['razem_strata']} not found"
            assert value == (-net).quantize(
                Decimal("0.01")
            ), f"Year {year}: poz. {pos_c['razem_strata']} shown {value} != |strata| {-net}"

    def test_dividend_to_pay_position_matches_report(self, all_year_reports):
        """poz. 47 (2024) lub 49 (2025+) wartość 'Różnica do zapłaty' ==
        report.dividends_tax_to_pay_pln."""
        from pit_exante.report import _pit38_dividend_positions

        verified = 0
        for year, text, r in all_year_reports:
            if r.dividends_income_pln <= 0:
                continue
            _, _, pos_to_pay = _pit38_dividend_positions(year)
            section = self._instr_section(text)
            value = self._extract_pln_after_position(section, pos_to_pay)
            assert value is not None, f"Year {year}: poz. {pos_to_pay} not found"
            expected = r.dividends_tax_to_pay_pln.quantize(Decimal("0.01"))
            assert (
                value == expected
            ), f"Year {year}: poz. {pos_to_pay} shown {value} != dividends_to_pay {expected}"
            verified += 1
        if verified == 0:
            pytest.skip("No years with dividends")

    def test_podatek_do_zaplaty_total_sums_correctly(self, all_year_reports):
        """'POZYCJA N — PODATEK DO ZAPŁATY' — końcowa kwota = poz. nalezny + dyw."""
        import re
        from decimal import ROUND_HALF_UP

        from pit_exante.models import TAX_RATE
        from pit_exante.report import _pit38_total_to_pay_position

        verified = 0
        for year, text, r in all_year_reports:
            section = self._instr_section(text)
            pos_total = _pit38_total_to_pay_position(year)
            idx = section.find(f"POZYCJA {pos_total} — PODATEK DO ZAPŁATY")
            if idx == -1:
                continue
            chunk = section[idx : idx + 400]
            match = re.search(rf"=\s+({_PL_AMOUNT_RE})\s+PLN", chunk)
            assert match is not None, f"Year {year}: total '= X PLN' line missing"
            shown = _pl_to_decimal(match.group(1))
            # Tool's net = papiery + pochodne (pochodne_net since 2025)
            net = (r.papiery_wart_income + r.pochodne_income) - (r.papiery_wart_cost + r.pochodne_cost)
            podatek_pap_pre = max(Decimal("0"), net * TAX_RATE)
            podatek_pap = podatek_pap_pre.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            expected = (podatek_pap + r.dividends_tax_to_pay_pln).quantize(Decimal("0.01"))
            assert shown == expected, f"Year {year}: PODATEK DO ZAPŁATY shown {shown} != computed {expected}"
            verified += 1
        assert verified > 0, "Expected at least one year with PODATEK DO ZAPŁATY summary"

    def test_section_l_pitzg_count_matches_breakdown(self, all_year_reports):
        """SEKCJA L (Liczba załączników PIT/ZG) == liczba krajów z papierami.
        Wariant 17: poz. 69. Wariant 18: poz. 72. Pozycja przez helper.
        """
        import re

        from pit_exante.report import _papiery_country_breakdown, _pit38_pitzg_count_position

        for year, text, r in all_year_reports:
            section = self._instr_section(text)
            pos_pitzg = _pit38_pitzg_count_position(year)
            match = re.search(rf"poz\.\s*{pos_pitzg}[^\n]*?(\d+)\s*$", section, re.MULTILINE)
            assert match is not None, f"Year {year}: poz. {pos_pitzg} line not found"
            shown_count = int(match.group(1))
            expected_count = len(_papiery_country_breakdown(r))
            assert (
                shown_count == expected_count
            ), f"Year {year}: poz. {pos_pitzg} PIT/ZG count {shown_count} != breakdown {expected_count}"


class TestPitZgNumericalCorrectness:
    """Wartości w 'ZAŁĄCZNIKI PIT/ZG' MUSZĄ matchować _papiery_country_breakdown."""

    @staticmethod
    def _pitzg_section(text: str) -> str:
        idx = text.find("ZAŁĄCZNIKI PIT/ZG")
        assert idx != -1
        return text[idx:]

    @staticmethod
    def _country_block(section: str, country_full_name: str) -> str | None:
        """Wytnij blok jednego kraju (od jego nagłówka do następnego ▌ lub końca)."""
        idx = section.find(country_full_name)
        if idx == -1:
            return None
        end = section.find("▌", idx + 1)
        return section[idx:end] if end != -1 else section[idx:]

    def test_pitzg_poz_29_matches_country_breakdown(self, all_year_reports):
        """Każdy WYMAGANY PIT/ZG: poz. 29 (Dochód) == max(0, breakdown[kraj].net)."""
        import re

        from pit_exante.report import _country_full_name, _papiery_country_breakdown

        verified = 0
        for year, text, r in all_year_reports:
            breakdown = _papiery_country_breakdown(r)
            if not breakdown:
                continue
            section = self._pitzg_section(text)
            for country, (income, cost) in breakdown.items():
                net = income - cost
                expected = max(net, Decimal("0")).quantize(Decimal("0.01"))
                full_name = _country_full_name(country)
                block = self._country_block(section, full_name)
                assert block is not None, f"Year {year}: block for {full_name} missing"
                match = re.search(rf"poz\.\s*29[^\n]*?({_PL_AMOUNT_RE})\s*PLN", block)
                assert match is not None, f"Year {year}/{country}: poz. 29 missing"
                shown = _pl_to_decimal(match.group(1))
                assert shown == expected, f"Year {year}/{country}: poz. 29 shown {shown}, expected {expected}"
                verified += 1
        assert verified > 0, "Expected at least one country with PIT/ZG breakdown"

    def test_per_country_breakdown_plus_fees_equals_papiery_section_c(self, all_year_reports):
        """Cross-section invariant:
            Σ (per-country net z _papiery_country_breakdown) - opłaty fee
            = papiery_wart_income - papiery_wart_cost (PIT-38 sekcja C wiersz 2 net)

        Czemu '-fees': breakdown wyklucza zdarzenia fee (income=0). Łączny papiery
        koszt zawiera fees. Stąd różnica = fees.
        """
        from pit_exante.report import _papiery_country_breakdown

        for year, _, r in all_year_reports:
            breakdown = _papiery_country_breakdown(r)
            if not breakdown:
                continue
            sum_per_country = sum(
                (income - cost for income, cost in breakdown.values()),
                Decimal("0"),
            )
            fee_costs = sum(
                (e.cost_pln for e in r.papiery_wart_events if e.event_type == "fee"),
                Decimal("0"),
            )
            actual = sum_per_country - fee_costs
            expected = r.papiery_wart_income - r.papiery_wart_cost
            assert actual == expected, (
                f"Year {year}: Σ per-kraj {sum_per_country} - opłaty {fee_costs} = "
                f"{actual}, expected {expected}"
            )


class TestQuantizeUsesHalfUp:
    """G1: report.py musi używać ROUND_HALF_UP w każdym quantize() — Python
    domyślnie używa HALF_EVEN (banker's rounding), co dla wartości .005 daje
    wyniki różne od calculator.py i powoduje display ≠ aggregate.
    """

    def test_python_default_quantize_is_half_even(self):
        # Sanity: nasza obawa była realna — Python default DZIELI od HALF_UP.
        # 2.845 HALF_EVEN → 2.84; HALF_UP → 2.85.
        assert Decimal("2.845").quantize(Decimal("0.01")) == Decimal("2.84")
        from decimal import ROUND_HALF_UP

        assert Decimal("2.845").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal("2.85")

    def test_report_py_source_has_no_quantize_without_explicit_rounding(self):
        # Static guardrail: każdy quantize() w report.py musi mieć explicit
        # rounding=ROUND_HALF_UP. AST-based — odporne na ruff format / wrapy.
        import ast

        report_path = ROOT / "src" / "pit_exante" / "report.py"
        tree = ast.parse(report_path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "quantize"):
                continue
            has_round_half_up = any(
                kw.arg == "rounding" and isinstance(kw.value, ast.Name) and kw.value.id == "ROUND_HALF_UP"
                for kw in node.keywords
            )
            # Także akceptuj pozycyjne ROUND_HALF_UP jako 2. argument.
            if not has_round_half_up and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast.Name) and arg.id == "ROUND_HALF_UP":
                    has_round_half_up = True
            assert has_round_half_up, (
                f"report.py:{node.lineno}: quantize() bez explicit rounding=ROUND_HALF_UP — "
                f"Python default HALF_EVEN różni się od calculator's HALF_UP dla wartości .005."
            )


class TestCsvOutput:
    """G4: CSV output schema, row counts, sum reconciliation."""

    @pytest.fixture
    def csv_text(self, tmp_path):
        if not TRANSACTIONS_PATH.exists():
            pytest.skip("data/transactions.json not present")
        reports, _ = calculate(TRANSACTIONS_PATH)
        csv_path = tmp_path / "test.csv"
        generate_csv(reports, csv_path)
        return csv_path.read_text(encoding="utf-8"), reports

    def test_header_present_with_expected_columns(self, csv_text):
        text, _ = csv_text
        reader = csv.reader(io.StringIO(text))
        header = next(reader)
        expected = [
            "Rok",
            "Data",
            "Typ",
            "Instrument",
            "Konto",
            "Przychód oryg.",
            "Koszt oryg.",
            "Waluta",
            "Kurs NBP",
            "Przychód PLN",
            "Koszt PLN",
            "Zysk/Strata PLN",
        ]
        assert header == expected, f"CSV header mismatch: {header}"

    def test_row_count_equals_sum_of_pit38_and_dividend_events(self, csv_text):
        text, reports = csv_text
        reader = csv.reader(io.StringIO(text))
        next(reader)  # skip header
        rows = list(reader)
        expected_count = sum(len(r.pit38_events) + len(r.dividend_events) for r in reports)
        assert len(rows) == expected_count, f"CSV row count {len(rows)} != events {expected_count}"

    def test_pit38_income_sum_per_year_matches_year_report(self, csv_text):
        # Filter rows by Typ != "dividend", sum Przychód PLN per Rok, compare to r.pit38_income.
        text, reports = csv_text
        reader = csv.reader(io.StringIO(text))
        next(reader)
        sums: dict[int, Decimal] = {}
        for row in reader:
            year = int(row[0])
            typ = row[2]
            if typ == "dividend":
                continue
            sums[year] = sums.get(year, Decimal("0")) + Decimal(row[9])
        for r in reports:
            expected = r.pit38_income
            actual = sums.get(r.year, Decimal("0"))
            assert (
                actual == expected
            ), f"Year {r.year}: CSV pit38 income sum {actual} != r.pit38_income {expected}"

    def test_dividend_typ_marker_present(self, csv_text):
        text, reports = csv_text
        any_year_has_dividends = any(r.dividend_events for r in reports)
        if not any_year_has_dividends:
            pytest.skip("No dividend events in real data — schema overload check N/A")
        reader = csv.reader(io.StringIO(text))
        next(reader)
        types = {row[2] for row in reader}
        assert "dividend" in types, "CSV must mark dividend rows with Typ='dividend' for filtering"


class TestLossCarryforwardNote:
    """Step 12: nuta o stratach z lat ubiegłych (art. 9 ust. 3 / ust. 6).

    Wbudowana w sekcji D — Branch A dla strat (zwiększa pulę przyszłą),
    Branch B dla zysków (lista propozycji 50%/rok), break-even pomijany.
    """

    @staticmethod
    def _make_report(year: int, profit_loss: Decimal) -> YearReport:  # noqa: F821
        """Minimalny YearReport: jeden TaxEvent + ustawione totale, wystarczy
        do wymuszenia renderingu sekcji C i D w generate_year_report."""
        from datetime import date as _date

        from pit_exante.models import InstrumentKind, TaxEvent, YearReport

        income = Decimal("1000")
        cost = income - profit_loss
        ev = TaxEvent(
            date=_date(year, 6, 15),
            symbol="TEST.NYSE",
            account_id="A",
            event_type="sell",
            income_original=Decimal("1000"),
            cost_original=cost,
            income_pln=income,
            cost_pln=cost,
            currency="USD",
            nbp_rate=Decimal("4.0"),
            details="test",
            kind=InstrumentKind.SECURITY,
        )
        return YearReport(
            year=year,
            pit38_income=income,
            pit38_cost=cost,
            pit38_profit_loss=profit_loss,
            pit38_events=[ev],
            papiery_wart_income=income,
            papiery_wart_cost=cost,
            papiery_wart_events=[ev],
        )

    def test_year_break_even_no_note(self):
        from pit_exante.report import _render_loss_carryforward_note

        r = self._make_report(2022, Decimal("0"))
        lines = _render_loss_carryforward_note(r, [r], Decimal("0"))
        assert lines == []

    def test_year_with_loss_renders_pool_for_future(self):
        from pit_exante.report import _render_loss_carryforward_note

        r = self._make_report(2024, Decimal("-1000.37"))
        lines = _render_loss_carryforward_note(r, [r], Decimal("-1000.37"))
        text = "\n".join(lines)
        assert "STRATY Z LAT UBIEGŁYCH" in text
        assert "stratą 1 000,37 PLN" in text
        assert "lat 2025–2029" in text
        assert "500,19 PLN/rok" in text
        # next year is 2025 (w18) — straty_lat poz. = 30
        assert "poz. 30 PIT-38 (2025)" in text

    def test_year_with_profit_no_prior_losses_renders_note_without_proposals(self):
        from pit_exante.report import _render_loss_carryforward_note

        r = self._make_report(2020, Decimal("1500.42"))
        lines = _render_loss_carryforward_note(r, [r], Decimal("1500.42"))
        text = "\n".join(lines)
        assert "STRATY Z LAT UBIEGŁYCH" in text
        assert "poz. 28" in text  # w17
        # 2020 → window 2015-2019 — no data
        assert "brak danych w kalkulatorze" in text
        assert "Brak strat w widzianym oknie" in text
        assert "Suma propozycji" not in text

    def test_year_with_profit_and_prior_losses_lists_proposals(self):
        from pit_exante.report import _render_loss_carryforward_note

        r2021 = self._make_report(2021, Decimal("-160.40"))
        r2023 = self._make_report(2023, Decimal("200.13"))
        lines = _render_loss_carryforward_note(r2023, [r2021, r2023], Decimal("200.13"))
        text = "\n".join(lines)
        assert "2021: strata 160,40 PLN" in text
        assert "max 50% = 80,20 PLN" in text
        assert "Suma propozycji (50%/rok klasycznie): 80,20 PLN" in text
        assert "dochód za ten rok (200,13 PLN)" in text

    def test_proposals_capped_by_current_year_income_message(self):
        """Even if prior loss/2 > current dochód, note must cite the cap.

        Strata 1000 (max 500/rok), dochód 50 → propozycja sumaryczna 500
        ALE displayowane z constraint "≤ dochód za ten rok (50,00 PLN)".
        """
        from pit_exante.report import _render_loss_carryforward_note

        r_loss = self._make_report(2023, Decimal("-1000.00"))
        r_curr = self._make_report(2024, Decimal("50.00"))
        lines = _render_loss_carryforward_note(r_curr, [r_loss, r_curr], Decimal("50.00"))
        text = "\n".join(lines)
        assert "max 50% = 500,00 PLN" in text
        assert "Suma propozycji (50%/rok klasycznie): 500,00 PLN" in text
        # Cap message references the smaller current-year income
        assert "≤ dochód za ten rok (50,00 PLN)" in text
        assert "odliczenie nie może wytworzyć nowej straty" in text

    def test_section_d_prints_straty_lat_position_when_profit_w17(self):
        from pit_exante.report import _render_pit38_filling_instructions

        r = self._make_report(2024, Decimal("500"))
        lines = _render_pit38_filling_instructions(r, [r])
        text = "\n".join(lines)
        # w17 (2024) → straty_lat poz. = 28
        assert "poz. 28 (Straty z lat ubiegłych): puste — patrz uwaga ↓" in text

    def test_section_d_prints_straty_lat_position_when_profit_w18(self):
        from pit_exante.report import _render_pit38_filling_instructions

        r = self._make_report(2025, Decimal("500"))
        lines = _render_pit38_filling_instructions(r, [r])
        text = "\n".join(lines)
        # w18 (2025) → straty_lat poz. = 30
        assert "poz. 30 (Straty z lat ubiegłych): puste — patrz uwaga ↓" in text

    def test_section_d_no_straty_lat_row_when_loss(self):
        """Loss branch keeps current 'Podstawa 0 / pozostałe puste' — no
        separate straty_lat row (nothing to deduct from). Note still rendered."""
        from pit_exante.report import _render_pit38_filling_instructions

        r = self._make_report(2024, Decimal("-1000.37"))
        lines = _render_pit38_filling_instructions(r, [r])
        text = "\n".join(lines)
        assert "poz. 28 (Straty z lat ubiegłych)" not in text
        assert "STRATY Z LAT UBIEGŁYCH (art. 9 ust. 3 ustawy o PIT)" in text

    def test_w17_vs_w18_loss_branch_points_to_correct_next_year_pos(self):
        """Loss in 2024 (w17) → mentions 2025 next year, pointing to poz. 30 (w18).
        Loss in 2023 (w17) → next year 2024 (w17), poz. 28.
        """
        from pit_exante.report import _render_loss_carryforward_note

        r24 = self._make_report(2024, Decimal("-100"))
        lines24 = _render_loss_carryforward_note(r24, [r24], Decimal("-100"))
        assert "poz. 30 PIT-38 (2025)" in "\n".join(lines24)

        r23 = self._make_report(2023, Decimal("-100"))
        lines23 = _render_loss_carryforward_note(r23, [r23], Decimal("-100"))
        assert "poz. 28 PIT-38 (2024)" in "\n".join(lines23)
