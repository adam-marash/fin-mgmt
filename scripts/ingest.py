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
    python scripts/ingest.py leumi-ils <csv_path>           # dry-run
    python scripts/ingest.py leumi-ils <csv_path> --commit  # write entries
    python scripts/ingest.py leumi-usd <csv_path>           # dry-run
    python scripts/ingest.py leumi-usd <csv_path> --commit  # write entries
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


LEUMI_ILS_COUNTERPARTY_MAP = {
    # counterparty_account -> (beancount_name, narration_prefix)
    "10-978-019740061": ("Boligo-1", "Boligo 1 distribution received"),
    "10-978-033450094": ("Carmel-Credit", "Carmel Credit (A.B.G Planning) - distribution received"),
}

# IBI pipe to Leumi ILS: each transfer maps 1:1 to a single investment.
# Matched against FO data. Format: (date, amount) -> beancount_name
IBI_ILS_DECOMPOSITION = {
    ("2023-02-27", 30637.0): "Reality-Germany",
    ("2023-03-09", 9835.0): "Boligo-2",
    ("2023-05-15", 11431.0): "Reality-Germany",
    ("2023-05-24", 3137.0): "Netz",
    ("2023-07-23", 14219.0): "Reality-Germany",
    ("2023-09-05", 5361.0): "Netz",
    ("2023-11-16", 12999.0): "Reality-Germany",
    ("2023-12-20", 7716.0): "Netz",
    ("2024-04-10", 2915.0): "Reality-Germany",
    ("2024-04-16", 7133.0): "Netz",
    ("2024-05-28", 9060.0): "Boligo-2",
    ("2024-06-20", 7122.0): "Reality-Germany",
    ("2024-08-14", 6418.0): "Boligo-2",
    ("2024-08-19", 7298.0): "Reality-Germany",
    ("2024-08-29", 4441.0): "Netz",
    ("2024-11-13", 6535.0): "Boligo-2",
    ("2024-12-03", 6919.0): "Reality-Germany",
    ("2025-02-19", 9987.0): "Boligo-2",
    ("2025-02-20", 6722.0): "Reality-Germany",
    ("2025-08-25", 4082.0): "Boligo-2",
    ("2025-09-09", 7084.0): "Reality-Germany",
    ("2025-09-17", 9608.0): "Netz",
    ("2025-11-18", 8318.0): "Boligo-2",
    # Beyond FO data coverage - probable matches based on quarterly patterns
    ("2025-12-24", 11214.0): "Reality-Germany",  # probable
    ("2025-12-30", 14322.0): "Netz",  # probable
    ("2026-02-19", 9139.0): "Boligo-2",  # probable
}


def process_leumi_ils(csv_path: str, dry_run: bool = True):
    """Process Leumi ILS translated CSV into ledger entries.

    Only processes investment_income category rows with known counterparties.
    IBI pipe transfers are skipped (need decomposition by investment).
    """
    knowledge = load_knowledge()
    existing = get_existing_entries()
    existing_tags = get_existing_link_tags(existing)
    filing_date = date.today().isoformat()
    bank_account = "Assets:Banks:Leumi:ILS"
    source_ref = "data/2026-03-05-leumi-transactions/ils-account-en.csv"

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    created = 0
    skipped_cat = 0
    skipped_dup = 0
    skipped_ibi = 0
    entries_to_write = []
    fx_dates = set()

    for row in rows:
        txn_date = row["date"]
        amount_str = row.get("amount", "").strip()
        if not amount_str:
            skipped_cat += 1
            continue
        amount = float(amount_str)
        category = row.get("category", "")
        counterparty_account = row.get("counterparty_account", "")
        counterparty = row.get("counterparty", "")

        # Only process investment income credits
        if category != "investment_income":
            skipped_cat += 1
            continue

        # IBI pipe - decompose using FO-matched lookup
        if counterparty_account == "12-600-000399420":
            ibi_key = (txn_date, amount)
            bc_name = IBI_ILS_DECOMPOSITION.get(ibi_key)
            if not bc_name:
                print(f"  UNMATCHED IBI pipe: {txn_date} {amount:,.2f} ILS - not in decomposition table")
                skipped_ibi += 1
                continue
            # Check if this is a probable match (last 3 entries)
            is_probable = txn_date >= "2025-12-01"
            narration = f"{bc_name} (via IBI pipe) - distribution received"
            # Fall through to normal processing below
        else:
            # Look up counterparty
            mapping = LEUMI_ILS_COUNTERPARTY_MAP.get(counterparty_account)
            if not mapping:
                print(f"  UNKNOWN counterparty: {txn_date} {amount:,.2f} ILS from {counterparty} ({counterparty_account})")
                continue
            bc_name, narration = mapping
            is_probable = False

        # Dedup
        if is_duplicate(txn_date, amount, bank_account, existing):
            skipped_dup += 1
            continue

        # Link tag
        offset_account = f"Assets:Receivable:{bc_name}"
        match = find_matching_receivable(bc_name, existing)
        if match:
            link_tag = match["link_tag"]
        else:
            link_tag = next_link_tag(bc_name, "dist", existing_tags)
            existing_tags.add(link_tag)

        hash_tags = ["provisional"] if is_probable else None

        entry_text = format_entry(
            txn_date=txn_date,
            amount=amount,
            currency="ILS",
            bank_account=bank_account,
            narration=narration,
            offset_account=offset_account,
            link_tag=link_tag,
            hash_tags=hash_tags,
            source_ref=source_ref,
        )

        if dry_run:
            print(f"\n--- {txn_date} {amount:,.2f} ILS -> {bc_name} (^{link_tag}) ---")
            print(entry_text)
        else:
            entries_to_write.append({
                "txn_date": txn_date,
                "entry_text": entry_text,
                "counterparty": counterparty,
                "amount": amount,
                "bc_name": bc_name,
            })
            fx_dates.add(txn_date)

        existing.append({
            "date": txn_date,
            "amount": amount,
            "account": bank_account,
            "currency": "ILS",
            "narration": narration,
            "link_tags": [link_tag],
            "file": "pending",
        })
        created += 1

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Leumi ILS Summary:")
    print(f"  Would create: {created}")
    print(f"  Skipped (non-investment): {skipped_cat}")
    print(f"  Skipped (IBI pipe - unmatched): {skipped_ibi}")
    print(f"  Skipped (already in ledger): {skipped_dup}")

    if not dry_run and entries_to_write:
        print(f"\nFetching FX rates for {len(fx_dates)} dates...")
        fetch_fx_for_dates(fx_dates)

        for item in entries_to_write:
            content_hash = hashlib.sha256(item["entry_text"].encode()).hexdigest()[:8]
            desc = f"credit-{item['bc_name'].lower()}"
            folder_name = f"{filing_date}-leumi-{desc}-{content_hash}"
            year = item["txn_date"][:4]

            folder_path = LEDGER_DIR / year / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)

            header = f"; Leumi ILS credit: {item['counterparty']}\n"
            header += f"; Amount: {item['amount']:,.2f} ILS on {item['txn_date']}\n\n"
            (folder_path / "entries.beancount").write_text(header + item["entry_text"] + "\n")

        if bean_check():
            print(f"  Wrote {len(entries_to_write)} entries. Ledger validates OK.")
        else:
            print("  WARNING: Ledger validation failed after writing entries!")


def process_leumi_usd(csv_path: str, dry_run: bool = True):
    """Process Leumi USD translated CSV into ledger entries.

    Handles Impact Debt distributions and IBI pipe transfers (Boligo 2).
    """
    knowledge = load_knowledge()
    existing = get_existing_entries()
    existing_tags = get_existing_link_tags(existing)
    filing_date = date.today().isoformat()
    bank_account = "Assets:Banks:Leumi:USD"
    source_ref = "data/2026-03-05-leumi-transactions/usd-account-en.csv"

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    created = 0
    skipped_cat = 0
    skipped_dup = 0
    entries_to_write = []
    fx_dates = set()

    for row in rows:
        txn_date = row["date"]
        amount_str = row.get("amount", "").strip()
        if not amount_str:
            skipped_cat += 1
            continue
        amount = float(amount_str)
        category = row.get("category", "")
        counterparty = row.get("counterparty", "")

        if category != "investment_income":
            skipped_cat += 1
            continue

        # Determine investment
        if counterparty == "Impact Debt FOF":
            bc_name = "Impact-Debt"
            narration = "Impact Debt FOF - distribution received"
        elif counterparty == "IBI":
            bc_name = "Boligo-2"
            narration = "Boligo 2 (via IBI pipe to Leumi USD) - distribution received"
        else:
            print(f"  UNKNOWN counterparty: {txn_date} {amount:,.2f} USD from {counterparty}")
            continue

        # Dedup
        if is_duplicate(txn_date, amount, bank_account, existing):
            skipped_dup += 1
            continue

        offset_account = f"Assets:Receivable:{bc_name}"
        match = find_matching_receivable(bc_name, existing)
        if match:
            link_tag = match["link_tag"]
        else:
            link_tag = next_link_tag(bc_name, "dist", existing_tags)
            existing_tags.add(link_tag)

        entry_text = format_entry(
            txn_date=txn_date,
            amount=amount,
            currency="USD",
            bank_account=bank_account,
            narration=narration,
            offset_account=offset_account,
            link_tag=link_tag,
            source_ref=source_ref,
        )

        if dry_run:
            print(f"\n--- {txn_date} ${amount:,.2f} -> {bc_name} (^{link_tag}) ---")
            print(entry_text)
        else:
            entries_to_write.append({
                "txn_date": txn_date,
                "entry_text": entry_text,
                "counterparty": counterparty,
                "amount": amount,
                "bc_name": bc_name,
            })
            fx_dates.add(txn_date)

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

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Leumi USD Summary:")
    print(f"  Would create: {created}")
    print(f"  Skipped (non-investment): {skipped_cat}")
    print(f"  Skipped (already in ledger): {skipped_dup}")

    if not dry_run and entries_to_write:
        print(f"\nFetching FX rates for {len(fx_dates)} dates...")
        fetch_fx_for_dates(fx_dates)

        for item in entries_to_write:
            content_hash = hashlib.sha256(item["entry_text"].encode()).hexdigest()[:8]
            desc = f"credit-{item['bc_name'].lower()}"
            folder_name = f"{filing_date}-leumi-{desc}-{content_hash}"
            year = item["txn_date"][:4]

            folder_path = LEDGER_DIR / year / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)

            header = f"; Leumi USD credit: {item['counterparty']}\n"
            header += f"; Amount: ${item['amount']:,.2f} on {item['txn_date']}\n\n"
            (folder_path / "entries.beancount").write_text(header + item["entry_text"] + "\n")

        if bean_check():
            print(f"  Wrote {len(entries_to_write)} entries. Ledger validates OK.")
        else:
            print("  WARNING: Ledger validation failed after writing entries!")


def report_balances():
    """Print receivable and suspense balances - the anomaly detection report."""
    existing = get_existing_entries()

    # Compute balances per (account, currency)
    balances = {}
    for e in existing:
        acct = e["account"]
        if acct.startswith("Assets:Receivable:") or acct == "Assets:Suspense":
            key = (acct, e["currency"])
            balances.setdefault(key, 0)
            balances[key] += e["amount"]

    if not balances:
        print("No receivable or suspense entries found.")
        return

    print("Receivable & Suspense Balances:")
    print("-" * 65)
    non_zero = 0
    for (acct, ccy) in sorted(balances):
        amt = balances[(acct, ccy)]
        if abs(amt) < 0.01:
            status = "RECONCILED"
        elif amt > 0:
            status = "PENDING RECEIPT"
            non_zero += 1
        else:
            status = "PENDING ANNOUNCEMENT"
            non_zero += 1
        print(f"  {acct:<45} {amt:>12,.2f} {ccy:<4} [{status}]")

    print(f"\n  {non_zero} non-zero / {len(balances)} total")



# === Wise ingestion ===

# EUR counterparty -> account mapping
WISE_EUR_ROUTING = {
    # Rental income
    "Booking.com B.V.": "Income:Rental:Cyprus:Villa-Tamara",
    "AIRBNB PAYMENTS LUXEMBOURG S.A.": "Income:Rental:Cyprus:Villa-Tamara",
    # Property management & maintenance
    "Lazydaze Ltd": "Expenses:Property:Cyprus:Villa-Tamara",
    "Lazydaze Ltd - Pool": "Expenses:Property:Cyprus:Villa-Tamara",
    "Lazydaze Limited": "Expenses:Property:Cyprus:Villa-Tamara",
    "blueworth LTD": "Expenses:Property:Cyprus:Villa-Tamara",
    "DOUBLEX ELECTRICAL AND MAINTENANCE LTD": "Expenses:Property:Cyprus:Villa-Tamara",
    "A & F STEPTOES LTD": "Expenses:Property:Cyprus:Villa-Tamara",
    "Andreas Christodoulou": "Expenses:Property:Cyprus:Villa-Tamara",
    "Christos Efstratiou": "Expenses:Property:Cyprus:Villa-Tamara",
    "NEOCLEOUS CHRISTAKIS": "Expenses:Property:Cyprus:Villa-Tamara",
    "Vachkov Georgi Krumov": "Expenses:Property:Cyprus:Villa-Tamara",
    "Euroblinds": "Expenses:Property:Cyprus:Villa-Tamara",
    "Michael Fieldhouse": "Expenses:Property:Cyprus:Villa-Tamara",
    "A.K.M Network LTD": "Expenses:Property:Cyprus:Villa-Tamara",
    "Leonidas & Anthoulis Ltd": "Expenses:Property:Cyprus:Villa-Tamara",
    # Utilities
    "Eac (internet) HTTPS://WWW.J": "Expenses:Property:Cyprus:Villa-Tamara",
    "Eac (internet) Strovolos": "Expenses:Property:Cyprus:Villa-Tamara",
    "Eac (internet) NICOSIA": "Expenses:Property:Cyprus:Villa-Tamara",
    "Eoa Paphou FCHARALAMBOUS": "Expenses:Property:Cyprus:Villa-Tamara",
    "Payia Municipality - Water Bill": "Expenses:Property:Cyprus:Villa-Tamara",
    "Dimos Akama - Peyia Pegeia": "Expenses:Property:Cyprus:Villa-Tamara",
    # Insurance
    "Genikes Insurance HTTPS://WWW.J": "Expenses:Property:Cyprus:Villa-Tamara",
    "Genikes Insurance Lefkosia": "Expenses:Property:Cyprus:Villa-Tamara",
    "GENERAL INSUR.-PAPHOS": "Expenses:Property:Cyprus:Villa-Tamara",
    # Legal
    "M. TIMOTHEOU AND CO LLC": "Expenses:Property:Cyprus:Villa-Tamara",
    "M.TIMOTHEOU AND CO LLC CLIENTS AC": "Expenses:Property:Cyprus:Villa-Tamara",
    "M. TIMOTHEOU & CO LLC (CLIENT AC MARASH": "Expenses:Property:Cyprus:Villa-Tamara",
    "ASSERTUS LTD (POOL CLIENT A/C)": "Expenses:Property:Cyprus:Villa-Tamara",
    # Furnishing & household
    "Ikea Nicosia": "Expenses:Property:Cyprus:Villa-Tamara",
    "Www.homemarket.com.cy Paphos": "Expenses:Property:Cyprus:Villa-Tamara",
    "Superhome Center (diy)ltd Strovolos": "Expenses:Property:Cyprus:Villa-Tamara",
    "Superhome Center (diy)ltd NICOSIA": "Expenses:Property:Cyprus:Villa-Tamara",
    "Pasant Ltd PAPHOS": "Expenses:Property:Cyprus:Villa-Tamara",
    # Internet
    "Paypal *Kmnetworklt 35314369001": "Expenses:Property:Cyprus:Villa-Tamara",
    # Cyprus tax
    "Tfa Portal-Tax Departmen WWW.MOF.GOV.C": "Expenses:Tax:Cyprus",
    # Self-transfer (from Tamar's other accounts)
    "TAMAR BAT SHEVA MARASH": "Equity:Opening-Balances",
    # HSBC transfer in
    "HSBC BANK PLC": "Equity:Opening-Balances",
    # Villa sale deposit (new property)
    "SHAVIV BERNARD DANIEL AND SHAVIV ADI": "Expenses:Property:Cyprus:Villa-YOLO",
    # Personal purchases (not property)
    "Next Directory INTERNET": "Expenses:Personal:Tamar",
    "Etsy.com - Loveartposter London": "Expenses:Personal:Tamar",
    "Dunelm Softfurnishings Leicester": "Expenses:Personal:Tamar",
    "Bill.me Riga": "Expenses:Personal:Tamar",
    "Airbnb * Inc 415-800-5959": "Expenses:Personal:Tamar",
}

WISE_GBP_ROUTING = {
    # TamarCreative income
    "SHEPSTONE BL": "Income:TamarCreative",
    "Mango L Holdings Ltd": "Income:TamarCreative",
    "MANGO L HOLDINGS L": "Income:TamarCreative",
    "D Zorn": "Income:TamarCreative",
    "GABAY M & Y": "Income:TamarCreative",
    "Karina Kizhner": "Income:TamarCreative",
    "TILE CENTRE LIMI": "Income:TamarCreative",
    # TamarCreative expenses
    "Barbara Shepstone": "Expenses:TamarCreative",
    "Studio 136 Architects Ltd": "Expenses:TamarCreative",
    "Fiverreu Limassol": "Expenses:TamarCreative",
    "Fiverreu Nicosia": "Expenses:TamarCreative",
    "Www.fiverr.com Limassol": "Expenses:TamarCreative",
    "Victorian Plumbing Ltd LANCASHIRE": "Expenses:TamarCreative",
    # Wix (website)
    "Wix.com 1184552683 LONDON": "Expenses:TamarCreative",
    "Wix.com 1122558177 London": "Expenses:TamarCreative",
    "Wix.com 1064244675 London": "Expenses:TamarCreative",
    # Google temp holds (test charges, net zero)
    "Google *Temporary Hold g.co/payhelp#": "Expenses:TamarCreative",
    # Personal (refunds etc.)
    "Dunelm Softfurnishings Leicester": "Expenses:Personal:Tamar",
}

WISE_USD_ROUTING = {
    # Tax
    "NYS DTF BILL PYT": "Expenses:Tax:US:NYS",
    "IRS  TREAS 310": "Income:Other",
    # Wix
    "Wix.com 1184552683 LONDON": "Expenses:TamarCreative",
    "Wix.com 1122558177 London": "Expenses:TamarCreative",
}

WISE_ROUTING = {
    "EUR": WISE_EUR_ROUTING,
    "GBP": WISE_GBP_ROUTING,
    "USD": WISE_USD_ROUTING,
}

WISE_BANK_ACCOUNTS = {
    "EUR": "Assets:Banks:Wise:Tamar:EUR",
    "GBP": "Assets:Banks:Wise:Tamar:GBP",
    "USD": "Assets:Banks:Wise:Tamar:USD",
}


def process_wise(csv_path: str, dry_run: bool = True):
    """Process normalized Wise CSV into ledger entries."""
    existing = get_existing_entries()
    filing_date = date.today().isoformat()

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if not rows:
        print("No rows found.")
        return

    currency = rows[0]["currency"]
    bank_account = WISE_BANK_ACCOUNTS.get(currency)
    routing = WISE_ROUTING.get(currency, {})
    source_ref = csv_path

    if not bank_account:
        print(f"ERROR: Unknown currency {currency}")
        return

    created = 0
    skipped_dup = 0
    skipped_minor = 0
    unrouted = []
    entries_to_write = []

    for row in rows:
        txn_date = row["date"]
        amount = float(row["amount"])
        description = row["description"]
        counterparty = row["counterparty"]
        detail_type = row["detail_type"]
        wise_id = row["wise_id"]
        fees = float(row["fees"] or 0)
        fx_from = row["fx_from"]
        fx_to = row["fx_to"]
        fx_rate = row["fx_rate"]
        fx_amount = row["fx_amount"]
        reference = row["reference"]

        # Skip cashback (tiny amounts, noise)
        if detail_type == "CARD_CASHBACK":
            skipped_minor += 1
            continue

        # Skip fee refunds
        if detail_type == "UNKNOWN" and "Fee refund" in description:
            skipped_minor += 1
            continue

        # Skip bank details purchase (one-time setup)
        if "bank details acquisition" in description.lower():
            skipped_minor += 1
            continue

        # Skip top-up (self-funding, no economic event)
        if detail_type == "MONEY_ADDED":
            skipped_minor += 1
            continue

        # FX conversions - special handling
        if detail_type == "CONVERSION":
            # Debit side (source currency leaves)
            if amount < 0:
                narration = f"Wise FX conversion {fx_from} to {fx_to} (rate {fx_rate})"
                entry_lines = [f'{txn_date} * "{narration}"']
                entry_lines.append(f'  source: "{source_ref}"')
                entry_lines.append(f"  {bank_account}  {amount:,.2f} {currency}")
                entry_lines.append(f"  Equity:FX-Conversion  {-amount:,.2f} {currency}")
                entry_text = "\n".join(entry_lines)

                if not is_duplicate(txn_date, amount, bank_account, existing):
                    if dry_run:
                        print(f"\n--- {txn_date} FX {amount:,.2f} {currency} -> {fx_amount} {fx_to} ---")
                        print(entry_text)
                    else:
                        entries_to_write.append({
                            "txn_date": txn_date,
                            "entry_text": entry_text,
                            "desc": f"wise-fx-{currency.lower()}",
                        })
                    existing.append({"date": txn_date, "amount": amount, "account": bank_account,
                                     "currency": currency, "narration": narration, "link_tags": [], "file": "pending"})
                    created += 1
                else:
                    skipped_dup += 1
            else:
                # Credit side (target currency arrives) - separate entry
                narration = f"Wise FX conversion {fx_from} to {fx_to} (rate {fx_rate})"
                entry_lines = [f'{txn_date} * "{narration}"']
                entry_lines.append(f'  source: "{source_ref}"')
                entry_lines.append(f"  {bank_account}  {amount:,.2f} {currency}")
                entry_lines.append(f"  Equity:FX-Conversion  {-amount:,.2f} {currency}")
                entry_text = "\n".join(entry_lines)

                if not is_duplicate(txn_date, amount, bank_account, existing):
                    if dry_run:
                        print(f"\n--- {txn_date} FX {amount:,.2f} {currency} (credit side) ---")
                        print(entry_text)
                    else:
                        entries_to_write.append({
                            "txn_date": txn_date,
                            "entry_text": entry_text,
                            "desc": f"wise-fx-{currency.lower()}",
                        })
                    existing.append({"date": txn_date, "amount": amount, "account": bank_account,
                                     "currency": currency, "narration": narration, "link_tags": [], "file": "pending"})
                    created += 1
                else:
                    skipped_dup += 1
            continue

        # Route by counterparty
        offset_account = routing.get(counterparty)
        if not offset_account:
            unrouted.append(f"  {txn_date} {amount:>10,.2f} {currency}  {counterparty!r}  ({description})")
            continue

        # Dedup
        if is_duplicate(txn_date, amount, bank_account, existing):
            skipped_dup += 1
            continue

        # Build narration
        if counterparty:
            # Avoid redundant "Foo - To: Foo" patterns
            desc_clean = description
            if desc_clean.startswith("To: ") or desc_clean.startswith("From: "):
                desc_name = desc_clean.split(": ", 1)[1] if ": " in desc_clean else ""
                if desc_name == counterparty:
                    desc_clean = ""
            narration = f"{counterparty} - {desc_clean}" if desc_clean and desc_clean != counterparty else counterparty
        else:
            narration = description

        # Truncate narration if too long
        if len(narration) > 120:
            narration = narration[:117] + "..."

        # Build entry
        entry_lines = [f'{txn_date} * "{narration}"']
        entry_lines.append(f'  source: "{source_ref}"')
        entry_lines.append(f"  {bank_account}  {amount:,.2f} {currency}")

        if fees > 0 and abs(amount) > fees:
            entry_lines.append(f"  Expenses:Bank-Fees  {fees:,.2f} {currency}")
            entry_lines.append(f"  {offset_account}  {-(amount + fees):,.2f} {currency}")
        else:
            entry_lines.append(f"  {offset_account}  {-amount:,.2f} {currency}")

        entry_text = "\n".join(entry_lines)

        if dry_run:
            print(f"\n--- {txn_date} {amount:>10,.2f} {currency} -> {offset_account} ---")
            print(entry_text)
        else:
            entries_to_write.append({
                "txn_date": txn_date,
                "entry_text": entry_text,
                "desc": f"wise-{currency.lower()}-{counterparty[:20].lower().replace(' ', '-')}",
            })

        existing.append({"date": txn_date, "amount": amount, "account": bank_account,
                         "currency": currency, "narration": narration, "link_tags": [], "file": "pending"})
        created += 1

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Wise {currency} Summary:")
    print(f"  Would create: {created}")
    print(f"  Skipped (already in ledger): {skipped_dup}")
    print(f"  Skipped (cashback/minor): {skipped_minor}")
    if unrouted:
        print(f"  Unrouted ({len(unrouted)}):")
        for item in unrouted:
            print(item)

    if not dry_run and entries_to_write:
        for item in entries_to_write:
            content_hash = hashlib.sha256(item["entry_text"].encode()).hexdigest()[:8]
            folder_name = f"{filing_date}-{item['desc'][:40]}-{content_hash}"
            year = item["txn_date"][:4]

            folder_path = LEDGER_DIR / year / folder_name
            folder_path.mkdir(parents=True, exist_ok=True)
            (folder_path / "entries.beancount").write_text(item["entry_text"] + "\n")

        if bean_check():
            print(f"  Wrote {len(entries_to_write)} entries. Ledger validates OK.")
        else:
            print("  WARNING: Ledger validation failed after writing entries!")


def main():
    parser = argparse.ArgumentParser(description="Ingest transactions into beancount ledger")
    parser.add_argument("command", choices=["hsbc-credit", "scan", "leumi-ils", "leumi-usd", "wise", "report"])
    parser.add_argument("csv_path", nargs="?", help="Path to CSV file")
    parser.add_argument("--commit", action="store_true", help="Actually write entries (default: dry run)")
    args = parser.parse_args()

    if args.command == "report":
        report_balances()
    elif args.command in ("hsbc-credit", "scan"):
        if not args.csv_path:
            parser.error("csv_path required for hsbc-credit/scan")
        process_hsbc_credits(args.csv_path, dry_run=not args.commit)
    elif args.command == "leumi-ils":
        if not args.csv_path:
            parser.error("csv_path required for leumi-ils")
        process_leumi_ils(args.csv_path, dry_run=not args.commit)
    elif args.command == "leumi-usd":
        if not args.csv_path:
            parser.error("csv_path required for leumi-usd")
        process_leumi_usd(args.csv_path, dry_run=not args.commit)
    elif args.command == "wise":
        if not args.csv_path:
            parser.error("csv_path required for wise")
        process_wise(args.csv_path, dry_run=not args.commit)


if __name__ == "__main__":
    main()
