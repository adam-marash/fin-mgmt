#!/usr/bin/env python3
"""Check FX conversions in the ledger against market rates in prices.beancount.

Flags any conversion where the implied rate deviates more than a configurable
threshold (default 3%) from the nearest market rate.

Usage:
    python scripts/check_fx_deviations.py                  # default 3%
    python scripts/check_fx_deviations.py --threshold 5    # 5% threshold
    python scripts/check_fx_deviations.py --verbose         # show all, not just deviations
"""

import argparse
import re
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data

LEDGER_DIR = Path(__file__).resolve().parent.parent / "ledger"
MAIN_FILE = LEDGER_DIR / "main.beancount"
PRICES_FILE = LEDGER_DIR / "prices.beancount"


def load_market_rates(prices_path: Path) -> dict[str, list[tuple[date, Decimal]]]:
    """Parse prices.beancount into a dict of currency -> sorted [(date, rate)] pairs.

    Rates are stored as USD per 1 unit of foreign currency.
    """
    rates: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    pattern = re.compile(
        r"^(\d{4}-\d{2}-\d{2})\s+price\s+(\w+)\s+([\d.]+)\s+USD"
    )
    for line in prices_path.read_text().splitlines():
        m = pattern.match(line.strip())
        if m:
            dt = date.fromisoformat(m.group(1))
            ccy = m.group(2)
            rate = Decimal(m.group(3))
            rates[ccy].append((dt, rate))

    # Sort each currency's rates by date for binary search
    for ccy in rates:
        rates[ccy].sort(key=lambda x: x[0])

    return dict(rates)


def lookup_rate(
    rates: dict[str, list[tuple[date, Decimal]]], ccy: str, dt: date
) -> Decimal | None:
    """Find the nearest market rate on or before the given date.

    Returns USD per 1 unit of ccy, or None if no rate is available.
    """
    if ccy not in rates:
        return None
    entries = rates[ccy]
    dates = [e[0] for e in entries]
    idx = bisect_right(dates, dt) - 1
    if idx < 0:
        return None
    return entries[idx][1]


def compute_market_cross_rate(
    rates: dict[str, list[tuple[date, Decimal]]],
    source_ccy: str,
    target_ccy: str,
    dt: date,
) -> Decimal | None:
    """Compute the market rate for source_ccy -> target_ccy using USD as pivot.

    Returns target_ccy per 1 unit of source_ccy.
    If one side is USD, only one lookup is needed.
    """
    if source_ccy == "USD":
        # 1 USD -> target_ccy: need target_ccy/USD = 1 / (USD/target_ccy)
        target_usd = lookup_rate(rates, target_ccy, dt)
        if target_usd is None:
            return None
        return Decimal(1) / target_usd
    elif target_ccy == "USD":
        # source_ccy -> USD directly
        return lookup_rate(rates, source_ccy, dt)
    else:
        # Cross rate via USD pivot
        source_usd = lookup_rate(rates, source_ccy, dt)
        target_usd = lookup_rate(rates, target_ccy, dt)
        if source_usd is None or target_usd is None:
            return None
        # source_ccy/USD / target_ccy/USD = target_ccy per source_ccy
        return source_usd / target_usd


def extract_fx_conversions(entries: list) -> list[dict]:
    """Extract all FX conversions from beancount entries.

    Returns a list of dicts with keys:
        date, narration, filename, lineno,
        source_ccy, target_ccy, amount, implied_rate
    """
    conversions = []
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue
        for posting in entry.postings:
            if posting.price is None:
                continue
            source_ccy = posting.units.currency
            target_ccy = posting.price.currency
            if source_ccy == target_ccy:
                continue
            implied_rate = float(posting.price.number)
            conversions.append({
                "date": entry.date,
                "narration": entry.narration,
                "filename": posting.meta.get("filename", "?"),
                "lineno": posting.meta.get("lineno", 0),
                "source_ccy": source_ccy,
                "target_ccy": target_ccy,
                "amount": float(posting.units.number),
                "implied_rate": implied_rate,
            })
    return conversions


def relative_path(filepath: str) -> str:
    """Convert absolute path to relative from the repo root."""
    try:
        return str(Path(filepath).relative_to(LEDGER_DIR.parent))
    except ValueError:
        return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Check FX conversions against market rates"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=3.0,
        help="Deviation threshold in percent (default: 3.0)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show all conversions, not just deviations",
    )
    args = parser.parse_args()

    if not MAIN_FILE.exists():
        print(f"Error: ledger not found at {MAIN_FILE}", file=sys.stderr)
        sys.exit(2)

    if not PRICES_FILE.exists():
        print(f"Error: prices file not found at {PRICES_FILE}", file=sys.stderr)
        sys.exit(2)

    # Load market rates from prices.beancount
    market_rates = load_market_rates(PRICES_FILE)
    print(f"Loaded market rates for {len(market_rates)} currencies: "
          f"{', '.join(sorted(market_rates.keys()))}")

    # Load ledger
    entries, errors, _ = loader.load_file(str(MAIN_FILE))
    if errors:
        print(f"Warning: {len(errors)} ledger errors", file=sys.stderr)

    # Extract FX conversions
    conversions = extract_fx_conversions(entries)
    print(f"Found {len(conversions)} FX conversions in ledger")
    print(f"Threshold: {args.threshold:.1f}%")
    print()

    deviations = []
    skipped = 0

    for conv in conversions:
        source_ccy = conv["source_ccy"]
        target_ccy = conv["target_ccy"]
        implied = conv["implied_rate"]

        market = compute_market_cross_rate(
            market_rates, source_ccy, target_ccy, conv["date"]
        )
        if market is None:
            skipped += 1
            continue

        market_f = float(market)
        if market_f == 0:
            skipped += 1
            continue

        deviation_pct = abs(implied - market_f) / market_f * 100.0

        record = {
            **conv,
            "market_rate": market_f,
            "deviation_pct": deviation_pct,
        }

        if deviation_pct > args.threshold:
            deviations.append(record)

        if args.verbose:
            flag = "***" if deviation_pct > args.threshold else "   "
            print(
                f"{flag} {conv['date']}  "
                f"{source_ccy}->{target_ccy}  "
                f"implied={implied:.6f}  "
                f"market={market_f:.6f}  "
                f"dev={deviation_pct:+.2f}%  "
                f"{conv['narration'][:50]}"
            )

    # Report deviations
    if deviations:
        if not args.verbose:
            print(f"{'Date':<12} {'Pair':<10} {'Implied':>12} {'Market':>12} "
                  f"{'Dev %':>8}  Description")
            print("-" * 100)

            for d in sorted(deviations, key=lambda x: x["deviation_pct"], reverse=True):
                pair = f"{d['source_ccy']}->{d['target_ccy']}"
                print(
                    f"{d['date']}  {pair:<10} {d['implied_rate']:>12.6f} "
                    f"{d['market_rate']:>12.6f} {d['deviation_pct']:>+7.2f}%  "
                    f"{d['narration'][:50]}"
                )
                print(f"{'':>12} {relative_path(d['filename'])}:{d['lineno']}")

    print()
    print(f"Summary: {len(deviations)} deviation(s) over {args.threshold:.1f}% "
          f"out of {len(conversions)} conversions "
          f"({skipped} skipped - no market rate)")

    sys.exit(1 if deviations else 0)


if __name__ == "__main__":
    main()
