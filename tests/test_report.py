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
                assert per_row_sum == cd.tax_to_deduct_pln, (
                    f"Year {year} country {code}: per-row {per_row_sum} != aggregate {cd.tax_to_deduct_pln}"
                )


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
            "Rok", "Data", "Typ", "Instrument", "Konto",
            "Przychód oryg.", "Koszt oryg.", "Waluta", "Kurs NBP",
            "Przychód PLN", "Koszt PLN", "Zysk/Strata PLN",
        ]
        assert header == expected, f"CSV header mismatch: {header}"

    def test_row_count_equals_sum_of_pit38_and_dividend_events(self, csv_text):
        text, reports = csv_text
        reader = csv.reader(io.StringIO(text))
        next(reader)  # skip header
        rows = list(reader)
        expected_count = sum(len(r.pit38_events) + len(r.dividend_events) for r in reports)
        assert len(rows) == expected_count, (
            f"CSV row count {len(rows)} != events {expected_count}"
        )

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
            assert actual == expected, (
                f"Year {r.year}: CSV pit38 income sum {actual} != r.pit38_income {expected}"
            )

    def test_dividend_typ_marker_present(self, csv_text):
        text, reports = csv_text
        any_year_has_dividends = any(r.dividend_events for r in reports)
        if not any_year_has_dividends:
            pytest.skip("No dividend events in real data — schema overload check N/A")
        reader = csv.reader(io.StringIO(text))
        next(reader)
        types = {row[2] for row in reader}
        assert "dividend" in types, "CSV must mark dividend rows with Typ='dividend' for filtering"
