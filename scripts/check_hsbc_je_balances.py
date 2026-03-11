#!/usr/bin/env python3
"""Extract opening/closing balances from HSBC JE PDFs and check continuity.

For each PDF statement, extracts the BALANCE BROUGHT FORWARD (opening) and
CLOSING BALANCE (closing) for each account. Then verifies that closing[N] ==
opening[N+1] for each account across all statements.

This is an OCR verification exercise - separate from transaction extraction.
"""

import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, fields
from pathlib import Path

from parse_hsbc_je_stmts import (
    ACCT_RE,
    ACCT_TYPES,
    ACCT_TYPE_CANONICAL,
    KNOWN_CURRENCIES,
    clean_amount,
    clean_ocr_line,
    detect_account_header,
    extract_statement_date,
    extract_summary_balances,
    is_boilerplate_page,
    is_composite,
    ocr_pdf,
)

PDF_DIR = Path("inbox/hsbc-je-dropbox")
OUT_PATH = Path("data/hsbc-je-balances.csv")


@dataclass
class BalanceRecord:
    statement_date: str
    source_file: str
    account_number: str
    account_type: str
    currency: str
    opening_balance: str
    closing_balance: str
    summary_balance: str  # from portfolio summary table (composite only)


# ---------------------------------------------------------------------------
# Balance extraction
# ---------------------------------------------------------------------------

AMOUNT_RE = re.compile(r"[\d,]+(?:\.\d+)?")


def extract_rightmost_amount(line: str) -> str:
    """Extract the rightmost monetary amount from a line."""
    amounts = AMOUNT_RE.findall(line)
    if not amounts:
        return ""
    # Take rightmost
    raw = amounts[-1]
    return clean_amount(raw)


def detect_currency_from_lines(lines: list[str], start: int, end: int) -> str:
    """Look for a currency code in the given line range."""
    for i in range(start, min(end, len(lines))):
        line = lines[i]
        for ccy in ("USD", "EUR", "GBP", "CHF", "JPY", "AUD"):
            if ccy in line:
                return ccy
    return ""


def extract_balances_from_pages(
    pages: list[str], pdf_name: str
) -> list[BalanceRecord]:
    """Extract opening/closing balances from all account sections in a PDF."""
    stmt_date = extract_statement_date(pages, pdf_name)
    if not stmt_date:
        return []

    composite = is_composite(pages)

    # Get summary balances for cross-check (composite only)
    summary_bals = extract_summary_balances(pages) if composite else {}

    # Combine all non-boilerplate pages
    all_lines = []
    for page in pages:
        if is_boilerplate_page(page):
            continue
        for line in page.split("\n"):
            all_lines.append(clean_ocr_line(line))

    records = []
    current_acct = ""
    current_type = ""
    current_ccy = ""
    opening = ""
    closing = ""
    in_fixed_deposits = False

    i = 0
    while i < len(all_lines):
        line = all_lines[i]

        # Detect account section header
        header = detect_account_header(line, all_lines, i)
        if header:
            # Save previous account if we have data
            if current_acct and (opening or closing):
                summary_val = ""
                if current_acct in summary_bals:
                    summary_val = str(summary_bals[current_acct])
                records.append(BalanceRecord(
                    statement_date=stmt_date,
                    source_file=pdf_name,
                    account_number=current_acct,
                    account_type=current_type,
                    currency=current_ccy,
                    opening_balance=opening,
                    closing_balance=closing,
                    summary_balance=summary_val,
                ))

            current_type, current_acct = header
            current_ccy = KNOWN_CURRENCIES.get(current_acct, "")
            opening = ""
            closing = ""

            # Check for fixed deposits section
            in_fixed_deposits = "FIXED DEPOSITS" in line or "FIXED DEPOSIT" in line

            # Try to detect currency from nearby lines
            if not current_ccy:
                current_ccy = detect_currency_from_lines(all_lines, i, i + 5)

            i += 1
            continue

        # Skip if no current account
        if not current_acct:
            i += 1
            continue

        # Skip fixed deposit table rows (different format)
        if in_fixed_deposits:
            # Fixed deposits have a table with account numbers - extract balance
            acct_match = ACCT_RE.search(line)
            if acct_match and acct_match.group(0) != current_acct:
                # New FD account row
                fd_acct = acct_match.group(0)
                amounts = AMOUNT_RE.findall(line[acct_match.end():])
                if amounts:
                    fd_bal = clean_amount(amounts[-1])
                    fd_ccy = ""
                    for ccy in ("USD", "EUR", "GBP", "CHF", "JPY", "AUD"):
                        if ccy in line:
                            fd_ccy = ccy
                            break
                    if not fd_ccy:
                        fd_ccy = KNOWN_CURRENCIES.get(fd_acct, "")
                    summary_val = ""
                    if fd_acct in summary_bals:
                        summary_val = str(summary_bals[fd_acct])
                    records.append(BalanceRecord(
                        statement_date=stmt_date,
                        source_file=pdf_name,
                        account_number=fd_acct,
                        account_type="FIXED DEPOSITS",
                        currency=fd_ccy,
                        opening_balance=fd_bal,
                        closing_balance=fd_bal,
                        summary_balance=summary_val,
                    ))
            i += 1
            continue

        # Detect opening balance
        stripped = re.sub(r"^[\[('{\\|]+\s*", "", line)
        if "BALANCE BROUGHT FORWARD" in stripped or "OPENING BALANCE" in stripped:
            amt = extract_rightmost_amount(line)
            if amt:
                opening = amt
            else:
                # Amount might be on the next line
                if i + 1 < len(all_lines):
                    amt = extract_rightmost_amount(all_lines[i + 1])
                    if amt:
                        opening = amt

        # Detect closing balance
        if "CLOSING BALANCE" in stripped or "BALANCE CARRIED FORWARD" in stripped:
            amt = extract_rightmost_amount(line)
            if amt:
                closing = amt
            else:
                if i + 1 < len(all_lines):
                    amt = extract_rightmost_amount(all_lines[i + 1])
                    if amt:
                        closing = amt

        i += 1

    # Don't forget last account
    if current_acct and (opening or closing):
        summary_val = ""
        if current_acct in summary_bals:
            summary_val = str(summary_bals[current_acct])
        records.append(BalanceRecord(
            statement_date=stmt_date,
            source_file=pdf_name,
            account_number=current_acct,
            account_type=current_type,
            currency=current_ccy,
            opening_balance=opening,
            closing_balance=closing,
            summary_balance=summary_val,
        ))

    # For composite statements, add any summary-only accounts (no section found)
    if composite:
        seen_accts = {r.account_number for r in records}
        for acct, bal in summary_bals.items():
            if acct not in seen_accts:
                ccy = KNOWN_CURRENCIES.get(acct, "")
                records.append(BalanceRecord(
                    statement_date=stmt_date,
                    source_file=pdf_name,
                    account_number=acct,
                    account_type="",
                    currency=ccy,
                    opening_balance="",
                    closing_balance=str(bal),
                    summary_balance=str(bal),
                ))

    return records


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def dedup_records(records: list[BalanceRecord]) -> list[BalanceRecord]:
    """Deduplicate by (statement_date, account_number), preferring more data."""
    by_key: dict[tuple[str, str], list[BalanceRecord]] = defaultdict(list)
    for r in records:
        by_key[(r.statement_date, r.account_number)].append(r)

    result = []
    conflicts = []
    for key, recs in sorted(by_key.items()):
        if len(recs) == 1:
            result.append(recs[0])
            continue

        # Pick the one with the most populated fields
        def score(r: BalanceRecord) -> int:
            s = 0
            if r.opening_balance:
                s += 1
            if r.closing_balance:
                s += 1
            if r.summary_balance:
                s += 1
            if r.currency:
                s += 1
            return s

        recs.sort(key=score, reverse=True)
        best = recs[0]

        # Check for conflicting values
        for other in recs[1:]:
            if (other.closing_balance and best.closing_balance and
                    other.closing_balance != best.closing_balance):
                conflicts.append((key, best, other))

        result.append(best)

    if conflicts:
        print(f"\nWARNING: {len(conflicts)} dedup conflicts:", file=sys.stderr)
        for key, a, b in conflicts[:10]:
            print(f"  {key}: close={a.closing_balance} vs {b.closing_balance} "
                  f"({a.source_file} vs {b.source_file})", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# Continuity check
# ---------------------------------------------------------------------------

def check_continuity(records: list[BalanceRecord]) -> list[dict]:
    """Check that closing[N] == opening[N+1] for each account."""
    by_acct: dict[str, list[BalanceRecord]] = defaultdict(list)
    for r in records:
        by_acct[r.account_number].append(r)

    gaps = []
    for acct, recs in sorted(by_acct.items()):
        recs.sort(key=lambda r: r.statement_date)
        for i in range(len(recs) - 1):
            curr = recs[i]
            nxt = recs[i + 1]

            if not curr.closing_balance or not nxt.opening_balance:
                continue

            try:
                close_val = float(curr.closing_balance)
                open_val = float(nxt.opening_balance)
            except ValueError:
                continue

            if abs(close_val - open_val) > 0.01:
                gaps.append({
                    "account": acct,
                    "currency": curr.currency or nxt.currency,
                    "stmt_close": curr.statement_date,
                    "stmt_open": nxt.statement_date,
                    "closing": curr.closing_balance,
                    "opening": nxt.opening_balance,
                    "diff": f"{open_val - close_val:.2f}",
                    "close_src": curr.source_file,
                    "open_src": nxt.source_file,
                })

    return gaps


# ---------------------------------------------------------------------------
# Summary vs closing cross-check
# ---------------------------------------------------------------------------

def check_summary_vs_closing(records: list[BalanceRecord]) -> list[dict]:
    """Check that portfolio summary balance matches closing balance."""
    mismatches = []
    for r in records:
        if not r.summary_balance or not r.closing_balance:
            continue
        try:
            summary_val = float(r.summary_balance)
            closing_val = float(r.closing_balance)
        except ValueError:
            continue
        if abs(summary_val - closing_val) > 0.01:
            mismatches.append({
                "account": r.account_number,
                "statement_date": r.statement_date,
                "summary": r.summary_balance,
                "closing": r.closing_balance,
                "diff": f"{closing_val - summary_val:.2f}",
                "source": r.source_file,
            })
    return mismatches


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract opening/closing balances from HSBC JE PDFs."
    )
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="OCR DPI (default: 150).",
    )
    parser.add_argument(
        "--output", "-o", type=str, default=str(OUT_PATH),
        help=f"Output CSV path (default: {OUT_PATH}).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print per-PDF progress.",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit number of PDFs to process (0=all).",
    )
    args = parser.parse_args()

    # Collect all PDFs
    pdfs = sorted(PDF_DIR.rglob("*.pdf"))
    print(f"Found {len(pdfs)} PDFs in {PDF_DIR}")

    if args.limit:
        pdfs = pdfs[:args.limit]
        print(f"Limiting to {args.limit} PDFs")

    # Process all PDFs
    all_records: list[BalanceRecord] = []
    errors = 0

    for i, pdf_path in enumerate(pdfs):
        rel = pdf_path.relative_to(PDF_DIR)
        if args.verbose:
            print(f"  [{i+1}/{len(pdfs)}] {rel}...", end=" ", flush=True)

        try:
            pages = ocr_pdf(str(pdf_path), dpi=args.dpi)
            recs = extract_balances_from_pages(pages, str(rel))
            all_records.extend(recs)
            if args.verbose:
                print(f"{len(recs)} accounts")
        except Exception as e:
            errors += 1
            if args.verbose:
                print(f"ERROR: {e}")
            else:
                print(f"  ERROR: {rel}: {e}", file=sys.stderr)

    print(f"\nExtracted {len(all_records)} balance records from {len(pdfs)} PDFs "
          f"({errors} errors)")

    # Dedup
    records = dedup_records(all_records)
    print(f"After dedup: {len(records)} unique (statement_date, account) combos")

    # Summary stats
    acct_counts = defaultdict(int)
    for r in records:
        acct_counts[f"{r.account_number} {r.currency}"] += 1
    print("\n=== Accounts ===")
    for acct, count in sorted(acct_counts.items()):
        print(f"  {acct}: {count} statements")

    # Write CSV
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [f.name for f in fields(BalanceRecord)]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in sorted(records, key=lambda r: (r.account_number, r.statement_date)):
            writer.writerow({fn: getattr(r, fn) for fn in fieldnames})
    print(f"\nWritten to {out_path}")

    # Check summary vs closing
    summary_mismatches = check_summary_vs_closing(records)
    if summary_mismatches:
        print(f"\n=== Summary vs Closing mismatches: {len(summary_mismatches)} ===")
        for m in summary_mismatches[:20]:
            print(f"  {m['account']} @ {m['statement_date']}: "
                  f"summary={m['summary']} close={m['closing']} "
                  f"diff={m['diff']}")

    # Check continuity
    gaps = check_continuity(records)
    print(f"\n=== Balance Continuity ===")
    if not gaps:
        print("All opening/closing balances are continuous!")
    else:
        print(f"{len(gaps)} discontinuities:")
        for g in gaps:
            print(f"  {g['account']} {g['currency']}: "
                  f"{g['stmt_close']} close={g['closing']} -> "
                  f"{g['stmt_open']} open={g['opening']} "
                  f"(diff={g['diff']})")


if __name__ == "__main__":
    main()
