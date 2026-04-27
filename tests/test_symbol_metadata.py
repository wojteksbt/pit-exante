"""Tests for symbol_metadata module — InstrumentKind classification."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pit_exante.models import InstrumentKind, UnknownInstrumentError, UnknownTypeError
from pit_exante.symbol_metadata import classify, get_symbol_type

# Minimal in-memory fixtures (independent of data/ files)
_SYMBOLS = {
    "VIG.US": {"symbolType": "CFD", "name": "Vanguard Dividend Appreciation ETF"},
    "VIG.ARCA": {"symbolType": "STOCK", "name": "Vanguard Dividend Appreciation ETF"},
    "MOS.NYSE": {"symbolType": "STOCK", "name": "The Mosaic Company"},
    "GBTC.ARCA": {"symbolType": "STOCK", "name": "Grayscale Bitcoin Trust ETF"},
    "MYSTERY.X": {"symbolType": "WEIRD_TYPE", "name": "Mystery instrument"},
}
_OVERRIDES = {
    "NGE.ARCA": "STOCK",
    "U/U.TMX": "STOCK",
    "EUR/USD.E.FX": "FX",
}


class TestClassify:
    def test_vig_us_is_derivative(self):
        assert classify("VIG.US", _SYMBOLS, _OVERRIDES) == InstrumentKind.DERIVATIVE

    def test_vig_arca_is_security(self):
        assert classify("VIG.ARCA", _SYMBOLS, _OVERRIDES) == InstrumentKind.SECURITY

    def test_unknown_symbol_raises(self):
        with pytest.raises(UnknownInstrumentError) as exc:
            classify("DOES_NOT_EXIST", _SYMBOLS, _OVERRIDES)
        assert "DOES_NOT_EXIST" in str(exc.value)

    def test_unknown_type_raises(self):
        with pytest.raises(UnknownTypeError) as exc:
            classify("MYSTERY.X", _SYMBOLS, _OVERRIDES)
        assert "WEIRD_TYPE" in str(exc.value)

    def test_override_used_when_404(self):
        # NGE.ARCA not in _SYMBOLS but in _OVERRIDES
        assert classify("NGE.ARCA", _SYMBOLS, _OVERRIDES) == InstrumentKind.SECURITY

    def test_override_fx_is_unknown_type(self):
        # FX is intentionally NOT in EXANTE_TYPE_TO_KIND — calculator handles fee
        # event from FX trades as SECURITY directly (KROK 3); classify() must
        # raise so caller knows symbol classification is not applicable.
        with pytest.raises(UnknownTypeError):
            classify("EUR/USD.E.FX", _SYMBOLS, _OVERRIDES)

    def test_classify_uses_symbolType_only_not_rollover_history(self):
        # Intent guard: classifier must not introspect rollover events.
        # Function signature has only (symbol_id, symbols, overrides) — by
        # construction it cannot peek at rollover history. This test pins
        # the API surface so a future "improvement" adding rollover-based
        # heuristic would require changing the signature (visible diff).
        import inspect

        sig = inspect.signature(classify)
        assert list(sig.parameters) == ["symbol_id", "symbols", "overrides"]


class TestGetSymbolType:
    def test_returns_metadata_symbolType(self):
        assert get_symbol_type("VIG.US", _SYMBOLS, _OVERRIDES) == "CFD"

    def test_returns_override(self):
        assert get_symbol_type("NGE.ARCA", _SYMBOLS, _OVERRIDES) == "STOCK"

    def test_metadata_takes_precedence_over_override(self):
        # If symbol exists in both, metadata wins (more authoritative)
        symbols = {"X": {"symbolType": "STOCK"}}
        overrides = {"X": "CFD"}
        assert get_symbol_type("X", symbols, overrides) == "STOCK"


class TestRealDataIntegration:
    """Validate against actual fetched metadata."""

    def test_all_27_stocks_classified_as_security(self):
        symbols_path = ROOT / "data" / "symbols.json"
        if not symbols_path.exists():
            pytest.skip("data/symbols.json not present")
        symbols = json.loads(symbols_path.read_text())
        stocks = [sid for sid, m in symbols.items() if m.get("symbolType") == "STOCK"]
        assert len(stocks) >= 1
        for sid in stocks:
            assert classify(sid, symbols, {}) == InstrumentKind.SECURITY

    def test_vig_us_classified_as_derivative_in_real_data(self):
        symbols_path = ROOT / "data" / "symbols.json"
        if not symbols_path.exists():
            pytest.skip("data/symbols.json not present")
        symbols = json.loads(symbols_path.read_text())
        if "VIG.US" not in symbols:
            pytest.skip("VIG.US not in fetched metadata")
        assert classify("VIG.US", symbols, {}) == InstrumentKind.DERIVATIVE
