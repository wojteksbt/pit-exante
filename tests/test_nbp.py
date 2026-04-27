"""Tests for NBP exchange rate module."""

import json
import socket
import pytest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante import nbp
from pit_exante.nbp import (
    get_rate,
    _fetch_from_api,
    _RETRY_DELAYS_S,
    _VALID_NBP_CURRENCIES,
)


def _mock_response(body: dict):
    class _Resp:
        def __enter__(self_inner):
            return self_inner
        def __exit__(self_inner, *args):
            return False
        def read(self_inner):
            return json.dumps(body).encode()
    return _Resp()


def _http_error(code: int) -> HTTPError:
    return HTTPError("http://test", code, "test", {}, None)


class TestGetRate:
    """Tests using cached rates from data/nbp_cache.json."""

    def test_pln_always_one(self):
        assert get_rate("PLN", date(2020, 1, 15)) == Decimal("1")

    def test_usd_rate_known(self):
        # USD on 2021-02-23 → lookup 2021-02-22 = 3.7135
        rate = get_rate("USD", date(2021, 2, 23))
        assert rate == Decimal("3.7135")

    def test_usd_rate_known_2(self):
        # USD on 2020-09-08 → lookup 2020-09-07 = 3.7666
        rate = get_rate("USD", date(2020, 9, 8))
        assert rate == Decimal("3.7666")

    def test_cad_rate(self):
        rate = get_rate("CAD", date(2024, 4, 10))
        assert rate == Decimal("2.8898")

    def test_sek_rate(self):
        rate = get_rate("SEK", date(2023, 2, 2))
        assert rate == Decimal("0.415")

    def test_case_insensitive(self):
        rate1 = get_rate("usd", date(2021, 2, 23))
        rate2 = get_rate("USD", date(2021, 2, 23))
        assert rate1 == rate2


class TestFetchValidation:
    """Defensive validation that NBP response matches the request."""

    def test_effective_date_mismatch_raises(self):
        body = {
            "code": "USD",
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.1234"}],
        }
        with patch("pit_exante.nbp.urlopen", return_value=_mock_response(body)):
            with pytest.raises(RuntimeError, match="NBP returned rate for 2024-12-23"):
                _fetch_from_api("USD", date(2024, 12, 24))

    def test_currency_code_mismatch_raises(self):
        body = {
            "code": "EUR",  # asked USD
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.1234"}],
        }
        with patch("pit_exante.nbp.urlopen", return_value=_mock_response(body)):
            with pytest.raises(RuntimeError, match="Currency mismatch"):
                _fetch_from_api("USD", date(2024, 12, 23))

    def test_valid_response_returns_rate(self):
        body = {
            "code": "USD",
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.0000"}],
        }
        with patch("pit_exante.nbp.urlopen", return_value=_mock_response(body)):
            rate = _fetch_from_api("USD", date(2024, 12, 23))
            assert rate == Decimal("4.0000")


class TestL5Refactor:
    """L5: NBP API as authority — no holiday calendar, retry on 404."""

    def test_invalid_currency_raises(self):
        with pytest.raises(ValueError, match="Unsupported currency"):
            get_rate("XXX", date(2024, 1, 15))

    def test_lowercase_invalid_currency_raises(self):
        # Validation happens after .upper(), so 'jpy' should fail same as 'JPY'
        with pytest.raises(ValueError, match="Unsupported currency"):
            get_rate("jpy", date(2024, 1, 15))

    def test_pre_archive_date_raises(self):
        # NBP table A archive starts 2002. Any earlier date should raise
        # before any API call is attempted.
        with patch("pit_exante.nbp.urlopen") as mock_url:
            with pytest.raises(RuntimeError, match="before NBP archive"):
                get_rate("USD", date(1990, 1, 1))
            mock_url.assert_not_called()

    def test_holiday_fallback_walks_back(self):
        # Simulate Wigilia 2025 (24.12.2025): NBP closed → 404 → walk back to 23.12.
        # Use date(2025, 12, 26) so initial d=2025-12-25 (BN 404) → 2025-12-24 (Wigilia 404)
        # → 2025-12-23 (success). Bypass cache via fresh state.
        original_cache = nbp._cache
        original_loaded = nbp._cache_loaded
        try:
            nbp._cache = {}
            nbp._cache_loaded = True  # skip _load_cache disk read

            calls = []

            def fake_urlopen(req, timeout=10):
                # Extract date from URL to decide response
                url = req.full_url if hasattr(req, "full_url") else req.get_full_url()
                calls.append(url)
                if "2025-12-23" in url:
                    return _mock_response({
                        "code": "USD",
                        "rates": [{"effectiveDate": "2025-12-23", "mid": "3.5848"}],
                    })
                # 404 for 24.12, 25.12, 26.12 (holidays/Wigilia)
                raise _http_error(404)

            with patch("pit_exante.nbp.urlopen", side_effect=fake_urlopen):
                with patch("pit_exante.nbp.time.sleep"):  # skip rate-limit sleep
                    rate = get_rate("USD", date(2025, 12, 26))
                    assert rate == Decimal("3.5848")
                    # 3 attempts: 25, 24, 23
                    assert len(calls) == 3
        finally:
            nbp._cache = original_cache
            nbp._cache_loaded = original_loaded

    def test_max_fallback_exceeded_raises(self):
        # All 7 attempts return 404 → RuntimeError with date range
        original_cache = nbp._cache
        original_loaded = nbp._cache_loaded
        try:
            nbp._cache = {}
            nbp._cache_loaded = True

            def always_404(req, timeout=10):
                raise _http_error(404)

            with patch("pit_exante.nbp.urlopen", side_effect=always_404):
                with patch("pit_exante.nbp.time.sleep"):
                    with pytest.raises(RuntimeError, match="No NBP rate within"):
                        get_rate("USD", date(2024, 6, 15))
        finally:
            nbp._cache = original_cache
            nbp._cache_loaded = original_loaded

    def test_valid_currencies_set(self):
        # Document the currency contract — if this changes, calculator may
        # need updates for handling new currencies.
        assert _VALID_NBP_CURRENCIES == frozenset({"USD", "EUR", "CAD", "SEK"})


class TestN3RetryBackoff:
    """N3: exponential backoff on 5xx/timeout, no retry on 4xx."""

    def test_5xx_retries_then_succeeds(self):
        body = {
            "code": "USD",
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.0000"}],
        }
        calls = {"n": 0}

        def fake_urlopen(req, timeout=10):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _http_error(503)
            return _mock_response(body)

        with patch("pit_exante.nbp.urlopen", side_effect=fake_urlopen):
            with patch("pit_exante.nbp.time.sleep"):
                rate = _fetch_from_api("USD", date(2024, 12, 23))
                assert rate == Decimal("4.0000")
                assert calls["n"] == 2

    def test_5xx_exhausts_retries_raises(self):
        def always_503(req, timeout=10):
            raise _http_error(503)

        with patch("pit_exante.nbp.urlopen", side_effect=always_503):
            with patch("pit_exante.nbp.time.sleep"):
                with pytest.raises(RuntimeError, match="NBP API failed after"):
                    _fetch_from_api("USD", date(2024, 12, 23))

    def test_timeout_retries_then_succeeds(self):
        body = {
            "code": "USD",
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.0000"}],
        }
        calls = {"n": 0}

        def fake_urlopen(req, timeout=10):
            calls["n"] += 1
            if calls["n"] == 1:
                raise socket.timeout("timed out")
            return _mock_response(body)

        with patch("pit_exante.nbp.urlopen", side_effect=fake_urlopen):
            with patch("pit_exante.nbp.time.sleep"):
                rate = _fetch_from_api("USD", date(2024, 12, 23))
                assert rate == Decimal("4.0000")
                assert calls["n"] == 2

    def test_urlerror_retries_then_succeeds(self):
        body = {
            "code": "USD",
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.0000"}],
        }
        calls = {"n": 0}

        def fake_urlopen(req, timeout=10):
            calls["n"] += 1
            if calls["n"] == 1:
                raise URLError("Connection refused")
            return _mock_response(body)

        with patch("pit_exante.nbp.urlopen", side_effect=fake_urlopen):
            with patch("pit_exante.nbp.time.sleep"):
                rate = _fetch_from_api("USD", date(2024, 12, 23))
                assert rate == Decimal("4.0000")
                assert calls["n"] == 2

    def test_4xx_does_not_retry(self):
        # Non-404 4xx (e.g., 401/429) propagates immediately, no retries.
        calls = {"n": 0}

        def fake_urlopen(req, timeout=10):
            calls["n"] += 1
            raise _http_error(401)

        with patch("pit_exante.nbp.urlopen", side_effect=fake_urlopen):
            with patch("pit_exante.nbp.time.sleep"):
                with pytest.raises(HTTPError):
                    _fetch_from_api("USD", date(2024, 12, 23))
                assert calls["n"] == 1

    def test_404_returns_none_no_retry(self):
        # 404 is a real signal — no retry, return None.
        calls = {"n": 0}

        def fake_urlopen(req, timeout=10):
            calls["n"] += 1
            raise _http_error(404)

        with patch("pit_exante.nbp.urlopen", side_effect=fake_urlopen):
            with patch("pit_exante.nbp.time.sleep"):
                result = _fetch_from_api("USD", date(2024, 12, 23))
                assert result is None
                assert calls["n"] == 1

    def test_retry_delays_exponential(self):
        # Document the delay schedule (1s/2s/4s).
        assert _RETRY_DELAYS_S == (1, 2, 4)
