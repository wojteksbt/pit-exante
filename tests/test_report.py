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
    """PIT-38(17) sekcja C dla zagranicznego brokera (Exante, brak PIT-8C):
    wiersz 1 (poz. 20-21) = 'Przychody wykazane w PIT-8C Część D' — NIE używamy
    wiersz 2 (poz. 22-23) = 'Inne przychody' — TU lądują wszystkie nasze dane
    wiersz 3 (poz. 24-27) = 'Razem' — suma wszystkich wierszy."""

    def test_papiery_maps_to_wiersz_2_inne_przychody(self, report_2025):
        text, _ = report_2025
        idx = text.find("Papiery wartościowe")
        assert idx != -1
        section_header = text[idx : idx + 200]
        assert "wiersz 2" in section_header, (
            "Papiery z zagr. brokera (brak PIT-8C) idą do wiersza 2 'Inne przychody', "
            "NIE do wiersza 1 (który jest dla PIT-8C Część D, poz. 20-21)"
        )
        assert "poz. 22" in section_header

    def test_pochodne_maps_to_wiersz_2_inne_przychody(self, report_2025):
        text, _ = report_2025
        idx = text.find("Instrumenty pochodne")
        assert idx != -1
        section_header = text[idx : idx + 200]
        assert "wiersz 2" in section_header, (
            "Pochodne z zagr. brokera idą do wiersza 2 'Inne przychody', "
            "sumują się z papierami w tej samej linii"
        )
        assert "poz. 22" in section_header

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
                deducts.append(Decimal(tokens[-1]))
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
        text, r = report_2025
        if not r.papiery_wart_events:
            pytest.skip("No papiery wart events in 2025")
        idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
        instr = text[idx:]
        assert "poz. 22" in instr  # Inne przychody — Przychód
        assert "poz. 23" in instr  # Inne przychody — Koszty

    def test_specifies_section_l_pitzg_count(self, report_2025):
        text, _ = report_2025
        idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
        instr = text[idx:]
        assert "SEKCJA L" in instr
        assert "poz. 69" in instr
        assert "PIT/ZG" in instr

    def test_specifies_pit38_total_to_pay_position(self, report_2025):
        """PODATEK DO ZAPŁATY total: poz. 49 (2024) lub poz. 51 (2025+ shift)."""
        from pit_exante.report import _pit38_total_to_pay_position

        text, r = report_2025
        idx = text.find("INSTRUKCJA WYPEŁNIENIA PIT-38")
        instr = text[idx:]
        expected_pos = _pit38_total_to_pay_position(r.year)
        assert f"PODATEK DO ZAPŁATY (poz. {expected_pos} PIT-38)" in instr

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
        w 2025+: raz w sekcji G dla dywidend, raz w 'PODATEK DO ZAPŁATY (poz. 49 PIT-38)')
        zwraca tę z dywidend. Tylko ta linia ma 'PLN' bezpośrednio po wartości; linia
        'PODATEK DO ZAPŁATY' ma 'zł' i ' PLN' jest dopiero w następnej linii bez 'poz.'.
        """
        import re

        pattern = rf"poz\.\s*{position_num}\b[^\n]*?([\d,]+\.\d{{2}})\s*PLN"
        match = re.search(pattern, section)
        if not match:
            return None
        return Decimal(match.group(1).replace(",", ""))

    def test_poz_22_matches_papiery_income(self, report_2025):
        text, r = report_2025
        if not r.papiery_wart_events:
            pytest.skip("No papiery wart events")
        section = self._instr_section(text)
        value = self._extract_pln_after_position(section, 22)
        assert value is not None, "poz. 22 not found in INSTRUKCJA"
        assert value == r.papiery_wart_income.quantize(
            Decimal("0.01")
        ), f"poz. 22 shown {value} != papiery_wart_income {r.papiery_wart_income}"

    def test_poz_23_matches_papiery_cost(self, report_2025):
        text, r = report_2025
        if not r.papiery_wart_events:
            pytest.skip("No papiery wart events")
        section = self._instr_section(text)
        value = self._extract_pln_after_position(section, 23)
        assert value is not None, "poz. 23 not found in INSTRUKCJA"
        assert value == r.papiery_wart_cost.quantize(
            Decimal("0.01")
        ), f"poz. 23 shown {value} != papiery_wart_cost {r.papiery_wart_cost}"

    def test_poz_27_matches_abs_strata_when_loss(self, all_year_reports):
        """Strata netto (przychód - koszty < 0) → poz. 27 = wartość bezwzględna."""
        for year, text, r in all_year_reports:
            if not r.papiery_wart_events:
                continue
            net = r.papiery_wart_income - r.papiery_wart_cost
            if net >= 0:
                continue
            section = self._instr_section(text)
            value = self._extract_pln_after_position(section, 27)
            assert value is not None, f"Year {year}: poz. 27 not found"
            assert value == (-net).quantize(
                Decimal("0.01")
            ), f"Year {year}: poz. 27 shown {value} != |strata| {-net}"

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
        """'PODATEK DO ZAPŁATY (poz. N PIT-38)' — końcowa kwota = poz. 33 + dyw."""
        import re
        from decimal import ROUND_HALF_UP

        from pit_exante.models import TAX_RATE
        from pit_exante.report import _pit38_total_to_pay_position

        verified = 0
        for year, text, r in all_year_reports:
            section = self._instr_section(text)
            pos_total = _pit38_total_to_pay_position(year)
            idx = section.find(f"PODATEK DO ZAPŁATY (poz. {pos_total}")
            if idx == -1:
                continue
            chunk = section[idx : idx + 400]
            # Linia "= X PLN" (osobna od "= poz. 33 (...) + poz. N (... zł)")
            match = re.search(r"=\s+([\d,]+\.\d{2})\s+PLN", chunk)
            assert match is not None, f"Year {year}: total '= X PLN' line missing"
            shown = Decimal(match.group(1).replace(",", ""))
            papiery_pl = r.papiery_wart_income - r.papiery_wart_cost
            podatek_pap_pre = max(Decimal("0"), papiery_pl * TAX_RATE)
            podatek_pap = podatek_pap_pre.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            expected = (podatek_pap + r.dividends_tax_to_pay_pln).quantize(Decimal("0.01"))
            assert shown == expected, f"Year {year}: PODATEK DO ZAPŁATY shown {shown} != computed {expected}"
            verified += 1
        assert verified > 0, "Expected at least one year with PODATEK DO ZAPŁATY summary"

    def test_section_l_pitzg_count_matches_breakdown(self, all_year_reports):
        """SEKCJA L poz. 69 (Liczba załączników PIT/ZG) == liczba krajów z papierami."""
        import re

        from pit_exante.report import _papiery_country_breakdown

        for year, text, r in all_year_reports:
            section = self._instr_section(text)
            match = re.search(r"poz\.\s*69[^\n]*?(\d+)\s*$", section, re.MULTILINE)
            assert match is not None, f"Year {year}: poz. 69 line not found"
            shown_count = int(match.group(1))
            expected_count = len(_papiery_country_breakdown(r))
            assert (
                shown_count == expected_count
            ), f"Year {year}: poz. 69 PIT/ZG count {shown_count} != breakdown {expected_count}"


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
                match = re.search(r"poz\.\s*29[^\n]*?([\d,]+\.\d{2})\s*PLN", block)
                assert match is not None, f"Year {year}/{country}: poz. 29 missing"
                shown = Decimal(match.group(1).replace(",", ""))
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
        # Static guardrail: każdy quantize w report.py musi mieć explicit
        # rounding=ROUND_HALF_UP. Catch nowych dodawanych quantize bez argumentu.
        report_path = ROOT / "src" / "pit_exante" / "report.py"
        source = report_path.read_text()
        for lineno, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if ".quantize(" in stripped and "import" not in stripped:
                assert "ROUND_HALF_UP" in stripped, (
                    f"report.py:{lineno}: quantize without explicit ROUND_HALF_UP — "
                    f"Python default HALF_EVEN diverges from calculator's HALF_UP for .005 values. "
                    f"Line: {stripped!r}"
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
