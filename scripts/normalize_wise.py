"""Normalize Wise (TransferWise) CSV statement exports into clean CSVs.

Wise exports one CSV per currency sub-account. This script normalizes
the date format (DD-MM-YYYY -> YYYY-MM-DD), cleans up descriptions,
and produces a unified output format suitable for ingest.py.

Usage:
    python scripts/normalize_wise.py <input_dir> [<output_dir>]

Input: directory containing Wise statement CSVs (statement_*_<CCY>_*.csv)
Output: one normalized CSV per currency in output_dir (default: same dir)
"""

import csv
import re
import sys
from datetime import datetime
from pathlib import Path


def parse_date(val: str) -> str:
    """Parse DD-MM-YYYY to YYYY-MM-DD."""
    val = val.strip()
    if not val:
        return ""
    try:
        return datetime.strptime(val, "%d-%m-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return val


def parse_amount(val: str) -> float:
    val = val.strip().replace(",", "")
    if not val:
        return 0.0
    return float(val)


def extract_currency(filename: str) -> str:
    """Extract currency from filename like statement_47655523_EUR_2023-01-10_2026-03-06.csv"""
    m = re.search(r"_([A-Z]{3})_", filename)
    return m.group(1) if m else "UNKNOWN"


def extract_account_id(filename: str) -> str:
    """Extract account ID from filename like statement_47655523_EUR_..."""
    m = re.search(r"statement_(\d+)_", filename)
    return m.group(1) if m else ""


def clean_description(desc: str) -> str:
    """Clean up Wise description for readability."""
    desc = desc.strip()
    # Remove redundant "Card transaction of X.XX CCY issued by" prefix
    desc = re.sub(r"Card transaction of [\d,.]+ [A-Z]{3} issued by ", "", desc)
    # Remove redundant "Sent money to " / "Received money from " prefix
    desc = re.sub(r"Sent money to ", "To: ", desc)
    desc = re.sub(r"Received money from ", "From: ", desc)
    desc = re.sub(r" with reference .*$", "", desc)
    # Remove redundant "Paid to " prefix
    desc = re.sub(r"Paid to ", "To: ", desc)
    return desc


def normalize_file(input_path: Path) -> list[dict]:
    """Normalize a single Wise CSV file. Returns list of row dicts."""
    currency = extract_currency(input_path.name)
    account_id = extract_account_id(input_path.name)
    rows = []

    with open(input_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = parse_date(row["Date"])
            amount = parse_amount(row["Amount"])
            balance = parse_amount(row["Running Balance"])
            description = clean_description(row["Description"])
            tx_type = row.get("Transaction Type", "")
            detail_type = row.get("Transaction Details Type", "")
            wise_id = row.get("TransferWise ID", "")
            payee = row.get("Payee Name", "").strip()
            payer = row.get("Payer Name", "").strip()
            counterparty = payee or payer or ""
            payee_account = row.get("Payee Account Number", "").strip()
            merchant = row.get("Merchant", "").strip()
            reference = row.get("Payment Reference", "").strip()
            fees = parse_amount(row.get("Total fees", "0"))
            fx_from = row.get("Exchange From", "").strip()
            fx_to = row.get("Exchange To", "").strip()
            fx_rate = row.get("Exchange Rate", "").strip()
            fx_amount = row.get("Exchange To Amount", "").strip()
            card_digits = row.get("Card Last Four Digits", "").strip()
            note = row.get("Note", "").strip()

            # Use merchant as counterparty for card transactions if no payee
            if not counterparty and merchant:
                counterparty = merchant

            rows.append({
                "date": date,
                "wise_id": wise_id,
                "amount": amount,
                "currency": currency,
                "balance": balance,
                "description": description,
                "counterparty": counterparty,
                "counterparty_account": payee_account,
                "reference": reference,
                "tx_type": tx_type,
                "detail_type": detail_type,
                "fees": fees,
                "fx_from": fx_from,
                "fx_to": fx_to,
                "fx_rate": fx_rate,
                "fx_amount": fx_amount,
                "card_digits": card_digits,
                "note": note,
                "account_id": account_id,
            })

    # Wise CSVs are newest-first; reverse to chronological
    rows.sort(key=lambda r: (r["date"], r["wise_id"]))
    return rows


OUTPUT_FIELDS = [
    "date", "wise_id", "amount", "currency", "balance",
    "description", "counterparty", "counterparty_account", "reference",
    "tx_type", "detail_type", "fees",
    "fx_from", "fx_to", "fx_rate", "fx_amount",
    "card_digits", "note", "account_id",
]


def write_csv(rows: list[dict], output_path: Path):
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Wrote {len(rows)} rows to {output_path}")


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input_dir> [<output_dir>]")
        sys.exit(1)

    input_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else input_dir

    if not input_dir.is_dir():
        print(f"ERROR: {input_dir} is not a directory")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(input_dir.glob("statement_*.csv"))
    if not csv_files:
        print(f"ERROR: No statement_*.csv files found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(csv_files)} Wise statement files in {input_dir}")

    for csv_file in csv_files:
        currency = extract_currency(csv_file.name)
        print(f"\nProcessing {csv_file.name} ({currency})...")
        rows = normalize_file(csv_file)

        # Skip fee-only rows (FEE- prefixed entries are just fee breakdowns)
        original_count = len(rows)
        rows = [r for r in rows if not r["wise_id"].startswith("FEE-")]
        if original_count != len(rows):
            print(f"  Filtered {original_count - len(rows)} fee breakdown rows")

        output_path = output_dir / f"wise-{currency.lower()}.csv"
        write_csv(rows, output_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
