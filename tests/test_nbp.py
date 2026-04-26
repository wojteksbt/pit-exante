"""Tests for NBP exchange rate module."""

import io
import json
import pytest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pit_exante.nbp import (
    get_rate,
    _previous_business_day,
    _is_business_day,
    _easter,
    _polish_holidays,
    _fetch_from_api,
)


class TestEaster:
    """Verify Easter calculation against known dates."""

    @pytest.mark.parametrize("year,expected", [
        (2020, date(2020, 4, 12)),
        (2021, date(2021, 4, 4)),
        (2022, date(2022, 4, 17)),
        (2023, date(2023, 4, 9)),
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
    ])
    def test_easter_dates(self, year, expected):
        assert _easter(year) == expected


class TestPolishHolidays:
    def test_fixed_holidays_2024(self):
        holidays = _polish_holidays(2024)
        assert date(2024, 1, 1) in holidays   # Nowy Rok
        assert date(2024, 1, 6) in holidays   # Trzech Króli
        assert date(2024, 5, 1) in holidays   # Święto Pracy
        assert date(2024, 5, 3) in holidays   # Konstytucja
        assert date(2024, 8, 15) in holidays  # Wniebowzięcie NMP
        assert date(2024, 11, 1) in holidays  # Wszystkich Świętych
        assert date(2024, 11, 11) in holidays # Niepodległości
        assert date(2024, 12, 25) in holidays # Boże Narodzenie
        assert date(2024, 12, 26) in holidays # Drugi dzień BN

    def test_easter_dependent_holidays_2024(self):
        holidays = _polish_holidays(2024)
        # Easter 2024 = March 31
        assert date(2024, 3, 31) in holidays  # Wielkanoc
        assert date(2024, 4, 1) in holidays   # Poniedziałek Wielkanocny
        assert date(2024, 5, 30) in holidays  # Boże Ciało (Easter + 60)

    def test_good_friday_not_holiday(self):
        # Poland does NOT observe Good Friday
        holidays = _polish_holidays(2020)
        # Easter 2020 = April 12, Good Friday = April 10
        assert date(2020, 4, 10) not in holidays

    def test_count(self):
        holidays = _polish_holidays(2024)
        assert len(holidays) == 12


class TestIsBusinessDay:
    def test_weekday_is_business(self):
        assert _is_business_day(date(2024, 3, 18)) is True  # Monday

    def test_saturday_not_business(self):
        assert _is_business_day(date(2024, 3, 16)) is False

    def test_sunday_not_business(self):
        assert _is_business_day(date(2024, 3, 17)) is False

    def test_new_year_not_business(self):
        assert _is_business_day(date(2024, 1, 1)) is False

    def test_easter_monday_not_business(self):
        assert _is_business_day(date(2024, 4, 1)) is False

    def test_good_friday_is_business(self):
        # Good Friday 2024 = March 29
        assert _is_business_day(date(2024, 3, 29)) is True


class TestPreviousBusinessDay:
    def test_monday_goes_to_friday(self):
        assert _previous_business_day(date(2024, 3, 18)) == date(2024, 3, 15)

    def test_tuesday_goes_to_monday(self):
        assert _previous_business_day(date(2024, 3, 19)) == date(2024, 3, 18)

    def test_skips_weekend(self):
        # Sunday → Friday
        assert _previous_business_day(date(2024, 3, 17)) == date(2024, 3, 15)

    def test_skips_easter_weekend(self):
        # Easter 2020 = April 12 (Sun), Easter Mon = April 13
        # Before April 14 (Tue) → April 10 (Fri, Good Friday is business day in PL)
        assert _previous_business_day(date(2020, 4, 14)) == date(2020, 4, 10)

    def test_skips_new_year(self):
        # Before Jan 2, 2020 (Thu) → Dec 31, 2019 (Tue)
        assert _previous_business_day(date(2020, 1, 2)) == date(2019, 12, 31)

    def test_skips_may_holidays(self):
        # May 1+3 are holidays in 2020; May 4 is Mon
        # Before May 4 → April 30 (Thu)
        assert _previous_business_day(date(2020, 5, 4)) == date(2020, 4, 30)


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

    def _mock_response(self, body: dict):
        class _Resp:
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *args):
                return False
            def read(self_inner):
                return json.dumps(body).encode()
        return _Resp()

    def test_effective_date_mismatch_raises(self):
        # NBP would never do this today, but if it ever returns "nearest available"
        # silently, we must fail-fast rather than poison the cache.
        body = {
            "code": "USD",
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.1234"}],
        }
        with patch("pit_exante.nbp.urlopen", return_value=self._mock_response(body)):
            with pytest.raises(RuntimeError, match="NBP returned rate for 2024-12-23"):
                _fetch_from_api("USD", date(2024, 12, 24))

    def test_currency_code_mismatch_raises(self):
        body = {
            "code": "EUR",  # asked USD
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.1234"}],
        }
        with patch("pit_exante.nbp.urlopen", return_value=self._mock_response(body)):
            with pytest.raises(RuntimeError, match="Currency mismatch"):
                _fetch_from_api("USD", date(2024, 12, 23))

    def test_valid_response_returns_rate(self):
        body = {
            "code": "USD",
            "rates": [{"effectiveDate": "2024-12-23", "mid": "4.0000"}],
        }
        with patch("pit_exante.nbp.urlopen", return_value=self._mock_response(body)):
            rate = _fetch_from_api("USD", date(2024, 12, 23))
            assert rate == Decimal("4.0000")
