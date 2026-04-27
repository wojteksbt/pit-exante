"""Unit tests for download_transactions module — symbol metadata fetching."""

from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path

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
        body = json.dumps(
            {
                "symbolId": "VIG.US",
                "symbolType": "CFD",
                "name": "Vanguard Dividend Appreciation ETF",
                "currency": "USD",
            }
        ).encode()

        def fake_urlopen(req):
            return _FakeResponse(body)

        import download_transactions

        monkeypatch.setattr(download_transactions, "urlopen", fake_urlopen)

        result = download_transactions.fetch_symbol_metadata("VIG.US")
        assert result["symbolType"] == "CFD"
        assert result["symbolId"] == "VIG.US"

    def test_returns_symbolType_for_stock(self, monkeypatch):
        body = json.dumps(
            {
                "symbolId": "VIG.ARCA",
                "symbolType": "STOCK",
                "name": "Vanguard Dividend Appreciation ETF",
            }
        ).encode()

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
