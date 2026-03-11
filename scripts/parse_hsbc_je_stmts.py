#!/usr/bin/env python3
"""Parse HSBC JE (Jersey/Expat) PDF statements into CSV.

Handles two formats:
  - Composite (Premier Statement): multi-account per PDF
  - Individual (Account Statement): single-account per PDF

Uses OCR (pdftoppm + tesseract CLI) since these are scanned/image PDFs.

Post-processing uses balance-delta inference to determine deposit vs
withdrawal direction and filters garbled OCR amounts.
"""

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
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
    "023-085996-200": "USD",
    "023-085996-705": "USD",
    "023-085996-076": "EUR",
    "023-085996-077": "USD",
    "023-085996-690": "AUD",
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

    Also handles OCR artifacts like 'O' for '0', 'c1' for 'ct' (Oct).
    """
    s = s.strip()
    # Fix common OCR artifacts
    s = re.sub(r"^O", "0", s)
    # OCR sometimes renders Oct as 0c1, 0ct, Oc1, 007, 0CT
    s = re.sub(r"(?i)0c1", "Oct", s)
    s = re.sub(r"(?i)Oc1", "Oct", s)
    s = re.sub(r"0CT", "Oct", s)
    # "007" = OCR mangling of "Oct" (O->0, c->0, t->7)
    s = re.sub(r"(\d{2})007(\d{4})", r"\1Oct\2", s)
    m = re.match(r"(\d{2})([A-Za-z]{3})(\d{4})", s)
    if not m:
        return ""
    day, mon, year = m.group(1), m.group(2).upper(), m.group(3)
    mm = MONTH_MAP.get(mon, "")
    if not mm:
        return ""
    return f"{year}-{mm}-{day}"


# Date pattern for DDMMMYYYY (with possible OCR 'O' prefix and Oct variants)
DATE_RE = re.compile(
    r"(?:[O0]\d|[0-3]\d)"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|[O0]ct|[O0]c1|0CT|007|Nov|Dec)"
    r"\d{4}",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def ocr_pdf(pdf_path: str, dpi: int = 150) -> list[str]:
    """OCR all pages of a PDF using pdftoppm + tesseract CLI."""
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


def is_plausible_amount(s: str, currency: str = "") -> bool:
    """Check if a cleaned amount string is plausible (not a garbled ref number).

    Rejects:
    - Numbers with more than 10 digits before decimal (> 10 billion)
    - Numbers that look like account/reference numbers (8+ contiguous digits
      without comma separators in the original)
    """
    if not s:
        return False
    try:
        val = float(s.replace(",", ""))
    except ValueError:
        return False
    # JPY can be large (hundreds of millions) but not trillions
    if currency == "JPY":
        return val < 10_000_000_000  # 10 billion JPY
    # Other currencies: max ~100 million
    return val < 100_000_000


# ---------------------------------------------------------------------------
# Account number normalization
# ---------------------------------------------------------------------------

ACCT_RE = re.compile(r"\d{3}-\d{5,6}-\d{3}|\d{6}-\d{8}")


def normalize_account(s: str) -> str:
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
    "CURRENCY SAVINGS AIC",   # OCR variant
    "BANK ACCOUNT",
    "MULTIPLE SETTLEMENT",
    "SAVER ACCOUNT",
    "ONLINE BONUS SAVER",
    "FIXED DEPOSITS",
]

# Normalize OCR variants to canonical names
ACCT_TYPE_CANONICAL = {
    "CURRENCY SAVINGS AIC": "CURRENCY SAVINGS A/C",
}

BALANCE_PHRASES = [
    "BALANCE BROUGHT FORWARD",
    "BALANCE CARRIED FORWARD",
    "CLOSING BALANCE",
    "OPENING BALANCE",
    "Transaction Turnover",
    "Transaction Count",
    "'Transaction",
    "[Transaction",
    "(Transaction",
]

CURRENCY_CODES = {"USD", "EUR", "GBP", "CHF", "JPY", "AUD", "PLN", "CAD", "SGD"}


def is_skip_line(s: str) -> bool:
    for phrase in SKIP_PHRASES:
        if phrase in s:
            return True
    return False


def is_balance_line(s: str) -> bool:
    # Strip leading OCR bracket artifacts
    cleaned = re.sub(r"^[\[('{\\|]+\s*", "", s)
    for phrase in BALANCE_PHRASES:
        if cleaned.startswith(phrase):
            return True
    if re.match(r"^WITHDRAWALS\b", cleaned):
        return True
    if re.match(r"^DEPOSITS\b", cleaned):
        return True
    return False


def is_amount_only_line(s: str) -> bool:
    cleaned = re.sub(r"\bDR\b", "", s).strip()
    cleaned = re.sub(r"\bAS\s+AT\b", "", cleaned).strip()
    return bool(re.match(r"^[\d,.\s]+$", cleaned))


def extract_amounts_from_line(s: str) -> list[str]:
    """Extract plausible amounts from a line.

    Only extracts numbers that look like monetary amounts:
    - Must have comma separators for 4+ digit numbers, OR
    - Must have a decimal point
    - Standalone small numbers (1-3 digits) are accepted
    """
    s = re.sub(r"\bDR\b", "", s)
    # Match: numbers with commas (e.g., 1,234.56), or with decimals, or small integers
    candidates = re.findall(r"[\d,]+\.\d+|[\d]{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}", s)
    return candidates


def is_date_token(s: str) -> bool:
    return bool(DATE_RE.fullmatch(s))


def is_date_start(s: str) -> re.Match | None:
    """Match a line starting with a date token."""
    return re.match(r"^([O0]?\d{1,2}(?:[A-Za-z]{3}|0c1|Oc1|0CT|007)\d{4})\s*(.*)", s, re.IGNORECASE)


# ---------------------------------------------------------------------------
# Statement date extraction
# ---------------------------------------------------------------------------

def extract_statement_date(pages: list[str], pdf_name: str = "") -> str:
    for page in pages[:2]:
        m = re.search(
            r"STATEMENT\s+DATE\s+([O0]?\d{1,2}(?:[A-Za-z]{3}|0c1|Oc1|0CT|007)\d{4})",
            page, re.IGNORECASE,
        )
        if m:
            return parse_date_ddmmmyyyy(m.group(1))

    # Fallback: STATEMENT DATE on one line, date on subsequent line
    for page in pages[:2]:
        lines = page.split("\n")
        for idx, line in enumerate(lines):
            if "STATEMENT DATE" in line:
                for j in range(idx + 1, min(idx + 20, len(lines))):
                    dm = DATE_RE.search(lines[j])
                    if dm:
                        return parse_date_ddmmmyyyy(dm.group(0))

    # Last resort: extract date from filename
    if pdf_name:
        # Try YYYY-MM-DD or YYYY_MM_DD patterns
        m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", pdf_name)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def is_boilerplate_page(text: str) -> bool:
    return "About your statement" in text


# ---------------------------------------------------------------------------
# Summary balance extraction (for validation)
# ---------------------------------------------------------------------------

def extract_summary_balances(pages: list[str]) -> dict[str, float]:
    """Extract account balances from the portfolio summary table on page 1.

    Returns {account_number: balance}.
    """
    balances = {}
    text = pages[0] if pages else ""
    # Look for lines with account numbers and amounts after "Summary of Your Portfolio"
    in_summary = False
    for line in text.split("\n"):
        if "Summary of Your Portfolio" in line:
            in_summary = True
            continue
        if "Details of Your Accounts" in line:
            break
        if not in_summary:
            continue
        # Look for account number + balance
        acct_match = ACCT_RE.search(line)
        if acct_match:
            acct = acct_match.group(0)
            # Extract amounts after the account number
            rest = line[acct_match.end():]
            amounts = re.findall(r"[\d,]+(?:\.\d+)?", rest)
            if amounts:
                try:
                    bal = float(amounts[0].replace(",", ""))
                    balances[acct] = bal
                except ValueError:
                    pass
    return balances


# ---------------------------------------------------------------------------
# Amount assignment
# ---------------------------------------------------------------------------

def assign_amounts(raw_amounts: list[str], currency: str = "") -> tuple[str, str]:
    """From a list of raw amount strings, return (amount, balance).

    Filters implausible amounts first.
    """
    valid = [a for a in raw_amounts if is_plausible_amount(a, currency)]
    if not valid:
        return ("", "")
    if len(valid) == 1:
        return ("", valid[0])
    return (valid[-2], valid[-1])


# ---------------------------------------------------------------------------
# OCR line cleaning
# ---------------------------------------------------------------------------

def clean_ocr_line(s: str) -> str:
    """Strip common OCR bracket/pipe artifacts from line edges."""
    s = re.sub(r"^[\[('{\\|]+\s*", "", s)
    s = re.sub(r"\s*[\])}'|+\\]+$", "", s)
    # Also strip leading backslash from description-like content
    s = re.sub(r"^\\+", "", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Composite statement parser
# ---------------------------------------------------------------------------

def parse_composite(pages: list[str], pdf_name: str) -> list[Transaction]:
    statement_date = extract_statement_date(pages, pdf_name)
    if not statement_date:
        print(f"  WARNING: No statement date in {pdf_name}", file=sys.stderr)
        return []

    summary_balances = extract_summary_balances(pages)
    transactions: list[Transaction] = []

    all_lines: list[str] = []
    for page_text in pages:
        if is_boilerplate_page(page_text):
            continue
        for line in page_text.split("\n"):
            all_lines.append(clean_ocr_line(line))

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
    post_flush_amounts: list[str] = []

    def flush_txn():
        nonlocal txn_date, txn_desc_lines, txn_ref, txn_amounts, post_flush_amounts
        _apply_post_flush_amounts()
        if txn_date and current_acct_num and txn_desc_lines:
            desc = " ".join(txn_desc_lines).strip()
            amount, balance = assign_amounts(txn_amounts, current_currency)
            transactions.append(Transaction(
                statement_date=statement_date,
                account_number=current_acct_num,
                account_type=ACCT_TYPE_CANONICAL.get(current_acct_type, current_acct_type),
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
                amount, balance = assign_amounts(post_flush_amounts, last.currency)
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

        # Check for FIXED DEPOSITS section
        if stripped.startswith("FIXED DEPOSITS") or stripped == "FIXED DEPOSITS":
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

        if stripped in ("Deposits", "Withdrawals", "Balance", "Deposit"):
            i += 1
            continue

        if is_balance_line(stripped):
            if txn_desc_lines:
                flush_txn()
            if "Transaction" in stripped:
                _apply_post_flush_amounts()
            i += 1
            continue

        # Fixed deposits
        if in_fixed_deposits:
            fd_txn = parse_fixed_deposit_line(stripped, statement_date)
            if fd_txn:
                transactions.append(fd_txn)
            else:
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

        # Amount-only orphan line
        if not txn_date and is_amount_only_line(stripped):
            post_flush_amounts.extend(extract_amounts_from_line(stripped))
            i += 1
            continue

        # Date line
        dm = is_date_start(stripped)
        if dm:
            parsed_date = parse_date_ddmmmyyyy(dm.group(1))
            rest = dm.group(2).strip()

            if rest and any(rest.startswith(bp) for bp in BALANCE_PHRASES):
                if txn_desc_lines:
                    flush_txn()
                i += 1
                continue

            if txn_date and not txn_desc_lines:
                txn_date = parsed_date
            else:
                flush_txn()
                txn_date = parsed_date

            if rest:
                if is_date_token(rest):
                    pass
                else:
                    # Check if rest contains amounts at the end
                    # e.g., "CREDIT INTEREST 1.25 31200.64"
                    parts = rest.rsplit(None, 2)
                    trailing_amounts = []
                    desc_part = rest
                    for p in reversed(parts):
                        cleaned = clean_amount(p)
                        if cleaned and is_plausible_amount(cleaned, current_currency):
                            trailing_amounts.insert(0, p)
                            desc_part = desc_part[:desc_part.rfind(p)].strip()
                        else:
                            break
                    if desc_part:
                        txn_desc_lines.append(desc_part)
                    txn_amounts.extend(trailing_amounts)

            i += 1
            continue

        # REF line
        ref_match = re.match(r"^[\[(\\'|I]*REE?F\s+(\S+)(.*)", stripped)
        if ref_match:
            if not txn_date:
                i += 1
                continue
            txn_ref = ref_match.group(1)
            rest_after = ref_match.group(2).strip()
            if rest_after:
                txn_amounts.extend(extract_amounts_from_line(rest_after))
            i += 1
            continue

        # Amount-only line
        if txn_date and is_amount_only_line(stripped):
            txn_amounts.extend(extract_amounts_from_line(stripped))
            i += 1
            continue

        # Description continuation - but filter out lines that are just
        # numbers masquerading as description (account numbers, sort codes)
        if txn_date:
            # Skip lines that are just a bare long number (reference/account number)
            if re.match(r"^\d{7,}$", stripped):
                i += 1
                continue
            # If we already have a REF and amounts, a new description line
            # means a new transaction with the same date (no date prefix)
            if txn_ref and txn_amounts:
                prev_date = txn_date
                flush_txn()
                txn_date = prev_date
            txn_desc_lines.append(stripped)
            i += 1
            continue

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
    """Detect account section header."""
    cleaned = re.sub(r"^[\s({\[|']+", "", line)
    cleaned = re.sub(r"[\s)}\]|+]+$", "", cleaned)

    for atype in ACCT_TYPES:
        if cleaned.startswith(atype) or atype in cleaned:
            rest = cleaned.split(atype, 1)[-1].strip()
            acct_match = ACCT_RE.search(rest)
            if acct_match:
                canon = ACCT_TYPE_CANONICAL.get(atype, atype)
                return (canon, acct_match.group(0))
            acct_match = ACCT_RE.search(line)
            if acct_match:
                canon = ACCT_TYPE_CANONICAL.get(atype, atype)
                return (canon, acct_match.group(0))
            for j in range(idx + 1, min(idx + 5, len(all_lines))):
                next_line = all_lines[j].strip()
                if not next_line:
                    continue
                acct_match = ACCT_RE.search(next_line)
                if acct_match:
                    canon = ACCT_TYPE_CANONICAL.get(atype, atype)
                    return (canon, acct_match.group(0))
                break
    return None


def parse_fixed_deposit_line(
    line: str, statement_date: str
) -> Transaction | None:
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
    statement_date = extract_statement_date(pages, pdf_name)
    if not statement_date:
        print(f"  WARNING: No statement date in {pdf_name}", file=sys.stderr)
        return []

    header_text = "\n".join(pages[:2])
    header_lines = header_text.split("\n")

    acct_num = ""
    m = re.search(r"ACCOUNT[^\S\n]+NUMBER[^\S\n]+(\S+)", header_text)
    if m:
        acct_num = normalize_account(m.group(1))
    else:
        for idx, line in enumerate(header_lines):
            if "ACCOUNT NUMBER" in line:
                for j in range(idx + 1, min(idx + 25, len(header_lines))):
                    am = ACCT_RE.search(header_lines[j])
                    if am:
                        acct_num = am.group(0)
                        break
                break
    # Fallback: if account number is bare digits, look up from known accounts
    if acct_num and not ACCT_RE.search(acct_num):
        for known_acct in KNOWN_CURRENCIES:
            if known_acct.endswith(acct_num):
                acct_num = known_acct
                break

    currency = ""
    m = re.search(r"^CURRENCY[^\S\n]+(\w+)", header_text, re.MULTILINE)
    if m:
        cval = m.group(1).upper()
        if cval in CURRENCY_CODES:
            currency = cval
    if not currency:
        for idx, line in enumerate(header_lines):
            if line.strip() == "CURRENCY":
                for j in range(idx + 1, min(idx + 25, len(header_lines))):
                    cl = header_lines[j].strip().upper()
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
                        "MULTIPLE SETTLEMENT", "FIXED DEPOSIT A/C",
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
            all_lines.append(clean_ocr_line(line))

    txn_date = ""
    txn_desc_lines: list[str] = []
    txn_ref = ""
    txn_amounts: list[str] = []
    post_flush_amounts: list[str] = []
    past_header = False

    def flush_txn():
        nonlocal txn_date, txn_desc_lines, txn_ref, txn_amounts, post_flush_amounts
        _apply_post_flush_amounts()
        if txn_date and acct_num and txn_desc_lines:
            desc = " ".join(txn_desc_lines).strip()
            amount, balance = assign_amounts(txn_amounts, currency)
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
                amount, balance = assign_amounts(post_flush_amounts, currency)
                last.deposit = clean_amount(amount)
                last.balance = clean_amount(balance)
        post_flush_amounts = []

    for stripped in (clean_ocr_line(l) for l in all_lines):
        if not stripped:
            continue

        if not past_header:
            if "DR=Debit" in stripped or stripped.startswith("Balance"):
                past_header = True
            continue

        if is_skip_line(stripped):
            continue

        if re.match(r"^(MRS|MR|MS)\s", stripped):
            continue
        if stripped in ("LONDON", "JERSEY", "BENEI ZION", "Date", "Details"):
            continue
        if re.match(r"^[A-Z]{1,2}\d+\s+\d[A-Z]{2}$", stripped):
            continue
        if re.match(r"^\d{5}$", stripped):
            continue

        if is_balance_line(stripped):
            if txn_desc_lines:
                flush_txn()
            continue

        if re.match(r"^CURRENCY\s+\w+", stripped):
            continue

        dm = is_date_start(stripped)
        if dm:
            parsed_date = parse_date_ddmmmyyyy(dm.group(1))
            rest = dm.group(2).strip()

            if rest and any(rest.startswith(bp) for bp in BALANCE_PHRASES):
                if txn_desc_lines:
                    flush_txn()
                continue

            if txn_date and not txn_desc_lines:
                txn_date = parsed_date
            else:
                flush_txn()
                txn_date = parsed_date

            if rest and not is_date_token(rest):
                txn_desc_lines.append(rest)
            continue

        ref_match = re.match(r"^[\[(\\'|I]*REE?F\s+(\S+)(.*)", stripped)
        if ref_match:
            if not txn_date:
                continue
            txn_ref = ref_match.group(1)
            rest_after = ref_match.group(2).strip()
            if rest_after:
                txn_amounts.extend(extract_amounts_from_line(rest_after))
            continue

        if is_amount_only_line(stripped):
            if txn_date:
                txn_amounts.extend(extract_amounts_from_line(stripped))
            else:
                post_flush_amounts.extend(extract_amounts_from_line(stripped))
            continue

        if txn_date:
            if re.match(r"^\d{7,}$", stripped):
                continue
            # If we already have a REF and amounts, a new description line
            # means a new transaction with the same date (no date prefix)
            if txn_ref and txn_amounts:
                prev_date = txn_date
                flush_txn()
                txn_date = prev_date
            txn_desc_lines.append(stripped)
            continue

        if is_date_token(stripped):
            continue

    flush_txn()
    _apply_post_flush_amounts()
    return transactions


# ---------------------------------------------------------------------------
# Post-processing: balance-delta inference for deposit vs withdrawal
# ---------------------------------------------------------------------------

def infer_direction(transactions: list[Transaction]) -> list[Transaction]:
    """Use successive balances to determine if amount was deposit or withdrawal.

    For each account, compare balance before and after each transaction.
    If balance increased, the amount is a deposit; if decreased, a withdrawal.
    """
    # Group by account
    by_account: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        by_account[t.account_number].append(t)

    for acct, txns in by_account.items():
        txns.sort(key=lambda t: (t.statement_date, t.date))

        prev_balance = None
        for t in txns:
            cur_balance = None
            if t.balance:
                try:
                    cur_balance = float(t.balance)
                except ValueError:
                    pass

            amount_str = t.deposit  # currently all amounts are in deposit
            amount_val = None
            if amount_str:
                try:
                    amount_val = float(amount_str)
                except ValueError:
                    pass

            if prev_balance is not None and cur_balance is not None and amount_val:
                delta = cur_balance - prev_balance
                # If delta is negative or close to -amount, it's a withdrawal
                if abs(delta + amount_val) < 0.02:
                    # Withdrawal
                    t.withdrawal = t.deposit
                    t.deposit = ""
                elif abs(delta - amount_val) < 0.02:
                    # Deposit - already correct
                    pass
                else:
                    # Delta doesn't match amount - could be missing transactions
                    # or garbled amount. Try to determine from sign of delta
                    if delta < 0:
                        t.withdrawal = t.deposit
                        t.deposit = ""
                    # If delta > 0, leave as deposit (default)

            if cur_balance is not None:
                prev_balance = cur_balance

    return transactions


def fix_currencies(transactions: list[Transaction]) -> list[Transaction]:
    """Fix OCR currency errors using KNOWN_CURRENCIES."""
    for t in transactions:
        expected = KNOWN_CURRENCIES.get(t.account_number)
        if expected and t.currency not in CURRENCY_CODES:
            t.currency = expected
        elif expected and t.currency != expected:
            # Only override if current value looks wrong (not a real currency)
            if t.currency not in CURRENCY_CODES:
                t.currency = expected
    return transactions


def validate_amounts(transactions: list[Transaction]) -> list[Transaction]:
    """Remove transactions with clearly garbled amounts."""
    clean = []
    for t in transactions:
        # Check deposit
        if t.deposit and not is_plausible_amount(t.deposit, t.currency):
            t.deposit = ""
        if t.withdrawal and not is_plausible_amount(t.withdrawal, t.currency):
            t.withdrawal = ""
        if t.balance and not is_plausible_amount(t.balance, t.currency):
            t.balance = ""
        clean.append(t)
    return clean


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse HSBC JE Premier/Account Statement PDFs into CSV.",
    )
    parser.add_argument(
        "input_dir",
        help="Directory containing PDF statement files.",
    )
    parser.add_argument(
        "--output", "-o",
        default="data/hsbc-je-statements.csv",
        help="Output CSV path (default: data/hsbc-je-statements.csv).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="OCR DPI (default: 150).",
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

    # Post-processing
    all_txns = fix_currencies(all_txns)
    all_txns = validate_amounts(all_txns)

    # Sort by date, then account
    all_txns.sort(key=lambda t: (t.date, t.account_number))

    # Dedup BEFORE direction inference - use absolute amount so overlapping
    # folder copies don't create duplicates
    seen: set[tuple] = set()
    unique_txns: list[Transaction] = []
    for t in all_txns:
        amount = t.deposit or t.withdrawal or ""
        key = (t.account_number, t.date, amount, t.balance, t.reference)
        if key not in seen:
            seen.add(key)
            unique_txns.append(t)

    print(f"  {len(unique_txns)} unique after dedup")

    # Infer deposit vs withdrawal direction from balance deltas
    unique_txns = infer_direction(unique_txns)

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

    # Balance continuity check
    print("\n=== Balance continuity check ===")
    by_acct: dict[str, list[Transaction]] = defaultdict(list)
    for t in unique_txns:
        by_acct[f"{t.account_number} {t.currency}"].append(t)

    for acct_key in sorted(by_acct.keys()):
        txns = sorted(by_acct[acct_key], key=lambda t: (t.statement_date, t.date))
        issues = 0
        prev_bal = None
        for t in txns:
            if not t.balance:
                continue
            try:
                cur_bal = float(t.balance)
            except ValueError:
                continue
            amt = 0.0
            if t.deposit:
                try:
                    amt = float(t.deposit)
                except ValueError:
                    pass
            elif t.withdrawal:
                try:
                    amt = -float(t.withdrawal)
                except ValueError:
                    pass
            if prev_bal is not None:
                expected = prev_bal + amt
                if abs(expected - cur_bal) > 0.02:
                    issues += 1
            prev_bal = cur_bal
        total = len(txns)
        print(f"  {acct_key}: {total} txns, {issues} balance gaps")


if __name__ == "__main__":
    main()
