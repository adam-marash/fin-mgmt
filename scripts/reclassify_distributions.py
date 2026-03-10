#!/usr/bin/env python3
"""Reclassify Unclassified distributions using FO data (issue #30).

Cross-references Income:Investments:*:Unclassified entries against FO CSV
to determine yield vs capital-return classification.

- Yield -> Income:Investments:<Investment>:Yield
- Capital-Return -> Assets:Investments:<Investment> (reduces cost basis)

Usage:
  python scripts/reclassify_distributions.py          # dry run
  python scripts/reclassify_distributions.py --write   # apply changes
"""
import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path('.')
FO_CSV = ROOT / 'data/2026-03-05-fo-transactions/tamar-transactions.csv'
KNOWLEDGE = ROOT / 'knowledge.json'
FO_SOURCE = str(FO_CSV)


def classify_account(cls_type, investment):
    """Return the target account for a given classification type."""
    if cls_type == 'Capital-Return':
        return f'Assets:Investments:{investment}'
    else:
        return f'Income:Investments:{investment}:{cls_type}'


def load_investment_map():
    """Build FO name -> beancount name mapping from knowledge.json."""
    with open(KNOWLEDGE) as f:
        knowledge = json.load(f)

    inv_map = {}
    for key, val in knowledge.get('investments', {}).items():
        if isinstance(val, dict):
            bc_name = val.get('beancount_name', key)
            for alias in val.get('aliases', []):
                inv_map[alias] = bc_name
            inv_map[key] = bc_name
            inv_map[bc_name] = bc_name

    # Manual additions not in knowledge.json
    inv_map['Liquidity Capital II, L.P'] = 'Liquidity-Capital'
    inv_map['Liquidity Capital II, L.P.'] = 'Liquidity-Capital'
    return inv_map


def load_fo_distributions(inv_map):
    """Load FO withdrawal entries grouped by (investment, date)."""
    fo_by_inv_date = defaultdict(list)

    with open(FO_CSV) as f:
        for row in csv.DictReader(f):
            if row['tx_type'] not in ('yield_withdrawal', 'withdrawal'):
                continue
            raw_name = row['investment']
            bc_name = inv_map.get(raw_name, raw_name)
            fo_date = row['date']
            classification = 'Yield' if row['tx_type'] == 'yield_withdrawal' else 'Capital-Return'
            amount = Decimal(row['amount'])
            currency = row['currency']
            amount_ils = Decimal(row['amount_ils']) if row['amount_ils'] else None

            fo_by_inv_date[(bc_name, fo_date)].append({
                'classification': classification,
                'amount': amount,
                'currency': currency,
                'amount_ils': amount_ils,
            })

    return fo_by_inv_date


def resolve_investment_name(investment, filepath):
    """Resolve ambiguous investment name using file path context."""
    if investment == 'Boligo' and filepath:
        fp = str(filepath).lower()
        if 'boligo-2' in fp:
            return 'Boligo-2'
        elif 'boligo-1' in fp:
            return 'Boligo-1'
    return investment


def find_fo_match(fo_data, investment, entry_date_str, filepath=None):
    """Find FO entries matching an investment and date (with +/- 5 day tolerance)."""
    d = date.fromisoformat(entry_date_str)

    resolved = resolve_investment_name(investment, filepath)
    investments_to_check = [resolved]
    if resolved == 'Boligo':
        investments_to_check = ['Boligo-1', 'Boligo-2']

    for inv in investments_to_check:
        for delta in range(-5, 6):
            check_date = str(d + timedelta(days=delta))
            key = (inv, check_date)
            if key in fo_data:
                return fo_data[key], check_date
    return None, None


def determine_classification(fo_entries, primary_amount, primary_currency):
    """Determine classification and split from FO entries."""
    types = set(e['classification'] for e in fo_entries)

    if len(types) == 1:
        return [(types.pop(), primary_amount)]

    # Mixed - need to split
    if fo_entries[0]['currency'] == primary_currency:
        result = []
        total_fo = sum(e['amount'] for e in fo_entries)
        running_total = Decimal(0)
        for i, e in enumerate(fo_entries):
            if i == len(fo_entries) - 1:
                amt = primary_amount - running_total
            else:
                amt = (e['amount'] * primary_amount / total_fo).quantize(
                    Decimal('0.01'), rounding=ROUND_HALF_UP
                )
            running_total += amt
            result.append((e['classification'], amt))
        return result

    # Primary is in different currency - use FO ILS equivalents
    total_fo_ils = sum(e['amount_ils'] for e in fo_entries if e['amount_ils'])
    if total_fo_ils == 0:
        return None

    result = []
    running_total = Decimal(0)
    for i, e in enumerate(fo_entries):
        if i == len(fo_entries) - 1:
            amt = primary_amount - running_total
        else:
            proportion = e['amount_ils'] / total_fo_ils
            amt = (proportion * primary_amount).quantize(
                Decimal('1'), rounding=ROUND_HALF_UP
            )
        running_total += amt
        result.append((e['classification'], amt))
    return result


def find_unclassified_entries(ledger_dir):
    """Find all Unclassified distribution entries in entry files."""
    entries = []
    for filepath in sorted(ledger_dir.rglob('entries.beancount')):
        with open(filepath) as f:
            lines = f.readlines()

        i = 0
        while i < len(lines):
            line = lines[i]
            match = re.match(r'^(\d{4}-\d{2}-\d{2})\s+[*!]\s+"([^"]*)"', line)
            if not match:
                i += 1
                continue

            txn_date = match.group(1)
            narration = match.group(2)

            j = i + 1
            unclassified_posting = None
            while j < len(lines):
                pline = lines[j]
                if pline.strip() == '' or (not pline[0:1].isspace() and pline.strip()):
                    if not pline.startswith(' ') and not pline.startswith('\t') and pline.strip():
                        break
                    if pline.strip() == '':
                        break

                stripped = pline.strip()
                if stripped.startswith(';') or not stripped:
                    j += 1
                    continue

                if 'Income:Investments:' in stripped and ':Unclassified' in stripped:
                    parts = stripped.split()
                    account = parts[0]
                    investment = account.split(':')[2]
                    for k, part in enumerate(parts[1:], 1):
                        cleaned = part.replace(',', '').lstrip('-')
                        try:
                            float(cleaned)
                            amount_str = part
                            currency = parts[k + 1] if k + 1 < len(parts) else None
                            amount = Decimal(part.replace(',', ''))
                            unclassified_posting = {
                                'line_idx': j,
                                'account': account,
                                'investment': investment,
                                'amount': amount,
                                'amount_str': amount_str,
                                'currency': currency,
                                'indent': pline[:len(pline) - len(pline.lstrip())],
                            }
                            break
                        except Exception:
                            continue
                j += 1

            if unclassified_posting:
                entries.append({
                    'filepath': filepath,
                    'txn_line_idx': i,
                    'txn_date': txn_date,
                    'narration': narration,
                    'end_idx': j,
                    **unclassified_posting,
                })

            i = j if j > i else i + 1

    return entries


def apply_reclassification(entry, classification_splits, dry_run=True):
    """Apply reclassification to an entry in its file."""
    filepath = entry['filepath']
    with open(filepath) as f:
        lines = f.readlines()

    line_idx = entry['line_idx']
    original_line = lines[line_idx]
    indent = entry['indent']
    investment = entry['investment']
    resolved_inv = resolve_investment_name(investment, filepath)

    if len(classification_splits) == 1:
        cls_type, _ = classification_splits[0]
        old_account = f'Income:Investments:{investment}:Unclassified'
        new_account = classify_account(cls_type, resolved_inv)
        new_line = original_line.replace(old_account, new_account)

        if dry_run:
            return f"  RECLASSIFY {old_account} -> {new_account}"

        lines[line_idx] = new_line
    else:
        new_lines = []
        for cls_type, amount in classification_splits:
            account = classify_account(cls_type, resolved_inv)
            if '.' in str(amount):
                amt_str = f'{amount:,.2f}'
            else:
                amt_str = f'{int(amount):,}'
            if amount > 0:
                amt_str = f'-{amt_str}'
            elif amount < 0 and not amt_str.startswith('-'):
                amt_str = f'-{amt_str}'
            new_lines.append(f'{indent}{account}  {amt_str} {entry["currency"]}\n')

        if dry_run:
            desc = ' + '.join(f'{c} {a}' for c, a in classification_splits)
            return f"  SPLIT -> {desc}"

        lines[line_idx:line_idx + 1] = new_lines

    # Add classification-source metadata if not present
    txn_line_idx = entry['txn_line_idx']
    has_classification_source = False
    insert_meta_at = None
    for k in range(txn_line_idx + 1, min(txn_line_idx + 10, len(lines))):
        if k >= len(lines):
            break
        l = lines[k].strip()
        if l.startswith('classification-source:'):
            has_classification_source = True
            break
        if l.startswith('source:') or l.startswith('fo-line:'):
            insert_meta_at = k + 1
        if l and not l.startswith(';') and not l.startswith('#'):
            if not any(l.startswith(prefix) for prefix in ('source:', 'fo-line:', 'classification-source:', 'investment:')):
                break

    if not has_classification_source and not dry_run:
        meta_line = f'  classification-source: "{FO_SOURCE}"\n'
        if insert_meta_at is not None:
            lines.insert(insert_meta_at, meta_line)
        else:
            lines.insert(txn_line_idx + 1, meta_line)

    if not dry_run:
        txn_line = lines[txn_line_idx]
        if '#provisional' in txn_line:
            txn_line = txn_line.replace(' #provisional', '')
            txn_line = txn_line.replace('#provisional ', '')
            txn_line = txn_line.replace('#provisional', '')
            lines[txn_line_idx] = txn_line

        with open(filepath, 'w') as f:
            f.writelines(lines)

    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()

    inv_map = load_investment_map()
    fo_data = load_fo_distributions(inv_map)
    unclassified = find_unclassified_entries(Path('ledger'))

    print(f'Found {len(unclassified)} Unclassified distribution entries')
    print()

    matched = 0
    unmatched = 0
    split_count = 0

    by_file = defaultdict(list)
    for entry in unclassified:
        by_file[str(entry['filepath'])].append(entry)

    for filepath_str, file_entries in sorted(by_file.items()):
        for entry in reversed(file_entries):
            fo_entries, fo_date = find_fo_match(
                fo_data, entry['investment'], entry['txn_date'],
                filepath=entry['filepath']
            )

            if fo_entries is None:
                unmatched += 1
                print(f"  NO MATCH: {entry['txn_date']} {entry['investment']} "
                      f"{entry['amount']} {entry['currency']}  ({entry['filepath']})")
                continue

            primary_amount = abs(entry['amount'])
            splits = determine_classification(
                fo_entries, primary_amount, entry['currency']
            )

            if splits is None:
                unmatched += 1
                print(f"  CANT SPLIT: {entry['txn_date']} {entry['investment']} "
                      f"(no ILS data)")
                continue

            splits = [(cls, -abs(amt)) for cls, amt in splits]

            matched += 1
            if len(splits) > 1:
                split_count += 1

            desc = apply_reclassification(entry, splits, dry_run=not args.write)
            if desc:
                print(f"  {entry['txn_date']} {entry['investment']:20s} "
                      f"{str(entry['amount']):>12s} {entry['currency']}  {desc}")

    mode = "APPLIED" if args.write else "DRY RUN"
    print(f'\n{mode}: {matched} matched ({split_count} splits), {unmatched} unmatched')


if __name__ == '__main__':
    main()
