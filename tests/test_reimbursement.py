"""REIMBURSEMENT (zwrot broker) → PIT-36 inne źródła art. 20 — ADR-0009.

Lock decyzji 2026-05-26: REIMBURSEMENT zawsze ląduje w odrębnej sekcji
PIT-36 inne źródła, NIE w PIT-38. Konsystentna reguła niezależna od kwoty.
"""

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante import calculator
from pit_exante.calculator import calculate
from pit_exante.models import InstrumentKind
from pit_exante.report import generate_csv, generate_year_report

TS_2025_02_25 = 1740484800000  # 2025-02-25 12:00 UTC (synthetic)
TS_2026_05_22 = 1779444800000  # 2026-05-22 12:00 UTC (synthetic, rounded to noon)


@pytest.fixture
def stable_nbp_rate(monkeypatch):
    """Deterministic NBP for tests: 4.0 PLN/USD."""

    def fake_get_rate(currency, transaction_date):
        return Decimal("1") if currency == "PLN" else Decimal("4.0")

    monkeypatch.setattr(calculator, "get_rate", fake_get_rate)


def _txn(**overrides):
    base = {
        "uuid": "test-uuid",
        "id": 1,
        "timestamp": TS_2026_05_22,
        "valueDate": "2026-05-22",
        "accountId": "TEST1234.001",
        "symbolId": None,
        "operationType": "REIMBURSEMENT",
        "sum": "25.00",
        "transactionPrice": None,
        "asset": "USD",
        "orderId": None,
        "parentUuid": None,
        "comment": None,
    }
    base.update(overrides)
    return base


def _write_run(tmp_path: Path, transactions: list[dict]) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "transactions.json"
    path.write_text(json.dumps(transactions))
    return path


class TestReimbursementClassification:
    def test_reimbursement_routes_to_other_sources(self, tmp_path, stable_nbp_rate):
        txn_path = _write_run(tmp_path, [_txn()])
        reports, _ = calculate(txn_path)

        assert len(reports) == 1
        report = reports[0]
        assert report.year == 2026

        # Trafiło do PIT-36 inne źródła, nie do PIT-38
        assert len(report.pit36_inne_zrodla_events) == 1
        assert report.pit36_inne_zrodla_events[0].kind == InstrumentKind.OTHER_SOURCES
        assert report.pit36_inne_zrodla_events[0].event_type == "reimbursement"
        # 25.00 × 4.0 = 100.00 PLN (synthetic fixture values)
        assert report.pit36_inne_zrodla_income_pln == Decimal("100.00")

        # PIT-38 nietknięte
        assert report.papiery_wart_events == []
        assert report.pochodne_events == []
        assert report.pit38_events == []
        assert report.pit38_income == Decimal("0")
        assert report.pit38_cost == Decimal("0")
        assert report.pit38_tax == Decimal("0")

    def test_reimbursement_amount_pln_uses_nbp_d_minus_1(self, tmp_path, monkeypatch):
        """NBP używa daty otrzymania (valueDate / effective_date) z konwencją D-1 (przez get_rate)."""
        captured_dates = []

        def capturing_rate(currency, transaction_date):
            captured_dates.append((currency, transaction_date))
            return Decimal("4.0")

        monkeypatch.setattr(calculator, "get_rate", capturing_rate)

        txn_path = _write_run(tmp_path, [_txn()])
        calculate(txn_path)

        # get_rate dostał valueDate=2026-05-22; konwencję D-1 stosuje get_rate
        # wewnętrznie (verified by test_nbp.py).
        assert ("USD", __import__("datetime").date(2026, 5, 22)) in captured_dates

    def test_reimbursement_unsupported_currency_fails_fast(self, tmp_path, stable_nbp_rate, monkeypatch):
        """ISC-27: REIMBURSEMENT w nieobsługiwanej walucie → ValueError.

        Parser normalizuje currency do BARE_CURRENCIES (default USD), więc
        rzeczywiście tylko regresja w parserze mogłaby dostarczyć GBP do
        calculatora. Symulujemy taką regresję monkey-patchem, żeby udowodnić
        że obrońca w branchu REIMBURSEMENT calculatora trzyma.
        """
        from pit_exante import parser

        monkeypatch.setattr(parser, "_derive_currency", lambda *a, **kw: "GBP")
        txn_path = _write_run(tmp_path, [_txn(uuid="bad-cur")])
        with pytest.raises(ValueError, match="REIMBURSEMENT in unsupported currency"):
            calculate(txn_path)

    def test_reimbursement_negative_sum_handled_as_absolute(self, tmp_path, stable_nbp_rate):
        """Defensive: gdyby kiedyś Exante wystawił REIMBURSEMENT ze znakiem ujemnym
        (clawback / cancellation refundu), kalkulator nie powinien wybuchnąć."""
        txn_path = _write_run(tmp_path, [_txn(sum="-25.00", uuid="neg-reimb")])
        reports, _ = calculate(txn_path)
        # Booked jako przychód abs() — kalkulator nie reklasyfikuje znaku;
        # ekonomiczne zerowanie/cofnięcie wymaga manual decision (consciously OOS).
        assert len(reports) == 1
        assert reports[0].pit36_inne_zrodla_income_pln == Decimal("100.00")


class TestCrossYearReimbursement:
    """Refund 2026 po SELL 2025: PIT-38 2025 nieruszone, PIT-36 2026 ma przychód."""

    def test_cross_year_reimbursement_independent_of_2025_pit38(self, tmp_path, stable_nbp_rate):
        # Standalone REIMBURSEMENT 2026 — minimalna fixture, bez SELL'i
        # (cykl krótkiej pozycji jest weryfikowany przez full integration, nie tym testem).
        txns = [
            _txn(
                uuid="reimb-2026",
                id=100,
                timestamp=TS_2026_05_22,
                valueDate="2026-05-22",
                operationType="REIMBURSEMENT",
                sum="25.00",
                asset="USD",
            )
        ]
        txn_path = _write_run(tmp_path, txns)
        reports, _ = calculate(txn_path)

        years = {r.year: r for r in reports}
        assert 2026 in years
        # Tylko rok 2026 (brak SELL'i 2025 w fixture).
        assert years[2026].pit36_inne_zrodla_income_pln == Decimal("100.00")
        assert years[2026].papiery_wart_income == Decimal("0")
        assert years[2026].pochodne_income == Decimal("0")


class TestReimbursementReporting:
    def test_pit36_inne_zrodla_section_renders(self, tmp_path, stable_nbp_rate):
        txn_path = _write_run(tmp_path, [_txn()])
        reports, _ = calculate(txn_path)
        text = generate_year_report(reports[0], all_reports=reports)

        # Sekcja istnieje
        assert "Inne źródła (art. 20 ust. 1)" in text
        assert "PIT-36 sekcja D" in text
        # Konkretna kwota (synthetic fixture)
        assert "100,00" in text
        # Instrukcja z kwotą wolną i linkami do legal/ADR
        assert "kwota wolna" in text.lower() or "Kwota wolna" in text
        assert "docs/legal/reimbursement-art20.md" in text
        assert "docs/adr/0009" in text

    def test_pit36_csv_contains_reimbursement_row(self, tmp_path, stable_nbp_rate):
        txn_path = _write_run(tmp_path, [_txn()])
        reports, _ = calculate(txn_path)
        csv_path = tmp_path / "out.csv"
        generate_csv(reports, csv_path)
        content = csv_path.read_text()
        assert "reimbursement" in content
        assert "100.00" in content

    def test_pit38_section_does_not_contain_reimbursement(self, tmp_path, stable_nbp_rate):
        """ISC-25 anti: REIMBURSEMENT nie wycieka do PIT-38 sekcji C."""
        txn_path = _write_run(tmp_path, [_txn()])
        reports, _ = calculate(txn_path)
        text = generate_year_report(reports[0], all_reports=reports)
        # Sekcja PIT-38 obecna (bo Suma kontrola się drukuje nawet bez eventów)
        # ale wewnątrz NIE powinno być reimbursement
        pit38_block = text.split("SUMA PIT-38")[0]
        assert "reimbursement" not in pit38_block.lower()
