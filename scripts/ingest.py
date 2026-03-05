#!/usr/bin/env python3
"""Ingest bank transactions into the beancount ledger.

Bank transactions are facts - book them immediately. The offsetting leg
goes to Assets:Receivable:<Investment> (known counterparty) or
Assets:Suspense (unknown). Negative receivable balances signal that an
announcement is pending. See ASSUMPTIONS.md for the full model.

Usage:
    python scripts/ingest.py scan <csv_path>                # dry-run
    python scripts/ingest.py hsbc-credit <csv_path>         # dry-run (same)
    python scripts/ingest.py hsbc-credit <csv_path> --commit  # write entries
    python scripts/ingest.py report                         # receivable balances
"""

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = ROOT / "ledger"
KNOWLEDGE_FILE = ROOT / "knowledge.json"


def load_knowledge() -> dict:
    return json.load(open(KNOWLEDGE_FILE))


def bean_check() -> bool:
    result = subprocess.run(
        [str(ROOT / ".venv/bin/bean-check"), str(LEDGER_DIR / "main.beancount")],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"VALIDATION FAILED:\n{result.stderr}", file=sys.stderr)
        return False
    return True


def fetch_fx_for_dates(dates: set[str]):
    """Batch-fetch FX rates for all transaction dates."""
    if not dates:
        return
    subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/fetch_fx.py")] + sorted(dates),
        capture_output=True, text=True
    )


def get_existing_entries() -> list[dict]:
    """Parse all ledger entries. Returns list of {date, amount, account, narration, link_tags, file}."""
    entries = []
    for bc_file in LEDGER_DIR.rglob("entries.beancount"):
        content = bc_file.read_text()
        # Simple regex parse - find transaction headers and their postings
        for block in re.split(r'\n(?=\d{4}-\d{2}-\d{2}\s)', content):
            header = re.match(r'(\d{4}-\d{2}-\d{2})\s+\*\s+"([^"]*)"(.*)', block)
            if not header:
                continue
            txn_date = header.group(1)
            narration = header.group(2)
            tag_str = header.group(3)
            link_tags = re.findall(r'\^([\w-]+)', tag_str)

            # Extract postings with amounts
            for posting in re.finditer(
                r'^\s+([\w:.-]+)\s+(-?[\d,.]+)\s+(\w+)', block, re.MULTILINE
            ):
                account = posting.group(1)
                amount = float(posting.group(2).replace(",", ""))
                currency = posting.group(3)
                entries.append({
                    "date": txn_date,
                    "amount": amount,
                    "account": account,
                    "currency": currency,
                    "narration": narration,
                    "link_tags": link_tags,
                    "file": str(bc_file),
                })
    return entries


def is_duplicate(txn_date: str, amount: float, account: str, existing: list[dict]) -> bool:
    """Check if a transaction already exists in the ledger."""
    for e in existing:
        if (e["date"] == txn_date and
            e["account"] == account and
            abs(e["amount"] - amount) < 0.01):
            return True
    return False


def get_existing_link_tags(existing: list[dict]) -> set[str]:
    """Extract all link tags from existing entries."""
    tags = set()
    for e in existing:
        tags.update(e["link_tags"])
    return tags


def find_matching_receivable(
    investment_name: str, existing: list[dict]
) -> dict | None:
    """Find the most recent positive receivable entry for this investment.

    If found, returns its link_tag so the bank credit can join the same event.
    """
    recv_account = f"Assets:Receivable:{investment_name}"
    # Compute running balance for this receivable
    balance = 0
    last_positive_tag = None
    for e in sorted(existing, key=lambda x: x["date"]):
        if e["account"] == recv_account:
            balance += e["amount"]
            if balance > 0.01 and e["link_tags"]:
                last_positive_tag = e["link_tags"][0]

    if balance > 0.01 and last_positive_tag:
        return {"balance": balance, "link_tag": last_positive_tag}
    return None


def next_link_tag(bc_name: str, tag_type: str, existing_tags: set[str]) -> str:
    """Generate next sequential ^link-tag."""
    prefix = f"{bc_name.lower()}-{tag_type}-"
    max_seq = 0
    for tag in existing_tags:
        if tag.startswith(prefix):
            try:
                seq = int(tag[len(prefix):])
                max_seq = max(max_seq, seq)
            except ValueError:
                pass
    return f"{prefix}{max_seq + 1}"


def resolve_investment(name: str, knowledge: dict) -> dict | None:
    """Resolve a CSV investment name to knowledge.json entry."""
    for inv_id, inv in knowledge.get("investments", {}).items():
        csv_aliases = [a.lower() for a in inv.get("csv_aliases", [])]
        bc_name = inv.get("beancount_name", "")
        if (name.lower() in csv_aliases or
            name.lower().replace(" ", "-") in inv_id.lower() or
            name.lower() in inv.get("name", "").lower() or
            name.lower().replace(" ", "-") == bc_name.lower()):
            return inv
    return None


def format_entry(
    txn_date: str,
    amount: float,
    currency: str,
    bank_account: str,
    narration: str,
    offset_account: str,
    link_tag: str | None = None,
    hash_tags: list[str] | None = None,
    fee: float = 0,
    source_ref: str = "",
) -> str:
    """Format a beancount transaction entry."""
    tag_str = ""
    if link_tag:
        tag_str += f" ^{link_tag}"
    if hash_tags:
        tag_str += " " + " ".join(f"#{t}" for t in hash_tags)

    lines = [f'{txn_date} * "{narration}"{tag_str}']
    if source_ref:
        lines.append(f'  source: "{source_ref}"')

    lines.append(f"  {bank_account}  {amount:,.2f} {currency}")

    if fee > 0:
        lines.append(f"  Expenses:Wire-Fees  {fee:,.2f} {currency}")
        lines.append(f"  {offset_account}  -{amount + fee:,.2f} {currency}")
    else:
        # No fee or negative fee (bank received more than expected) -
        # receivable absorbs the full bank amount
        lines.append(f"  {offset_account}  -{amount:,.2f} {currency}")

    return "\n".join(lines)


def process_hsbc_credits(csv_path: str, dry_run: bool = True):
    """Process HSBC credit assignment CSV into ledger entries."""
    knowledge = load_knowledge()
    existing = get_existing_entries()
    existing_tags = get_existing_link_tags(existing)
    filing_date = date.today().isoformat()
    bank_account = "Assets:Banks:HSBC-GU:Tamar-Direct:USD-Income-7003"
    source_ref = "data/2026-03-05-hsbc-gu-credit-assignment/hsbc-gu-credit-assignment.csv"

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = [r for r in reader if not r.get("date", "").startswith("#")]

    created = 0
    skipped_status = 0
    skipped_dup = 0
    skipped_unknown = 0
    needs_review = []
    fx_dates = set()
    entries_to_write = []

    for row in rows:
        txn_date = row["date"]
        amount = float(row["amount"].replace(",", ""))
        status = row["status"]
        investment_csv = row.get("investment", "")
        fee = float(row.get("fee", 0) or 0)

        # Skip non-investment statuses
        if status not in ("matched", "probable"):
            skipped_status += 1
            continue

        # Resolve investment
        inv = resolve_investment(investment_csv, knowledge)
        if not inv:
            needs_review.append(f"  {txn_date} ${amount:,.2f} - unknown: '{investment_csv}'")
            skipped_unknown += 1
            continue

        bc_name = inv.get("beancount_name", investment_csv)

        # Dedup check
        if is_duplicate(txn_date, amount, bank_account, existing):
            skipped_dup += 1
            continue

        # Determine offset account and link tag
        offset_account = f"Assets:Receivable:{bc_name}"

        # Check for matching positive receivable (announcement arrived first)
        match = find_matching_receivable(bc_name, existing)
        if match:
            link_tag = match["link_tag"]
        else:
            # No prior announcement - generate new link tag
            link_tag = next_link_tag(bc_name, "dist", existing_tags)
            existing_tags.add(link_tag)

        hash_tags = ["provisional"] if status == "probable" else None
        narration = f"{investment_csv} - wire received"

        entry_text = format_entry(
            txn_date=txn_date,
            amount=amount,
            currency="USD",
            bank_account=bank_account,
            narration=narration,
            offset_account=offset_account,
            link_tag=link_tag,
            hash_tags=hash_tags,
            fee=fee,
            source_ref=source_ref,
        )

        if dry_run:
            print(f"\n--- {txn_date} ${amount:,.2f} -> {bc_name} (^{link_tag}) ---")
            print(entry_text)
        else:
            entries_to_write.append({
                "txn_date": txn_date,
                "entry_text": entry_text,
                "investment_csv": investment_csv,
                "amount": amount,
                "bc_name": bc_name,
            })
            fx_dates.add(txn_date)

        # Register in existing to prevent self-duplication within batch
        existing.append({
            "date": txn_date,
            "amount": amount,
            "account": bank_account,
            "currency": "USD",
            "narration": narration,
            "link_tags": [link_tag],
            "file": "pending",
        })
        created += 1

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Summary:")
    print(f"  Would create: {created}")
    print(f"  Skipped (non-investment status): {skipped_status}")
    print(f"  Skipped (already in ledger): {skipped_dup}")
    if needs_review:
        print(f"  Needs review ({len(needs_review)}):")
        for item in needs_review:
            print(item)

    if not dry_run and entries_to_write:
        # Fetch FX rates for all new dates
        print(f"\nFetching FX rates for {len(fx_dates)} dates...")
        fetch_fx_for_dates(fx_dates)

        # Write entries
        for item in entries_to_write:
            content_hash = hashlib.sha256(item["entry_text"].encode()).hexdigest()[:8]
            desc = f"credit-{item['bc_name'].lower()}"
            folder_name = f"{filing_date}-hsbc-{desc}-{content_hash}"
            year = item["txn_date"][:4]

            folder_path = LEDGER_DIR / year / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)

            header = f"; Bank credit: {item['investment_csv']}\n"
            header += f"; Amount: ${item['amount']:,.2f} on {item['txn_date']}\n\n"
            (folder_path / "entries.beancount").write_text(header + item["entry_text"] + "\n")

        # Validate
        if bean_check():
            print(f"  Wrote {len(entries_to_write)} entries. Ledger validates OK.")
        else:
            print("  WARNING: Ledger validation failed after writing entries!")


def report_balances():
    """Print receivable and suspense balances - the anomaly detection report."""
    existing = get_existing_entries()

    # Compute balances per receivable account
    balances = {}
    for e in existing:
        acct = e["account"]
        if acct.startswith("Assets:Receivable:") or acct == "Assets:Suspense":
            balances.setdefault(acct, {"amount": 0, "currency": e["currency"]})
            balances[acct]["amount"] += e["amount"]

    if not balances:
        print("No receivable or suspense entries found.")
        return

    print("Receivable & Suspense Balances:")
    print("-" * 60)
    for acct in sorted(balances):
        b = balances[acct]
        if abs(b["amount"]) < 0.01:
            status = "RECONCILED"
        elif b["amount"] > 0:
            status = "PENDING RECEIPT"
        else:
            status = "PENDING ANNOUNCEMENT"
        print(f"  {acct:<45} {b['amount']:>12,.2f} {b['currency']}  [{status}]")

    non_zero = {a: b for a, b in balances.items() if abs(b["amount"]) > 0.01}
    print(f"\n  {len(non_zero)} non-zero / {len(balances)} total")


def main():
    parser = argparse.ArgumentParser(description="Ingest transactions into beancount ledger")
    parser.add_argument("command", choices=["hsbc-credit", "scan", "report"])
    parser.add_argument("csv_path", nargs="?", help="Path to CSV file")
    parser.add_argument("--commit", action="store_true", help="Actually write entries (default: dry run)")
    args = parser.parse_args()

    if args.command == "report":
        report_balances()
    elif args.command in ("hsbc-credit", "scan"):
        if not args.csv_path:
            parser.error("csv_path required for hsbc-credit/scan")
        process_hsbc_credits(args.csv_path, dry_run=not args.commit)


if __name__ == "__main__":
    main()
