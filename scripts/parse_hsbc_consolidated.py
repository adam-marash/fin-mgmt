#!/usr/bin/env python3
"""Parse HSBC GU consolidated PDF statements (multi-account, multi-portfolio)."""

import csv
import re
import sys
from dataclasses import dataclass, fields
from pathlib import Path

import pdfplumber


@dataclass
class Transaction:
    account: str
    account_desc: str
    currency: str
    portfolio: str
    period_start: str
    period_end: str
    date: str
    value_date: str
    title: str
    reference: str
    debit: str
    credit: str
    balance: str
    counterparty: str


def parse_date(d: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD."""
    parts = d.split("/")
    if len(parts) == 3:
        return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return d


def parse_period_date(d: str) -> str:
    """Convert '31 December 2023' or '9 October 2017' to '2023-12-31'."""
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    parts = d.strip().split()
    if len(parts) == 3:
        day = parts[0].zfill(2)
        month = months.get(parts[1].lower(), "00")
        year = parts[2]
        return f"{year}-{month}-{day}"
    return d


SKIP_FRAGMENTS = [
    "Client 639B", "St Peter Port",
    "Date Title Value date",
    "HSBC Private Bank", "Arnold House",
    "www.privatebanking", "T. +", "F. +",
    "Disclaimer", "Pleasecheckthe", "Bank(Suisse)",
    "ServicesCommission", "CHE-101", "Authority FINMA",
    "Schemeoffers", "5 year period", "of 20", "of 83",
]


def parse_consolidated(pdf_path: str) -> list[Transaction]:
    """Parse a consolidated multi-account statement PDF."""
    transactions: list[Transaction] = []

    with pdfplumber.open(pdf_path) as pdf:
        # Extract period from first page
        first_text = pdf.pages[0].extract_text() or ""
        period_match = re.search(r"from (.+?) to (.+?)$", first_text, re.MULTILINE)
        period_start = parse_period_date(period_match.group(1)) if period_match else ""
        period_end = parse_period_date(period_match.group(2)) if period_match else ""

        # Collect all lines with IBAN/portfolio context
        all_lines: list[str] = []
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.split("\n"):
                all_lines.append(line)

    # State tracking
    current_account = ""
    current_account_desc = ""
    current_currency = ""
    current_portfolio = ""
    current_txn: Transaction | None = None

    i = 0
    while i < len(all_lines):
        line = all_lines[i].strip()

        # Portfolio header
        port_match = re.match(r"Portfolio\s+(\S+)", line)
        if port_match:
            current_portfolio = port_match.group(1)
            i += 1
            continue

        # IBAN section header
        iban_match = re.match(r"IBAN : \S+ - (\S+) - (.+)", line)
        if iban_match:
            # Save pending transaction before switching account
            if current_txn:
                transactions.append(current_txn)
                current_txn = None

            current_account = iban_match.group(1)
            current_account_desc = iban_match.group(2).strip()
            ccy_match = re.search(r"(USD|EUR|GBP|CHF|JPY|AUD|PLN)", current_account_desc)
            current_currency = ccy_match.group(1) if ccy_match else "?"
            i += 1
            continue

        # Skip noise
        if not line or any(frag in line for frag in SKIP_FRAGMENTS):
            i += 1
            continue

        # Skip balance/total lines
        if "Balance carried forward" in line or "Total new balance" in line:
            i += 1
            continue

        # Skip page number patterns like "18 of 20"
        if re.match(r"^\d+ of \d+$", line):
            i += 1
            continue

        # Skip "Account statement from" repeated headers
        if "Account statement from" in line:
            i += 1
            continue

        # Skip "Debit Credit Balance" header
        if line == "Debit Credit Balance":
            i += 1
            continue

        # Transaction start: DD/MM/YYYY
        date_match = re.match(r"^(\d{2}/\d{2}/\d{4})\s+(.+)", line)
        if date_match:
            if current_txn:
                transactions.append(current_txn)

            txn_date = parse_date(date_match.group(1))
            rest = date_match.group(2)

            # Extract reference number in parentheses
            ref_match = re.search(r"\((\d{8,})\)", rest)
            reference = ref_match.group(1) if ref_match else ""

            # Extract value date and amounts from end of line
            vd_match = re.search(
                r"(\d{2}/\d{2}/\d{4})\s+([\d,.-]+(?:\s+[\d,.-]+)*)\s*$", rest
            )

            value_date = ""
            debit = ""
            credit = ""
            balance = ""

            if vd_match:
                value_date = parse_date(vd_match.group(1))
                amounts_str = vd_match.group(2)
                amounts = amounts_str.split()

                if len(amounts) == 3:
                    debit = amounts[0]
                    credit = amounts[1]
                    balance = amounts[2]
                elif len(amounts) == 2:
                    title_text = rest[:vd_match.start()].strip()
                    if any(kw in title_text.lower() for kw in [
                        "credit", "incoming", "redemption", "interest",
                    ]):
                        credit = amounts[0]
                        balance = amounts[1]
                    else:
                        debit = amounts[0]
                        balance = amounts[1]
                elif len(amounts) == 1:
                    balance = amounts[0]

                title_text = rest[:vd_match.start()].strip()
                if ref_match and ref_match.start() >= 0:
                    title_text = re.sub(r"\(\d{8,}\)", "", title_text).strip()
            else:
                title_text = rest
                if ref_match:
                    title_text = re.sub(r"\(\d{8,}\)", "", title_text).strip()

            current_txn = Transaction(
                account=current_account,
                account_desc=current_account_desc,
                currency=current_currency,
                portfolio=current_portfolio,
                period_start=period_start,
                period_end=period_end,
                date=txn_date,
                value_date=value_date,
                title=title_text.strip(": "),
                reference=reference,
                debit=debit,
                credit=credit,
                balance=balance,
                counterparty="",
            )
            i += 1
            continue

        # Continuation lines
        if current_txn and line:
            if line.startswith("By order of "):
                current_txn.counterparty = line[len("By order of "):].strip()
            elif line.startswith("In favour of "):
                if not current_txn.counterparty:
                    current_txn.counterparty = line[len("In favour of "):].strip()
            elif line.startswith("Outgoing payment:"):
                pass
            # Lines that are just reference/deposit metadata - skip
            elif any(line.startswith(p) for p in [
                "3RD PARTY", "Redemption:", "Open new deposit:",
                "Credit interests:", "Unwind:",
            ]):
                pass
            # If we have no counterparty and no amounts yet, this might be
            # a title-only line (no date prefix, e.g. "(193180630)")
            elif not current_txn.counterparty:
                # Address continuation - skip
                pass

        i += 1

    # Last transaction
    if current_txn:
        transactions.append(current_txn)

    return transactions


def main():
    pdf_paths = sys.argv[1:] if len(sys.argv) > 1 else [
        "tmp/hsbcgu 9 Oct 2017 to 1 Jan 2022.pdf",
        "tmp/hsbcgu 01 Jan 2022 to date.pdf",
    ]

    all_txns: list[Transaction] = []
    for path in pdf_paths:
        if not Path(path).exists():
            print(f"  SKIP: {path} not found", file=sys.stderr)
            continue
        print(f"Parsing {path}...")
        txns = parse_consolidated(path)
        print(f"  {len(txns)} transactions")
        all_txns.extend(txns)

    # Sort by date, then account
    all_txns.sort(key=lambda t: (t.date, t.account))

    # Dedup
    seen: set[tuple] = set()
    unique: list[Transaction] = []
    for t in all_txns:
        key = (t.account, t.date, t.value_date, t.reference, t.debit, t.credit)
        if key not in seen:
            seen.add(key)
            unique.append(t)

    print(f"\n{len(unique)} unique transactions after dedup (from {len(all_txns)} total)")

    # Write CSV
    out_dir = Path("data/2026-03-06-hsbc-gu-consolidated")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "consolidated-statements.csv"

    fieldnames = [f.name for f in fields(Transaction)]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in unique:
            writer.writerow({fn: getattr(t, fn) for fn in fieldnames})

    print(f"Written to {out_path}")

    # Summary by account
    from collections import Counter
    acct_counts: Counter[str] = Counter()
    for t in unique:
        acct_counts[f"{t.account} {t.currency} ({t.account_desc})"] += 1
    print("\n=== Transactions by account ===")
    for acct, count in acct_counts.most_common():
        print(f"  {acct}: {count}")

    # Filter to non-money-market transactions for analysis
    real_txns = [t for t in unique if "Money Market" not in t.title]
    print(f"\n=== Real transactions (excl money market rollovers): {len(real_txns)} ===")
    acct_counts2: Counter[str] = Counter()
    for t in real_txns:
        acct_counts2[f"{t.account} {t.currency} ({t.account_desc})"] += 1
    for acct, count in acct_counts2.most_common():
        print(f"  {acct}: {count}")


if __name__ == "__main__":
    main()
