"""Tests for download_transactions module — symbol metadata fetching."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


class TestFetchSymbolMetadata:
    """fetch_symbol_metadata returns parsed JSON with symbolType."""

    def test_returns_symbolType_for_cfd(self, monkeypatch):
        body = json.dumps({
            "symbolId": "VIG.US",
            "symbolType": "CFD",
            "name": "Vanguard Dividend Appreciation ETF",
            "currency": "USD",
        }).encode()

        def fake_urlopen(req):
            return _FakeResponse(body)

        import download_transactions
        monkeypatch.setattr(download_transactions, "urlopen", fake_urlopen)

        result = download_transactions.fetch_symbol_metadata("VIG.US")
        assert result["symbolType"] == "CFD"
        assert result["symbolId"] == "VIG.US"

    def test_returns_symbolType_for_stock(self, monkeypatch):
        body = json.dumps({
            "symbolId": "VIG.ARCA",
            "symbolType": "STOCK",
            "name": "Vanguard Dividend Appreciation ETF",
        }).encode()

        def fake_urlopen(req):
            return _FakeResponse(body)

        import download_transactions
        monkeypatch.setattr(download_transactions, "urlopen", fake_urlopen)

        result = download_transactions.fetch_symbol_metadata("VIG.ARCA")
        assert result["symbolType"] == "STOCK"

    def test_404_returns_none(self, monkeypatch):
        from urllib.error import HTTPError

        def fake_urlopen(req):
            raise HTTPError(req.full_url, 404, "Not Found", {}, BytesIO(b""))

        import download_transactions
        monkeypatch.setattr(download_transactions, "urlopen", fake_urlopen)

        result = download_transactions.fetch_symbol_metadata("DELISTED.X")
        assert result is None


class TestSymbolCoverage:
    """Every unique symbolId in transactions.json must be classifiable."""

    def test_all_symbols_in_transactions_have_metadata_or_override(self):
        txns_path = ROOT / "data" / "transactions.json"
        symbols_path = ROOT / "data" / "symbols.json"
        overrides_path = ROOT / "config" / "symbol_overrides.json"

        if not txns_path.exists():
            pytest.skip("data/transactions.json not present (download required)")

        txns = json.loads(txns_path.read_text())
        unique_symbols = {t["symbolId"] for t in txns if t.get("symbolId")}

        symbols = json.loads(symbols_path.read_text()) if symbols_path.exists() else {}
        overrides_raw = json.loads(overrides_path.read_text())
        overrides = {k: v for k, v in overrides_raw.items() if not k.startswith("_")}

        classifiable = set(symbols) | set(overrides)
        missing = unique_symbols - classifiable
        assert not missing, (
            f"Symbols missing from data/symbols.json AND config/symbol_overrides.json: "
            f"{sorted(missing)}. Add to overrides or fetch via download_transactions.py."
        )
