"""NBP exchange rate fetcher with caching."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_NBP_API = "https://api.nbp.pl/api/exchangerates/rates/a/{currency}/{date}/?format=json"

_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "nbp_cache.json"

_cache: dict[str, str] = {}
_cache_loaded: bool = False
_cache_dirty: bool = False
_last_request_time: float = 0.0


def _load_cache() -> None:
    global _cache, _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    if _CACHE_PATH.exists():
        with open(_CACHE_PATH) as f:
            _cache = json.load(f)


def _save_cache() -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CACHE_PATH, "w") as f:
        json.dump(_cache, f, indent=2, sort_keys=True)


def _easter(year: int) -> date:
    """Calculate Easter Sunday using the Anonymous Gregorian algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _polish_holidays(year: int) -> set[date]:
    """Return set of Polish public holidays for a given year."""
    easter_sun = _easter(year)
    easter_mon = easter_sun + timedelta(days=1)
    corpus_christi = easter_sun + timedelta(days=60)

    return {
        date(year, 1, 1),    # Nowy Rok
        date(year, 1, 6),    # Trzech Króli
        easter_sun,
        easter_mon,
        date(year, 5, 1),    # Święto Pracy
        date(year, 5, 3),    # Konstytucja 3 Maja
        corpus_christi,
        date(year, 8, 15),   # Wniebowzięcie NMP
        date(year, 11, 1),   # Wszystkich Świętych
        date(year, 11, 11),  # Niepodległości
        date(year, 12, 25),  # Boże Narodzenie
        date(year, 12, 26),  # Drugi dzień BN
    }


# Pre-compute holidays for relevant years
_holidays_cache: dict[int, set[date]] = {}


def _is_business_day(d: date) -> bool:
    """Check if date is a Polish business day."""
    if d.weekday() >= 5:  # Saturday or Sunday
        return False
    if d.year not in _holidays_cache:
        _holidays_cache[d.year] = _polish_holidays(d.year)
    return d not in _holidays_cache[d.year]


def _previous_business_day(d: date) -> date:
    """Find the last business day strictly before the given date.

    Per art. 11a ust. 1-2 ustawy o PIT: average NBP rate from the last
    business day preceding the transaction date.
    """
    d = d - timedelta(days=1)
    while not _is_business_day(d):
        d = d - timedelta(days=1)
    return d


def _fetch_from_api(currency: str, d: date) -> Decimal | None:
    """Fetch rate from NBP API. Returns None if not found (404)."""
    global _last_request_time

    # Rate limiting: max 1 req/s
    elapsed = time.time() - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    url = _NBP_API.format(currency=currency.upper(), date=d.isoformat())
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "pit-exante/1.0"})

    try:
        _last_request_time = time.time()
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            rate_entry = data["rates"][0]
            if rate_entry["effectiveDate"] != d.isoformat():
                raise RuntimeError(
                    f"NBP returned rate for {rate_entry['effectiveDate']}, "
                    f"asked {d.isoformat()} ({currency})"
                )
            if data.get("code", "").upper() != currency.upper():
                raise RuntimeError(
                    f"Currency mismatch: asked {currency}, got {data.get('code')}"
                )
            return Decimal(str(rate_entry["mid"]))
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def get_rate(currency: str, transaction_date: date) -> Decimal:
    """Get NBP exchange rate for the last business day before transaction_date.

    Returns Decimal(1) for PLN transactions.
    """
    if currency.upper() == "PLN":
        return Decimal("1")

    _load_cache()

    lookup_date = _previous_business_day(transaction_date)

    # Try cache first
    cache_key = f"{currency.upper()}_{lookup_date.isoformat()}"
    if cache_key in _cache:
        return Decimal(_cache[cache_key])

    # Fetch from API, retrying with earlier dates if 404
    global _cache_dirty
    d = lookup_date
    for _ in range(5):  # max 5 retries
        rate = _fetch_from_api(currency, d)
        if rate is not None:
            _cache[cache_key] = str(rate)
            _cache_dirty = True
            return rate
        d = _previous_business_day(d)

    raise RuntimeError(f"Could not fetch NBP rate for {currency} around {transaction_date}")


def save_cache_if_dirty() -> None:
    """Save cache to disk if any new rates were fetched. Call at end of processing."""
    if _cache_dirty:
        _save_cache()
