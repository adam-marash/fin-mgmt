"""Normalize Bank Leumi HTML-as-XLS exports into clean CSVs.

Handles two formats:
- ILS account (תנועות בחשבון): table class='xlTable', 9 columns
- FX/USD account (תנועות בחשבון מט"ח): table id='ctlActivityTable', 7 columns
"""

import csv
import sys
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup


def parse_date(val: str) -> str:
    val = val.strip()
    if not val:
        return ""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return val


def parse_amount(val: str) -> float:
    """Parse Israeli-formatted number: 1,234.56 or empty/0.00"""
    val = val.strip().replace(",", "")
    if not val:
        return 0.0
    try:
        return float(val)
    except ValueError:
        return 0.0


def normalize_ils(soup, output_path: str):
    """ILS account format: xlTable class."""
    tbl = soup.find("table", class_="xlTable")
    if not tbl:
        print("ERROR: No xlTable found")
        return

    trs = tbl.find_all("tr")
    rows = []

    for tr in trs:
        cells = tr.find_all(["td", "th"])
        vals = [c.get_text(strip=True) for c in cells]

        # Skip header rows
        if len(vals) < 7 or vals[0] in ("תאריך", "תנועות בחשבון", ""):
            continue

        date = parse_date(vals[0])
        value_date = parse_date(vals[1])
        description = vals[2]
        reference = vals[3]
        debit = parse_amount(vals[4])
        credit = parse_amount(vals[5])
        balance = parse_amount(vals[6])
        extended = vals[7] if len(vals) > 7 else ""
        note = vals[8] if len(vals) > 8 else ""

        amount = credit - debit  # positive = credit, negative = debit

        rows.append({
            "date": date,
            "value_date": value_date,
            "description": description,
            "extended": extended,
            "reference": reference,
            "amount": amount,
            "balance": balance,
            "note": note,
        })

    fieldnames = ["date", "value_date", "description", "extended", "reference",
                   "amount", "balance", "note"]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")
    if rows:
        dates = [r["date"] for r in rows if r["date"]]
        print(f"Date range: {min(dates)} to {max(dates)}")


def normalize_fx(soup, output_path: str):
    """FX/USD account format: ctlActivityTable."""
    tbl = soup.find("table", id="ctlActivityTable")
    if not tbl:
        print("ERROR: No ctlActivityTable found")
        return

    trs = tbl.find_all("tr")
    rows = []

    for tr in trs:
        cells = tr.find_all(["td", "th"])
        vals = [c.get_text(strip=True) for c in cells]

        if len(vals) < 5 or vals[0] == "תאריך":
            continue

        date = parse_date(vals[0])
        description = vals[1]
        extended = vals[2]
        reference = vals[3]
        debit = parse_amount(vals[4]) if len(vals) > 4 else 0
        credit = parse_amount(vals[5]) if len(vals) > 5 else 0
        balance = parse_amount(vals[6]) if len(vals) > 6 else 0

        amount = credit - debit

        rows.append({
            "date": date,
            "value_date": "",
            "description": description,
            "extended": extended,
            "reference": reference,
            "amount": amount,
            "balance": balance,
            "note": "",
        })

    fieldnames = ["date", "value_date", "description", "extended", "reference",
                   "amount", "balance", "note"]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {output_path}")
    if rows:
        dates = [r["date"] for r in rows if r["date"]]
        print(f"Date range: {min(dates)} to {max(dates)}")


def normalize(input_path: str, output_path: str | None = None):
    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read()

    soup = BeautifulSoup(content, "html.parser")

    if output_path is None:
        output_path = str(Path(input_path).with_suffix(".csv"))

    # Detect format
    if soup.find("table", class_="xlTable"):
        print("Detected: ILS account format")
        normalize_ils(soup, output_path)
    elif soup.find("table", id="ctlActivityTable"):
        print("Detected: FX/USD account format")
        normalize_fx(soup, output_path)
    else:
        print("ERROR: Unrecognized Leumi export format")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <input.xls> [output.csv]")
        sys.exit(1)
    output = sys.argv[2] if len(sys.argv) > 2 else None
    normalize(sys.argv[1], output)
