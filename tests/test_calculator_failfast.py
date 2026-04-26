"""Fail-fast guards for tax anomalies — H4, H8, H2, CFD-div, unknown-instrument."""

import json
import pytest
from decimal import Decimal
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante import calculator


# Timestamps — use 2024 dates so any NBP lookup hits a stable epoch.
# (get_rate is mocked in fixtures below, so the values are nominal.)
TS_2024_01_15 = 1705320000000  # 2024-01-15 12:00 UTC
TS_2024_01_15_PLUS_3MIN = TS_2024_01_15 + 3 * 60 * 1000  # >2 min, fails timestamp match
TS_2025_02_03 = 1738580400000  # 2025-02-03 12:00 UTC
TS_2026_03_15 = 1773748800000  # 2026-03-15 12:00 UTC


@pytest.fixture
def stable_nbp_rate(monkeypatch):
    """Make get_rate deterministic — return 4.0 for any non-PLN, 1.0 for PLN."""
    def fake_get_rate(currency, transaction_date):
        return Decimal("1") if currency == "PLN" else Decimal("4.0")
    monkeypatch.setattr(calculator, "get_rate", fake_get_rate)


def _txn(**overrides):
    """Build an Exante-style transaction dict with sensible defaults."""
    base = {
        "uuid": "test-uuid",
        "id": 1,
        "timestamp": TS_2024_01_15,
        "valueDate": "2024-01-15",
        "accountId": "TEST0001.001",
        "symbolId": None,
        "operationType": "DIVIDEND",
        "sum": "1.0",
        "transactionPrice": None,
        "asset": "USD",
        "orderId": None,
        "parentUuid": None,
        "comment": None,
    }
    base.update(overrides)
    return base


def _write_run(tmp_path: Path, transactions: list[dict],
               symbol_overrides: dict | None = None) -> Path:
    """Write transactions.json + optional config files; return transactions path."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    txn_path = data_dir / "transactions.json"
    txn_path.write_text(json.dumps(transactions))

    if symbol_overrides is not None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "symbol_overrides.json").write_text(
            json.dumps(symbol_overrides)
        )
    return txn_path


class TestH4OverRefund:
    def test_refund_exceeding_original_wht_raises(self, tmp_path, stable_nbp_rate):
        # AAPL.NASDAQ dividend 100 USD with WHT 15. Then standalone US TAX refund
        # of 20 USD (positive sum, refund) — exceeds the 15 WHT → over-refund.
        txns = [
            _txn(uuid="div1", id=1, timestamp=TS_2024_01_15,
                 symbolId="AAPL.NASDAQ", operationType="DIVIDEND",
                 sum="100.0", asset="AAPL.NASDAQ"),
            _txn(uuid="tax1", id=2, timestamp=TS_2024_01_15 + 1000,
                 symbolId="AAPL.NASDAQ", operationType="TAX",
                 sum="-15.0", asset="USD", parentUuid="div1"),
            # Standalone US TAX refund (no parentUuid, comment with symbol).
            # Timestamp >2 min after dividend → _match_tax_by_timestamp fails →
            # falls into parent_div lookup → over-refund.
            _txn(uuid="refund1", id=3,
                 timestamp=TS_2024_01_15 + 3 * 24 * 3600 * 1000,  # 3 days later
                 valueDate="2024-01-18",
                 symbolId=None, operationType="US TAX",
                 sum="20.0", asset="USD",
                 comment="2 shares ExD 2024-01-15 PD 2024-01-15 dividend "
                         "AAPL.NASDAQ 100.00 USD"),
        ]
        path = _write_run(tmp_path, txns,
                          symbol_overrides={"AAPL.NASDAQ": "STOCK"})
        with pytest.raises(ValueError, match="Over-refund detected"):
            calculator.calculate(path)


class TestH8PlnUnknownCountry:
    def test_pln_dividend_unknown_exchange_raises(self, tmp_path, stable_nbp_rate):
        # PLN dividend with unrecognized exchange → derive_country returns "??"
        # → fail-fast (we don't know which UPO rate applies for an unknown source).
        # asset="PLN" makes parser._derive_currency return PLN; symbolId points to
        # an unknown exchange (.WSE not in _EXCHANGE_COUNTRY).
        txns = [
            _txn(uuid="div1", id=1, timestamp=TS_2024_01_15,
                 symbolId="MYSTERY.WSE", operationType="DIVIDEND",
                 sum="50.0", asset="PLN"),
        ]
        path = _write_run(tmp_path, txns,
                          symbol_overrides={"MYSTERY.WSE": "STOCK"})
        with pytest.raises(ValueError, match="Unknown country for PLN dividend"):
            calculator.calculate(path)


class TestH2RefundCrossYear:
    def test_refund_in_later_year_raises(self, tmp_path, stable_nbp_rate):
        # Dividend in 2024, standalone US TAX refund in 2025 → cross-year.
        txns = [
            _txn(uuid="div1", id=1, timestamp=TS_2024_01_15,
                 valueDate="2024-01-15",
                 symbolId="AAPL.NASDAQ", operationType="DIVIDEND",
                 sum="100.0", asset="AAPL.NASDAQ"),
            _txn(uuid="tax1", id=2, timestamp=TS_2024_01_15 + 1000,
                 valueDate="2024-01-15",
                 symbolId="AAPL.NASDAQ", operationType="TAX",
                 sum="-15.0", asset="USD", parentUuid="div1"),
            # Standalone US TAX refund in 2025 — small enough not to trigger H4
            _txn(uuid="refund1", id=3, timestamp=TS_2025_02_03,
                 valueDate="2025-02-03",
                 symbolId=None, operationType="US TAX",
                 sum="5.0", asset="USD",
                 comment="2 shares ExD 2024-01-15 PD 2024-01-15 dividend "
                         "AAPL.NASDAQ 100.00 USD"),
        ]
        path = _write_run(tmp_path, txns,
                          symbol_overrides={"AAPL.NASDAQ": "STOCK"})
        with pytest.raises(ValueError, match="Refund cross-year detected"):
            calculator.calculate(path)


class TestCfdDividendRaises:
    def test_dividend_on_cfd_raises(self, tmp_path, stable_nbp_rate):
        # Dividend on a symbol classified as CFD → DERIVATIVE → fail-fast.
        # No anomaly at parsing stage — the fail-fast happens in post-loop
        # kind classification.
        txns = [
            _txn(uuid="div1", id=1, timestamp=TS_2024_01_15,
                 symbolId="FAKE.CFD", operationType="DIVIDEND",
                 sum="10.0", asset="FAKE.CFD"),
        ]
        path = _write_run(tmp_path, txns,
                          symbol_overrides={"FAKE.CFD": "CFD"})
        with pytest.raises(ValueError, match="CFD/derivative dividend not supported"):
            calculator.calculate(path)


class TestUnknownInstrumentRaises:
    def test_dividend_on_unknown_symbol_raises(self, tmp_path, stable_nbp_rate):
        # Dividend on a symbol with no metadata and no overrides → fail-fast
        # in the post-loop classification.
        txns = [
            _txn(uuid="div1", id=1, timestamp=TS_2024_01_15,
                 symbolId="UNKNOWN.SYM", operationType="DIVIDEND",
                 sum="10.0", asset="UNKNOWN.SYM"),
        ]
        # No symbol_overrides written → empty dict
        path = _write_run(tmp_path, txns, symbol_overrides={})
        with pytest.raises(ValueError, match="Unknown instrument"):
            calculator.calculate(path)
