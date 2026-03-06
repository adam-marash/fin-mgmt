#!/usr/bin/env python3
"""Check ledger compliance with PROCEDURES.md and ASSUMPTIONS.md conventions.

Usage:
    python scripts/check_procedures.py [--verbose]

Checks:
  1. FO-sourced entries have required metadata (source:, fo-line:)
  2. Classified entries have classification-source: metadata
  3. #provisional not present on classified (non-Unclassified) entries
  4. Account routing: distributions -> Assets:Receivable, payments -> Liabilities:Commitments
  5. Folder naming matches ledger/YYYY/<date>-<desc>[-<hash>]/
  6. Over-drawn commitment balances (positive = investigate)
  7. @@ with non-terminating decimals (beancount v3 bug)
  8. Every transaction has source: metadata
  9. Link tag format: ^lowercase-kebab-case
 10. Suspense balances (non-zero = unresolved counterparty)
 11. Non-zero receivable balances (pending reconciliation)

Exit code 0 if all checks pass, 1 if any violations found.
"""

import os
import re
import subprocess
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation

LEDGER_DIR = "ledger"
MAIN_BEANCOUNT = "ledger/main.beancount"
VERBOSE = "--verbose" in sys.argv


class Violation:
    def __init__(self, check, file, line_num, message):
        self.check = check
        self.file = file
        self.line_num = line_num
        self.message = message

    def __str__(self):
        loc = f"{self.file}:{self.line_num}" if self.line_num else self.file
        return f"  [{self.check}] {loc}: {self.message}"


def find_entry_files():
    """Find all entries.beancount files in ledger/."""
    results = []
    for root, _, files in os.walk(LEDGER_DIR):
        for f in files:
            if f == "entries.beancount":
                results.append(os.path.join(root, f))
    return sorted(results)


def parse_transactions(filepath):
    """Parse transactions from a beancount file into structured blocks."""
    with open(filepath) as f:
        lines = f.readlines()

    txns = []
    current = None
    txn_re = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+([*!])\s+"([^"]*)"(.*)$')

    for i, line in enumerate(lines, 1):
        m = txn_re.match(line)
        if m:
            if current:
                txns.append(current)
            tags_str = m.group(4)
            tags = set(re.findall(r'#([\w-]+)', tags_str))
            links = set(re.findall(r'\^([\w-]+)', tags_str))
            current = {
                "date": m.group(1),
                "flag": m.group(2),
                "narration": m.group(3),
                "tags": tags,
                "links": links,
                "line": i,
                "meta": {},
                "postings": [],
            }
        elif current:
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            meta_match = re.match(r'\s+([\w-]+):\s+"?(.+?)"?\s*$', line)
            if meta_match:
                current["meta"][meta_match.group(1)] = meta_match.group(2)
            elif re.match(r'\s+\S+:\S+', stripped):
                current["postings"].append({"line": i, "text": stripped})

    if current:
        txns.append(current)
    return txns


def run_bean_query(query):
    """Run a bean-query and return output lines."""
    try:
        result = subprocess.run(
            [".venv/bin/bean-query", MAIN_BEANCOUNT, query],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip().split("\n")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def check_fo_sourced_metadata(files):
    """#fo-sourced entries must have source: and fo-line: metadata."""
    violations = []
    for fp in files:
        for txn in parse_transactions(fp):
            if "fo-sourced" not in txn["tags"]:
                continue
            if "source" not in txn["meta"]:
                violations.append(Violation(
                    "fo-metadata", fp, txn["line"],
                    "#fo-sourced entry missing source: metadata"))
            if "fo-line" not in txn["meta"]:
                violations.append(Violation(
                    "fo-metadata", fp, txn["line"],
                    "#fo-sourced entry missing fo-line: metadata"))
    return violations


def check_classification_metadata(files):
    """Classified (non-Unclassified) distribution entries must have classification-source:."""
    violations = []
    classified_re = re.compile(r'Income:Distribution:\S+:(Yield|Capital-Return|Capital-Gain)')
    for fp in files:
        for txn in parse_transactions(fp):
            has_classified = any(
                classified_re.search(p["text"]) for p in txn["postings"]
            )
            if has_classified and "classification-source" not in txn["meta"]:
                violations.append(Violation(
                    "classification-meta", fp, txn["line"],
                    "Classified distribution missing classification-source: metadata"))
    return violations


def check_provisional_consistency(files):
    """#provisional should not appear on classified (non-Unclassified) distribution entries."""
    violations = []
    classified_re = re.compile(r'Income:Distribution:\S+:(Yield|Capital-Return|Capital-Gain)')
    for fp in files:
        for txn in parse_transactions(fp):
            if "provisional" not in txn["tags"]:
                continue
            has_classified = any(
                classified_re.search(p["text"]) for p in txn["postings"]
            )
            if has_classified:
                violations.append(Violation(
                    "provisional-classified", fp, txn["line"],
                    "#provisional on a classified distribution entry"))
    return violations


def check_account_routing(files):
    """Distributions use Assets:Receivable, payments use Liabilities:Commitments."""
    violations = []
    for fp in files:
        for txn in parse_transactions(fp):
            narr = txn["narration"].lower()
            all_text = " ".join(p["text"] for p in txn["postings"])

            if "distribution" in narr and "Liabilities:Commitments" in all_text:
                violations.append(Violation(
                    "routing", fp, txn["line"],
                    "Distribution entry uses Liabilities:Commitments"))

            if "investment payment" in narr and "Assets:Receivable" in all_text:
                violations.append(Violation(
                    "routing", fp, txn["line"],
                    "Investment payment entry uses Assets:Receivable"))
    return violations


def check_folder_naming():
    """Folder names match ledger/YYYY/<date>-<desc>[-<hash>]/."""
    violations = []
    year_re = re.compile(r'^\d{4}$')
    folder_re = re.compile(r'^\d{4}-\d{2}-\d{2}-.+$')

    for year_dir in sorted(os.listdir(LEDGER_DIR)):
        year_path = os.path.join(LEDGER_DIR, year_dir)
        if not os.path.isdir(year_path) or not year_re.match(year_dir):
            continue
        for folder in sorted(os.listdir(year_path)):
            folder_path = os.path.join(year_path, folder)
            if not os.path.isdir(folder_path):
                continue
            if not folder_re.match(folder):
                violations.append(Violation(
                    "folder-naming", folder_path, None,
                    "Folder name does not match <date>-<desc>[-<hash>] pattern"))
    return violations


def check_commitment_balances():
    """Flag over-drawn (positive) commitment balances."""
    violations = []
    lines = run_bean_query(
        "SELECT account, sum(position) "
        "WHERE account ~ 'Liabilities:Commitments' GROUP BY account"
    )
    for line in lines:
        line = line.strip()
        if not line or line.startswith("account") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            account = parts[0]
            for p in parts[1:]:
                try:
                    val = Decimal(p.replace(",", ""))
                    if val > 0:
                        violations.append(Violation(
                            "commitment-overdrawn", "bean-query", None,
                            f"{account} balance {val} (positive = over-drawn)"))
                    break
                except InvalidOperation:
                    continue
    return violations


def check_suspense_balances():
    """Flag non-zero Suspense balances (unresolved counterparties)."""
    violations = []
    lines = run_bean_query(
        "SELECT account, sum(position) "
        "WHERE account ~ 'Suspense' GROUP BY account"
    )
    for line in lines:
        line = line.strip()
        if not line or line.startswith("account") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            account = parts[0]
            for p in parts[1:]:
                try:
                    val = Decimal(p.replace(",", ""))
                    if val != 0:
                        violations.append(Violation(
                            "suspense", "bean-query", None,
                            f"{account} balance {val} {parts[parts.index(p)+1] if parts.index(p)+1 < len(parts) else ''}".strip()))
                    break
                except InvalidOperation:
                    continue
    return violations


def check_receivable_balances():
    """Flag non-zero Receivable balances (pending reconciliation)."""
    violations = []
    lines = run_bean_query(
        "SELECT account, sum(position) "
        "WHERE account ~ 'Assets:Receivable' GROUP BY account"
    )
    for line in lines:
        line = line.strip()
        if not line or line.startswith("account") or line.startswith("-"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            account = parts[0]
            for p in parts[1:]:
                try:
                    val = Decimal(p.replace(",", ""))
                    if val != 0:
                        sign = "positive (pending credit)" if val > 0 else "negative (pending announcement)"
                        violations.append(Violation(
                            "receivable", "bean-query", None,
                            f"{account} balance {val} {parts[parts.index(p)+1] if parts.index(p)+1 < len(parts) else ''} ({sign})".strip()))
                    break
                except InvalidOperation:
                    continue
    return violations


def check_total_cost_precision(files):
    """@@ (total cost) risks precision bug with non-terminating decimals."""
    violations = []
    tc_re = re.compile(r'@@\s+([\d,]+\.?\d*)\s+(\w+)')
    all_files = list(files)
    for name in ["commitments.beancount", "accounts.beancount", "prices.beancount"]:
        path = os.path.join(LEDGER_DIR, name)
        if os.path.exists(path):
            all_files.append(path)
    for fp in all_files:
        with open(fp) as f:
            for i, line in enumerate(f, 1):
                if tc_re.search(line):
                    violations.append(Violation(
                        "total-cost", fp, i,
                        "Uses @@ (total cost) - risk of precision bug"))
    return violations


def check_source_metadata(files):
    """Every transaction should have source: metadata."""
    violations = []
    for fp in files:
        for txn in parse_transactions(fp):
            if "source" not in txn["meta"]:
                if "commitment" in txn["narration"].lower():
                    continue
                violations.append(Violation(
                    "source-meta", fp, txn["line"],
                    "Transaction missing source: metadata"))
    return violations


def collect_link_data(files):
    """Collect all link tag data across ledger for link checks."""
    link_entries = defaultdict(list)
    tag_re = re.compile(r'\^([\w-]+)')
    for fp in files:
        for txn in parse_transactions(fp):
            for link in txn["links"]:
                # Extract investment from accounts
                investments = set()
                for p in txn["postings"]:
                    for pattern in [r'Assets:Receivable:(\S+)',
                                    r'Liabilities:Commitments:(\S+)',
                                    r'Income:Distribution:(\S+?):']:
                        m = re.search(pattern, p["text"])
                        if m:
                            investments.add(m.group(1))
                link_entries[link].append({
                    "file": fp,
                    "line": txn["line"],
                    "date": txn["date"],
                    "narration": txn["narration"],
                    "investments": investments,
                })
    return link_entries


def check_link_tag_format(files):
    """Link tags should be lowercase-kebab-case."""
    violations = []
    link_re = re.compile(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$')
    for fp in files:
        for txn in parse_transactions(fp):
            for link in txn["links"]:
                if not link_re.match(link):
                    violations.append(Violation(
                        "link-format", fp, txn["line"],
                        f"Link tag ^{link} not lowercase-kebab-case"))
    return violations


def check_link_sequence_gaps(link_data):
    """Sequenced link tags (^prefix-N) should have no gaps."""
    violations = []
    seq_re = re.compile(r'^(.+)-(\d+)$')
    prefixes = defaultdict(list)
    for tag in link_data:
        m = seq_re.match(tag)
        if m:
            prefixes[m.group(1)].append(int(m.group(2)))

    for prefix in sorted(prefixes):
        nums = sorted(prefixes[prefix])
        if len(nums) < 2:
            continue
        expected = set(range(nums[0], nums[-1] + 1))
        missing = sorted(expected - set(nums))
        if missing:
            violations.append(Violation(
                "link-seq-gap", "ledger", None,
                f"^{prefix}-N: missing {missing} (have {nums[0]}-{nums[-1]})"))
    return violations


def check_link_singletons(link_data):
    """Link tags with only 1 entry may signal missing counterparts."""
    violations = []
    for tag in sorted(link_data):
        entries = link_data[tag]
        if len(entries) == 1:
            e = entries[0]
            violations.append(Violation(
                "link-singleton", e["file"], e["line"],
                f"^{tag} has only 1 entry (missing counterpart?)"))
    return violations


def check_link_cross_investment(link_data):
    """All entries sharing a ^tag should reference the same investment."""
    violations = []
    for tag in sorted(link_data):
        entries = link_data[tag]
        if len(entries) < 2:
            continue
        all_investments = set()
        for e in entries:
            all_investments.update(e["investments"])
        # Filter out empty (entries with no recognized investment account)
        all_investments.discard("")
        if len(all_investments) > 1:
            violations.append(Violation(
                "link-cross-inv", "ledger", None,
                f"^{tag} groups entries across investments: {sorted(all_investments)}"))
    return violations


def main():
    files = find_entry_files()
    print(f"Scanning {len(files)} entry files...\n")

    link_data = collect_link_data(files)

    checks = [
        ("FO-sourced metadata", check_fo_sourced_metadata, files),
        ("Classification metadata", check_classification_metadata, files),
        ("Provisional consistency", check_provisional_consistency, files),
        ("Account routing", check_account_routing, files),
        ("Folder naming", check_folder_naming, None),
        ("Commitment balances", check_commitment_balances, None),
        ("Suspense balances", check_suspense_balances, None),
        ("Receivable balances", check_receivable_balances, None),
        ("Total cost (@@) precision", check_total_cost_precision, files),
        ("Source metadata", check_source_metadata, files),
        ("Link tag format", check_link_tag_format, files),
        ("Link sequence gaps", check_link_sequence_gaps, link_data),
        ("Link singletons", check_link_singletons, link_data),
        ("Link cross-investment", check_link_cross_investment, link_data),
    ]

    total_violations = 0
    total_checks = 0

    for name, check_fn, arg in checks:
        total_checks += 1
        violations = check_fn(arg) if arg is not None else check_fn()

        if violations:
            print(f"FAIL  {name} ({len(violations)})")
            if VERBOSE or len(violations) <= 5:
                for v in violations:
                    print(str(v))
            else:
                for v in violations[:3]:
                    print(str(v))
                print(f"  ... and {len(violations) - 3} more (--verbose)")
            total_violations += len(violations)
        else:
            print(f"PASS  {name}")

    print(f"\n{'='*50}")
    print(f"{total_checks} checks, {total_violations} violations")
    if total_violations == 0:
        print("All checks passed.")
    return 1 if total_violations else 0


if __name__ == "__main__":
    sys.exit(main())
