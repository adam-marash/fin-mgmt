#!/usr/bin/env python3
"""Fetch FX rates from Frankfurter API and append to prices.beancount.

Usage:
    python scripts/fetch_fx.py 2025-12-22          # single date
    python scripts/fetch_fx.py 2025-12-22 2025-12-24  # multiple dates
    python scripts/fetch_fx.py --from 2025-01-01 --to 2025-12-31  # date range

Fetches FX rates against USD and merges into ledger/prices.beancount,
keeping all entries sorted by date. Skips dates that already have entries.
"""

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError
import json

LEDGER_DIR = Path(__file__).resolve().parent.parent / "ledger"
PRICES_FILE = LEDGER_DIR / "prices.beancount"

# Frankfurter uses EUR as base. We want USD-denominated prices.
PAIRS = ["EUR", "GBP", "ILS", "JPY", "PLN", "CHF", "AUD"]
BASE = "USD"


def existing_entries(prices_path: Path) -> set[tuple[str, str]]:
    """Parse prices.beancount and return set of (date, currency) pairs already present."""
    entries = set()
    if prices_path.exists():
        for line in prices_path.read_text().splitlines():
            m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+price\s+(\w+)\s+", line)
            if m:
                entries.add((m.group(1), m.group(2)))
    return entries


def fetch_rates(dt: str) -> dict[str, float] | None:
    """Fetch rates for a single date from Frankfurter API.

    Returns dict like {"EUR": 1.0423, "GBP": 1.2534, "ILS": 0.2741}
    as USD per 1 unit of each currency.
    """
    symbols = ",".join(PAIRS)
    url = f"https://api.frankfurter.app/{dt}?base={BASE}&symbols={symbols}"
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req) as resp:
            data = json.loads(resp.read())
    except (URLError, json.JSONDecodeError) as e:
        print(f"  Warning: failed to fetch {dt}: {e}", file=sys.stderr)
        return None

    if "rates" not in data:
        print(f"  Warning: no rates in response for {dt}", file=sys.stderr)
        return None

    # Frankfurter returns "1 USD = X EUR" but we want "1 EUR = Y USD"
    # So we invert: 1/rate
    result = {}
    for ccy, rate in data["rates"].items():
        if ccy in PAIRS and rate > 0:
            result[ccy] = round(1.0 / rate, 6)
    return result


def format_price_lines(dt: str, rates: dict[str, float]) -> list[str]:
    """Format beancount price directives."""
    lines = []
    for ccy in PAIRS:
        if ccy in rates:
            lines.append(f"{dt} price {ccy} {rates[ccy]:.6f} USD")
    return lines


def date_range(start: str, end: str) -> list[str]:
    """Generate list of date strings between start and end inclusive."""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    dates = []
    while s <= e:
        dates.append(s.isoformat())
        s += timedelta(days=1)
    return dates


def main():
    parser = argparse.ArgumentParser(description="Fetch FX rates into prices.beancount")
    parser.add_argument("dates", nargs="*", help="Specific dates (YYYY-MM-DD)")
    parser.add_argument("--from", dest="from_date", help="Range start date")
    parser.add_argument("--to", dest="to_date", help="Range end date")
    args = parser.parse_args()

    if args.from_date and args.to_date:
        target_dates = date_range(args.from_date, args.to_date)
    elif args.dates:
        target_dates = args.dates
    else:
        parser.error("Provide specific dates or --from/--to range")

    already = existing_entries(PRICES_FILE)
    # A date needs fetching if any of the target currencies are missing
    already_dates = {d for d, _ in already}
    dates_complete = {d for d in already_dates
                      if all((d, ccy) in already for ccy in PAIRS)}
    to_fetch = [d for d in target_dates if d not in dates_complete]

    if not to_fetch:
        print("All requested dates already in prices.beancount")
        return

    skipped = len(target_dates) - len(to_fetch)
    print(f"Fetching {len(to_fetch)} dates ({skipped} already complete)...")

    new_lines = []
    for dt in sorted(to_fetch):
        rates = fetch_rates(dt)
        if rates:
            # Filter out currencies we already have for this date
            new_rates = {c: r for c, r in rates.items()
                         if (dt, c) not in already}
            if new_rates:
                lines = format_price_lines(dt, new_rates)
                new_lines.extend(lines)
                print(f"  {dt}: {', '.join(f'{c}={r:.4f}' for c, r in new_rates.items())}")

    if not new_lines:
        print("No new rates fetched.")
        return

    # Merge new entries into file, sorted by date then currency
    header_lines = []
    entry_lines = []
    if PRICES_FILE.exists():
        for line in PRICES_FILE.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith(";") or (not entry_lines and stripped == ""):
                header_lines.append(line)
            elif stripped:
                entry_lines.append(stripped)

    entry_lines.extend(new_lines)

    # Deduplicate by (date, currency), keeping last
    seen: dict[tuple[str, str], str] = {}
    for entry in entry_lines:
        m = re.match(r"(\d{4}-\d{2}-\d{2}) price (\w+) ", entry)
        if m:
            seen[(m.group(1), m.group(2))] = entry

    sorted_entries = sorted(seen.values(), key=lambda e: e[:14])  # date + currency

    # Write with blank lines between date groups
    with open(PRICES_FILE, "w") as f:
        f.write("\n".join(header_lines) + "\n")
        prev_date = None
        for entry in sorted_entries:
            cur_date = entry[:10]
            if prev_date and cur_date != prev_date:
                f.write("\n")
            f.write(entry + "\n")
            prev_date = cur_date
        f.write("\n")

    print(f"\nWrote {len(sorted_entries)} price directives to {PRICES_FILE.name} (sorted)")


if __name__ == "__main__":
    main()
