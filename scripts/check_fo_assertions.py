#!/usr/bin/env python3
"""Cross-check FO (family office) transaction data against ledger entries.

Loads the FO normalized CSV and beancount ledger, then matches each FO
transaction to ledger entries by investment name, date (with tolerance),
amount, and currency. Reports matched, FO-only, and ledger-only items.

Usage:
    python scripts/check_fo_assertions.py
    python scripts/check_fo_assertions.py --tolerance 7
    python scripts/check_fo_assertions.py --fo-csv path/to/file.csv
"""

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

# Beancount v3
from beancount import loader
from beancount.core import data as bdata

ROOT = Path(__file__).resolve().parent.parent
LEDGER_FILE = ROOT / "ledger" / "main.beancount"
FO_CSV = ROOT / "data" / "2026-03-05-fo-transactions" / "tamar-transactions.csv"
KNOWLEDGE_FILE = ROOT / "knowledge.json"

# Accounts that represent FO-managed investment flows.
# We match on these account fragments to find ledger entries relevant to FO.
INVESTMENT_ACCOUNT_PREFIXES = (
    "Assets:Receivable:",
    "Assets:Holdings:",
    "Assets:Investments:",
)

# Accounts whose postings carry the investment amount for matching.
# We look for the posting to the receivable/investment account.
MATCH_ACCOUNT_PREFIXES = (
    "Assets:Receivable:",
)

# Bank account postings carry the settlement amount.
BANK_ACCOUNT_PREFIXES = (
    "Assets:Banks:",
)


@dataclass
class FORow:
    """A single FO transaction row."""
    date: date
    date_uncertain: bool
    owner: str
    investment: str
    tx_type: str      # deposit, withdrawal, yield_withdrawal
    ext_type: str
    amount: Decimal
    currency: str
    amount_ils: Decimal
    line_num: int
    matched: bool = False
    match_info: str = ""


@dataclass
class LedgerTxn:
    """A ledger transaction relevant to FO-managed investments."""
    date: date
    narration: str
    beancount_name: str   # e.g. "Electra-MIF-II" from account name
    amount: Decimal       # absolute amount on the receivable posting
    currency: str
    is_inflow: bool       # True if money flows to investor (distribution/withdrawal)
    filename: str
    matched: bool = False
    match_info: str = ""


def load_knowledge() -> dict:
    """Load knowledge.json and build FO-name-to-beancount-name mapping."""
    with open(KNOWLEDGE_FILE) as f:
        knowledge = json.load(f)
    return knowledge


def build_fo_to_beancount_map(knowledge: dict) -> dict[str, str]:
    """Build a mapping from FO investment name to beancount account name.

    Uses the aliases and Hebrew names from knowledge.json investments.
    """
    mapping = {}
    investments = knowledge.get("investments", {})

    for _key, inv in investments.items():
        bc_name = inv.get("beancount_name")
        if not bc_name:
            continue

        # Map each alias to the beancount name
        for alias in inv.get("aliases", []):
            mapping[alias] = bc_name

        # Map the canonical name too
        if inv.get("name"):
            mapping[inv["name"]] = bc_name

        # Map Hebrew name if present
        if inv.get("hebrew_name"):
            mapping[inv["hebrew_name"]] = bc_name

        # Map csv_aliases if present
        for alias in inv.get("csv_aliases", []):
            mapping[alias] = bc_name

    return mapping


def normalize_fo_investment_name(raw: str) -> str:
    """Strip whitespace and normalize FO investment names."""
    return raw.strip()


def load_fo_csv(path: Path) -> list[FORow]:
    """Load and parse the FO transaction CSV."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):  # line 2 is first data row
            date_str = row["date"].strip()
            if not date_str:
                continue

            amount_str = row["amount"].strip()
            if not amount_str:
                continue

            amount_ils_str = row.get("amount_ils", "").strip()

            rows.append(FORow(
                date=date.fromisoformat(date_str),
                date_uncertain=row.get("date_uncertain", "").strip() == "Y",
                owner=row.get("owner", "").strip(),
                investment=normalize_fo_investment_name(row["investment"]),
                tx_type=row.get("tx_type", "").strip(),
                ext_type=row.get("ext_type", "").strip(),
                amount=Decimal(amount_str),
                currency=row.get("currency", "").strip(),
                amount_ils=Decimal(amount_ils_str) if amount_ils_str else Decimal(0),
                line_num=i,
            ))
    return rows


def load_ledger_txns() -> list[LedgerTxn]:
    """Load beancount ledger and extract transactions touching investment accounts."""
    entries, errors, _options = loader.load_file(str(LEDGER_FILE))
    if errors:
        print(f"WARNING: {len(errors)} beancount errors found", file=sys.stderr)

    txns = []
    for entry in entries:
        if not isinstance(entry, bdata.Transaction):
            continue

        # Find postings to receivable/investment accounts
        for posting in entry.postings:
            account = posting.account
            if not any(account.startswith(pfx) for pfx in MATCH_ACCOUNT_PREFIXES):
                continue

            # Extract the beancount investment name from account
            # e.g. "Assets:Receivable:Electra-MIF-II" -> "Electra-MIF-II"
            parts = account.split(":")
            bc_name = parts[-1] if len(parts) >= 3 else account

            units = posting.units
            amount = units.number
            currency = units.currency

            # Determine direction: negative receivable = bank credit received
            # positive receivable = announcement/distribution booked
            is_inflow = amount > 0  # positive on receivable = distribution expected

            meta = entry.meta or {}
            filename = meta.get("filename", "")

            txns.append(LedgerTxn(
                date=entry.date,
                narration=entry.narration or "",
                beancount_name=bc_name,
                amount=abs(amount),
                currency=currency,
                is_inflow=is_inflow,
                filename=filename,
            ))

    return txns


def _score_match(
    fo: FORow, lt: LedgerTxn, tolerance_days: int,
) -> tuple[float, int, bool] | None:
    """Score a potential FO-to-ledger match.

    Returns (amount_pct_diff, day_diff, is_cross_ccy) or None if no match.
    Lower score is better.
    """
    day_diff = abs((fo.date - lt.date).days)
    max_tolerance = 45 if fo.date_uncertain else tolerance_days
    if day_diff > max_tolerance:
        return None

    if fo.amount == 0 or lt.amount == 0:
        return None

    # Same-currency match: compare amounts directly
    if fo.currency == lt.currency:
        amount_diff = abs(fo.amount - lt.amount)
        amount_pct = float(amount_diff / fo.amount) * 100
        return (amount_pct, day_diff, False)

    # Cross-currency match: FO records in original currency (USD/EUR) but
    # ledger records in settlement currency (ILS). Try matching FO's
    # amount_ils against the ledger ILS amount.
    if lt.currency == "ILS" and fo.amount_ils > 0:
        amount_diff = abs(fo.amount_ils - lt.amount)
        amount_pct = float(amount_diff / fo.amount_ils) * 100
        # Only accept cross-currency if reasonably close (within 5%)
        if amount_pct <= 5:
            return (amount_pct, day_diff, True)

    return None


def match_fo_to_ledger(
    fo_rows: list[FORow],
    ledger_txns: list[LedgerTxn],
    fo_to_bc: dict[str, str],
    tolerance_days: int = 5,
) -> tuple[list[dict], list[FORow], list[LedgerTxn]]:
    """Match FO rows to ledger transactions.

    Strategy:
    - Each FO transaction is matched to at most one ledger posting.
    - The ledger often has paired entries (announcement = positive receivable,
      bank credit = negative receivable). We prefer matching the positive
      (announcement) side since it's closer to the FO's record, but will
      match either.
    - For ILS-settled investments, the FO records amounts in original currency
      (USD/EUR) while the ledger uses ILS. We cross-match using the FO's
      amount_ils field.

    Returns (matched, fo_only, ledger_only).
    """
    matched = []
    # Build index of ledger txns by beancount_name for lookup
    # Include all currencies so cross-currency matching works
    ledger_index: dict[str, list[LedgerTxn]] = defaultdict(list)
    for lt in ledger_txns:
        ledger_index[lt.beancount_name].append(lt)

    for fo in fo_rows:
        bc_name = fo_to_bc.get(fo.investment)
        if bc_name is None:
            bc_name = _fuzzy_lookup(fo.investment, fo_to_bc)

        if bc_name is None:
            continue  # Will show up as FO-only

        candidates = ledger_index.get(bc_name, [])

        best_match = None
        best_score = None

        for lt in candidates:
            if lt.matched:
                continue

            result = _score_match(fo, lt, tolerance_days)
            if result is None:
                continue

            amount_pct, day_diff, is_cross = result
            # Score tuple: prefer same-currency, then low amount diff, then close date
            score = (is_cross, amount_pct, day_diff)

            if best_match is None or score < best_score:
                best_match = lt
                best_score = score

        if best_match is not None:
            result = _score_match(fo, best_match, tolerance_days)
            amount_pct, day_diff, is_cross = result

            fo.matched = True
            best_match.matched = True

            discrepancy = ""
            if is_cross:
                amount_diff = abs(fo.amount_ils - best_match.amount)
                if amount_pct > 0.01:
                    discrepancy = (
                        f"ILS amount diff: {amount_diff:.2f} ILS "
                        f"({amount_pct:.1f}%)"
                    )
                cross_note = (
                    f"cross-ccy: FO {fo.amount:.2f} {fo.currency} "
                    f"matched to {best_match.amount:.2f} ILS"
                )
                discrepancy = (
                    f"{discrepancy}, {cross_note}" if discrepancy else cross_note
                )
            else:
                amount_diff = abs(fo.amount - best_match.amount)
                if amount_pct > 0.01:
                    discrepancy = (
                        f"amount diff: {amount_diff:.2f} {fo.currency} "
                        f"({amount_pct:.1f}%)"
                    )

            if day_diff > 0:
                date_note = f"date diff: {day_diff}d"
                discrepancy = (
                    f"{discrepancy}, {date_note}" if discrepancy else date_note
                )

            matched.append({
                "fo": fo,
                "ledger": best_match,
                "discrepancy": discrepancy,
            })

    fo_only = [fo for fo in fo_rows if not fo.matched]
    ledger_only = [lt for lt in ledger_txns if not lt.matched]

    return matched, fo_only, ledger_only


def _normalize_for_fuzzy(s: str) -> str:
    """Normalize a string for fuzzy comparison: lowercase, collapse whitespace
    around punctuation, strip."""
    s = s.lower().strip()
    # Collapse whitespace around dashes: "III - USD" -> "iii-usd"
    s = re.sub(r'\s*-\s*', '-', s)
    # Collapse multiple spaces
    s = re.sub(r'\s+', ' ', s)
    return s


def _fuzzy_lookup(investment_name: str, fo_to_bc: dict[str, str]) -> str | None:
    """Try to find a beancount name via substring matching."""
    name_norm = _normalize_for_fuzzy(investment_name)
    for alias, bc_name in fo_to_bc.items():
        alias_norm = _normalize_for_fuzzy(alias)
        if alias_norm in name_norm or name_norm in alias_norm:
            return bc_name
    return None


def get_fo_managed_bc_names(
    knowledge: dict, fo_to_bc: dict[str, str],
) -> set[str]:
    """Return set of beancount names for investments managed by FO.

    Includes both investments explicitly tagged with family_office=the-service
    in knowledge.json AND any investment that appears in the FO CSV
    (determined by the fo_to_bc mapping).
    """
    names = set()
    investments = knowledge.get("investments", {})
    for _key, inv in investments.items():
        if inv.get("family_office") == "the-service" and inv.get("beancount_name"):
            names.add(inv["beancount_name"])
    # Also include all beancount names reachable from FO CSV aliases
    names.update(fo_to_bc.values())
    return names


def format_amount(amount: Decimal, currency: str) -> str:
    """Format amount with comma separators."""
    return f"{amount:,.2f} {currency}"


def main():
    parser = argparse.ArgumentParser(
        description="Cross-check FO transactions against beancount ledger."
    )
    parser.add_argument(
        "--fo-csv", type=Path, default=FO_CSV,
        help="Path to FO transaction CSV (default: %(default)s)",
    )
    parser.add_argument(
        "--tolerance", type=int, default=5,
        help="Date tolerance in days for matching (default: %(default)s)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show all matched entries (not just those with discrepancies)",
    )
    args = parser.parse_args()

    # Load data
    print("Loading knowledge.json ...")
    knowledge = load_knowledge()
    fo_to_bc = build_fo_to_beancount_map(knowledge)
    fo_managed = get_fo_managed_bc_names(knowledge, fo_to_bc)

    print(f"  {len(fo_to_bc)} FO name aliases mapped to {len(set(fo_to_bc.values()))} beancount accounts")
    print(f"  {len(fo_managed)} FO-managed investments identified")

    print(f"Loading FO CSV from {args.fo_csv} ...")
    fo_rows = load_fo_csv(args.fo_csv)
    print(f"  {len(fo_rows)} FO transactions loaded")

    print(f"Loading ledger from {LEDGER_FILE} ...")
    ledger_txns = load_ledger_txns()
    print(f"  {len(ledger_txns)} ledger postings to receivable accounts loaded")
    print()

    # Filter ledger to FO-managed investments only
    fo_ledger_txns = [lt for lt in ledger_txns if lt.beancount_name in fo_managed]
    print(f"  {len(fo_ledger_txns)} ledger postings for FO-managed investments")
    print()

    # Run matching
    matched, fo_only, ledger_only = match_fo_to_ledger(
        fo_rows, fo_ledger_txns, fo_to_bc, args.tolerance,
    )

    # Filter ledger-only to FO-managed investments
    ledger_only_fo = [lt for lt in ledger_only if lt.beancount_name in fo_managed]

    # Report matched
    matched_with_disc = [m for m in matched if m["discrepancy"]]
    print("=" * 80)
    print(f"MATCHED: {len(matched)} FO transactions matched to ledger entries")
    if matched_with_disc:
        print(f"  ({len(matched_with_disc)} with discrepancies)")
    print("=" * 80)

    if args.verbose:
        for m in matched:
            fo = m["fo"]
            lt = m["ledger"]
            disc = m["discrepancy"]
            marker = " [!]" if disc else ""
            print(
                f"  {fo.date}  {fo.investment[:40]:<40s}  "
                f"{format_amount(fo.amount, fo.currency):>20s}  "
                f"{fo.tx_type:<18s}{marker}"
            )
            if disc:
                print(f"           -> {disc}")
    elif matched_with_disc:
        print()
        for m in matched_with_disc:
            fo = m["fo"]
            lt = m["ledger"]
            disc = m["discrepancy"]
            print(
                f"  FO line {fo.line_num}: {fo.date}  {fo.investment[:40]:<40s}  "
                f"{format_amount(fo.amount, fo.currency):>20s}  {fo.tx_type}"
            )
            print(
                f"   Ledger: {lt.date}  {lt.narration[:50]:<50s}  "
                f"{format_amount(lt.amount, lt.currency):>20s}"
            )
            print(f"   Discrepancy: {disc}")
            print()

    # Report FO-only
    # Separate unmapped (no beancount name) from unmatched
    unmapped_fo = []
    unmatched_fo = []
    for fo in fo_only:
        bc_name = fo_to_bc.get(fo.investment)
        if bc_name is None:
            bc_name = _fuzzy_lookup(fo.investment, fo_to_bc)
        if bc_name is None:
            unmapped_fo.append(fo)
        else:
            unmatched_fo.append(fo)

    print()
    print("=" * 80)
    print(f"FO-ONLY: {len(fo_only)} FO transactions with no ledger match")
    print("=" * 80)

    if unmapped_fo:
        print(f"\n  --- Unmapped investments (no beancount account found): {len(unmapped_fo)} ---")
        for fo in unmapped_fo:
            print(
                f"  FO line {fo.line_num}: {fo.date}  {fo.investment[:50]:<50s}  "
                f"{format_amount(fo.amount, fo.currency):>20s}  {fo.tx_type}"
            )

    if unmatched_fo:
        print(f"\n  --- Mapped but unmatched: {len(unmatched_fo)} ---")
        for fo in unmatched_fo:
            bc_name = fo_to_bc.get(fo.investment) or _fuzzy_lookup(fo.investment, fo_to_bc)
            print(
                f"  FO line {fo.line_num}: {fo.date}  {fo.investment[:40]:<40s}  "
                f"{format_amount(fo.amount, fo.currency):>20s}  {fo.tx_type}  "
                f"-> {bc_name}"
            )

    # Report ledger-only
    print()
    print("=" * 80)
    print(f"LEDGER-ONLY: {len(ledger_only_fo)} ledger postings for FO-managed "
          f"investments with no FO match")
    print("=" * 80)

    if ledger_only_fo:
        # Group by investment
        by_inv: dict[str, list[LedgerTxn]] = defaultdict(list)
        for lt in ledger_only_fo:
            by_inv[lt.beancount_name].append(lt)

        for inv_name in sorted(by_inv.keys()):
            txns = sorted(by_inv[inv_name], key=lambda t: t.date)
            print(f"\n  {inv_name} ({len(txns)} unmatched):")
            for lt in txns:
                direction = "IN " if lt.is_inflow else "OUT"
                print(
                    f"    {lt.date}  {direction}  "
                    f"{format_amount(lt.amount, lt.currency):>20s}  "
                    f"{lt.narration[:50]}"
                )

    # Per-investment summary
    print()
    print("=" * 80)
    print("PER-INVESTMENT SUMMARY")
    print("=" * 80)

    # Gather stats per investment
    inv_stats: dict[str, dict] = defaultdict(lambda: {
        "fo_total": 0, "matched": 0, "fo_only": 0, "ledger_only": 0,
    })

    for m in matched:
        bc = m["ledger"].beancount_name
        inv_stats[bc]["matched"] += 1
        inv_stats[bc]["fo_total"] += 1

    for fo in unmatched_fo:
        bc = fo_to_bc.get(fo.investment) or _fuzzy_lookup(fo.investment, fo_to_bc)
        if bc:
            inv_stats[bc]["fo_only"] += 1
            inv_stats[bc]["fo_total"] += 1

    for lt in ledger_only_fo:
        inv_stats[lt.beancount_name]["ledger_only"] += 1

    print(f"  {'Investment':<25s} {'FO':>4s} {'Match':>6s} {'FO-only':>8s} {'Ldg-only':>9s}")
    print(f"  {'-'*25} {'----':>4s} {'------':>6s} {'--------':>8s} {'---------':>9s}")
    for inv in sorted(inv_stats.keys()):
        s = inv_stats[inv]
        print(
            f"  {inv:<25s} {s['fo_total']:>4d} {s['matched']:>6d} "
            f"{s['fo_only']:>8d} {s['ledger_only']:>9d}"
        )

    # Summary
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"  FO transactions:        {len(fo_rows)}")
    print(f"  Matched:                {len(matched)}")
    print(f"    - exact:              {len(matched) - len(matched_with_disc)}")
    print(f"    - with discrepancy:   {len(matched_with_disc)}")
    print(f"  FO-only:                {len(fo_only)}")
    print(f"    - unmapped:           {len(unmapped_fo)}")
    print(f"    - mapped, no match:   {len(unmatched_fo)}")
    print(f"  Ledger-only (FO-mgd):   {len(ledger_only_fo)}")
    match_rate = len(matched) / len(fo_rows) * 100 if fo_rows else 0
    print(f"  Match rate:             {match_rate:.1f}%")
    print()
    print("Notes:")
    print("  - Unmapped FO entries: investment names not found in knowledge.json")
    print("  - FO-only mapped: FO has transaction but ledger has no receivable entry")
    print("    (may not yet be booked, or booked under a different structure)")
    print("  - Ledger-only: often the paired side of a matched entry")
    print("    (ledger books both announcement and bank receipt)")
    print("  - Cross-currency matches: FO records original currency,")
    print("    ledger records in settlement currency (ILS)")


if __name__ == "__main__":
    main()
