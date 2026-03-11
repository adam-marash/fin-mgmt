#!/usr/bin/env python3
"""Parse HSBC UK individual PDF statements into CSV/SQLite.

Usage:
    python scripts/parse_hsbc_uk_stmts.py [--db scratch/tamar-solo-9564/tamar-solo-9564.db]
"""

import argparse
import csv
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

STMT_DIR = Path("inbox/hsbc-uk-stmts")

MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

# Payment type codes that start a new transaction
PAY_TYPES = {"DD", "BP", "CR", "VIS", "TFR", "DR", "ATM", "SO", ")))"}

# Date patterns - with or without spaces (older statements concatenate)
DATE_RE = re.compile(r"^(\d{2})\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*(\d{2})$", re.IGNORECASE)
AMOUNT_RE = re.compile(r"[\d,]+\.\d{2}")


@dataclass
class Transaction:
    date: str
    pay_type: str
    description: str
    paid_out: float | None = None
    paid_in: float | None = None
    balance: float | None = None


def parse_date(day: str, mon: str, year: str) -> str:
    """Convert '02 Jan 18' components to '2018-01-02'."""
    y = int(year)
    full_year = 2000 + y if y < 100 else y
    return f"{full_year}-{MONTHS[mon.lower()]}-{day}"


def parse_amount(s: str) -> float:
    """Parse '6,105.00' to 6105.00."""
    return float(s.replace(",", ""))


def extract_transactions_from_page(page) -> list[Transaction]:
    """Extract transactions from a single page using table extraction."""
    tables = page.extract_tables({
        "vertical_strategy": "explicit",
        "horizontal_strategy": "text",
        "explicit_vertical_lines": [],
        "snap_tolerance": 5,
    })

    # Fallback to text-based parsing
    return extract_transactions_from_text(page.extract_text())


def extract_transactions_from_text(text: str) -> list[Transaction]:
    """Parse transactions from raw text of a statement page."""
    lines = text.split("\n")
    transactions = []
    current_date = None
    current_txn = None
    in_table = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Skip header/footer lines - normalize by removing spaces for comparison
        line_upper = line.upper()
        line_nospace = line_upper.replace(" ", "")

        # Always handle balance forward markers
        if "BALANCEBROUGHTFORWARD" in line_nospace:
            in_table = True
            continue
        if "BALANCECARRIEDFORWARD" in line_nospace:
            # Save current txn before leaving table
            if current_txn:
                transactions.append(current_txn)
                current_txn = None
            in_table = False
            continue

        # Skip non-table lines (but NOT when inside table - descriptions
        # may contain substrings like "MRSTAMAR" that match skip patterns)
        if not in_table:
            if any(skip in line_nospace for skip in [
                "PAYMENTTYPEANDDETAILS", "PAIDOUT", "PAIDIN",
                "YOURBANKACCOUNT", "ACCOUNTSUMMARY", "ACCOUNTNAME",
                "MRSTBS", "MRSTAMAR", "CONTACTTEL", "TEXTPHONE",
                "WWW.HSBC", "OPENINGBALANCE", "PAYMENTSIN", "PAYMENTSOUT",
                "CLOSINGBALANCE", "INTERNATIONALBANK", "BRANCHIDENTIFIER",
                "SORTCODE", "SHEETNUMBER", "SEEREVERSE", "USEDBYDEAF",
                "NEWBONDSTREET", "CREDITINTEREST", "ARRANGEDOVERDRAFT",
                "BALANCEVARIABLE", "YOURDEPOSIT",
                "INFORMATIONABOUT", "FSCS", "CREDITINTERESTRATE",
                "OVERDRAFTINTERESTRATE",
            ]):
                continue

        if not in_table:
            # Check if first token looks like a date (to enter table area)
            parts = line.split()
            # Try matching first token as concatenated date (e.g., "02Jan18")
            if parts:
                m = DATE_RE.match(parts[0])
                if not m and len(parts) >= 3:
                    m = DATE_RE.match(" ".join(parts[:3]))
            if m:
                in_table = True
                # Fall through to process this line
            else:
                continue

        # Try to parse date from start of line
        parts = line.split()
        # Try concatenated date first (e.g., "02Jan18")
        m = None
        if parts:
            m = DATE_RE.match(parts[0])
            if m:
                # Concatenated date - rest starts from parts[1]
                date_consumed = 1
            elif len(parts) >= 3:
                m = DATE_RE.match(" ".join(parts[:3]))
                if m:
                    date_consumed = 3
        if m:
            current_date = parse_date(m.group(1), m.group(2), m.group(3))
            rest = " ".join(parts[date_consumed:])
            if not rest:
                continue
            parts = rest.split()
        # else: continuation line, parts unchanged

        if not current_date:
            continue

        # Check if line starts with a payment type
        pay_type = None
        if parts and parts[0] in PAY_TYPES:
            pay_type = parts[0]
            rest_parts = parts[1:]
        elif parts and len(parts) > 1 and parts[0] + " " + parts[1] in {"NON STG"}:
            pay_type = "FEE"
            rest_parts = parts[2:]
        else:
            rest_parts = parts

        if pay_type:
            # Save previous transaction
            if current_txn:
                transactions.append(current_txn)

            # Start new transaction
            # Extract amounts from the end of the line
            rest_text = " ".join(rest_parts)
            amounts = AMOUNT_RE.findall(rest_text)

            # Remove amounts from description
            desc = rest_text
            for amt in amounts:
                desc = desc.replace(amt, "").strip()
            desc = re.sub(r"\s+", " ", desc).strip()

            current_txn = Transaction(
                date=current_date,
                pay_type=pay_type,
                description=desc,
            )

            # Assign amounts: last amount with balance is the balance
            # For lines with amounts, we need context (paid out vs paid in)
            # CR and "ADVICE CONFIRMS" are paid in, most others are paid out
            if amounts:
                _assign_amounts(current_txn, amounts)

        elif current_txn:
            # Continuation line - may have description text and/or amounts
            rest_text = " ".join(rest_parts)
            amounts = AMOUNT_RE.findall(rest_text)

            desc_part = rest_text
            for amt in amounts:
                desc_part = desc_part.replace(amt, "").strip()
            desc_part = re.sub(r"\s+", " ", desc_part).strip()

            if desc_part:
                current_txn.description += " " + desc_part

            if amounts:
                _assign_amounts(current_txn, amounts)

    # Don't forget last transaction
    if current_txn:
        transactions.append(current_txn)

    return transactions


def _assign_amounts(txn: Transaction, amounts: list[str]):
    """Assign parsed amounts to paid_out, paid_in, balance fields.

    Heuristic:
    - If 3 amounts: paid_out/paid_in, then balance
    - If 2 amounts: amount + balance
    - If 1 amount: just the transaction amount (balance comes later or on same line)

    CR type = paid_in, others = paid_out (unless already set)
    """
    parsed = [parse_amount(a) for a in amounts]

    is_credit = txn.pay_type == "CR"

    if len(parsed) == 1:
        if txn.paid_out is None and txn.paid_in is None:
            if is_credit:
                txn.paid_in = parsed[0]
            else:
                txn.paid_out = parsed[0]
        elif txn.paid_out is not None or txn.paid_in is not None:
            # Already have amount, this must be balance
            txn.balance = parsed[0]
    elif len(parsed) == 2:
        if txn.paid_out is None and txn.paid_in is None:
            # First is amount, second is balance
            if is_credit:
                txn.paid_in = parsed[0]
            else:
                txn.paid_out = parsed[0]
            txn.balance = parsed[1]
        else:
            # Already have amount, these might be amount + balance for continuation
            txn.balance = parsed[-1]
    elif len(parsed) >= 3:
        # Complex case - likely paid_out, paid_in, balance or multiple on one line
        txn.balance = parsed[-1]
        if is_credit:
            txn.paid_in = parsed[0]
        else:
            txn.paid_out = parsed[0]


def parse_statement(pdf_path: Path) -> tuple[list[Transaction], dict]:
    """Parse a single HSBC UK statement PDF."""
    metadata = {}
    all_text_parts = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            # Extract period from first page
            period_match = re.search(
                r"(\d+\s+\w+\s+\d{4})\s+to\s+(\d+\s+\w+\s+\d{4})", text
            )
            if period_match and "period_start" not in metadata:
                metadata["period_start"] = period_match.group(1)
                metadata["period_end"] = period_match.group(2)

            # Extract summary (handle concatenated text too)
            ob_match = re.search(r"Opening\s*Balance\s+([\d,]+\.\d{2})", text)
            cb_match = re.search(r"Closing\s*Balance\s+([\d,]+\.\d{2})", text)
            if ob_match:
                metadata["opening_balance"] = parse_amount(ob_match.group(1))
            if cb_match:
                metadata["closing_balance"] = parse_amount(cb_match.group(1))

            all_text_parts.append(text)

    # Parse all pages as one continuous text to handle cross-page transactions
    combined_text = "\n".join(all_text_parts)
    all_txns = extract_transactions_from_text(combined_text)

    return all_txns, metadata


def clean_description(desc: str) -> str:
    """Remove leaked footer/header text from descriptions."""
    # Remove common leaked text patterns (concatenated without spaces)
    for pattern in [
        r"BALANCECARRIEDFORWARD.*", r"BALANCEBROUGHTFORWARD.*",
        r"Creditinterest.*", r"ArrangedOverdraft.*",
        r"Credit Interest Rate.*", r"Overdraft Interest Rate.*",
        r"\d+\s+New Bond Street.*",
    ]:
        desc = re.sub(pattern, "", desc, flags=re.IGNORECASE)
    return desc.strip()


def format_description(txn: Transaction) -> str:
    """Format transaction description similar to Xero bank import style."""
    desc = clean_description(txn.description.strip())
    if txn.pay_type and txn.pay_type != ")))":
        return f"{txn.pay_type} {desc}"
    elif txn.pay_type == ")))":
        return f"))) {desc}"
    return desc


def main():
    parser = argparse.ArgumentParser(description="Parse HSBC UK statements")
    parser.add_argument("--db", help="SQLite database to insert into")
    parser.add_argument("--csv", help="Output CSV file")
    parser.add_argument("--start", default="2018-01", help="Start year-month (default: 2018-01)")
    parser.add_argument("--end", default="2019-12", help="End year-month (default: 2019-12)")
    parser.add_argument("--dry-run", action="store_true", help="Print transactions without saving")
    args = parser.parse_args()

    # Find matching PDFs
    pdf_files = sorted(STMT_DIR.glob("*.pdf"))

    # Filter by date range
    start_ym = args.start.replace("-", "_")
    end_ym = args.end.replace("-", "_")

    selected = []
    for f in pdf_files:
        # Extract year-month from filename
        # Formats: "2018_01_statement_9564.pdf" or "2019_08 statement 9564.pdf"
        name = f.stem
        ym_match = re.match(r"(\d{4})[_-](\d{2})", name)
        if ym_match:
            ym = f"{ym_match.group(1)}_{ym_match.group(2)}"
            if start_ym <= ym <= end_ym:
                selected.append(f)

    print(f"Found {len(selected)} statements in range {args.start} to {args.end}")

    all_transactions = []
    for pdf_path in sorted(selected):
        print(f"  Parsing {pdf_path.name}...", end=" ")
        txns, meta = parse_statement(pdf_path)
        print(f"{len(txns)} transactions", end="")
        if "opening_balance" in meta:
            print(f" (open: {meta['opening_balance']:,.2f}, close: {meta.get('closing_balance', 0):,.2f})", end="")
        print()

        for txn in txns:
            all_transactions.append(txn)

    print(f"\nTotal: {len(all_transactions)} transactions")

    if args.dry_run:
        for txn in all_transactions:
            amount = -txn.paid_out if txn.paid_out else txn.paid_in if txn.paid_in else 0
            desc = format_description(txn)
            print(f"  {txn.date}  {amount:>10.2f}  {desc}")
        return

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "description", "amount", "balance"])
            for txn in all_transactions:
                amount = -txn.paid_out if txn.paid_out else txn.paid_in if txn.paid_in else 0
                desc = format_description(txn)
                writer.writerow([txn.date, desc, amount, txn.balance or ""])
        print(f"Wrote {args.csv}")

    if args.db:
        db = sqlite3.connect(args.db)
        # Check for existing pre-2020 transactions
        existing = db.execute(
            "SELECT COUNT(*) FROM transactions WHERE date < '2020-01-01'"
        ).fetchone()[0]
        if existing > 0:
            print(f"WARNING: {existing} pre-2020 transactions already exist in DB. Skipping insert.")
            print("Delete them first if you want to re-import.")
            db.close()
            return

        # Find max ID
        max_id = db.execute("SELECT COALESCE(MAX(id), 0) FROM transactions").fetchone()[0]
        next_id = max_id + 1

        inserted = 0
        for txn in all_transactions:
            amount = -txn.paid_out if txn.paid_out else txn.paid_in if txn.paid_in else 0
            desc = format_description(txn)
            db.execute(
                "INSERT INTO transactions (id, date, description, amount, category, beancount_account) VALUES (?, ?, ?, ?, ?, ?)",
                (next_id, txn.date, desc, amount, "pdf-statement", None),
            )
            next_id += 1
            inserted += 1

        db.commit()
        print(f"Inserted {inserted} transactions into {args.db}")
        db.close()


if __name__ == "__main__":
    main()
