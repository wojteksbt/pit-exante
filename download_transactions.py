#!/usr/bin/env python3
"""Download all transactions from Exante API."""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
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
    # Discover subaccounts from first fetch
    print("Fetching ACC001.001...")
    txns_001 = download_all("ACC001.001")

    print("Fetching ACC001.002...")
    txns_002 = download_all("ACC001.002")

    all_txns = txns_001 + txns_002
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


if __name__ == "__main__":
    main()
