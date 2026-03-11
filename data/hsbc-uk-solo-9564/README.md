# HSBC UK Solo Account 9564

Tamar's personal current account at HSBC UK (sort code 40-03-22, account 71342649).

## Sources

- **PDF statements** (`inbox/hsbc-uk-stmts/`): 113 PDFs covering Feb 2016 - Mar 2026. Parsed with `scripts/parse_hsbc_uk_stmts.py`.
- **Xero bank reconciliation export** (`xero-export.csv`): 4,577 bank statement lines, Jan 2020 - Mar 2026, with Xero account categorizations.

## Database

`tamar-solo-9564.db` is the reconciliation database. Key tables:

| Table | Purpose |
|-------|---------|
| `transactions` | 5,271 active transactions (2018-2026), each with `beancount_account` and `statement_file` |
| `statement_balances` | Opening/closing balance per PDF statement (91 statements) |
| `pdf_transactions` | Raw parsed PDF output (batch 4 = canonical, 4,907 txns) |
| `pdf_parse_batches` | Parse batch metadata for reproducibility |
| `description_rules` | 156 pattern-to-account mapping rules |
| `account_mapping` | Xero account ID to beancount account (42 mappings) |

## Reconciliation status

- **91/91 PDF statements reconciled to zero.** Every transaction is stamped with its source statement; each statement's transactions sum exactly to closing minus opening balance.
- **Opening balance**: GBP 11,132.65 (Dec 30, 2017)
- **Closing balance**: GBP 4,960.69 (Mar 3, 2026)
- **Two gap periods** without PDF statements:
  - Sep 2019 (Aug 30 - Sep 29): 19 manual catch-up transactions, verified against chain break.
  - May - Oct 2025 (Apr 30 - Oct 29): 368 Xero-sourced transactions, GBP 29 adjustment applied.
- **225 duplicate-flagged** transactions excluded (Xero reimports, boundary shifts, interaccount transfers).
