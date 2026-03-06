#!/usr/bin/env python3
"""Generate #fo-sourced ledger entries for FO transactions with no ledger match.

Runs the FO-to-ledger matcher, then creates entries.beancount files for
unmatched FO transactions. Each entry is tagged #fo-sourced to indicate
provenance. Entries are grouped by year and investment.

Usage:
    python scripts/generate_fo_entries.py              # dry run (show what would be created)
    python scripts/generate_fo_entries.py --write       # create entries
    python scripts/generate_fo_entries.py --write --include-unmapped  # include previously unmapped investments
"""

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from beancount import loader
from beancount.core import data as bdata

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = ROOT / "ledger"
LEDGER_FILE = LEDGER_DIR / "main.beancount"
FO_CSV = ROOT / "data" / "2026-03-05-fo-transactions" / "tamar-transactions.csv"
KNOWLEDGE_FILE = ROOT / "knowledge.json"


@dataclass
class FORow:
    date: date
    date_uncertain: bool
    owner: str
    investment: str
    tx_type: str
    ext_type: str
    amount: Decimal
    currency: str
    amount_ils: Decimal
    line_num: int


def load_knowledge():
    with open(KNOWLEDGE_FILE) as f:
        return json.load(f)


def build_fo_to_beancount_map(knowledge):
    mapping = {}
    for _key, inv in knowledge.get("investments", {}).items():
        bc_name = inv.get("beancount_name")
        if not bc_name:
            continue
        for alias in inv.get("aliases", []):
            mapping[alias] = bc_name
        if inv.get("name"):
            mapping[inv["name"]] = bc_name
        if inv.get("hebrew_name"):
            mapping[inv["hebrew_name"]] = bc_name
        for alias in inv.get("csv_aliases", []):
            mapping[alias] = bc_name
    return mapping


def normalize(s):
    s = s.lower().strip()
    s = re.sub(r'\s*-\s*', '-', s)
    s = re.sub(r'\s+', ' ', s)
    return s


def fuzzy_lookup(name, mapping):
    name_n = normalize(name)
    for alias, bc in mapping.items():
        alias_n = normalize(alias)
        if alias_n in name_n or name_n in alias_n:
            return bc
    return None


def load_fo_csv(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            date_str = row["date"].strip()
            amount_str = row["amount"].strip()
            if not date_str or not amount_str:
                continue
            amount_ils_str = row.get("amount_ils", "").strip()
            rows.append(FORow(
                date=date.fromisoformat(date_str),
                date_uncertain=row.get("date_uncertain", "").strip() == "Y",
                owner=row.get("owner", "").strip(),
                investment=row["investment"].strip(),
                tx_type=row.get("tx_type", "").strip(),
                ext_type=row.get("ext_type", "").strip(),
                amount=Decimal(amount_str),
                currency=row.get("currency", "").strip(),
                amount_ils=Decimal(amount_ils_str) if amount_ils_str else Decimal(0),
                line_num=i,
            ))
    return rows


def load_existing_receivable_entries(entries):
    """Extract all receivable postings from ledger for matching."""
    txns = []
    for entry in entries:
        if not isinstance(entry, bdata.Transaction):
            continue
        for posting in entry.postings:
            if posting.account.startswith("Assets:Receivable:"):
                bc_name = posting.account.split(":")[-1]
                txns.append({
                    "date": entry.date,
                    "bc_name": bc_name,
                    "amount": abs(posting.units.number),
                    "currency": posting.units.currency,
                })
    return txns


def is_matched(fo, existing, tolerance_days=5):
    """Check if an FO row already has a matching ledger entry."""
    max_tol = 45 if fo.date_uncertain else tolerance_days
    for e in existing:
        if e["bc_name"] != fo._bc_name:
            continue
        day_diff = abs((fo.date - e["date"]).days)
        if day_diff > max_tol:
            continue
        # Same currency direct match
        if fo.currency == e["currency"]:
            pct = abs(fo.amount - e["amount"]) / fo.amount * 100 if fo.amount else 100
            if pct < 5:
                return True
        # Cross-currency (FO original vs ledger ILS)
        if e["currency"] == "ILS" and fo.amount_ils > 0:
            pct = abs(fo.amount_ils - e["amount"]) / fo.amount_ils * 100
            if pct < 5:
                return True
    return False


def generate_entry(fo, bc_name):
    """Generate a beancount entry for an FO transaction."""
    amt = fo.amount
    ccy = fo.currency

    if fo.tx_type == "deposit":
        # Capital call: money goes to investment
        # Positive receivable = capital deployed, bank debit expected to clear it
        narration = f"{bc_name} - capital call (FO-sourced)"
        lines = [
            f'{fo.date} * "{narration}" #fo-sourced #provisional',
            f'  source: "{FO_CSV.relative_to(ROOT)}"',
            f'  fo-line: "{fo.line_num}"',
            f'  Assets:Receivable:{bc_name}  {amt} {ccy}',
            f'  Assets:Suspense  -{amt} {ccy}',
        ]
    elif fo.tx_type in ("yield_withdrawal", "withdrawal"):
        if fo.tx_type == "yield_withdrawal":
            narration = f"{bc_name} - distribution (FO-sourced)"
        else:
            narration = f"{bc_name} - capital return (FO-sourced)"
        lines = [
            f'{fo.date} * "{narration}" #fo-sourced #provisional',
            f'  source: "{FO_CSV.relative_to(ROOT)}"',
            f'  fo-line: "{fo.line_num}"',
            f'  Assets:Receivable:{bc_name}  {amt} {ccy}',
            f'  Income:Distribution:{bc_name}:Unclassified  -{amt} {ccy}',
        ]
    else:
        return None

    return "\n".join(lines)


def short_hash(s):
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def main():
    parser = argparse.ArgumentParser(
        description="Generate #fo-sourced ledger entries for unmatched FO transactions"
    )
    parser.add_argument(
        "--write", action="store_true",
        help="Actually create entries (default is dry run)",
    )
    parser.add_argument(
        "--include-unmapped", action="store_true",
        help="Include investments that were previously unmapped (now have beancount names)",
    )
    args = parser.parse_args()

    knowledge = load_knowledge()
    fo_to_bc = build_fo_to_beancount_map(knowledge)
    fo_rows = load_fo_csv(FO_CSV)

    print(f"Loaded {len(fo_rows)} FO transactions")

    # Load ledger for matching
    entries, errors, _ = loader.load_file(str(LEDGER_FILE))
    if errors:
        print(f"WARNING: {len(errors)} beancount errors", file=sys.stderr)

    existing = load_existing_receivable_entries(entries)
    print(f"Loaded {len(existing)} existing receivable postings")
    print()

    # Resolve FO names to beancount names and find unmatched
    unmatched = []
    matched_count = 0
    unmapped_count = 0

    for fo in fo_rows:
        bc_name = fo_to_bc.get(fo.investment)
        if bc_name is None:
            bc_name = fuzzy_lookup(fo.investment, fo_to_bc)
        if bc_name is None:
            unmapped_count += 1
            continue

        fo._bc_name = bc_name

        if is_matched(fo, existing):
            matched_count += 1
        else:
            unmatched.append((fo, bc_name))

    print(f"Matched (already in ledger): {matched_count}")
    print(f"Unmapped (no beancount name): {unmapped_count}")
    print(f"Unmatched (need entries):     {len(unmatched)}")
    print()

    if not unmatched:
        print("Nothing to generate.")
        return

    # Group by year and investment for folder creation
    by_year_inv = defaultdict(list)
    for fo, bc_name in unmatched:
        year = str(fo.date.year)
        by_year_inv[(year, bc_name)].append((fo, bc_name))

    # Generate entries
    total_entries = 0
    for (year, bc_name), items in sorted(by_year_inv.items()):
        # Create one folder per year/investment combination
        folder_name = f"2026-03-07-{bc_name.lower()}-fo-sourced-{short_hash(f'{year}-{bc_name}')}"
        folder_path = LEDGER_DIR / year / folder_name

        entry_blocks = []
        for fo, bc in items:
            entry = generate_entry(fo, bc)
            if entry:
                entry_blocks.append(entry)
                total_entries += 1

        if not entry_blocks:
            continue

        content = (
            f"; FO-sourced entries for {bc_name} ({year})\n"
            f"; Source: {FO_CSV.relative_to(ROOT)}\n"
            f"; Generated by scripts/generate_fo_entries.py\n"
            f"; These entries are provisional - replace with primary source when available\n\n"
            + "\n\n".join(entry_blocks)
            + "\n"
        )

        if args.write:
            folder_path.mkdir(parents=True, exist_ok=True)
            entries_file = folder_path / "entries.beancount"
            entries_file.write_text(content)
            print(f"  WROTE {entries_file.relative_to(ROOT)} ({len(entry_blocks)} entries)")
        else:
            print(f"  {folder_path.relative_to(ROOT)}/ ({len(entry_blocks)} entries)")
            for fo, bc in items:
                direction = "OUT" if fo.tx_type == "deposit" else "IN"
                print(f"    {fo.date} {direction} {fo.amount:>12,.2f} {fo.currency}  {fo.tx_type}")

    print()
    print(f"Total: {total_entries} entries across {len(by_year_inv)} folders")
    if not args.write:
        print("\nDry run - use --write to create entries")


if __name__ == "__main__":
    main()
