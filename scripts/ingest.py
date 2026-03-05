#!/usr/bin/env python3
"""Ingest transactions into the beancount ledger.

This script handles the three matching problems from ASSUMPTIONS.md:
1. Entry matching - new transaction finds open receivable/payable
2. Evidence matching - secondary source confirms/contradicts ledger
3. Enrichment matching - additional detail for existing entries

Usage:
    python scripts/ingest.py hsbc-credit <csv_path>   # process HSBC credits
    python scripts/ingest.py scan <csv_path>           # dry-run: show what would be created
"""

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = ROOT / "ledger"
KNOWLEDGE_FILE = ROOT / "knowledge.json"


def load_knowledge() -> dict:
    return json.load(open(KNOWLEDGE_FILE))


def save_knowledge(k: dict):
    json.dump(k, open(KNOWLEDGE_FILE, "w"), indent=2, ensure_ascii=False)


def bean_check() -> bool:
    """Run bean-check and return True if ledger validates."""
    result = subprocess.run(
        [str(ROOT / ".venv/bin/bean-check"), str(LEDGER_DIR / "main.beancount")],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"VALIDATION FAILED:\n{result.stderr}", file=sys.stderr)
        return False
    return True


def fetch_fx_for_date(dt: str):
    """Ensure prices.beancount has rates for this date."""
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/fetch_fx.py"), dt],
        capture_output=True, text=True
    )


def get_existing_link_tags() -> dict[str, list[str]]:
    """Scan ledger for existing ^link-tags. Returns {tag: [files]}."""
    tags = {}
    for bc_file in LEDGER_DIR.rglob("entries.beancount"):
        content = bc_file.read_text()
        for m in re.finditer(r"\^([\w-]+)", content):
            tag = m.group(1)
            tags.setdefault(tag, []).append(str(bc_file))
    return tags


def get_open_receivables() -> dict[str, list[dict]]:
    """Parse ledger to find open receivables with non-zero balances.

    Returns {investment_name: [{date, amount, currency, link_tag, file}]}
    """
    # Use beancount API to find open receivable balances
    try:
        sys.path.insert(0, str(ROOT / ".venv/lib/python3.12/site-packages"))
        import beancount.loader
        import beancount.core.data as data

        entries, errors, options = beancount.loader.load_file(
            str(LEDGER_DIR / "main.beancount")
        )

        # Find all receivable postings and compute balances
        receivables = {}
        for entry in entries:
            if not isinstance(entry, data.Transaction):
                continue
            for posting in entry.postings:
                if posting.account.startswith("Assets:Receivable:"):
                    inv = posting.account.split(":")[-1]
                    if inv not in receivables:
                        receivables[inv] = {"balance": 0, "currency": None, "entries": []}
                    amt = float(posting.units.number)
                    receivables[inv]["balance"] += amt
                    receivables[inv]["currency"] = posting.units.currency
                    # Extract link tag
                    link_tag = None
                    if entry.links:
                        link_tag = list(entry.links)[0]
                    receivables[inv]["entries"].append({
                        "date": str(entry.date),
                        "amount": amt,
                        "narration": entry.narration,
                        "link_tag": link_tag,
                    })

        # Filter to non-zero balances
        open_receivables = {}
        for inv, data_dict in receivables.items():
            if abs(data_dict["balance"]) > 0.01:
                open_receivables[inv] = data_dict

        return open_receivables

    except Exception as e:
        print(f"Warning: could not parse ledger for receivables: {e}", file=sys.stderr)
        return {}


def get_open_payables() -> dict:
    """Similar to get_open_receivables but for Liabilities:Payable:*"""
    # TODO: implement when we have capital calls
    return {}


def match_counterparty(counterparty: str, knowledge: dict) -> list[dict]:
    """Match a bank counterparty name to investment(s) via knowledge.json.

    Returns list of candidate investments with match confidence.
    """
    if not counterparty:
        return []

    cp_upper = counterparty.upper()
    candidates = []

    for inv_id, inv in knowledge.get("investments", {}).items():
        for name in inv.get("counterparty_names", []):
            if name.upper() in cp_upper or cp_upper in name.upper():
                candidates.append({
                    "investment_id": inv_id,
                    "beancount_name": inv.get("beancount_name", inv_id),
                    "confidence": "high",
                    "matched_name": name,
                })
                break

    return candidates


def next_link_tag(investment_beancount_name: str, tag_type: str, existing_tags: dict) -> str:
    """Generate next sequential ^link-tag for an investment.

    E.g., if ^electra-mif-ii-dist-36 exists, returns ^electra-mif-ii-dist-37
    """
    prefix = f"{investment_beancount_name.lower()}-{tag_type}-"
    max_seq = 0
    for tag in existing_tags:
        if tag.startswith(prefix):
            try:
                seq = int(tag[len(prefix):])
                max_seq = max(max_seq, seq)
            except ValueError:
                pass
    return f"{prefix}{max_seq + 1}"


def find_matching_receivable(
    investment_name: str,
    amount: float,
    txn_date: str,
    open_receivables: dict,
    tolerance_pct: float = 0.05,
) -> dict | None:
    """Find an open receivable that matches this bank credit.

    Matches by investment name. Amount tolerance handles wire fees.
    Returns the matching receivable entry or None.
    """
    if investment_name not in open_receivables:
        return None

    recv = open_receivables[investment_name]
    balance = recv["balance"]

    # Check if the bank credit amount is close to the receivable balance
    # (within tolerance, accounting for wire fees which reduce the credit)
    if balance > 0 and amount > 0:
        # Bank credit should be <= receivable (fees reduce it)
        if amount <= balance and (balance - amount) / balance < tolerance_pct:
            return {
                "balance": balance,
                "currency": recv["currency"],
                "fee": round(balance - amount, 2),
                "entries": recv["entries"],
                "link_tag": recv["entries"][-1].get("link_tag"),
            }

    return None


def make_folder_name(filing_date: str, source: str, desc: str, content_hash: str) -> str:
    """Generate ledger folder name: <date>-<source>-<desc>-<hash>"""
    return f"{filing_date}-{source}-{desc}-{content_hash[:8]}"


def generate_bank_credit_entry(
    txn_date: str,
    amount: float,
    currency: str,
    bank_account: str,
    counterparty: str,
    investment_name: str | None,
    link_tag: str | None,
    fee: float = 0,
    source_ref: str = "",
    tags: list[str] | None = None,
) -> str:
    """Generate beancount entry for a bank credit (wire received)."""
    tag_str = ""
    if link_tag:
        tag_str += f" ^{link_tag}"
    if tags:
        tag_str += " " + " ".join(f"#{t}" for t in tags)

    narration = f'"{counterparty or "Unknown"} - wire received"'

    lines = [f'{txn_date} * {narration}{tag_str}']
    if source_ref:
        lines.append(f'  source: "{source_ref}"')

    lines.append(f"  {bank_account}  {amount:,.2f} {currency}")

    if fee > 0:
        lines.append(f"  Expenses:Wire-Fees  {fee:,.2f} {currency}")

    if investment_name:
        total = amount + fee
        lines.append(f"  Assets:Receivable:{investment_name}  -{total:,.2f} {currency}")
    else:
        lines.append(f"  Income:Unidentified  -{amount + fee:,.2f} {currency}")

    return "\n".join(lines)


def process_hsbc_credit_assignment(csv_path: str, dry_run: bool = True):
    """Process the HSBC credit assignment CSV into ledger entries.

    Each row is a matched bank credit with investment assignment.
    """
    knowledge = load_knowledge()
    existing_tags = get_existing_link_tags()
    open_receivables = get_open_receivables()
    filing_date = date.today().isoformat()

    # Bank account mapping for HSBC GU
    bank_account = "Assets:Banks:HSBC-GU:Tamar-Direct:USD-Income-7003"

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if not r.get("date", "").startswith("#")]

    created = 0
    skipped = 0
    needs_review = []

    for row in rows:
        txn_date = row["date"]
        amount = float(row["amount"].replace(",", ""))
        status = row["status"]
        investment = row.get("investment", "")
        fee = float(row.get("fee", 0) or 0)
        notes = row.get("notes", "")

        # Skip non-matched or special types
        if status not in ("matched", "probable"):
            skipped += 1
            continue

        # Find investment in knowledge.json
        inv_match = None
        for inv_id, inv in knowledge.get("investments", {}).items():
            bc_name = inv.get("beancount_name", "")
            csv_aliases = [a.lower() for a in inv.get("csv_aliases", [])]
            if (investment.lower() in csv_aliases or
                investment.lower().replace(" ", "-") in inv_id.lower() or
                investment.lower() in inv.get("name", "").lower() or
                investment.lower().replace(" ", "-") == bc_name.lower()):
                inv_match = inv
                break

        if not inv_match:
            needs_review.append(f"  {txn_date} ${amount:,.2f} - no investment match for '{investment}'")
            continue

        bc_name = inv_match.get("beancount_name", investment)

        # Check if this transaction already exists in the ledger
        # (simple check: look for same date + similar amount in existing entries)
        already_exists = False
        for recv_inv, recv_data in (open_receivables | {}).items():
            for entry in recv_data.get("entries", []):
                # A wire-received entry clearing this exact amount on this date
                if entry["date"] == txn_date and abs(entry["amount"] + amount) < 0.01:
                    already_exists = True
                    break

        # Check for matching receivable
        match = find_matching_receivable(bc_name, amount, txn_date, open_receivables)

        if match:
            link_tag = match["link_tag"]
            computed_fee = match["fee"]
        else:
            # No open receivable - this is a standalone bank credit
            # Generate a new link tag
            link_tag = next_link_tag(bc_name, "dist", existing_tags)
            existing_tags[link_tag] = []  # register it
            computed_fee = fee

        entry = generate_bank_credit_entry(
            txn_date=txn_date,
            amount=amount,
            currency="USD",
            bank_account=bank_account,
            counterparty=investment,
            investment_name=bc_name,
            link_tag=link_tag,
            fee=computed_fee,
            tags=["provisional"] if status == "probable" else None,
        )

        if dry_run:
            print(f"\n--- {txn_date} ${amount:,.2f} -> {bc_name} (^{link_tag}) ---")
            print(entry)
        else:
            # Create folder and write entry
            content_hash = hashlib.sha256(entry.encode()).hexdigest()[:8]
            desc = f"credit-{bc_name.lower()}"
            folder_name = make_folder_name(filing_date, "hsbc", desc, content_hash)
            year = txn_date[:4]

            folder_path = LEDGER_DIR / year / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)

            entry_file = folder_path / "entries.beancount"
            entry_file.write_text(f"; Bank credit: {investment}\n; Amount: ${amount:,.2f} on {txn_date}\n\n{entry}\n")

            # Fetch FX rates for this date
            fetch_fx_for_date(txn_date)

            created += 1

        created += 1

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Summary:")
    print(f"  Created: {created}")
    print(f"  Skipped (non-matched): {skipped}")
    if needs_review:
        print(f"  Needs review ({len(needs_review)}):")
        for item in needs_review:
            print(item)

    if not dry_run:
        if bean_check():
            print("  Ledger validates OK")
        else:
            print("  WARNING: Ledger validation failed!")


def main():
    parser = argparse.ArgumentParser(description="Ingest transactions into beancount ledger")
    parser.add_argument("command", choices=["hsbc-credit", "scan"])
    parser.add_argument("csv_path", help="Path to CSV file")
    parser.add_argument("--commit", action="store_true", help="Actually write entries (default: dry run)")
    args = parser.parse_args()

    dry_run = not args.commit

    if args.command in ("hsbc-credit", "scan"):
        process_hsbc_credit_assignment(args.csv_path, dry_run=dry_run)


if __name__ == "__main__":
    main()
