#!/usr/bin/env python3
"""Download all transactions and symbol metadata from Exante API."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from base64 import b64encode
# Load .env manually (no external dependencies)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

API_KEY = os.environ["EXANTE_API_KEY"]
SECRET = os.environ["EXANTE_SECRET"]
BASE_URL = "https://api-live.exante.eu/md/3.0"

auth = b64encode(f"{API_KEY}:{SECRET}".encode()).decode()
HEADERS = {
    "Authorization": f"Basic {auth}",
    "User-Agent": "pit-exante/1.0",
    "Accept": "application/json",
}

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)


def fetch_transactions(account_id: str, offset: int = 0, limit: int = 1000) -> list:
    url = f"{BASE_URL}/transactions?accountId={account_id}&limit={limit}&offset={offset}&order=ASC"
    req = Request(url, headers=HEADERS)
    with urlopen(req) as resp:
        return json.loads(resp.read())


def fetch_symbol_metadata(symbol_id: str) -> dict | None:
    """Fetch symbol metadata from Exante /symbols/{id}. Returns None on 404 (delisted)."""
    url = f"{BASE_URL}/symbols/{symbol_id}"
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def download_all_symbol_metadata(symbol_ids: list[str]) -> tuple[dict, list[str]]:
    """Fetch metadata for all symbols. Returns (found_metadata, missing_404_ids)."""
    found: dict = {}
    missing: list[str] = []
    for sid in symbol_ids:
        meta = fetch_symbol_metadata(sid)
        if meta is None:
            missing.append(sid)
            print(f"  404: {sid}")
        else:
            found[sid] = meta
            print(f"  OK:  {sid}  symbolType={meta.get('symbolType')}")
        time.sleep(0.5)  # rate limit (Exante: 60 req/min on Symbols scope)
    return found, missing


def download_all(account_id: str) -> list:
    all_txns = []
    offset = 0
    limit = 1000
    while True:
        print(f"  Fetching offset={offset}...")
        batch = fetch_transactions(account_id, offset=offset, limit=limit)
        all_txns.extend(batch)
        print(f"  Got {len(batch)} transactions")
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(2)  # respect rate limits
    return all_txns


def main():
    # Subaccounts from EXANTE_SUBACCOUNTS env var, comma-separated.
    # Example: EXANTE_SUBACCOUNTS="ABC1234.001,ABC1234.002"
    # Fallback: single account from EXANTE_ACCOUNT (no .NNN suffix).
    raw = os.environ.get("EXANTE_SUBACCOUNTS", "").strip()
    if raw:
        subaccounts = [s.strip() for s in raw.split(",") if s.strip()]
    else:
        subaccounts = [os.environ["EXANTE_ACCOUNT"]]

    all_txns: list = []
    for sub in subaccounts:
        print(f"Fetching {sub}...")
        all_txns.extend(download_all(sub))

    all_txns.sort(key=lambda t: t["timestamp"])

    out_path = DATA_DIR / "transactions.json"
    with open(out_path, "w") as f:
        json.dump(all_txns, f, indent=2)

    print(f"\nTotal: {len(all_txns)} transactions saved to {out_path}")

    # Summary by operation type
    types = {}
    for t in all_txns:
        op = t["operationType"]
        types[op] = types.get(op, 0) + 1
    print("\nOperation types:")
    for op, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"  {op}: {count}")

    # Date range
    if all_txns:
        first = datetime.fromtimestamp(all_txns[0]["timestamp"] / 1000)
        last = datetime.fromtimestamp(all_txns[-1]["timestamp"] / 1000)
        print(f"\nDate range: {first.date()} to {last.date()}")

    # Symbol metadata — needed for InstrumentKind classification (CFD vs STOCK)
    unique_symbols = sorted({t["symbolId"] for t in all_txns if t.get("symbolId")})
    print(f"\nFetching metadata for {len(unique_symbols)} unique symbols...")
    found, missing = download_all_symbol_metadata(unique_symbols)

    symbols_path = DATA_DIR / "symbols.json"
    with open(symbols_path, "w") as f:
        json.dump(found, f, indent=2)
    print(f"\n{len(found)} symbols saved to {symbols_path}")

    if missing:
        missing_path = DATA_DIR / "symbols_missing.json"
        with open(missing_path, "w") as f:
            json.dump(missing, f, indent=2)
        print(
            f"{len(missing)} symbols returned 404 (delisted/rebrand) — see {missing_path}. "
            f"Add them to config/symbol_overrides.json with manual symbolType."
        )


if __name__ == "__main__":
    main()
