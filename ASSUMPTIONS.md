# Assumptions

Carried forward from the accounting project (~/life/accounting, adam-marash/aviv). These are problem-space truths, not solution choices.

## People & Structure

- Two people: Adam and Tamar. Tamar holds most assets in her name.
- Multiple accounts across both people, mixed formats (CSV, PDF, email), some in Hebrew.
- Ediblesites Ltd is out of scope.
- Secondary goal: preserve all data in calendar-year / USD views for personal use.

## Data Sources

- Bank accounts (Barclays, Leumi, etc.) in GBP/USD/EUR/ILS.
- Investment funds managed via family offices, with trustees as intermediaries.
- Tax documents (P60, P11D, 1042-S, SA302).
- Emails from trustees, family offices, fund administrators.
- The same economic event can appear in up to 4 sources (investee, trustee, bank, family office) in different currencies and on different dates.

## Document Types

- **Transactional** - contains extractable transaction rows (invoices, trustee reports with disbursements, FO summaries with line items).
- **Reference** - legitimate document with no transaction rows (tax forms, NAV statements, unit confirmations). Needs to be classified, linked, and indexed but has no data to import.
- **Noise** - no accounting value (delivery notifications, marketing).

## Financial

- No "primary" currency. Source data is stored in its native currency.
- Reports can be generated in any currency using FX rates.
- Investments and accounts each have a definitive settlement currency.
- Original currency always preserved alongside any conversion.
- Direct cross-rates needed (USD/ILS, USD/EUR, GBP/USD etc.) for accurate reconciliation.
- HMRC accepts transaction-date rates for SA.
- Historical FX rates must be stored and reusable.
- Flag any conversion where implied rate deviates >3% from market rate.

## Objectives (equal priority)

1. **Management information system** - Adam's personal view of all finances, denominated in USD. The primary internal tool.
2. **UK Self Assessment** - output materials for accountant/bookkeeper. Tax year: 6 Apr - 5 Apr (2025/26 is the current year). Adam and Tamar file separately.
3. **US tax reporting** - produce reports a bookkeeper can use to verify alignment with the accountant filing US returns.
4. **Israel tax reporting** - joint filing. Withholding credits from Israeli-source investments.
5. **Other jurisdictions** - extensible to additional tax regimes as needed.

The system should be able to produce jurisdiction-specific views from the same underlying data. A bookkeeper or accountant should be able to take the output and verify or file without needing to understand the system. Accountants receive reports (PDF/spreadsheet), not access to the ledger.

## UK Self Assessment (SA pages)

- SA100: UK dividends, interest, pension, state benefits
- SA102: Employment
- SA103: Self-employment
- SA104: UK property
- SA105: Partnerships
- SA106: Foreign dividends/interest/employment/rental/pension/other
- SA108: Capital gains
- Non-SA categories also needed: own-account transfers, personal expenses, investment fees, bank fees.

## Ledger Format

- Beancount (plaintext double-entry accounting). Chosen for: Python-native, strict validation, multi-currency, metadata/links, plugin API, established tooling.
- Every ledger entry carries a source reference (document path + page/field where applicable).
- Related entries are linked via `^link-tags` (e.g., all parts of a distribution share `^electra-dist-36`).
- Reporting dimensions use `#hash-tags` (e.g., `#uk-reportable`, `#us-reportable`, `#il-withholding`, `#provisional`, `#sa106`).
- Single ledger for both Adam and Tamar. Ownership is expressed through the account hierarchy, not separate ledgers. Jurisdiction-specific and per-person reporting is achieved through queries and tags.
- Account hierarchy uses colon-separated segments for natural grouping (e.g., `Assets:Banks:HSBC-GU:Tamar-Direct:USD-Income-7003`). No spaces in account names.

## Transaction Model

### Bank transactions are facts

- A bank credit or debit is a self-contained fact. It is booked immediately upon ingestion, without waiting for a matching announcement or notice.
- If the counterparty is recognized (via `knowledge.json`), the offsetting leg goes to `Assets:Receivable:<Investment>` (for credits) or the appropriate liability account (for debits) - see "Commitments and capital calls" below.
- If the counterparty is unknown, the offsetting leg goes to `Assets:Suspense` (a general holding account).
- Receivable balances can be positive or negative. Both are signals, not errors:
  - **Positive receivable**: an announcement was booked but the bank credit hasn't arrived yet ("where's my money?").
  - **Negative receivable**: a bank credit arrived but no announcement has been processed yet ("what is this payment for?").
  - **Zero**: fully reconciled.
- The receivable balance report is the primary anomaly detection tool. Any non-zero balance demands investigation or patience.

### Distributions (money in)

- When a distribution announcement arrives (from Tzur, email, etc.), it creates a receivable and records income classification. If the bank credit already arrived (negative receivable), the announcement moves the balance toward zero.
- Tax withholding clears part of the receivable and creates a tax credit per jurisdiction.
- Classification of income (yield vs capital return vs capital gain) may be provisional at distribution time. Unclassified amounts sit in `Income:Unclassified:<Investment>` per payee and are reclassified when the annual tax certificate arrives. This ambiguity is expected, not an error. The unclassified-per-payee pattern allows a debtors report (who owes us) and an open questions report (what needs classifying at tax time).

### Commitments and capital calls (money out)

- Signing an investment agreement creates a commitment (liability) for the full amount: `Liabilities:Commitments:<Investment>`. This is the total unfunded obligation. It is not due immediately.
- A capital call notice draws down part of the commitment: reduces the commitment and creates an immediate payable (`Liabilities:Payable:<Investment>`). The total obligation does not change - it shifts from unfunded to funded.
- The bank debit clears the payable.
- The unfunded commitment balance shows how much more could be called. The payable balance shows what is due now.
- Capital returns (money coming back from the fund) are distributions, not commitment events. They route through `Assets:Receivable` and `Income:Distribution:<Investment>:Capital-Return`, same as yield. They do not reduce or re-open the commitment. When a fund concludes with uncalled commitment, the remainder is explicitly released.
- FO-sourced entries stand in for the investment-side document (distribution announcement or capital call notice) only, never for the bank leg.
- Note: the FO CSV "deposit" records the actual bank movement (step 3), not the capital call notice (step 2). Commitments and capital call notices come from separate documents (investment agreements, call letters). See `PROCEDURES.md` for implementation details.

## Foreign Currency & Tax

- Entries are recorded in their native currency. Beancount never converts at entry time.
- GBP/USD/ILS equivalents for tax reporting are stored as metadata on entries (e.g., `rate-gbp`, `amount-gbp`), not as converted amounts. This avoids Xero-style phantom FX imbalances.
- Foreign currency receipts carry a GBP cost basis (`{rate GBP}`) for HMRC section 252 TCGA 1992. Disposal of foreign currency triggers a chargeable FX gain/loss.
- Multiple `operating_currency` directives enable USD management reports and GBP/ILS tax reports from the same ledger.
- FX rates stored in a `prices.beancount` file, used at report time only.

## Reconciliation

- **Mutual corroboration principle**: for family-office-managed investments, multiple independent sources must support each other like a Leonardo stick bridge - bank movements, trustee statements, family office transaction reports, and investee reports each confirm the others. No single source is trusted alone. Any material discrepancy between sources (beyond small differences from FX spreads or bank fees) is a red flag requiring human attention.
- Reconciliation is structural, not procedural: the double-entry ledger *is* the reconciliation. Receivable balances show what's outstanding. Unclassified income shows what needs tax categorization.
- The FO (family office) is a secondary source. Its primary role is to validate, not to create entries. However, FO data may serve as a **provisional primary source** when no counterparty-side document (distribution notice, trustee report) exists yet. FO-sourced entries are tagged `#fo-sourced` to record provenance. When a primary source arrives, the FO entry is demoted to an assertion. See `PROCEDURES.md` for the tagging and demotion patterns.
- When FO data is used as corroboration (its normal role), it produces `assertions.beancount` files in the ledger folder. These contain balance assertions or note metadata that cross-reference the primary entry. Mismatches between FO assertions and ledger actuals are flagged automatically.
- A single trustee report line item can decompose into multiple components (gross dividend, withholding tax, carried interest, transfer fee, net transfer) each with different reconciliation paths.
- Expected source coverage varies by event type:
  - Disbursement: investee + trustee + family office + bank
  - Management fee: trustee + bank
  - Capital call: investee + trustee + bank
  - Salary: employer + bank
  - Own-account transfer: source bank + destination bank
  - FO fee: family office + bank
  - Dividend: investee + bank

## Entity Resolution

- Counterparties appear under different names across sources (e.g., "ABC TRUSTEES", "A.B.C. TRUST LTD").
- Aliases can be source-specific (a bank may show a different name than a trustee report).
- Entity-to-investment links are what connect a bank transaction showing a trustee name to the underlying investment.

## Document Triage

Three categories at ingestion:
1. **Duplicate** (same SHA-256 hash as an already-filed document) - discard.
2. **Primary source** (creates or clears ledger entries) - bank statements, distribution announcements, capital call notices, investment agreements. Parsed into the ledger; original filed as backing.
3. **Secondary source** (validates but doesn't create entries) - FO summaries, quarterly reports, email confirmations. Filed and linked to relevant entries as corroboration. Mismatches trigger alerts.

Overlapping bank statements don't add value if the transactions are already in the ledger. A trustee distribution breakdown is irreplaceable - it's the only source of gross/tax/carry decomposition.

## Deduplication

- Document-level: SHA-256 hash checked against existing hashes under `ledger/` before filing. Duplicate documents discarded at ingestion.
- Transaction-level: beancount's own validation catches double-booked entries. Balance assertions extracted from bank statement closing balances are the ultimate guard - a duplicated entry will cause `bean-check` to fail.
- Overlapping rows from consecutive bank statements are expected. The ingestion process checks whether entries already exist in the ledger before creating new ones.

## Ledger Storage

- `ledger/` is the book of record, git-tracked for audit trail and backup.
- Each filed document lives in its own folder under `ledger/YYYY/` alongside its `entries.beancount` interpretation. Provenance is physical - the source document and its parsed entries sit together.
- The master `main.beancount` includes all entries via glob: `include "2025/*/entries.beancount"`.
- Secondary sources (FO summaries) produce `assertions.beancount` (notes/balance checks) rather than entries.
- Git diffs show exactly what changed and when. Reclassifications at tax time are visible as clean diffs with explanatory commit messages.

## Repository Structure

Everything is git-tracked. The only gitignored items are `.venv/` and `__pycache__/`.

```
fin-mgmt/
  ledger/                     # Book of record
    main.beancount            # Entry point (includes everything)
    accounts.beancount        # Chart of accounts
    prices.beancount          # FX rates
    2024/                     # Folders by year
    2025/
      2025-12-22-electra-a3f2c1/
        dist-notice.pdf       # Source document
        entries.beancount     # Parsed interpretation
      ...
  inbox/                      # Landing zone (aim: zero)
    <documents awaiting triage>
  dups/                       # Discarded duplicates (kept for auditability)
  data/                       # Date-prefixed folders: raw exports + cleaned CSVs
    2026-03-05-hsbc-gu-transactions/
      raw.xlsx                # Original export
      all-transactions.csv    # Normalized
    ...
  scripts/                    # Python scripts (normalizers, parsers, email fetcher)
  tmp/                        # Source files awaiting filing into ledger/
  config/                     # Credentials (gitignored separately if needed)
```

### Triage workflow

```
Document arrives -> inbox/
  |
  +-- Duplicate (SHA-256 match)? -> dups/
  +-- Primary source? -> ledger/YYYY/<date>-<source>-<hash>/ (with entries.beancount)
  +-- Secondary source (FO, investment stmt)? -> data/<date>-<source>-<desc>/
  |     (cleaned CSV + assertions.beancount linked to ledger entries)
  +-- Noise? -> delete
```

Target: inbox at zero. Triage promptly - inbox is git-tracked because triage lag is real and documents must not be lost in the gap between arrival and filing.

### What lives where

- **ledger/**: beancount files + filed source documents. The book of record.
- **inbox/**: documents awaiting triage. Transient but backed up.
- **dups/**: hash-matched duplicates. Cheap insurance, never referenced.
- **data/**: date-prefixed folders containing raw exports (XLSX, HTML-as-XLS) alongside their cleaned CSVs. Referenced by ledger entries as evidence. Hard to regenerate - always retained. Folders (not bare files) to allow multiple files, worksheets, notes.
- **tmp/**: raw source files awaiting filing. Cleaned up once contents are filed to `ledger/`.
- **scripts/**: Python scripts (normalizers, parsers, email fetcher).

### Ledger folder naming

Folders under `ledger/YYYY/` are named `<date>-<source>-<desc>-<hash>/`. The folder name is a "birth certificate" - it records what was known when the document was filed. It does not attempt to describe the economic event. Semantic grouping is handled by `^link-tags` inside the beancount entries, not by folder names. Folders are never renamed after filing. See `PROCEDURES.md` for the naming convention details.

### Reconciliation engine

When data arrives, the system must match it against existing ledger state. There are three distinct matching problems:

1. **Entry matching** - a new transaction finds its counterpart in the ledger. Bank credits/debits are booked immediately; if a matching receivable/payable already exists, reuse its `^link-tag`. Ambiguous counterparty: ask Adam.
2. **Evidence matching** - a secondary source (FO CSV, investment statement) confirms or contradicts existing entries. Produces `assertions.beancount` notes. Mismatches alert Adam.
3. **Enrichment matching** - two views of the same transaction from the same source system (e.g., HSBC `***` payee vs detail statement). Match by bank + account + date + amount. The detail enriches the existing entry regardless of arrival order.

### Cumulative source handling

Some sources (FO, bank exports) produce cumulative data spanning previously reported periods. Track a high-water mark per source (in `knowledge.json`) to identify the delta. Only new rows are processed.

### Gap detection

Balance reports are the primary anomaly detection tool. Non-zero receivable, payable, or suspense balances are signals requiring investigation. The FO layer adds a second dimension by comparing FO data against ledger entries. Reports should run after every ingestion batch.

## Documents & Formats

- Raw documents are never modified; all processing creates new files.
- Lineage must be visible: raw -> translated -> normalized -> decomposition.
- Hebrew documents need translation before processing.
- Normalized CSVs serve as an auditable bridge between raw documents and imported data.

## Human-in-the-Loop

- Approval required for unstructured documents (PDFs, emails).
- Human resolves: classification, extraction errors, ambiguous reconciliation matches, uncategorized transactions, unmatched counterparties, duplicate resolution.
- Corrections should feed back into learned patterns.

## Audit & Reversibility

- Git provides the append-only audit trail. Every commit is a state change.
- Imports are batch-reversible: `git revert` removes a batch of entries. Source documents remain for re-import.
- The ledger can be fully validated at any time with `bean-check main.beancount`.
