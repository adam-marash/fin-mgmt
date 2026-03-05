#!/usr/bin/env python3
"""Parse HSBC GU individual PDF statements into CSV."""

import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

PDF_DIRS = [Path("tmp/hsbc gu"), Path("tmp/hsbcgu-more")]
OUT_DIR = Path("data/hsbc-gu")


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
    """Convert '31 December 2023' to '2023-12-31'."""
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


def extract_text_pages(pdf_path: str) -> list[str]:
    """Extract text from all pages, skipping disclaimer pages."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            pages.append(text)
    return pages


def parse_statement(pdf_path: str) -> list[Transaction]:
    """Parse a single account statement PDF into transactions."""
    pages = extract_text_pages(pdf_path)
    if not pages:
        return []

    full_text = "\n".join(pages)

    # Check for nil
    if "No transactions were carried out" in full_text:
        return []

    # Extract account info from first page
    iban_match = re.search(r"IBAN : \S+ - (\S+) - (.+)", pages[0])
    if not iban_match:
        return []

    account = iban_match.group(1)
    account_desc = iban_match.group(2).strip()

    # Extract currency from account description
    currency_match = re.search(r"(USD|EUR|GBP|CHF|JPY|AUD|PLN)", account_desc)
    currency = currency_match.group(1) if currency_match else "?"

    # Extract portfolio
    portfolio_match = re.search(r"Portfolio (\S+)", pages[0])
    portfolio = portfolio_match.group(1) if portfolio_match else "?"

    # Extract period
    period_match = re.search(r"from (.+?) to (.+?)$", pages[0], re.MULTILINE)
    period_start = parse_period_date(period_match.group(1)) if period_match else ""
    period_end = parse_period_date(period_match.group(2)) if period_match else ""

    # Parse transactions - they follow specific patterns
    transactions = []

    # Transaction pattern: DD/MM/YYYY <title> (<ref>) DD/MM/YYYY <debit> <credit> <balance>
    # But the text extraction makes this tricky - amounts and descriptions can be on separate lines
    # Strategy: find transaction lines starting with DD/MM/YYYY

    # Concatenate all page texts, removing headers
    lines = []
    for page_text in pages:
        for line in page_text.split("\n"):
            # Skip header/footer lines
            if any(skip in line for skip in [
                "Client 639B", "Portfolio 639C", "St Peter Port",
                "Account statement from", "Date Title Value date",
                "IBAN :", "HSBC Private Bank", "Arnold House",
                "www.privatebanking", "T. +", "F. +",
                "Disclaimer", "Pleasecheckthe", "Bank(Suisse)",
                "ServicesCommission", "CHE-101", "Authority FINMA",
                "Schemeoffers", "5 year period"
            ]):
                continue
            lines.append(line)

    # Now parse transaction blocks
    i = 0
    current_txn = None

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # Check for "Balance carried forward" - skip
        if "Balance carried forward" in line:
            i += 1
            continue

        # Check for "Total new balance" - skip
        if "Total new balance" in line:
            i += 1
            continue

        # Transaction start: DD/MM/YYYY
        date_match = re.match(r"^(\d{2}/\d{2}/\d{4})\s+(.+)", line)
        if date_match:
            # Save previous transaction
            if current_txn:
                transactions.append(current_txn)

            txn_date = parse_date(date_match.group(1))
            rest = date_match.group(2)

            # Extract reference number in parentheses
            ref_match = re.search(r"\((\d{9,})\)", rest)
            reference = ref_match.group(1) if ref_match else ""

            # Extract value date and amounts from the rest
            # Pattern: ... DD/MM/YYYY [debit] [credit] balance
            # The value date and amounts are at the end of the line
            vd_match = re.search(r"(\d{2}/\d{2}/\d{4})\s+([\d,.]+(?:\s+[\d,.]+)*)\s*$", rest)

            value_date = ""
            debit = ""
            credit = ""
            balance = ""

            if vd_match:
                value_date = parse_date(vd_match.group(1))
                amounts_str = vd_match.group(2)
                amounts = amounts_str.split()

                # Parse amounts - could be 1, 2, or 3 numbers
                # The last number is always balance
                # If 3 numbers: debit, credit, balance
                # If 2 numbers: either (debit, balance) or (credit, balance)
                # If 1 number: balance only (usually balance carried forward)
                if len(amounts) == 3:
                    debit = amounts[0]
                    credit = amounts[1]
                    balance = amounts[2]
                elif len(amounts) == 2:
                    # Need to determine if it's debit or credit from context
                    title_text = rest[:vd_match.start()].strip()
                    if any(kw in title_text.lower() for kw in [
                        "credit", "incoming", "redemption", "interest",
                        "credit interests"
                    ]):
                        credit = amounts[0]
                        balance = amounts[1]
                    else:
                        debit = amounts[0]
                        balance = amounts[1]
                elif len(amounts) == 1:
                    balance = amounts[0]

                # Title is everything between date and value_date
                title_text = rest[:vd_match.start()].strip()
                # Remove reference from title
                if ref_match:
                    title_text = re.sub(r"\(\d{9,}\)", "", title_text).strip()
            else:
                title_text = rest
                if ref_match:
                    title_text = re.sub(r"\(\d{9,}\)", "", title_text).strip()

            current_txn = Transaction(
                account=account,
                account_desc=account_desc,
                currency=currency,
                portfolio=portfolio,
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

        # Continuation lines (counterparty info)
        if current_txn and line:
            # Check for "By order of" or "In favour of" patterns
            if line.startswith("By order of "):
                current_txn.counterparty = line[len("By order of "):].strip()
            elif line.startswith("In favour of "):
                if not current_txn.counterparty:
                    current_txn.counterparty = line[len("In favour of "):].strip()
            elif line.startswith("Outgoing payment:"):
                pass  # redundant info
            elif current_txn.counterparty and not any(
                line.startswith(prefix) for prefix in [
                    "3RD PARTY", "Redemption:", "Open new deposit:",
                    "Credit interests:", "Account Transfer",
                ]
            ):
                # Could be continuation of counterparty address - skip
                pass
            elif line.startswith("3RD PARTY CHARGE"):
                current_txn.title += " - " + line

        i += 1

    # Don't forget the last transaction
    if current_txn:
        transactions.append(current_txn)

    return transactions


def main():
    all_txns: list[Transaction] = []
    nil_count = 0
    err_count = 0
    total_files = 0

    for pdf_dir in PDF_DIRS:
        if not pdf_dir.exists():
            continue
        pdf_files = sorted(
            f for f in os.listdir(pdf_dir)
            if f.startswith("TAMAR MARASH (PAO 1)_1201-Account") and f.endswith(".pdf")
        )
        total_files += len(pdf_files)

        print(f"Processing {len(pdf_files)} PDFs from {pdf_dir}/...")

        for fname in pdf_files:
            path = str(pdf_dir / fname)
            try:
                txns = parse_statement(path)
                if txns:
                    all_txns.extend(txns)
                else:
                    nil_count += 1
            except Exception as e:
                print(f"  ERROR: {fname}: {e}", file=sys.stderr)
                err_count += 1

    print(f"  {len(all_txns)} transactions extracted, {nil_count} nil statements, {err_count} errors")

    # Sort by date, then account
    all_txns.sort(key=lambda t: (t.date, t.account))

    # Remove duplicates (same statement period can appear multiple times if re-downloaded)
    seen = set()
    unique_txns = []
    for t in all_txns:
        key = (t.account, t.date, t.value_date, t.reference, t.debit, t.credit)
        if key not in seen:
            seen.add(key)
            unique_txns.append(t)

    print(f"  {len(unique_txns)} unique transactions after dedup")

    # Write CSV
    out_path = OUT_DIR / "individual-statements.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "account", "account_desc", "currency", "portfolio",
            "period_start", "period_end", "date", "value_date",
            "title", "reference", "debit", "credit", "balance", "counterparty",
        ])
        for t in unique_txns:
            writer.writerow([
                t.account, t.account_desc, t.currency, t.portfolio,
                t.period_start, t.period_end, t.date, t.value_date,
                t.title, t.reference, t.debit, t.credit, t.balance, t.counterparty,
            ])

    print(f"  Written to {out_path}")

    # Summary by account
    from collections import Counter
    acct_counts = Counter()
    for t in unique_txns:
        acct_counts[f"{t.account} {t.currency} {t.account_desc}"] += 1
    print("\n=== Transactions by account ===")
    for acct, count in acct_counts.most_common():
        print(f"  {acct}: {count}")


if __name__ == "__main__":
    main()
