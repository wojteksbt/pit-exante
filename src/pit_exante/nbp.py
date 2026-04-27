"""NBP exchange rate fetcher with caching."""

from __future__ import annotations

import json
import socket
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_NBP_API = "https://api.nbp.pl/api/exchangerates/rates/a/{currency}/{date}/?format=json"

_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "nbp_cache.json"

# NBP table A archive starts 2002-01-02. Earlier dates have no published mid-rate.
_NBP_ARCHIVE_START_YEAR = 2002

# How many days back to walk on 404 (weekend/holiday). Margin for hypothetical
# 6+-day closure if Sejm legislates further holidays.
_MAX_FALLBACK_DAYS = 7

# Backoff delays for transient NBP failures (5xx, network timeout). 4xx is NOT
# retried — 404 is a real signal of "no publication that day".
_RETRY_DELAYS_S = (1, 2, 4)

# Currencies for which NBP publishes table A mid-rates and the calculator supports.
# PLN is handled by an early-return in get_rate (rate=1, no API call).
_VALID_NBP_CURRENCIES = frozenset({"USD", "EUR", "CAD", "SEK"})

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


def _fetch_from_api(currency: str, d: date) -> Decimal | None:
    """Fetch rate from NBP API. Returns None on 404 (no publication that day).

    Raises RuntimeError if NBP returns a rate for a different date or currency
    than requested (defensive — guards against silent corruption if NBP API
    ever changes behavior). Transient failures (5xx, network timeout) retry
    with exponential backoff per _RETRY_DELAYS_S; 4xx propagates immediately.
    """
    global _last_request_time

    # Rate limiting: max 1 req/s
    elapsed = time.time() - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    url = _NBP_API.format(currency=currency.upper(), date=d.isoformat())
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "pit-exante/1.0"})

    last_err: Exception | None = None
    for attempt_index, backoff in enumerate((0,) + _RETRY_DELAYS_S):
        if backoff:
            time.sleep(backoff)
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
            if 500 <= e.code < 600:
                last_err = e
                continue
            raise
        except (URLError, socket.timeout) as e:
            last_err = e
            continue

    raise RuntimeError(
        f"NBP API failed after {len(_RETRY_DELAYS_S) + 1} attempts "
        f"for {currency} {d.isoformat()}: {last_err}"
    )


def get_rate(currency: str, transaction_date: date) -> Decimal:
    """Get NBP table A mid-rate for the last published day before transaction_date.

    Per art. 11a ust. 1-2 ustawy o PIT: średni kurs NBP z ostatniego dnia
    roboczego poprzedzającego dzień transakcji. NBP API is the authority for
    which dates have a published rate — 404 means no publication that day
    (weekend/holiday) and we walk back up to 7 days.

    Returns Decimal(1) for PLN. Pre-2002 dates raise immediately.
    """
    cur = currency.upper()
    if cur == "PLN":
        return Decimal("1")
    if cur not in _VALID_NBP_CURRENCIES:
        raise ValueError(
            f"Unsupported currency {currency!r}; "
            f"expected one of {sorted(_VALID_NBP_CURRENCIES)} or PLN"
        )

    _load_cache()

    # Cache key = deterministic "transaction_date - 1 calendar day", independent
    # of which actual date NBP published on. Subsequent runs hit immediately
    # without re-walking the 404 chain. Legacy entries (keyed by previous
    # _previous_business_day convention) are picked up via the step-key check
    # below and back-filled into primary_key.
    primary_key = f"{cur}_{(transaction_date - timedelta(days=1)).isoformat()}"
    if primary_key in _cache:
        return Decimal(_cache[primary_key])

    global _cache_dirty
    d = transaction_date - timedelta(days=1)
    for i in range(_MAX_FALLBACK_DAYS):
        if d.year < _NBP_ARCHIVE_START_YEAR:
            raise RuntimeError(
                f"Date {d} before NBP archive (started {_NBP_ARCHIVE_START_YEAR})"
            )
        # On fallback steps, check legacy cache entries before hitting API.
        # First iteration (i=0) is already covered by primary_key check above.
        if i > 0:
            step_key = f"{cur}_{d.isoformat()}"
            if step_key in _cache:
                rate = Decimal(_cache[step_key])
                _cache[primary_key] = str(rate)
                _cache_dirty = True
                return rate
        rate = _fetch_from_api(cur, d)
        if rate is not None:
            _cache[primary_key] = str(rate)
            _cache_dirty = True
            return rate
        d -= timedelta(days=1)

    raise RuntimeError(
        f"No NBP rate within {_MAX_FALLBACK_DAYS} days back from "
        f"{transaction_date} for {currency}"
    )


def save_cache_if_dirty() -> None:
    """Save cache to disk if any new rates were fetched. Call at end of processing."""
    if _cache_dirty:
        _save_cache()
