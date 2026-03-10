#!/usr/bin/env python3
"""Sweep Equity:FX-Conversion pipe entries into single multi-currency entries.

Finds paired FX-Conversion entries (same date, complementary currencies) and
merges them into single entries using beancount's native `@ rate` syntax.

Handles three categories:
  A) Already-merged entries with auto-balanced FX-Conversion line
  B) Same-file pairs (two FX-Conversion txns in one file)
  C) Cross-file pairs (two FX-Conversion txns in different files)

Usage:
    python scripts/sweep_fx_conversion.py              # dry run
    python scripts/sweep_fx_conversion.py --commit      # apply changes
    python scripts/sweep_fx_conversion.py --verbose      # show detail
"""

import argparse
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as date_type, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from beancount import loader
from beancount.core import data

ROOT = Path(__file__).resolve().parent.parent
LEDGER_DIR = ROOT / "ledger"
MAIN_FILE = LEDGER_DIR / "main.beancount"

FX_ACCOUNT = "Equity:FX-Conversion"


@dataclass
class FXHalf:
    """One half of an FX conversion (one entry with an FX-Conversion posting)."""
    date: str
    narration: str
    file_path: str
    line_no: int
    bank_account: str
    bank_amount: Decimal
    bank_currency: str
    fx_amount: Decimal
    fx_currency: str
    source: str
    tags: set = field(default_factory=set)
    links: set = field(default_factory=set)
    meta: dict = field(default_factory=dict)
    # For Category A: entries with both bank legs + auto-balanced FX-Conversion
    is_auto_balanced: bool = False
    other_bank_account: str = ""
    other_bank_amount: Decimal = Decimal(0)
    other_bank_currency: str = ""


def extract_fx_entries(entries: list) -> list[FXHalf]:
    """Extract all transactions that have an Equity:FX-Conversion posting."""
    results = []
    for entry in entries:
        if not isinstance(entry, data.Transaction):
            continue

        fx_postings = []
        bank_postings = []
        for posting in entry.postings:
            acct = posting.account
            if acct == FX_ACCOUNT:
                fx_postings.append(posting)
            elif acct.startswith("Assets:Banks:") or acct.startswith("Assets:Deposits:"):
                bank_postings.append(posting)

        if not fx_postings:
            continue

        file_path = entry.meta.get("filename", "")
        line_no = entry.meta.get("lineno", 0)
        source = entry.meta.get("source", "")

        # Category A: two bank postings + FX-Conversion (auto-balanced in source,
        # but beancount expands to 2 FX postings after loading)
        if len(bank_postings) == 2 and len(fx_postings) in (1, 2):
            bp_src = [p for p in bank_postings if p.units.number < 0]
            bp_tgt = [p for p in bank_postings if p.units.number > 0]
            if bp_src and bp_tgt and bp_src[0].units.currency != bp_tgt[0].units.currency:
                src = bp_src[0]
                tgt = bp_tgt[0]
                half = FXHalf(
                    date=str(entry.date),
                    narration=entry.narration,
                    file_path=file_path,
                    line_no=line_no,
                    bank_account=src.account,
                    bank_amount=src.units.number,
                    bank_currency=src.units.currency,
                    fx_amount=Decimal(0),
                    fx_currency="",
                    source=source,
                    tags=set(entry.tags),
                    links=set(entry.links),
                    meta={k: v for k, v in entry.meta.items()
                          if k not in ("filename", "lineno")
                          and not k.startswith("__")},
                    is_auto_balanced=True,
                    other_bank_account=tgt.account,
                    other_bank_amount=tgt.units.number,
                    other_bank_currency=tgt.units.currency,
                )
                results.append(half)
                continue

        # Standard case: one bank posting + one FX-Conversion posting
        if len(fx_postings) == 1 and len(bank_postings) == 1:
            fp = fx_postings[0]
            bp = bank_postings[0]
            half = FXHalf(
                date=str(entry.date),
                narration=entry.narration,
                file_path=file_path,
                line_no=line_no,
                bank_account=bp.account,
                bank_amount=bp.units.number,
                bank_currency=bp.units.currency,
                fx_amount=fp.units.number,
                fx_currency=fp.units.currency,
                source=source,
                tags=set(entry.tags),
                links=set(entry.links),
                meta={k: v for k, v in entry.meta.items()
                      if k not in ("filename", "lineno")
                      and not k.startswith("__")},
            )
            results.append(half)
        else:
            print(f"  WARNING: unusual FX entry at {file_path}:{line_no} "
                  f"({len(fx_postings)} FX postings, {len(bank_postings)} bank postings) "
                  f"- skipping", file=sys.stderr)

    return results


def find_pairs(halves: list[FXHalf], date_tolerance: int = 1) -> tuple[
    list[FXHalf],                        # Category A (auto-balanced)
    list[tuple[FXHalf, FXHalf]],         # Paired (B or C)
    list[FXHalf],                        # Unmatched
]:
    """Match FX-Conversion halves into pairs.

    Matches debits (bank_amount < 0) with credits (bank_amount > 0) on the
    same date (or within date_tolerance days) with different currencies.
    """
    cat_a = []
    standard = []

    for h in halves:
        if h.is_auto_balanced:
            cat_a.append(h)
        else:
            standard.append(h)

    # Separate into debits and credits
    debits = [h for h in standard if h.bank_amount < 0]
    credits = [h for h in standard if h.bank_amount > 0]

    pairs = []
    debit_used: set[int] = set()
    credit_used: set[int] = set()

    # First pass: exact date match
    for i, d in enumerate(debits):
        d_date = date_type.fromisoformat(d.date)
        for j, c in enumerate(credits):
            if j in credit_used:
                continue
            if d.bank_currency == c.bank_currency:
                continue
            if d.date == c.date:
                pairs.append((d, c))
                debit_used.add(i)
                credit_used.add(j)
                break

    # Second pass: fuzzy date match (within tolerance)
    if date_tolerance > 0:
        for i, d in enumerate(debits):
            if i in debit_used:
                continue
            d_date = date_type.fromisoformat(d.date)
            for j, c in enumerate(credits):
                if j in credit_used:
                    continue
                if d.bank_currency == c.bank_currency:
                    continue
                c_date = date_type.fromisoformat(c.date)
                if abs((d_date - c_date).days) <= date_tolerance:
                    pairs.append((d, c))
                    debit_used.add(i)
                    credit_used.add(j)
                    break

    # Collect unmatched
    unmatched = []
    for i, d in enumerate(debits):
        if i not in debit_used:
            unmatched.append(d)
    for j, c in enumerate(credits):
        if j not in credit_used:
            unmatched.append(c)

    return cat_a, pairs, unmatched


def compute_rate(source_amount: Decimal, target_amount: Decimal) -> Decimal:
    """Compute per-unit rate: source_currency per 1 unit of target_currency.

    source_amount is positive (absolute value of what left the source bank).
    target_amount is positive (what arrived at the target bank).

    Uses enough decimal places that target_amount * rate is within 0.005 of
    source_amount (beancount's default tolerance).
    """
    # Need precision so that target * rate ~ source within 0.005
    # Required decimals = ceil(log10(target / 0.005)) + safety margin
    # For amounts up to 10M, 10 decimal places suffice
    raw = source_amount / target_amount
    # Strip trailing zeros for cleaner output
    return raw.quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP).normalize()


def build_merged_entry(
    debit: FXHalf,
    credit: FXHalf,
    *,
    rate: Decimal | None = None,
) -> str:
    """Build a merged multi-currency beancount entry from a debit/credit pair."""
    source_abs = abs(debit.bank_amount)
    target_abs = abs(credit.bank_amount)
    if rate is None:
        rate = compute_rate(source_abs, target_abs)

    # Pick the better narration (longer = more detail)
    narration = debit.narration if len(debit.narration) >= len(credit.narration) else credit.narration
    # If narration doesn't mention rate, append it
    if "rate" not in narration.lower():
        # Compute the human-friendly rate (how many target per 1 source)
        human_rate = (target_abs / source_abs).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        narration += f" (rate {human_rate})"

    # Tags and links from both sides
    all_tags = debit.tags | credit.tags
    all_links = debit.links | credit.links

    tag_str = ""
    if all_tags:
        tag_str += " " + " ".join(f"#{t}" for t in sorted(all_tags))
    if all_links:
        tag_str += " " + " ".join(f"^{l}" for l in sorted(all_links))

    # Use the earlier date if they differ
    entry_date = min(debit.date, credit.date)
    lines = [f'{entry_date} * "{narration}"{tag_str}']

    # Metadata
    lines.append(f'  source: "{debit.source}"')
    if credit.source and credit.source != debit.source:
        lines.append(f'  source-2: "{credit.source}"')

    # Extra metadata from both sides (exclude standard/internal keys)
    skip_keys = {"source", "source-2", "filename", "lineno"}
    for key, val in sorted(debit.meta.items()):
        if key not in skip_keys and not key.startswith("__"):
            lines.append(f'  {key}: "{val}"')
    for key, val in sorted(credit.meta.items()):
        if key not in skip_keys and not key.startswith("__") and key not in debit.meta:
            lines.append(f'  {key}: "{val}"')

    # Postings: source (debit) and target (credit) with @ rate
    lines.append(f"  {debit.bank_account}  {debit.bank_amount:,.2f} {debit.bank_currency}")
    lines.append(
        f"  {credit.bank_account}  {credit.bank_amount:,.2f} {credit.bank_currency}"
        f" @ {rate} {debit.bank_currency}"
    )

    return "\n".join(lines)


def build_cat_a_entry(half: FXHalf) -> str:
    """Build a merged entry for Category A (already has both bank legs)."""
    source_abs = abs(half.bank_amount)
    target_abs = abs(half.other_bank_amount)
    rate = compute_rate(source_abs, target_abs)

    narration = half.narration
    if "rate" not in narration.lower():
        human_rate = (target_abs / source_abs).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        narration += f" (rate {human_rate})"

    tag_str = ""
    if half.tags:
        tag_str += " " + " ".join(f"#{t}" for t in sorted(half.tags))
    if half.links:
        tag_str += " " + " ".join(f"^{l}" for l in sorted(half.links))

    lines = [f'{half.date} * "{narration}"{tag_str}']
    lines.append(f'  source: "{half.source}"')

    skip_keys = {"source", "filename", "lineno"}
    for key, val in sorted(half.meta.items()):
        if key not in skip_keys and not key.startswith("__"):
            lines.append(f'  {key}: "{val}"')

    lines.append(f"  {half.bank_account}  {half.bank_amount:,.2f} {half.bank_currency}")
    lines.append(
        f"  {half.other_bank_account}  {half.other_bank_amount:,.2f} {half.other_bank_currency}"
        f" @ {rate} {half.bank_currency}"
    )

    return "\n".join(lines)


def parse_file_into_blocks(file_path: str) -> list[dict]:
    """Parse a beancount file into transaction blocks.

    Returns list of dicts with keys:
      - type: 'header' (comments before first txn), 'transaction', 'other'
      - text: the raw text
      - line_start: 1-based line number of first line
      - line_end: 1-based line number of last line
    """
    content = Path(file_path).read_text()
    lines = content.split("\n")

    blocks = []
    current_block_lines = []
    current_start = 1
    in_transaction = False

    # Pattern for transaction start: date followed by txflag
    txn_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}\s+[*!]")
    # Pattern for any directive start (not indented, starts with date or keyword)
    directive_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+|;|$)")

    for i, line in enumerate(lines):
        line_no = i + 1

        if txn_pattern.match(line):
            # Flush previous block
            if current_block_lines:
                blocks.append({
                    "type": "transaction" if in_transaction else "other",
                    "text": "\n".join(current_block_lines),
                    "line_start": current_start,
                    "line_end": line_no - 1,
                })
            current_block_lines = [line]
            current_start = line_no
            in_transaction = True
        elif in_transaction and (line.startswith("  ") or line.strip() == ""):
            # Continuation of transaction (indented line or blank separator)
            if line.strip() == "" and current_block_lines and current_block_lines[-1].strip() == "":
                # Double blank = end of transaction
                blocks.append({
                    "type": "transaction",
                    "text": "\n".join(current_block_lines),
                    "line_start": current_start,
                    "line_end": line_no - 1,
                })
                current_block_lines = [line]
                current_start = line_no
                in_transaction = False
            else:
                current_block_lines.append(line)
        elif in_transaction and not line.startswith("  ") and line.strip() != "":
            # New directive/comment = end of previous transaction
            blocks.append({
                "type": "transaction",
                "text": "\n".join(current_block_lines),
                "line_start": current_start,
                "line_end": line_no - 1,
            })
            current_block_lines = [line]
            current_start = line_no
            in_transaction = False
        else:
            current_block_lines.append(line)

    # Flush last block
    if current_block_lines:
        blocks.append({
            "type": "transaction" if in_transaction else "other",
            "text": "\n".join(current_block_lines),
            "line_start": current_start,
            "line_end": current_start + len(current_block_lines) - 1,
        })

    return blocks


def find_block_for_line(blocks: list[dict], line_no: int) -> int | None:
    """Find the block index containing the given line number."""
    for i, block in enumerate(blocks):
        if block["line_start"] <= line_no <= block["line_end"]:
            return i
    return None


def remove_block(blocks: list[dict], idx: int) -> list[dict]:
    """Remove a block by index, cleaning up adjacent blank lines."""
    return [b for i, b in enumerate(blocks) if i != idx]


def replace_block_text(blocks: list[dict], idx: int, new_text: str) -> list[dict]:
    """Replace a block's text content."""
    new_blocks = list(blocks)
    new_blocks[idx] = {**new_blocks[idx], "text": new_text}
    return new_blocks


def blocks_to_text(blocks: list[dict]) -> str:
    """Reconstruct file content from blocks."""
    texts = [b["text"] for b in blocks]
    result = "\n".join(texts)
    # Ensure single trailing newline
    return result.rstrip("\n") + "\n"


def get_preceding_comments(file_path: str, txn_line: int) -> str:
    """Get comment lines immediately preceding a transaction."""
    lines = Path(file_path).read_text().split("\n")
    comments = []
    i = txn_line - 2  # 0-based, line before txn
    while i >= 0 and lines[i].startswith(";"):
        comments.insert(0, lines[i])
        i -= 1
    return "\n".join(comments)


def file_has_only_fx(file_path: str, fx_line_nos: set[int]) -> bool:
    """Check if a file contains only FX-Conversion transactions (plus comments/headers)."""
    blocks = parse_file_into_blocks(file_path)
    for block in blocks:
        if block["type"] == "transaction":
            # Check if this transaction block contains an FX-Conversion line
            is_fx = any(
                block["line_start"] <= ln <= block["line_end"]
                for ln in fx_line_nos
            )
            if not is_fx:
                return False
    return True


def apply_cat_a(half: FXHalf, merged_text: str, dry_run: bool, verbose: bool) -> bool:
    """Apply Category A merge (replace FX-Conversion auto-balance with @ rate)."""
    file_path = half.file_path
    content = Path(file_path).read_text()
    lines = content.split("\n")

    # Find the transaction block starting at half.line_no
    # Look for the Equity:FX-Conversion line within this transaction
    start = half.line_no - 1  # 0-based
    fx_line_idx = None
    for i in range(start, min(start + 20, len(lines))):
        if FX_ACCOUNT in lines[i]:
            fx_line_idx = i
            break

    if fx_line_idx is None:
        print(f"  ERROR: could not find FX-Conversion line in {file_path}:{half.line_no}",
              file=sys.stderr)
        return False

    # Find the full transaction block (from date line to next blank or date line)
    txn_start = half.line_no - 1
    txn_end = txn_start + 1
    while txn_end < len(lines):
        line = lines[txn_end]
        if line.strip() == "" or (re.match(r"^\d{4}-\d{2}-\d{2}\s+", line) and txn_end > txn_start):
            break
        txn_end += 1

    # Also grab preceding comment lines
    comment_start = txn_start
    while comment_start > 0 and lines[comment_start - 1].startswith(";"):
        comment_start -= 1

    # Build replacement: preceding comments + merged entry
    comment_lines = lines[comment_start:txn_start]
    replacement_parts = []
    if comment_lines:
        replacement_parts.append("\n".join(comment_lines))
    replacement_parts.append(merged_text)
    replacement = "\n".join(replacement_parts)

    # Replace in file
    new_lines = lines[:comment_start] + [replacement] + lines[txn_end:]
    new_content = "\n".join(new_lines)

    if not dry_run:
        Path(file_path).write_text(new_content)

    return True


def apply_pair(
    debit: FXHalf,
    credit: FXHalf,
    merged_text: str,
    dry_run: bool,
    verbose: bool,
) -> bool:
    """Apply a paired merge. Returns True on success."""
    same_file = debit.file_path == credit.file_path

    if same_file:
        return apply_same_file_pair(debit, credit, merged_text, dry_run, verbose)
    else:
        return apply_cross_file_pair(debit, credit, merged_text, dry_run, verbose)


def apply_same_file_pair(
    debit: FXHalf,
    credit: FXHalf,
    merged_text: str,
    dry_run: bool,
    verbose: bool,
) -> bool:
    """Merge two FX entries in the same file into one."""
    file_path = debit.file_path
    blocks = parse_file_into_blocks(file_path)

    debit_idx = find_block_for_line(blocks, debit.line_no)
    credit_idx = find_block_for_line(blocks, credit.line_no)

    if debit_idx is None or credit_idx is None:
        print(f"  ERROR: could not find transaction blocks in {file_path}", file=sys.stderr)
        return False

    # Gather preceding comments for both blocks
    debit_comments = get_preceding_comments(file_path, debit.line_no)
    credit_comments = get_preceding_comments(file_path, credit.line_no)

    # Build replacement with combined comments
    comments = []
    if debit_comments:
        comments.append(debit_comments)
    if credit_comments and credit_comments != debit_comments:
        comments.append(credit_comments)

    replacement = ""
    if comments:
        replacement = "\n".join(comments) + "\n"
    replacement += merged_text

    # Replace the first (earlier) block with merged, remove the second
    first_idx = min(debit_idx, credit_idx)
    second_idx = max(debit_idx, credit_idx)

    # Also check if there's a comment-only block before each txn block
    # that should be absorbed
    new_blocks = list(blocks)
    # Remove second block first (higher index)
    # Check if preceding block is comments for the removed txn
    if second_idx > 0 and new_blocks[second_idx - 1]["type"] == "other":
        text = new_blocks[second_idx - 1]["text"].strip()
        if text and all(l.strip().startswith(";") or l.strip() == "" for l in text.split("\n")):
            # It's a comment block for the second txn - remove it too
            new_blocks = [b for i, b in enumerate(new_blocks) if i not in (second_idx - 1, second_idx)]
            if first_idx > second_idx - 1:
                first_idx -= 2
        else:
            new_blocks = remove_block(new_blocks, second_idx)
    else:
        new_blocks = remove_block(new_blocks, second_idx)

    # Replace first block
    new_blocks = replace_block_text(new_blocks, first_idx, replacement)

    if not dry_run:
        Path(file_path).write_text(blocks_to_text(new_blocks))

    return True


def apply_cross_file_pair(
    debit: FXHalf,
    credit: FXHalf,
    merged_text: str,
    dry_run: bool,
    verbose: bool,
) -> bool:
    """Merge two FX entries from different files."""
    # Determine which file keeps the entry
    # Prefer the file with more non-FX content (the "mixed" file)
    debit_blocks = parse_file_into_blocks(debit.file_path)
    credit_blocks = parse_file_into_blocks(credit.file_path)

    debit_txn_count = sum(1 for b in debit_blocks if b["type"] == "transaction")
    credit_txn_count = sum(1 for b in credit_blocks if b["type"] == "transaction")

    # Put merged entry in the file with more transactions (it's the "main" file)
    # If equal, prefer the debit side
    if credit_txn_count > debit_txn_count:
        keep_file = credit.file_path
        keep_line = credit.line_no
        keep_blocks = credit_blocks
        remove_file = debit.file_path
        remove_line = debit.line_no
        remove_blocks = debit_blocks
    else:
        keep_file = debit.file_path
        keep_line = debit.line_no
        keep_blocks = debit_blocks
        remove_file = credit.file_path
        remove_line = credit.line_no
        remove_blocks = credit_blocks

    # Replace the kept entry with the merged version
    keep_idx = find_block_for_line(keep_blocks, keep_line)
    if keep_idx is None:
        print(f"  ERROR: could not find block in {keep_file}:{keep_line}", file=sys.stderr)
        return False

    # Preserve any preceding comments
    comments = get_preceding_comments(keep_file, keep_line)
    replacement = ""
    if comments:
        replacement = comments + "\n"
    replacement += merged_text

    new_keep_blocks = replace_block_text(keep_blocks, keep_idx, replacement)

    # Remove entry from the other file
    remove_idx = find_block_for_line(remove_blocks, remove_line)
    if remove_idx is None:
        print(f"  ERROR: could not find block in {remove_file}:{remove_line}", file=sys.stderr)
        return False

    remove_txn_count = sum(1 for b in remove_blocks if b["type"] == "transaction")

    if not dry_run:
        Path(keep_file).write_text(blocks_to_text(new_keep_blocks))

        if remove_txn_count <= 1:
            # File has only this transaction - delete the whole folder
            folder = Path(remove_file).parent
            # Remove all files in folder
            for f in folder.iterdir():
                f.unlink()
            folder.rmdir()
            if verbose:
                rel = folder.relative_to(ROOT)
                print(f"    Deleted folder: {rel}")
        else:
            # Remove just this transaction from the file
            # Also remove preceding comment block if it belongs to this txn
            indices_to_remove = {remove_idx}
            if remove_idx > 0 and remove_blocks[remove_idx - 1]["type"] == "other":
                text = remove_blocks[remove_idx - 1]["text"].strip()
                if text and all(l.strip().startswith(";") or l.strip() == "" for l in text.split("\n")):
                    indices_to_remove.add(remove_idx - 1)

            new_remove_blocks = [b for i, b in enumerate(remove_blocks)
                                 if i not in indices_to_remove]
            Path(remove_file).write_text(blocks_to_text(new_remove_blocks))

    return True


def relative_path(filepath: str) -> str:
    """Convert absolute path to relative from repo root."""
    try:
        return str(Path(filepath).relative_to(ROOT))
    except ValueError:
        return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Sweep FX-Conversion pipe entries into single multi-currency entries"
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Apply changes (default is dry run)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show detailed output",
    )
    args = parser.parse_args()
    dry_run = not args.commit

    if dry_run:
        print("DRY RUN - no changes will be made (use --commit to apply)\n")

    # Load ledger
    print("Loading ledger...")
    entries, errors, _ = loader.load_file(str(MAIN_FILE))
    if errors:
        print(f"Warning: {len(errors)} ledger errors", file=sys.stderr)

    # Extract FX-Conversion entries
    halves = extract_fx_entries(entries)
    print(f"Found {len(halves)} FX-Conversion entries\n")

    # Categorize and pair
    cat_a, pairs, unmatched = find_pairs(halves)

    same_file_pairs = [(d, c) for d, c in pairs if d.file_path == c.file_path]
    cross_file_pairs = [(d, c) for d, c in pairs if d.file_path != c.file_path]

    print(f"Category A (auto-balanced, single entry): {len(cat_a)}")
    print(f"Category B (same-file pairs): {len(same_file_pairs)}")
    print(f"Category C (cross-file pairs): {len(cross_file_pairs)}")
    print(f"Unmatched: {len(unmatched)}")
    print()

    if unmatched:
        print("=== UNMATCHED (will not be modified) ===")
        for h in unmatched:
            print(f"  {h.date}  {h.bank_amount:>12,.2f} {h.bank_currency:>4}  "
                  f"{relative_path(h.file_path)}:{h.line_no}")
            print(f"           {h.narration[:60]}")
        print()

    # Process Category A
    if cat_a:
        print("=== Category A: Replace auto-balanced FX-Conversion with @ rate ===")
        for half in cat_a:
            merged = build_cat_a_entry(half)
            rate = compute_rate(abs(half.bank_amount), abs(half.other_bank_amount))
            print(f"  {half.date}  {abs(half.bank_amount):>12,.2f} {half.bank_currency} -> "
                  f"{abs(half.other_bank_amount):>12,.2f} {half.other_bank_currency}  "
                  f"@ {rate}")
            print(f"    File: {relative_path(half.file_path)}:{half.line_no}")
            if args.verbose:
                print(f"    Merged:\n{merged}\n")

            if not dry_run:
                apply_cat_a(half, merged, dry_run=False, verbose=args.verbose)
        print()

    # Process pairs
    if pairs:
        print("=== Pairs: Merge into single multi-currency entries ===")
        for debit, credit in pairs:
            source_abs = abs(debit.bank_amount)
            target_abs = abs(credit.bank_amount)
            rate = compute_rate(source_abs, target_abs)
            merged = build_merged_entry(debit, credit, rate=rate)

            same = "same-file" if debit.file_path == credit.file_path else "cross-file"
            print(f"  {debit.date}  {source_abs:>12,.2f} {debit.bank_currency} -> "
                  f"{target_abs:>12,.2f} {credit.bank_currency}  "
                  f"@ {rate}  ({same})")
            print(f"    Debit:  {relative_path(debit.file_path)}:{debit.line_no}")
            print(f"    Credit: {relative_path(credit.file_path)}:{credit.line_no}")
            if args.verbose:
                print(f"    Merged:\n{merged}\n")

            if not dry_run:
                success = apply_pair(debit, credit, merged, dry_run=False, verbose=args.verbose)
                if not success:
                    print("    FAILED - skipping")
        print()

    # Validate
    if not dry_run:
        print("Validating with bean-check...")
        result = subprocess.run(
            [str(ROOT / ".venv/bin/bean-check"), str(MAIN_FILE)],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print("bean-check: OK")
        else:
            print("bean-check: FAILED", file=sys.stderr)
            print(result.stderr, file=sys.stderr)
            sys.exit(1)

    # Summary
    total_merged = len(cat_a) + len(pairs)
    print(f"\n{'Would merge' if dry_run else 'Merged'}: {total_merged} FX conversions")
    print(f"Unmatched: {len(unmatched)}")


if __name__ == "__main__":
    main()
