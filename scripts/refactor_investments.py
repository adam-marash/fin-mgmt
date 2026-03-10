#!/usr/bin/env python3
"""Refactor capital call entries to 4-leg pattern (issue #31).

For each capital call entry (has Liabilities:Commitments + bank/suspense),
adds two new legs:
  Assets:Investments:X   +amount CCY
  Equity:Commitments     -amount CCY

where amount/CCY come from the Liabilities:Commitments posting.

Skips commitment openings (Equity:Commitments already present without bank/suspense).
Skips entries that already have Assets:Investments.

Usage:
  python scripts/refactor_investments.py          # dry run
  python scripts/refactor_investments.py --write   # apply changes
"""
import argparse
import re
import sys
from pathlib import Path
from collections import defaultdict


def find_commitment_postings(lines, start_idx):
    """Given transaction start line, find the commitment posting and check pattern."""
    postings = []
    i = start_idx + 1
    while i < len(lines):
        line = lines[i]
        # Transaction ends at blank line, next directive, or EOF
        if line.strip() == '' or (line[0:1] not in (' ', '\t', '') and line.strip()):
            if not line.startswith(' ') and not line.startswith('\t') and line.strip():
                break
            if line.strip() == '':
                break
        if line.strip().startswith(';'):
            i += 1
            continue
        # Check if it's a posting (starts with whitespace and has an account)
        stripped = line.strip()
        if stripped and not stripped.startswith(';'):
            postings.append((i, stripped))
        i += 1
    return postings, i


def parse_posting_account(posting_text):
    """Extract account name from a posting line."""
    # Account is the first token (colon-separated segments)
    parts = posting_text.split()
    if parts:
        return parts[0]
    return None


def extract_amount_from_commitment(posting_text):
    """Extract amount and currency from a Liabilities:Commitments posting."""
    # Pattern: Liabilities:Commitments:X  123,456.00 USD
    # Remove the account part
    parts = posting_text.split()
    account = parts[0]
    investment = account.split(':')[2]

    # Find the amount - it's the numeric value after the account
    amount_str = None
    currency = None
    for j, part in enumerate(parts[1:], 1):
        # Skip metadata like ; comments
        if part == ';':
            break
        # Check if it looks like a number (with optional commas)
        cleaned = part.replace(',', '')
        try:
            float(cleaned)
            amount_str = part
            # Currency should be next
            if j + 1 < len(parts) and parts[j + 1] != ';':
                currency = parts[j + 1]
            break
        except ValueError:
            continue

    return investment, amount_str, currency


def get_indent(lines, posting_indices):
    """Get the indentation used in existing postings."""
    for idx, _ in posting_indices:
        line = lines[idx]
        indent = len(line) - len(line.lstrip())
        return line[:indent]
    return '  '


def process_file(filepath, dry_run=True):
    """Process a single entries.beancount file."""
    with open(filepath, 'r') as f:
        lines = f.readlines()

    modifications = []  # (line_after_which_to_insert, new_lines)
    i = 0
    while i < len(lines):
        line = lines[i]

        # Look for transaction start lines (date + * or !)
        match = re.match(r'^(\d{4}-\d{2}-\d{2})\s+[*!]\s+', line)
        if not match:
            i += 1
            continue

        # Found a transaction - analyze its postings
        postings, end_idx = find_commitment_postings(lines, i)

        accounts = []
        commitment_posting = None
        has_investment = False
        has_equity_commitment = False
        has_bank = False
        has_suspense = False

        for pidx, ptext in postings:
            acc = parse_posting_account(ptext)
            if acc is None:
                continue
            accounts.append((pidx, acc, ptext))
            if acc.startswith('Liabilities:Commitments:'):
                commitment_posting = (pidx, acc, ptext)
            if acc.startswith('Assets:Investments:'):
                has_investment = True
            if acc == 'Equity:Commitments':
                has_equity_commitment = True
            if acc.startswith('Assets:Banks:'):
                has_bank = True
            if acc.startswith('Assets:Suspense') or acc.startswith('Assets:Deposits:'):
                has_suspense = True

        # Skip if no commitment posting
        if commitment_posting is None:
            i = end_idx
            continue

        # Skip commitment openings (has Equity:Commitments but no bank/suspense)
        if has_equity_commitment and not has_bank and not has_suspense:
            i = end_idx
            continue

        # Skip if already has Assets:Investments (already refactored)
        if has_investment:
            i = end_idx
            continue

        # Skip if no bank or suspense (not a capital call payment)
        if not has_bank and not has_suspense:
            i = end_idx
            continue

        # This is a capital call entry that needs refactoring
        investment, amount_str, currency = extract_amount_from_commitment(commitment_posting[2])

        if amount_str is None or currency is None:
            print(f"WARNING: Could not parse amount from {filepath}:{commitment_posting[0]+1}: {commitment_posting[2]}")
            i = end_idx
            continue

        # Determine indentation from existing postings
        indent = get_indent(lines, postings)

        # Find the last posting line to insert after
        last_posting_idx = postings[-1][0]

        # Create new legs
        new_lines = [
            f"{indent}Assets:Investments:{investment}  {amount_str} {currency}\n",
            f"{indent}Equity:Commitments  -{amount_str} {currency}\n",
        ]

        modifications.append((last_posting_idx, new_lines, line.strip()[:80]))

        i = end_idx

    if not modifications:
        return 0

    if dry_run:
        for last_idx, new_lines, txn_desc in modifications:
            print(f"  {filepath}:{last_idx+1} after: {lines[last_idx].strip()[:60]}")
            for nl in new_lines:
                print(f"    + {nl.rstrip()}")
        return len(modifications)

    # Apply modifications in reverse order (so line numbers stay valid)
    for last_idx, new_lines, _ in reversed(modifications):
        for j, nl in enumerate(new_lines):
            lines.insert(last_idx + 1 + j, nl)

    with open(filepath, 'w') as f:
        f.writelines(lines)

    return len(modifications)


def main():
    parser = argparse.ArgumentParser(description='Refactor capital call entries to 4-leg pattern')
    parser.add_argument('--write', action='store_true', help='Apply changes (default: dry run)')
    args = parser.parse_args()

    ledger_dir = Path('ledger')
    files = sorted(ledger_dir.rglob('entries.beancount'))

    total = 0
    for f in files:
        count = process_file(f, dry_run=not args.write)
        total += count

    mode = "APPLIED" if args.write else "DRY RUN"
    print(f"\n{mode}: {total} entries would be/were modified across {len(files)} files")


if __name__ == '__main__':
    main()
