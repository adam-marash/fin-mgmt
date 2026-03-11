#!/usr/bin/env python3
"""Fix HSBC JE statement CSV using LLM to correct OCR parsing errors.

Identifies intra-statement balance gaps, OCRs the relevant PDFs,
sends the account section text to Claude for structured extraction,
and patches the CSV.
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from dataclasses import fields
from pathlib import Path

import anthropic

from parse_hsbc_je_stmts import (
    ACCT_RE,
    ACCT_TYPES,
    KNOWN_CURRENCIES,
    Transaction,
    clean_ocr_line,
    extract_statement_date,
    is_composite,
    ocr_pdf,
)

CSV_PATH = Path("data/hsbc-je-statements.csv")
PDF_DIR = Path("inbox/hsbc-je-dropbox")


# ---------------------------------------------------------------------------
# Step 1: Find intra-statement balance gaps
# ---------------------------------------------------------------------------

def load_csv() -> list[dict]:
    with open(CSV_PATH) as f:
        return list(csv.DictReader(f))


def find_problem_combos(txns: list[dict]) -> list[tuple[str, str, str]]:
    """Return [(account, currency, statement_date)] with intra-statement issues."""
    by_acct: dict[str, list[dict]] = defaultdict(list)
    for t in txns:
        by_acct[f"{t['account_number']} {t['currency']}"].append(t)

    problems = set()
    for acct_key, acct_txns in by_acct.items():
        acct_txns.sort(key=lambda t: (t["statement_date"], t["date"]))
        prev_bal = None
        prev_stmt = ""
        for t in acct_txns:
            stmt = t["statement_date"]
            # Missing balance is a problem
            if not t["balance"]:
                if stmt == prev_stmt or prev_stmt == "":
                    acct, ccy = acct_key.split(" ", 1)
                    problems.add((acct, ccy, stmt))
                continue
            cur_bal = float(t["balance"])
            amt = 0.0
            if t["deposit"]:
                amt = float(t["deposit"])
            elif t["withdrawal"]:
                amt = -float(t["withdrawal"])
            if prev_bal is not None and stmt == prev_stmt:
                expected = prev_bal + amt
                if abs(expected - cur_bal) > 0.02:
                    acct, ccy = acct_key.split(" ", 1)
                    problems.add((acct, ccy, stmt))
            prev_bal = cur_bal
            prev_stmt = stmt

    return sorted(problems)


# ---------------------------------------------------------------------------
# Step 2: Map statement_date -> PDF file
# ---------------------------------------------------------------------------

def build_date_to_pdf_map() -> dict[str, list[Path]]:
    """Map statement dates to PDF file paths using filename patterns."""
    pdfs = sorted(PDF_DIR.rglob("*.pdf"))
    date_to_pdfs: dict[str, list[Path]] = defaultdict(list)
    for p in pdfs:
        m = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", p.name)
        if m:
            d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            date_to_pdfs[d].append(p)
    return date_to_pdfs


def find_pdf_for_date(
    stmt_date: str,
    date_map: dict[str, list[Path]],
    ocr_cache: dict[str, list[str]],
) -> Path | None:
    """Find the PDF for a given statement date."""
    # Direct match
    if stmt_date in date_map:
        return date_map[stmt_date][0]

    # Fuzzy: statement date may differ from filename by a few days
    # Try nearby dates
    y, m, d = stmt_date.split("-")
    for delta in range(-3, 4):
        from datetime import date, timedelta
        try:
            candidate = date(int(y), int(m), int(d)) + timedelta(days=delta)
            key = candidate.strftime("%Y-%m-%d")
            if key in date_map:
                return date_map[key][0]
        except ValueError:
            continue

    # Try OCR-based lookup from cache
    for pdf_str, pages in ocr_cache.items():
        sd = extract_statement_date(pages, Path(pdf_str).name)
        if sd == stmt_date:
            return Path(pdf_str)

    return None


# ---------------------------------------------------------------------------
# Step 3: Extract account section from OCR text
# ---------------------------------------------------------------------------

def extract_account_section(
    pages: list[str], account: str, currency: str
) -> str:
    """Extract the OCR text section for a specific account."""
    all_text = "\n".join(pages)
    lines = all_text.split("\n")

    # Find the account header
    section_lines = []
    in_section = False
    acct_suffix = account.split("-")[-1] if "-" in account else account

    for i, line in enumerate(lines):
        cleaned = clean_ocr_line(line)

        # Detect start of our account section
        if not in_section:
            if acct_suffix in cleaned:
                for atype in ACCT_TYPES + ["BANK ACCOUNT", "FIXED DEPOSITS"]:
                    if atype in cleaned:
                        in_section = True
                        section_lines.append(cleaned)
                        break
                if not in_section and ACCT_RE.search(cleaned):
                    in_section = True
                    section_lines.append(cleaned)
            continue

        # Detect end: next account header or certain markers
        if in_section:
            # Check if this is a new account section
            is_new_section = False
            for atype in ACCT_TYPES + ["BANK ACCOUNT", "FIXED DEPOSITS"]:
                if atype in cleaned and acct_suffix not in cleaned:
                    is_new_section = True
                    break
            if is_new_section:
                break

            # Also break on summary sections
            if "Summary of Your Portfolio" in cleaned:
                break
            if "About your statement" in cleaned:
                break

            section_lines.append(cleaned)

    return "\n".join(section_lines)


# ---------------------------------------------------------------------------
# Step 4: LLM extraction
# ---------------------------------------------------------------------------

def llm_extract_transactions(
    client: anthropic.Anthropic,
    section_text: str,
    account: str,
    currency: str,
    statement_date: str,
    existing_txns: list[dict],
) -> list[dict]:
    """Use Claude to extract transactions from an account section."""
    # Build context about what we already have
    existing_summary = ""
    if existing_txns:
        existing_summary = "Current (possibly incorrect) transactions for this section:\n"
        for t in existing_txns:
            dep = t.get("deposit", "")
            wth = t.get("withdrawal", "")
            bal = t.get("balance", "")
            existing_summary += (
                f"  {t['date']} | dep={dep} wth={wth} bal={bal} | "
                f"{t['description'][:60]}\n"
            )

    prompt = f"""You are extracting bank transactions from OCR text of an HSBC statement.

Account: {account}
Currency: {currency}
Statement date: {statement_date}

OCR TEXT OF THE ACCOUNT SECTION:
```
{section_text}
```

{existing_summary}

Extract ALL transactions from this section. For each transaction provide:
- date: YYYY-MM-DD format
- description: the transaction description (clean up OCR artifacts)
- reference: the REF code if present (e.g. YIRO-09057)
- deposit: amount if money came IN (empty string if withdrawal)
- withdrawal: amount if money went OUT (empty string if deposit)
- balance: the running balance after this transaction

IMPORTANT:
- The opening/closing balance lines are NOT transactions - use them to verify your math
- Each transaction's balance should equal: previous_balance + deposit - withdrawal
- Amounts should be plain numbers without commas (e.g. 1234.56 not 1,234.56)
- {currency} amounts {"have no decimal places" if currency == "JPY" else "have 2 decimal places"}
- If you see "DR" next to an amount or balance, it means debit (negative/withdrawal)
- Distinguish deposits from withdrawals based on column position in the original or the running balance

Return ONLY a JSON array of objects. No markdown, no explanation."""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()

    # Parse JSON - handle possible markdown wrapping
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"    JSON parse error: {e}", file=sys.stderr)
        print(f"    Raw response: {text[:500]}", file=sys.stderr)
        return []

    return result


# ---------------------------------------------------------------------------
# Step 5: Patch CSV
# ---------------------------------------------------------------------------

def patch_csv(
    txns: list[dict],
    patches: dict[tuple[str, str], list[dict]],
) -> list[dict]:
    """Replace transactions for patched statement-account combos."""
    # Build set of (account, statement_date) to replace
    to_replace = set(patches.keys())

    # Keep non-patched transactions
    result = [
        t for t in txns
        if (t["account_number"], t["statement_date"]) not in to_replace
    ]

    # Add patched transactions
    for (acct, stmt_date), new_txns in patches.items():
        result.extend(new_txns)

    # Sort
    result.sort(key=lambda t: (t.get("date", ""), t.get("account_number", "")))
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fix HSBC JE CSV using LLM for balance gap correction."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be fixed without writing.",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Limit number of problem combos to process (0=all).",
    )
    args = parser.parse_args()

    # Load current CSV
    txns = load_csv()
    print(f"Loaded {len(txns)} transactions from {CSV_PATH}")

    # Find problems
    problems = find_problem_combos(txns)
    print(f"Found {len(problems)} problem statement-account combos")

    if not problems:
        print("No problems found!")
        return

    # Group by statement_date to minimize OCR (one PDF can have multiple accounts)
    by_stmt: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for acct, ccy, stmt_date in problems:
        by_stmt[stmt_date].append((acct, ccy))

    print(f"Across {len(by_stmt)} unique statement dates")

    # Build PDF map
    date_map = build_date_to_pdf_map()

    # Initialize Claude client
    client = anthropic.Anthropic()

    # OCR cache
    ocr_cache: dict[str, list[str]] = {}

    # Process
    patches: dict[tuple[str, str], list[dict]] = {}
    processed = 0
    limit = args.limit or len(by_stmt)

    for stmt_date in sorted(by_stmt.keys()):
        if processed >= limit:
            break

        accounts = by_stmt[stmt_date]
        print(f"\n--- {stmt_date} ({len(accounts)} accounts) ---")

        # Find PDF
        pdf_path = find_pdf_for_date(stmt_date, date_map, ocr_cache)
        if not pdf_path:
            # Try scanning all PDFs in the directory
            print(f"  No PDF found for {stmt_date}, scanning...")
            for p in sorted(PDF_DIR.rglob("*.pdf")):
                if str(p) in ocr_cache:
                    continue
                pages = ocr_pdf(str(p))
                ocr_cache[str(p)] = pages
                sd = extract_statement_date(pages, p.name)
                if sd == stmt_date:
                    pdf_path = p
                    break
            if not pdf_path:
                print(f"  SKIP: no PDF found for {stmt_date}")
                continue

        print(f"  PDF: {pdf_path.relative_to(PDF_DIR)}")

        # OCR (cached)
        pdf_key = str(pdf_path)
        if pdf_key not in ocr_cache:
            print(f"  OCR-ing {pdf_path.name}...")
            ocr_cache[pdf_key] = ocr_pdf(pdf_key)
        pages = ocr_cache[pdf_key]

        for acct, ccy in accounts:
            print(f"  Account: {acct} {ccy}")

            # Extract section
            section = extract_account_section(pages, acct, ccy)
            if not section or len(section) < 20:
                print(f"    SKIP: no section text found")
                continue

            # Get existing transactions for comparison
            existing = [
                t for t in txns
                if t["account_number"] == acct
                and t["statement_date"] == stmt_date
            ]

            print(f"    Section: {len(section)} chars, {len(existing)} existing txns")

            if args.dry_run:
                print(f"    [DRY RUN] Would send to LLM")
                continue

            # LLM extract
            llm_txns = llm_extract_transactions(
                client, section, acct, ccy, stmt_date, existing,
            )

            if not llm_txns:
                print(f"    LLM returned no transactions")
                continue

            print(f"    LLM returned {len(llm_txns)} transactions")

            # Validate LLM output - check balance continuity
            llm_ok = True
            prev_bal = None
            for lt in llm_txns:
                bal = lt.get("balance", "")
                dep = lt.get("deposit", "")
                wth = lt.get("withdrawal", "")
                if not bal:
                    continue
                try:
                    cur = float(bal)
                except ValueError:
                    continue
                amt = 0.0
                if dep:
                    try:
                        amt = float(dep)
                    except ValueError:
                        pass
                if wth:
                    try:
                        amt = -float(wth)
                    except ValueError:
                        pass
                if prev_bal is not None:
                    expected = prev_bal + amt
                    if abs(expected - cur) > 0.02:
                        print(
                            f"    LLM balance gap: prev={prev_bal} + "
                            f"amt={amt} = {expected}, got={cur}"
                        )
                        llm_ok = False
                prev_bal = cur

            if not llm_ok:
                print(f"    WARNING: LLM output has balance gaps, using anyway")

            # Convert to CSV row format
            acct_type = ""
            for t in existing:
                if t.get("account_type"):
                    acct_type = t["account_type"]
                    break

            new_rows = []
            for lt in llm_txns:
                new_rows.append({
                    "statement_date": stmt_date,
                    "account_number": acct,
                    "account_type": acct_type,
                    "currency": ccy,
                    "date": lt.get("date", ""),
                    "description": lt.get("description", ""),
                    "reference": lt.get("reference", ""),
                    "deposit": str(lt.get("deposit", "")),
                    "withdrawal": str(lt.get("withdrawal", "")),
                    "balance": str(lt.get("balance", "")),
                })

            patches[(acct, stmt_date)] = new_rows

            # Show diff
            print(f"    Replacing {len(existing)} txns with {len(new_rows)}")
            for r in new_rows:
                dep = r["deposit"] or "-"
                wth = r["withdrawal"] or "-"
                print(
                    f"      {r['date']} dep={dep} wth={wth} "
                    f"bal={r['balance']} {r['description'][:50]}"
                )

        processed += 1

    if args.dry_run:
        print(f"\n[DRY RUN] Would patch {len(patches)} statement-account combos")
        return

    if not patches:
        print("\nNo patches to apply")
        return

    # Apply patches
    print(f"\nApplying {len(patches)} patches...")
    result = patch_csv(txns, patches)

    # Write
    fieldnames = [f.name for f in fields(Transaction)]
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in result:
            writer.writerow({fn: t.get(fn, "") for fn in fieldnames})

    print(f"Written {len(result)} transactions to {CSV_PATH}")

    # Re-check
    print("\n=== Post-fix balance check ===")
    remaining = find_problem_combos(result)
    print(f"Remaining problem combos: {len(remaining)}")
    for acct, ccy, stmt in remaining:
        print(f"  {acct} {ccy} @ {stmt}")


if __name__ == "__main__":
    main()
