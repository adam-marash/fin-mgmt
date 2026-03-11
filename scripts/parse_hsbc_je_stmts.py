#!/usr/bin/env python3
"""Parse HSBC JE (Jersey/Expat) PDF statements into CSV.

Handles two formats:
  - Composite (Premier Statement): multi-account per PDF (folders 1, 4)
  - Individual (Account Statement): single-account per PDF (folders 2, 3)

Uses OCR (pytesseract + pdf2image) since these are scanned/image PDFs.

The OCR output from these table-based PDFs is unreliable in column
positioning - amounts, dates, and descriptions can appear on separate
lines in unpredictable orders. The parser uses a state machine that
collects lines between transaction boundaries and assembles them.

Amount assignment strategy: OCR cannot reliably distinguish the
Deposits vs Withdrawals column. When only one amount is present
alongside the balance, we place it in `deposit` by default; downstream
consumers should verify against successive balances to determine
actual direction.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, fields
from pathlib import Path


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Transaction:
    statement_date: str
    account_number: str
    account_type: str
    currency: str
    date: str
    description: str
    reference: str
    deposit: str
    withdrawal: str
    balance: str


# Known account-to-currency mappings (fallback when OCR is ambiguous)
KNOWN_CURRENCIES = {
    "023-085996-363": "CHF",
    "406162-21316966": "GBP",
    "023-085996-362": "CHF",
    "023-085996-360": "AUD",
    "023-085996-361": "JPY",
    "023-085996-540": "EUR",
    "023-085996-200": "CHF",  # Fixed deposit - can vary
    "023-085996-705": "USD",
    "023-085996-076": "EUR",
    "023-085996-077": "USD",
}


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def parse_date_ddmmmyyyy(s: str) -> str:
    """Convert DDMMMYYYY (e.g. '07Dec2023') to YYYY-MM-DD.

    Also handles OCR artifacts like leading 'O' instead of '0'.
    """
    s = s.strip()
    # Fix common OCR: leading O -> 0
    s = re.sub(r"^O", "0", s)
    m = re.match(r"(\d{2})([A-Za-z]{3})(\d{4})", s)
    if not m:
        return ""
    day, mon, year = m.group(1), m.group(2).upper(), m.group(3)
    mm = MONTH_MAP.get(mon, "")
    if not mm:
        return ""
    return f"{year}-{mm}-{day}"


# Date pattern for DDMMMYYYY (with possible OCR 'O' prefix)
DATE_RE = re.compile(
    r"(?:[O0]\d|[0-3]\d)"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\d{4}",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def ocr_pdf(pdf_path: str, dpi: int = 150) -> list[str]:
    """OCR all pages of a PDF using pdftoppm + tesseract CLI.

    Much faster than pdf2image + pytesseract (~1s/page vs ~2min/page).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        prefix = os.path.join(tmpdir, "pg")
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), pdf_path, prefix],
            check=True, capture_output=True,
        )
        page_imgs = sorted(Path(tmpdir).glob("pg-*.png"))
        pages = []
        for img_path in page_imgs:
            out_base = str(img_path.with_suffix(""))
            subprocess.run(
                ["tesseract", str(img_path), out_base],
                check=True, capture_output=True,
            )
            text = Path(out_base + ".txt").read_text()
            pages.append(text)
        return pages


# ---------------------------------------------------------------------------
# Detect statement type
# ---------------------------------------------------------------------------

def is_composite(pages: list[str]) -> bool:
    """Detect composite (Premier Statement) vs individual (Account Statement)."""
    text = "\n".join(pages[:2])
    if "Premier Statement" in text or "Summary of Your Portfolio" in text:
        return True
    if "Account Statement" in text:
        return False
    return True


# ---------------------------------------------------------------------------
# Parse amounts
# ---------------------------------------------------------------------------

def clean_amount(s: str) -> str:
    """Clean an amount string: remove commas, spaces, trailing periods."""
    if not s:
        return ""
    s = s.strip().rstrip(".")
    s = s.replace(",", "").replace(" ", "")
    s = s.replace("..", ".").replace(",,", ",")
    if not s or s == ".":
        return ""
    try:
        float(s)
    except ValueError:
        return ""
    return s


# ---------------------------------------------------------------------------
# Account number normalization
# ---------------------------------------------------------------------------

ACCT_RE = re.compile(r"\d{3}-\d{5,6}-\d{3}|\d{6}-\d{8}")


def normalize_account(s: str) -> str:
    """Extract and normalize account number from OCR text."""
    m = ACCT_RE.search(s)
    return m.group(0) if m else s.strip()


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

SKIP_PHRASES = [
    "IBAN:", "IBAN ", "BIC:", "BIC ", "Page ", "www.expat",
    "HSBC <X}", "HSBC <x}", "HSBC (X)", "HSBC €",
    "Details of Your Accounts",
    "Transaction Details", "Date Transaction",
    "Date Details",
    "(DR=Debit)",
    "Statement Details",
    "DESPATCH CODE", "CUSTOMER NUMBER", "STATEMENT DATE",
    "Your Portfolio at a Glance",
    "Total Deposits", "Total Borrowings",
    "Credit Cards", "Net Position",
    "DEPOSITS AND INVESTMENTS",
    "CHEQUE AND SAVINGS",
    "FIXED/NOTICE",
    "TOTAL DEPOSITS",
    "Premier Statement",
    "Branch Name", "Branch Number",
    "Account Statement",
    "ACCOUNT NUMBER", "STMT SHEET", "Product Type",
    "Despatch Code",
    "UNITED KINGDOM", "ISRAEL",
    "We have made some important",
    "For more information",
    "MIDLJESH", "MIDLJUESH", "MIDLGB22",
    "4 - CASHIER ORDER", "5 - DEMAND DRAFT",
    "6 - TELEGRAPHIC TRANSFER",
    "0-NIL", "1 - RENEW", "2 - RENEW", "3 - WITHDRAW",
    "*- PAST DUE",
    "Deposits Withdrawals",
    "Withdrawals Deposits",
    "Deposits Balance",
    "Withdrawals Balance",
]

ACCT_TYPES = [
    "CURRENCY SAVINGS A/C",
    "BANK ACCOUNT",
    "MULTIPLE SETTLEMENT",
    "SAVER ACCOUNT",
    "ONLINE BONUS SAVER",
    "FIXED DEPOSITS",
]

BALANCE_PHRASES = [
    "BALANCE BROUGHT FORWARD",
    "BALANCE CARRIED FORWARD",
    "CLOSING BALANCE",
    "OPENING BALANCE",
    "Transaction Turnover",
    "Transaction Count",
    "'Transaction",  # OCR artifact
]

CURRENCY_CODES = {"USD", "EUR", "GBP", "CHF", "JPY", "AUD", "PLN", "CAD", "SGD"}


def is_skip_line(s: str) -> bool:
    for phrase in SKIP_PHRASES:
        if phrase in s:
            return True
    return False


def is_balance_line(s: str) -> bool:
    for phrase in BALANCE_PHRASES:
        if s.startswith(phrase):
            return True
    if re.match(r"^WITHDRAWALS\b", s):
        return True
    if re.match(r"^DEPOSITS\b", s):
        return True
    return False


def is_amount_only_line(s: str) -> bool:
    cleaned = re.sub(r"\bDR\b", "", s).strip()
    cleaned = re.sub(r"\bAS\s+AT\b", "", cleaned).strip()
    return bool(re.match(r"^[\d,.\s]+$", cleaned))


def extract_amounts(s: str) -> list[str]:
    s = re.sub(r"\bDR\b", "", s)
    return re.findall(r"[\d,]+(?:\.\d+)?", s)


def is_date_token(s: str) -> bool:
    return bool(DATE_RE.fullmatch(s))


def is_date_start(s: str) -> re.Match | None:
    """Match a line starting with a date token."""
    return re.match(r"^([O0]?\d{1,2}[A-Za-z]{3}\d{4})\s*(.*)", s)


# ---------------------------------------------------------------------------
# Statement date extraction
# ---------------------------------------------------------------------------

def extract_statement_date(pages: list[str]) -> str:
    for page in pages[:2]:
        # Try same-line: "STATEMENT DATE 07MAR2014"
        m = re.search(
            r"STATEMENT\s+DATE\s+([O0]?\d{1,2}[A-Za-z]{3}\d{4})", page
        )
        if m:
            return parse_date_ddmmmyyyy(m.group(1))

    # Fallback: STATEMENT DATE on one line, date value on a later line
    # (OCR sometimes splits label and value into different lines)
    for page in pages[:2]:
        lines = page.split("\n")
        for idx, line in enumerate(lines):
            if "STATEMENT DATE" in line:
                # Check if date is on same line (already tried above) or
                # scan subsequent lines for first date token
                for j in range(idx + 1, min(idx + 20, len(lines))):
                    dm = DATE_RE.search(lines[j])
                    if dm:
                        return parse_date_ddmmmyyyy(dm.group(0))
    return ""


def is_boilerplate_page(text: str) -> bool:
    return "About your statement" in text


# ---------------------------------------------------------------------------
# Amount assignment
# ---------------------------------------------------------------------------

def assign_amounts(raw_amounts: list[str]) -> tuple[str, str]:
    """From a list of raw amount strings, return (amount, balance).

    Strategy: the last amount is typically the balance (rightmost column).
    If there are 2+ amounts, the second-to-last is the transaction amount.
    If there is exactly 1 amount, it is the balance (transaction-only rows
    without balance are rare; the amount will still be useful).
    """
    if not raw_amounts:
        return ("", "")
    if len(raw_amounts) == 1:
        return ("", raw_amounts[0])
    # 2+: second-to-last is amount, last is balance
    return (raw_amounts[-2], raw_amounts[-1])


# ---------------------------------------------------------------------------
# Composite statement parser
# ---------------------------------------------------------------------------

def parse_composite(pages: list[str], pdf_name: str) -> list[Transaction]:
    """Parse a composite Premier Statement (multi-account)."""
    statement_date = extract_statement_date(pages)
    if not statement_date:
        print(f"  WARNING: No statement date in {pdf_name}", file=sys.stderr)
        return []

    transactions: list[Transaction] = []

    all_lines: list[str] = []
    for page_text in pages:
        if is_boilerplate_page(page_text):
            continue
        for line in page_text.split("\n"):
            all_lines.append(line.strip())

    # State
    current_acct_type = ""
    current_acct_num = ""
    current_currency = ""
    in_fixed_deposits = False
    in_account_section = False
    in_summary = False

    txn_date = ""
    txn_desc_lines: list[str] = []
    txn_ref = ""
    txn_amounts: list[str] = []
    # After flushing, orphaned amounts go to the last emitted transaction
    post_flush_amounts: list[str] = []

    def flush_txn():
        nonlocal txn_date, txn_desc_lines, txn_ref, txn_amounts, post_flush_amounts
        # First, apply any post-flush amounts to the previously emitted txn
        _apply_post_flush_amounts()
        if txn_date and current_acct_num and txn_desc_lines:
            desc = " ".join(txn_desc_lines).strip()
            amount, balance = assign_amounts(txn_amounts)
            transactions.append(Transaction(
                statement_date=statement_date,
                account_number=current_acct_num,
                account_type=current_acct_type,
                currency=current_currency,
                date=txn_date,
                description=desc,
                reference=txn_ref,
                deposit=clean_amount(amount),
                withdrawal="",
                balance=clean_amount(balance),
            ))
        txn_date = ""
        txn_desc_lines = []
        txn_ref = ""
        txn_amounts = []
        post_flush_amounts = []

    def _apply_post_flush_amounts():
        nonlocal post_flush_amounts
        if post_flush_amounts and transactions:
            last = transactions[-1]
            if not last.deposit and not last.balance:
                amount, balance = assign_amounts(post_flush_amounts)
                last.deposit = clean_amount(amount)
                last.balance = clean_amount(balance)
        post_flush_amounts = []

    i = 0
    while i < len(all_lines):
        stripped = all_lines[i]

        if not stripped:
            i += 1
            continue

        # Portfolio summary section
        if "Summary of Your Portfolio" in stripped:
            in_summary = True
            i += 1
            continue
        if in_summary:
            if "Details of Your Accounts" in stripped:
                in_summary = False
            i += 1
            continue

        # Check for FIXED DEPOSITS section (no account number in header)
        if stripped.startswith("FIXED DEPOSITS"):
            flush_txn()
            current_acct_type = "FIXED DEPOSITS"
            current_acct_num = ""
            in_fixed_deposits = True
            in_account_section = True
            current_currency = ""
            i += 1
            continue

        # Account section header
        acct_header = detect_account_header(stripped, all_lines, i)
        if acct_header:
            flush_txn()
            current_acct_type, current_acct_num = acct_header
            in_fixed_deposits = False
            in_account_section = True
            current_currency = KNOWN_CURRENCIES.get(current_acct_num, "")
            i += 1
            continue

        # Standalone currency code
        if in_account_section and stripped in CURRENCY_CODES:
            if not current_currency:
                current_currency = stripped
            i += 1
            continue

        # Currency from inline mention
        if in_account_section and not current_currency:
            for ccy in CURRENCY_CODES:
                if stripped.endswith(ccy) or stripped == ccy:
                    current_currency = ccy
                    break

        if is_skip_line(stripped):
            i += 1
            continue

        # Skip standalone column header words
        if stripped in ("Deposits", "Withdrawals", "Balance", "Deposit"):
            i += 1
            continue

        if is_balance_line(stripped):
            if txn_desc_lines:
                flush_txn()
            # Transaction Turnover/Count marks end of orphaned amounts
            if stripped.startswith("Transaction"):
                _apply_post_flush_amounts()
            i += 1
            continue

        # Fixed deposits
        if in_fixed_deposits:
            fd_txn = parse_fixed_deposit_line(stripped, statement_date)
            if fd_txn:
                transactions.append(fd_txn)
            else:
                # Try multi-line: date on this line, data on following lines
                fd_txn = parse_fixed_deposit_multiline(
                    stripped, all_lines, i, statement_date
                )
                if fd_txn:
                    transactions.append(fd_txn)
            i += 1
            continue

        if not in_account_section:
            i += 1
            continue

        # Amount-only line when no active transaction - these are
        # orphaned amounts belonging to the last emitted transaction
        if not txn_date and is_amount_only_line(stripped):
            post_flush_amounts.extend(extract_amounts(stripped))
            i += 1
            continue

        # Date line
        dm = is_date_start(stripped)
        if dm:
            parsed_date = parse_date_ddmmmyyyy(dm.group(1))
            rest = dm.group(2).strip()

            # Date + balance phrase -> skip
            if rest and any(rest.startswith(bp) for bp in BALANCE_PHRASES):
                if txn_desc_lines:
                    flush_txn()
                i += 1
                continue

            # If we have a date but no description yet, update the date
            if txn_date and not txn_desc_lines:
                txn_date = parsed_date
            else:
                flush_txn()
                txn_date = parsed_date

            if rest:
                if is_date_token(rest):
                    pass
                else:
                    txn_desc_lines.append(rest)

            i += 1
            continue

        # REF line
        ref_match = re.match(r"^REE?F\s+(\S+)(.*)", stripped)
        if ref_match:
            if not txn_date:
                i += 1
                continue
            txn_ref = ref_match.group(1)
            rest_after = ref_match.group(2).strip()
            if rest_after:
                txn_amounts.extend(extract_amounts(rest_after))
            i += 1
            continue

        # Amount-only line
        if txn_date and is_amount_only_line(stripped):
            txn_amounts.extend(extract_amounts(stripped))
            i += 1
            continue

        # Description continuation
        if txn_date:
            txn_desc_lines.append(stripped)
            i += 1
            continue

        # Orphan date token
        if is_date_token(stripped):
            i += 1
            continue

        i += 1

    flush_txn()
    _apply_post_flush_amounts()
    return transactions


def detect_account_header(
    line: str, all_lines: list[str], idx: int
) -> tuple[str, str] | None:
    """Detect account section header, handling split across lines.

    OCR often wraps headers in parentheses/brackets:
      ( BANK ACCOUNT 023-085996-705 +)
      ('SAVER ACCOUNT 023-085996-540 )
    """
    # Strip OCR artifacts: leading/trailing parens, brackets, pipes, etc.
    cleaned = re.sub(r"^[\s({\[|']+", "", line)
    cleaned = re.sub(r"[\s)}\]|+]+$", "", cleaned)

    for atype in ACCT_TYPES:
        if cleaned.startswith(atype) or atype in cleaned:
            rest = cleaned.split(atype, 1)[-1].strip()
            acct_match = ACCT_RE.search(rest)
            if acct_match:
                return (atype, acct_match.group(0))
            # Also search the original line
            acct_match = ACCT_RE.search(line)
            if acct_match:
                return (atype, acct_match.group(0))
            # Check next few non-empty lines
            for j in range(idx + 1, min(idx + 5, len(all_lines))):
                next_line = all_lines[j].strip()
                if not next_line:
                    continue
                acct_match = ACCT_RE.search(next_line)
                if acct_match:
                    return (atype, acct_match.group(0))
                break
    return None


def parse_fixed_deposit_line(
    line: str, statement_date: str
) -> Transaction | None:
    """Parse a single-line fixed deposit entry."""
    fd_match = re.match(
        r"([O0]?\d{1,2}[A-Za-z]{3}\d{4})\s+"
        r"([O0]?\d{1,2}[A-Za-z]{3}\d{4})\s+"
        r"(\S+)\s+"
        r"(USD|EUR|GBP|CHF|JPY|AUD)\s+"
        r"([\d,.]+)\s+"
        r"([\d.]+)\s*%?\s*"
        r"([\d,.]+)",
        line,
    )
    if not fd_match:
        return None

    start_date = parse_date_ddmmmyyyy(fd_match.group(1))
    maturity = parse_date_ddmmmyyyy(fd_match.group(2))
    acct = normalize_account(fd_match.group(3))
    ccy = fd_match.group(4)
    principal = clean_amount(fd_match.group(5))
    rate = fd_match.group(6)
    balance = clean_amount(fd_match.group(7))

    desc = (
        f"Fixed deposit: start {start_date}, maturity {maturity}, "
        f"rate {rate}%, principal {principal}"
    )
    return Transaction(
        statement_date=statement_date,
        account_number=acct,
        account_type="FIXED DEPOSITS",
        currency=ccy,
        date=start_date,
        description=desc,
        reference="",
        deposit="",
        withdrawal="",
        balance=balance,
    )


def parse_fixed_deposit_multiline(
    line: str, all_lines: list[str], idx: int, statement_date: str
) -> Transaction | None:
    """Parse fixed deposit data split across multiple OCR lines.

    Collects dates from one line and account+amounts from nearby lines.
    """
    # Check if this line has an account number + currency + amounts
    # Pattern: ACCT_NUM CCY AMOUNT RATE% AMOUNT
    fd_data_match = re.match(
        r"(\d{3}-\d{5,6}-\d{3})\s+"
        r"(USD|EUR|GBP|CHF|JPY|AUD)\s+"
        r"([\d,.]+)\s+"
        r"([\d.]+)\s*%?\s*"
        r"([\d,.]+)",
        line,
    )
    if not fd_data_match:
        return None

    acct = fd_data_match.group(1)
    ccy = fd_data_match.group(2)
    principal = clean_amount(fd_data_match.group(3))
    rate = fd_data_match.group(4)
    balance = clean_amount(fd_data_match.group(5))

    # Look backwards for dates
    start_date = ""
    maturity = ""
    for j in range(idx - 1, max(idx - 8, -1), -1):
        prev = all_lines[j].strip()
        dm = DATE_RE.fullmatch(prev)
        if dm:
            d = parse_date_ddmmmyyyy(dm.group(0))
            if not maturity:
                maturity = d
            elif not start_date:
                start_date = d
                break

    if not start_date and maturity:
        start_date = maturity
        maturity = ""

    desc = (
        f"Fixed deposit: start {start_date}, maturity {maturity}, "
        f"rate {rate}%, principal {principal}"
    )
    return Transaction(
        statement_date=statement_date,
        account_number=acct,
        account_type="FIXED DEPOSITS",
        currency=ccy,
        date=start_date,
        description=desc,
        reference="",
        deposit="",
        withdrawal="",
        balance=balance,
    )


# ---------------------------------------------------------------------------
# Individual statement parser
# ---------------------------------------------------------------------------

def parse_individual(pages: list[str], pdf_name: str) -> list[Transaction]:
    """Parse an individual Account Statement (single account)."""
    statement_date = extract_statement_date(pages)
    if not statement_date:
        print(f"  WARNING: No statement date in {pdf_name}", file=sys.stderr)
        return []

    # Scan first 2 pages for metadata (OCR may split across pages/lines)
    header_text = "\n".join(pages[:2])
    header_lines = header_text.split("\n")

    acct_num = ""
    # Use [^\S\n] to match spaces but not newlines
    m = re.search(r"ACCOUNT[^\S\n]+NUMBER[^\S\n]+(\S+)", header_text)
    if m:
        acct_num = normalize_account(m.group(1))
    else:
        # ACCOUNT NUMBER on separate line from value
        for idx, line in enumerate(header_lines):
            if "ACCOUNT NUMBER" in line:
                for j in range(idx + 1, min(idx + 25, len(header_lines))):
                    am = ACCT_RE.search(header_lines[j])
                    if am:
                        acct_num = am.group(0)
                        break
                break

    currency = ""
    m = re.search(r"^CURRENCY[^\S\n]+(\w+)", header_text, re.MULTILINE)
    if m:
        currency = m.group(1)
    else:
        # CURRENCY on separate line from value
        for idx, line in enumerate(header_lines):
            if line.strip() == "CURRENCY":
                for j in range(idx + 1, min(idx + 25, len(header_lines))):
                    cl = header_lines[j].strip()
                    if cl in CURRENCY_CODES:
                        currency = cl
                        break
                break

    acct_type = ""
    m = re.search(r"Product[^\S\n]+Type[^\S\n]+(.+)", header_text)
    if m:
        acct_type = m.group(1).strip()
    else:
        for idx, line in enumerate(header_lines):
            if "Product Type" in line:
                for j in range(idx + 1, min(idx + 25, len(header_lines))):
                    pt = header_lines[j].strip()
                    if pt and pt in (
                        "ONLINE BONUS SAVER", "BANK ACCOUNT",
                        "CURRENCY SAVINGS A/C", "SAVER ACCOUNT",
                        "MULTIPLE SETTLEMENT",
                    ):
                        acct_type = pt
                        break
                break

    if not acct_num:
        print(f"  WARNING: No account number in {pdf_name}", file=sys.stderr)
        return []

    if not currency:
        currency = KNOWN_CURRENCIES.get(acct_num, "")

    transactions: list[Transaction] = []

    all_lines: list[str] = []
    for page_text in pages:
        if is_boilerplate_page(page_text):
            continue
        for line in page_text.split("\n"):
            all_lines.append(line.strip())

    txn_date = ""
    txn_desc_lines: list[str] = []
    txn_ref = ""
    txn_amounts: list[str] = []
    post_flush_amounts: list[str] = []
    # Don't parse transactions until past the column headers
    past_header = False

    def flush_txn():
        nonlocal txn_date, txn_desc_lines, txn_ref, txn_amounts, post_flush_amounts
        _apply_post_flush_amounts()
        if txn_date and acct_num and txn_desc_lines:
            desc = " ".join(txn_desc_lines).strip()
            amount, balance = assign_amounts(txn_amounts)
            transactions.append(Transaction(
                statement_date=statement_date,
                account_number=acct_num,
                account_type=acct_type,
                currency=currency,
                date=txn_date,
                description=desc,
                reference=txn_ref,
                deposit=clean_amount(amount),
                withdrawal="",
                balance=clean_amount(balance),
            ))
        txn_date = ""
        txn_desc_lines = []
        txn_ref = ""
        txn_amounts = []
        post_flush_amounts = []

    def _apply_post_flush_amounts():
        nonlocal post_flush_amounts
        if post_flush_amounts and transactions:
            last = transactions[-1]
            if not last.deposit and not last.balance:
                amount, balance = assign_amounts(post_flush_amounts)
                last.deposit = clean_amount(amount)
                last.balance = clean_amount(balance)
        post_flush_amounts = []

    for stripped in (l.strip() for l in all_lines):
        if not stripped:
            continue

        # Detect column headers - marks start of transaction area
        if not past_header:
            if "(DR=Debit)" in stripped or stripped.startswith("Balance"):
                past_header = True
            continue

        if is_skip_line(stripped):
            continue

        # Skip address lines
        if re.match(r"^(MRS|MR|MS)\s", stripped):
            continue
        if stripped in ("LONDON", "JERSEY", "BENEI ZION", "Date", "Details"):
            continue
        if re.match(r"^[A-Z]{1,2}\d+\s+\d[A-Z]{2}$", stripped):
            continue
        if re.match(r"^\d{5}$", stripped):
            continue

        if is_balance_line(stripped):
            # Only flush if we have actual description content
            # (OCR may interleave balance lines between date and description)
            if txn_desc_lines:
                flush_txn()
            continue

        if re.match(r"^CURRENCY\s+\w+", stripped):
            continue

        # Date line
        dm = is_date_start(stripped)
        if dm:
            parsed_date = parse_date_ddmmmyyyy(dm.group(1))
            rest = dm.group(2).strip()

            if rest and any(rest.startswith(bp) for bp in BALANCE_PHRASES):
                if txn_desc_lines:
                    flush_txn()
                continue

            # If we already have a date but no description, just update
            # the date (handles OCR interleaving BBF date with txn date)
            if txn_date and not txn_desc_lines:
                txn_date = parsed_date
            else:
                flush_txn()
                txn_date = parsed_date

            if rest and not is_date_token(rest):
                txn_desc_lines.append(rest)
            continue

        # REF line - can appear even without txn_date due to OCR ordering
        ref_match = re.match(r"^REE?F\s+(\S+)(.*)", stripped)
        if ref_match:
            if not txn_date:
                # REF without a date - likely orphaned from garbled OCR
                continue
            txn_ref = ref_match.group(1)
            rest_after = ref_match.group(2).strip()
            if rest_after:
                txn_amounts.extend(extract_amounts(rest_after))
            continue

        # Amount-only line - when no active txn, orphaned amounts
        if is_amount_only_line(stripped):
            if txn_date:
                txn_amounts.extend(extract_amounts(stripped))
            else:
                post_flush_amounts.extend(extract_amounts(stripped))
            continue

        # Description continuation
        if txn_date:
            txn_desc_lines.append(stripped)
            continue

        if is_date_token(stripped):
            continue

    flush_txn()
    _apply_post_flush_amounts()
    return transactions


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse HSBC JE Premier/Account Statement PDFs into CSV.",
    )
    parser.add_argument(
        "input_dir",
        help="Directory containing PDF statement files (searched recursively).",
    )
    parser.add_argument(
        "--output", "-o",
        default="data/hsbc-je-statements.csv",
        help="Output CSV path (default: data/hsbc-je-statements.csv).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="OCR DPI (default: 300).",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"ERROR: {input_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    pdf_files = sorted(input_dir.rglob("*.pdf"))
    if not pdf_files:
        print(f"No PDF files found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(pdf_files)} PDFs in {input_dir}")

    all_txns: list[Transaction] = []
    composite_count = 0
    individual_count = 0
    error_count = 0
    nil_count = 0

    for pdf_path in pdf_files:
        rel = pdf_path.relative_to(input_dir)
        try:
            pages = ocr_pdf(str(pdf_path), dpi=args.dpi)
        except Exception as e:
            print(f"  ERROR OCR {rel}: {e}", file=sys.stderr)
            error_count += 1
            continue

        if not pages:
            nil_count += 1
            continue

        comp = is_composite(pages)
        if comp:
            composite_count += 1
            txns = parse_composite(pages, pdf_path.name)
        else:
            individual_count += 1
            txns = parse_individual(pages, pdf_path.name)

        if txns:
            all_txns.extend(txns)
            print(f"  {rel}: {len(txns)} txns ({'composite' if comp else 'individual'})")
        else:
            nil_count += 1
            print(f"  {rel}: 0 txns ({'composite' if comp else 'individual'})")

    print(f"\nTotal: {len(all_txns)} raw transactions")
    print(f"  Composite: {composite_count}, Individual: {individual_count}")
    print(f"  Errors: {error_count}, Nil: {nil_count}")

    # Sort by date, then account
    all_txns.sort(key=lambda t: (t.date, t.account_number))

    # Dedup: same account + date + reference + amount + balance
    seen: set[tuple] = set()
    unique_txns: list[Transaction] = []
    for t in all_txns:
        key = (t.account_number, t.date, t.deposit, t.withdrawal, t.balance, t.reference)
        if key not in seen:
            seen.add(key)
            unique_txns.append(t)

    print(f"  {len(unique_txns)} unique after dedup")

    # Write CSV
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [f.name for f in fields(Transaction)]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in unique_txns:
            writer.writerow({fn: getattr(t, fn) for fn in fieldnames})

    print(f"\nWritten to {out_path}")

    # Summary by account
    from collections import Counter
    acct_counts: Counter[str] = Counter()
    for t in unique_txns:
        acct_counts[f"{t.account_number} {t.currency} ({t.account_type})"] += 1
    print("\n=== Transactions by account ===")
    for acct, count in acct_counts.most_common():
        print(f"  {acct}: {count}")


if __name__ == "__main__":
    main()
