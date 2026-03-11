#!/usr/bin/env python3
"""HSBC JE reconciliation database.

Builds a SQLite database from existing CSV data, then runs three checks:
1. Intra-statement: do transactions bridge opening to closing balance?
2. Cross-statement continuity: does closing[N] == opening[N+1]?
3. Coverage: does every transaction combo have balance data?

Usage:
    python scripts/reconcile_hsbc_je.py build     # Build DB from CSVs
    python scripts/reconcile_hsbc_je.py check      # Run all checks
    python scripts/reconcile_hsbc_je.py problems   # Show problem list with PDF paths
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/hsbc-je-reconciliation.db")
TXN_CSV = Path("data/hsbc-je-statements.csv")
BAL_CSV = Path("data/hsbc-je-balances.csv")
PDF_DIR = Path("inbox/hsbc-je-dropbox")

SCHEMA = """
CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY,
    statement_date TEXT NOT NULL,
    account_number TEXT NOT NULL,
    account_type TEXT,
    currency TEXT,
    source_file TEXT,
    opening_balance REAL,
    closing_balance REAL,
    summary_balance REAL,
    verified INTEGER DEFAULT 0,  -- 1 = human-verified correct
    UNIQUE(account_number, statement_date)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    statement_date TEXT NOT NULL,
    account_number TEXT NOT NULL,
    account_type TEXT,
    currency TEXT,
    date TEXT,
    description TEXT,
    reference TEXT,
    deposit REAL,
    withdrawal REAL,
    balance REAL,
    seq INTEGER,  -- order within statement
    verified INTEGER DEFAULT 0,
    FOREIGN KEY (account_number, statement_date)
        REFERENCES statements(account_number, statement_date)
);

CREATE INDEX IF NOT EXISTS idx_txn_stmt
    ON transactions(account_number, statement_date);

CREATE INDEX IF NOT EXISTS idx_stmt_acct_date
    ON statements(account_number, statement_date);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # load order flexibility
    conn.row_factory = sqlite3.Row
    return conn


def build_db():
    """Build SQLite DB from existing CSVs."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
        print(f"Removed existing {DB_PATH}")

    conn = connect()
    conn.executescript(SCHEMA)

    # Load balances -> statements table
    with open(BAL_CSV) as f:
        rows = list(csv.DictReader(f))

    bal_count = 0
    for r in rows:
        def to_float(s):
            if not s or s.strip() == "":
                return None
            try:
                return float(s)
            except ValueError:
                return None

        conn.execute(
            """INSERT OR REPLACE INTO statements
               (statement_date, account_number, account_type, currency,
                source_file, opening_balance, closing_balance, summary_balance)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r["statement_date"],
                r["account_number"],
                r["account_type"],
                r["currency"],
                r["source_file"],
                to_float(r["opening_balance"]),
                to_float(r["closing_balance"]),
                to_float(r["summary_balance"]),
            ),
        )
        bal_count += 1

    print(f"Loaded {bal_count} statement records from {BAL_CSV}")

    # Load transactions
    with open(TXN_CSV) as f:
        rows = list(csv.DictReader(f))

    # Assign sequence numbers within each (account, statement_date)
    from collections import defaultdict
    seq_counters: dict[tuple, int] = defaultdict(int)

    # Ensure statements exist for transaction combos not in balance CSV
    txn_stmts = set()
    txn_count = 0
    for r in rows:
        key = (r["account_number"], r["statement_date"])
        if key not in txn_stmts:
            txn_stmts.add(key)
            # Insert statement stub if not already present
            existing = conn.execute(
                "SELECT id FROM statements WHERE account_number=? AND statement_date=?",
                key,
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO statements
                       (statement_date, account_number, account_type, currency)
                       VALUES (?, ?, ?, ?)""",
                    (r["statement_date"], r["account_number"],
                     r["account_type"], r["currency"]),
                )

        def to_float(s):
            if not s or s.strip() == "":
                return None
            try:
                return float(s)
            except ValueError:
                return None

        seq_counters[key] += 1
        conn.execute(
            """INSERT INTO transactions
               (statement_date, account_number, account_type, currency,
                date, description, reference, deposit, withdrawal, balance, seq)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r["statement_date"],
                r["account_number"],
                r["account_type"],
                r["currency"],
                r["date"],
                r["description"],
                r["reference"],
                to_float(r["deposit"]),
                to_float(r["withdrawal"]),
                to_float(r["balance"]),
                seq_counters[key],
            ),
        )
        txn_count += 1

    conn.commit()
    print(f"Loaded {txn_count} transactions from {TXN_CSV}")

    # Summary
    stmt_total = conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
    with_txns = conn.execute(
        """SELECT COUNT(DISTINCT account_number || '|' || statement_date)
           FROM transactions"""
    ).fetchone()[0]
    print(f"\nTotal statements: {stmt_total}")
    print(f"Statements with transactions: {with_txns}")
    print(f"Statements without transactions (zero-activity): {stmt_total - with_txns}")

    conn.close()


def check_intra(conn: sqlite3.Connection) -> list[dict]:
    """Check: do transactions bridge opening to closing balance?"""
    problems = []

    stmts = conn.execute(
        """SELECT s.account_number, s.statement_date, s.currency,
                  s.opening_balance, s.closing_balance, s.source_file
           FROM statements s
           WHERE EXISTS (
               SELECT 1 FROM transactions t
               WHERE t.account_number = s.account_number
                 AND t.statement_date = s.statement_date
                 AND t.balance IS NOT NULL
           )
           AND s.opening_balance IS NOT NULL
           AND s.closing_balance IS NOT NULL
           ORDER BY s.account_number, s.statement_date"""
    ).fetchall()

    for s in stmts:
        txns = conn.execute(
            """SELECT deposit, withdrawal, balance
               FROM transactions
               WHERE account_number=? AND statement_date=?
               ORDER BY seq""",
            (s["account_number"], s["statement_date"]),
        ).fetchall()

        # Check: opening + sum(deposits) - sum(withdrawals) = closing
        total_dep = sum(t["deposit"] or 0 for t in txns)
        total_wth = sum(t["withdrawal"] or 0 for t in txns)
        expected_closing = s["opening_balance"] + total_dep - total_wth

        if abs(expected_closing - s["closing_balance"]) > 0.02:
            problems.append({
                "type": "intra",
                "account": s["account_number"],
                "currency": s["currency"],
                "statement_date": s["statement_date"],
                "source_file": s["source_file"],
                "opening": s["opening_balance"],
                "closing": s["closing_balance"],
                "expected_closing": round(expected_closing, 2),
                "diff": round(s["closing_balance"] - expected_closing, 2),
                "txn_count": len(txns),
            })

    return problems


def check_continuity(conn: sqlite3.Connection) -> list[dict]:
    """Check: does closing[N] == opening[N+1] for each account?"""
    problems = []

    accounts = conn.execute(
        "SELECT DISTINCT account_number FROM statements ORDER BY account_number"
    ).fetchall()

    for row in accounts:
        acct = row["account_number"]
        stmts = conn.execute(
            """SELECT statement_date, opening_balance, closing_balance,
                      source_file, currency
               FROM statements
               WHERE account_number=?
               ORDER BY statement_date""",
            (acct,),
        ).fetchall()

        for i in range(len(stmts) - 1):
            curr = stmts[i]
            nxt = stmts[i + 1]

            if curr["closing_balance"] is None or nxt["opening_balance"] is None:
                continue

            diff = nxt["opening_balance"] - curr["closing_balance"]
            if abs(diff) > 0.02:
                problems.append({
                    "type": "continuity",
                    "account": acct,
                    "currency": curr["currency"],
                    "close_date": curr["statement_date"],
                    "close_file": curr["source_file"],
                    "closing": curr["closing_balance"],
                    "open_date": nxt["statement_date"],
                    "open_file": nxt["source_file"],
                    "opening": nxt["opening_balance"],
                    "diff": round(diff, 2),
                })

    return problems


def check_coverage(conn: sqlite3.Connection) -> list[dict]:
    """Check: every transaction combo has balance data."""
    problems = []

    rows = conn.execute(
        """SELECT DISTINCT t.account_number, t.statement_date, t.currency
           FROM transactions t
           LEFT JOIN statements s
             ON t.account_number = s.account_number
            AND t.statement_date = s.statement_date
           WHERE s.opening_balance IS NULL OR s.closing_balance IS NULL
           ORDER BY t.account_number, t.statement_date"""
    ).fetchall()

    for r in rows:
        problems.append({
            "type": "coverage",
            "account": r["account_number"],
            "currency": r["currency"],
            "statement_date": r["statement_date"],
        })

    return problems


def run_checks():
    """Run all checks and report."""
    conn = connect()

    print("=== Coverage (transactions without balance data) ===")
    coverage = check_coverage(conn)
    if not coverage:
        print("OK - all transaction combos have balance data")
    else:
        print(f"{len(coverage)} gaps:")
        for p in coverage:
            print(f"  {p['account']} {p['currency']} @ {p['statement_date']}")

    print("\n=== Intra-statement (txns don't bridge opening to closing) ===")
    intra = check_intra(conn)
    if not intra:
        print("OK - all transaction sets bridge correctly")
    else:
        print(f"{len(intra)} mismatches:")
        for p in intra:
            print(
                f"  {p['account']} {p['currency']} @ {p['statement_date']}: "
                f"open={p['opening']} + txns -> {p['expected_closing']}, "
                f"but close={p['closing']} (diff={p['diff']}) "
                f"[{p['txn_count']} txns, {p['source_file']}]"
            )

    print("\n=== Cross-statement continuity ===")
    continuity = check_continuity(conn)
    if not continuity:
        print("OK - all closing/opening balances continuous")
    else:
        print(f"{len(continuity)} gaps:")
        for p in continuity:
            print(
                f"  {p['account']} {p['currency']}: "
                f"{p['close_date']} close={p['closing']} -> "
                f"{p['open_date']} open={p['opening']} "
                f"(diff={p['diff']})"
            )

    total = len(coverage) + len(intra) + len(continuity)
    print(f"\n=== TOTAL: {total} problems ===")
    conn.close()
    return total


def show_problems():
    """Show consolidated problem list with PDF paths for fixing."""
    conn = connect()

    coverage = check_coverage(conn)
    intra = check_intra(conn)
    continuity = check_continuity(conn)

    # Collect all PDFs that need reading
    pdfs_needed: dict[str, list[str]] = {}  # source_file -> [reasons]

    for p in coverage:
        # Find any PDF for this date
        row = conn.execute(
            "SELECT source_file FROM statements WHERE account_number=? AND statement_date=?",
            (p["account"], p["statement_date"]),
        ).fetchone()
        pdf = row["source_file"] if row and row["source_file"] else "UNKNOWN"
        pdfs_needed.setdefault(pdf, []).append(
            f"  COVERAGE: {p['account']} {p['currency']} @ {p['statement_date']} - need opening/closing"
        )

    for p in intra:
        pdf = p.get("source_file", "UNKNOWN") or "UNKNOWN"
        pdfs_needed.setdefault(pdf, []).append(
            f"  INTRA: {p['account']} {p['currency']} @ {p['statement_date']} - "
            f"txns give {p['expected_closing']} but close={p['closing']} (diff={p['diff']})"
        )

    for p in continuity:
        for pdf_key in [p.get("close_file"), p.get("open_file")]:
            if pdf_key:
                pdfs_needed.setdefault(pdf_key, []).append(
                    f"  CONTINUITY: {p['account']} {p['currency']}: "
                    f"{p['close_date']} close={p['closing']} -> "
                    f"{p['open_date']} open={p['opening']} (diff={p['diff']})"
                )

    print(f"=== {len(pdfs_needed)} PDFs need review ===\n")
    for pdf, reasons in sorted(pdfs_needed.items()):
        full_path = PDF_DIR / pdf if pdf != "UNKNOWN" else "UNKNOWN"
        print(f"{full_path}")
        for r in sorted(set(reasons)):
            print(r)
        print()

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="HSBC JE reconciliation database.")
    parser.add_argument(
        "command",
        choices=["build", "check", "problems"],
        help="Command to run.",
    )
    args = parser.parse_args()

    if args.command == "build":
        build_db()
        print()
        run_checks()
    elif args.command == "check":
        run_checks()
    elif args.command == "problems":
        show_problems()


if __name__ == "__main__":
    main()
