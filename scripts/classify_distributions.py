#!/usr/bin/env python3
"""Classify distribution entries as Yield or Capital-Return based on FO CSV.

Usage:
    python scripts/classify_distributions.py [--dry-run] [--investment NAME]

Reads FO CSV, matches to ledger entries by investment+amount+date,
replaces :Unclassified with :Yield or :Capital-Return, adds classification-source metadata,
removes #provisional tag from classified entries.
"""

import csv
import os
import re
import sys
from datetime import datetime

FO_CSV = "data/2026-03-05-fo-transactions/tamar-transactions.csv"
LEDGER_DIR = "ledger"

# FO Hebrew/English name -> Beancount name
FO_MAP = {
    "אלקטרה USA 2": "Electra-MIF-II",
    "דיור מוגן באטלנטה בוליגו": "Boligo-1",
    "דיור מוגן באטלנטה בוליגו 2": "Boligo-2",
    "מגורים Multi-Family ו Single-Family בניו הייבן - נץ": "Netz",
    "ריאליטי גרמניה - רכישת פורטפוליו של סופרמרקטים": "Reality-Germany",
    "עסקת הרטפורד קונטיקט - התחדשות עירונית": "Hartford-CT",
    "דאטה סנטר LA": "Data-Center-LA",
    "Liquidity Capital II, L.P": "Liquidity-Capital",
    "Pollen Street Credit Fund III-USD": "Pollen-Street",
    "Viola Credit ALF III": "Viola-Credit",
    "קאליבר- Caliber": "Caliber",
    "ISF - III": "ISF-III",
    "Faro-Point FRG-X": "FRG-X",
    "KDC Media Fund - Stardom Ventures - Stardom Ventures": "KDC-Stardom",
    "Coller Capital VIII": "Coller-Capital",
    "כרמל קרדיט": "Carmel-Credit",
    "אלקטרה בי. טי. אר 1 - Electra BTR": "Electra-BTR",
    "מגורים Multi-Family בפילדלפיה - גלפנד": "Pelham-Park",
    "מרילנד, Gatewater Landing, גלפנד": "Gatewater",
    "Serviced Apartments, Vienna": "Vienna-Apartment",
}

SKIP = {"Impact-Debt", "IBI-Portfolio", "Yalin-Portfolio"}


def load_fo_distributions():
    """Load FO CSV distributions into lookup by beancount name."""
    fo_data = {}
    with open(FO_CSV) as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if row[6] not in ("yield_withdrawal", "withdrawal"):
                continue
            bc_name = FO_MAP.get(row[3])
            if not bc_name or bc_name in SKIP:
                continue
            fo_data.setdefault(bc_name, []).append({
                "date": row[0],
                "amount": float(row[8]),
                "fo_type": row[6],
                "classification": "Yield" if row[6] == "yield_withdrawal" else "Capital-Return",
                "matched": False,
            })
    return fo_data


def find_unclassified_files(investment):
    """Find all ledger entry files with :Unclassified for a given investment."""
    results = []
    for root, _, files in os.walk(LEDGER_DIR):
        for fname in files:
            if fname != "entries.beancount":
                continue
            fpath = os.path.join(root, fname)
            with open(fpath) as f:
                content = f.read()
            if f"Income:Distribution:{investment}:Unclassified" in content:
                results.append(fpath)
    return sorted(results)


def match_fo_entry(fo_entries, ledger_date, ledger_amount, tolerance_days=5, tolerance_pct=0.05):
    """Find matching FO entry by date proximity and amount similarity."""
    ld = datetime.strptime(ledger_date, "%Y-%m-%d")
    best_match = None
    best_score = float("inf")
    for fo in fo_entries:
        if fo["matched"]:
            continue
        fd = datetime.strptime(fo["date"], "%Y-%m-%d")
        day_diff = abs((ld - fd).days)
        if day_diff > tolerance_days:
            continue
        amt_diff = abs(fo["amount"] - ledger_amount) / max(fo["amount"], 0.01)
        if amt_diff > tolerance_pct:
            continue
        score = day_diff + amt_diff * 100
        if score < best_score:
            best_score = score
            best_match = fo
    return best_match


def classify_file(fpath, investment, fo_entries, dry_run=True):
    """Classify entries in a single file."""
    with open(fpath) as f:
        content = f.read()

    original = content
    unclassified_pattern = f"Income:Distribution:{investment}:Unclassified"
    if unclassified_pattern not in content:
        return []

    has_capital_return = any(e["classification"] == "Capital-Return" for e in fo_entries)

    # First pass: determine classification for each entry
    date_re = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+[*!]")
    amount_re = re.compile(
        rf"Income:Distribution:{re.escape(investment)}:Unclassified\s+(-?[\d,]+\.?\d*)\s+(\w+)"
    )

    # Collect all entries with their dates and amounts
    entries_info = []
    current_date = None
    for line in content.split("\n"):
        dm = date_re.match(line)
        if dm:
            current_date = dm.group(1)
        am = amount_re.search(line)
        if am and current_date:
            amount = abs(float(am.group(1).replace(",", "")))
            currency = am.group(2)
            entries_info.append({"date": current_date, "amount": amount, "currency": currency})

    # Match each entry to FO data
    classifications = []
    for entry in entries_info:
        if not has_capital_return:
            # All yield - classify directly
            classifications.append(("Yield", "yield_withdrawal"))
        else:
            match = match_fo_entry(fo_entries, entry["date"], entry["amount"])
            if not match:
                match = match_fo_entry(fo_entries, entry["date"], entry["amount"],
                                       tolerance_days=15, tolerance_pct=0.15)
            if match:
                classifications.append((match["classification"], match["fo_type"]))
                match["matched"] = True
            else:
                classifications.append(None)  # Unmatched

    # Second pass: rewrite the file line by line
    lines = content.split("\n")
    new_lines = []
    entry_idx = 0
    in_txn = False
    source_added = False

    results = []

    for line in lines:
        # Detect transaction header
        if date_re.match(line):
            in_txn = True
            source_added = False
            # Remove #provisional if this entry will be classified
            if entry_idx < len(classifications) and classifications[entry_idx] is not None:
                line = line.replace(" #provisional", "")
            new_lines.append(line)
            continue

        # After source: metadata, add classification-source
        if in_txn and not source_added and line.strip().startswith("source:"):
            new_lines.append(line)
            if entry_idx < len(classifications) and classifications[entry_idx] is not None:
                cls, fo_type = classifications[entry_idx]
                indent = "  "
                new_lines.append(f'{indent}classification-source: "{FO_CSV} ({fo_type})"')
                source_added = True
            continue

        # Also handle fo-line: metadata (add classification-source after it if source: already passed)
        if in_txn and not source_added and line.strip().startswith("fo-line:"):
            new_lines.append(line)
            if entry_idx < len(classifications) and classifications[entry_idx] is not None:
                cls, fo_type = classifications[entry_idx]
                new_lines.append(f'  classification-source: "{FO_CSV} ({fo_type})"')
                source_added = True
            continue

        # Replace :Unclassified on income line
        if unclassified_pattern in line:
            if entry_idx < len(classifications) and classifications[entry_idx] is not None:
                cls, fo_type = classifications[entry_idx]
                line = line.replace(f":{investment}:Unclassified", f":{investment}:{cls}")
                results.append((cls, fpath, entries_info[entry_idx]["date"]))
            else:
                results.append(("UNMATCHED", fpath, entries_info[entry_idx]["date"] if entry_idx < len(entries_info) else "?"))

            # If we haven't added classification-source yet (no source: line), add before income line
            if not source_added and entry_idx < len(classifications) and classifications[entry_idx] is not None:
                cls2, fo_type2 = classifications[entry_idx]
                new_lines.append(f'  classification-source: "{FO_CSV} ({fo_type2})"')
                source_added = True

            entry_idx += 1
            in_txn = False
            source_added = False

        new_lines.append(line)

    new_content = "\n".join(new_lines)
    if new_content != original and not dry_run:
        with open(fpath, "w") as f:
            f.write(new_content)

    return results


def main():
    dry_run = "--dry-run" in sys.argv
    filter_inv = None
    for i, arg in enumerate(sys.argv):
        if arg == "--investment" and i + 1 < len(sys.argv):
            filter_inv = sys.argv[i + 1]

    fo_data = load_fo_distributions()

    total_classified = 0
    total_unmatched = 0
    new_accounts = set()

    for investment in sorted(fo_data):
        if filter_inv and investment != filter_inv:
            continue

        fo_entries = fo_data[investment]
        files = find_unclassified_files(investment)
        if not files:
            continue

        has_yield = any(e["classification"] == "Yield" for e in fo_entries)
        has_cr = any(e["classification"] == "Capital-Return" for e in fo_entries)
        if has_yield:
            new_accounts.add(f"Income:Distribution:{investment}:Yield")
        if has_cr:
            new_accounts.add(f"Income:Distribution:{investment}:Capital-Return")

        print(f"\n{'='*60}")
        print(f"  {investment} ({len(files)} files, {len(fo_entries)} FO entries)")
        print(f"{'='*60}")

        for e in fo_entries:
            e["matched"] = False

        for fpath in files:
            results = classify_file(fpath, investment, fo_entries, dry_run=dry_run)
            for cls, fp, date in results:
                if cls == "UNMATCHED":
                    print(f"  UNMATCHED: {fp} date={date}")
                    total_unmatched += 1
                else:
                    print(f"  {cls:15s} {fp}")
                    total_classified += 1

    print(f"\n{'='*60}")
    print(f"SUMMARY: {total_classified} classified, {total_unmatched} unmatched")
    if dry_run:
        print("DRY RUN - no files modified")

    # Print accounts that need to be opened
    existing = set()
    with open(os.path.join(LEDGER_DIR, "accounts.beancount")) as f:
        for line in f:
            m = re.search(r"open\s+(Income:Distribution:\S+)", line)
            if m:
                existing.add(m.group(1))

    needed = new_accounts - existing
    if needed:
        print(f"\nNew accounts to open ({len(needed)}):")
        for a in sorted(needed):
            print(f"  {a}")


if __name__ == "__main__":
    main()
