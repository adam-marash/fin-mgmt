# Procedures

Implementation patterns and usage guide. For design decisions and problem-space truths, see `ASSUMPTIONS.md`.

## Data Patterns

### Wire fees

Bank payment = FO amount + $45 (consistent across all matched investment payments). The $45 is a HSBC outgoing wire fee. When matching FO deposits to bank debits, allow for this difference.

### FO date vs bank date

FO records the fund's settlement date. Bank records the payment date. The bank date is typically 5-22 days earlier than FO. The FO-to-ledger matcher uses a tolerance window (default 5 days, 45 days for date-uncertain entries).

### FO deposit semantics

An FO "deposit" records the actual bank movement (step 3: investment payment), not the capital call notice (step 2). FO-sourced entries from deposits are labeled "investment payment", not "capital call".

## Source Hierarchy and Demotion

Primary source (bank statement, investment agreement) supersedes secondary (FO).

### Creating FO-sourced entries

When no primary source exists, FO data serves as a provisional primary source:

```
2024-06-15 * "ISF-III - investment payment (FO-sourced)" #fo-sourced #provisional
  source: "data/2026-03-05-fo-transactions/tamar-transactions.csv"
  fo-line: "123"
  Liabilities:Commitments:ISF-III  100,000.00 USD
  Assets:Suspense  -100,000.00 USD
```

Tags: `#fo-sourced` (provenance marker, persists) + `#provisional` (removed when primary source arrives).

### Demoting to assertion

When a bank primary source arrives for an FO-sourced entry, the entry is replaced with a note assertion:

```beancount
; FO corroboration (superseded by bank primary source)
; Original: Liquidity-Capital - investment payment (FO-sourced), fo-line 85
2021-10-14 note Liabilities:Commitments:Liquidity-Capital "FO deposit 60000.00 USD corroborates bank payment 2021-10-05 60045.00 USD (diff 45 = wire fee). Source: ledger/2021/2026-03-05-hsbc-stmt-0fb01a72/entries.beancount"
```

The note format:
- Date: FO transaction date
- Account: the relevant commitment or receivable account
- Text: FO amount, bank amount, difference explanation, cross-reference to primary entry

### When to demote

Demote when:
- A bank statement entry exists for the same investment, similar amount (within 5% or $45 wire fee), and nearby date (within tolerance)
- The bank entry is the primary source, and the FO entry would be a duplicate

Do not demote when:
- No matching bank entry exists (FO entry is still the best available source)
- The amounts differ significantly (investigate first)

## Three-Step Commitment Model

### Step 1: Commitment (from agreements)

```beancount
2019-01-01 * "Investment commitment - Boligo-1 (provisional)" #fo-sourced #provisional
  Liabilities:Commitments:Boligo-1  -500,000.00 USD
  Equity:Commitments  500,000.00 USD
```

- Source: investment agreements (primary) or FO deposit totals (provisional)
- Stored in `ledger/commitments.beancount`
- Negative balance = unfunded obligation

### Step 2: Capital call (from call notices) - NOT YET IMPLEMENTED

Would move from `Liabilities:Commitments` to `Liabilities:Payable`. Waiting on capital call notice documents.

### Step 3: Investment payment (from bank/FO)

```beancount
2021-10-05 * "Investment payment - Liquidity-Capital" ^liquidity-capital-payment-3
  Liabilities:Commitments:Liquidity-Capital  60,045.00 USD
  Assets:Banks:HSBC-GU:Tamar-Direct:USD-Capital-5637  -60,045.00 USD
```

- Bank statement amount includes wire fee
- Draws down the commitment directly (steps 2+3 combined)

### Commitment balance signals

| Balance | Meaning | Action |
|---------|---------|--------|
| Negative | Unfunded obligation remaining | Normal state |
| Zero | Fully funded | Done |
| Positive | Over-drawn | Investigate: duplicate entries (issue #12), wrong commitment amount, or wire fees |

### Excluded from commitment tracking

- IBI-Portfolio and Yalin-Portfolio (managed portfolios, not commitment-based)

## Account Routing

| Flow | Account pattern | Example |
|------|----------------|---------|
| Distribution (money in) | `Assets:Receivable:<Investment>` | `Assets:Receivable:Electra-MIF-II` |
| Investment payment (money out) | `Liabilities:Commitments:<Investment>` | `Liabilities:Commitments:Boligo-1` |
| Unknown counterparty | `Assets:Suspense` | `Assets:Suspense` or `Assets:Suspense:Betegy` |
| Personal expense | `Expenses:Personal:<Person>` | `Expenses:Personal:Tamar` |
| Commitment offset | `Equity:Commitments` | multi-currency (USD, EUR, ILS) |

### Receivable balance signals

- **Positive**: announcement booked, bank credit pending ("where's my money?")
- **Negative**: bank credit arrived, announcement pending ("what is this for?")
- **Zero**: fully reconciled

## FX Patterns

- Put `@ rate TARGET_CCY` on the asset/source-currency side, let beancount auto-balance the other side
- Known beancount v3 bug: `@@` with non-terminating decimals causes precision errors. Use `@ rate` instead.
- Frankfurter API uses `.app` domain (not `.dev`)

### FX deviation checking

Three genuine deviations remain in the ledger, all HSBC bank spread (3-4%). These are expected - HSBC applies its own spread to FX conversions.

## Script Usage

### Ingesting new FO data

```bash
# 1. Normalize FO export
python scripts/normalize_fo.py <raw.xlsx>

# 2. Cross-check against ledger
python scripts/check_fo_assertions.py

# 3. Generate entries for unmatched (dry run first)
python scripts/generate_fo_entries.py
python scripts/generate_fo_entries.py --write

# 4. Validate
bean-check ledger/main.beancount
```

### Ingesting bank data

```bash
# HSBC GU
python scripts/normalize_hsbc.py <raw.xlsx>          # single account
python scripts/normalize_hsbc_all.py <raw.xlsx>      # all accounts

# Leumi
python scripts/normalize_leumi.py <raw.html>

# Then ingest
python scripts/ingest.py <mode> <csv>
bean-check ledger/main.beancount
```

### Checking FX rates

```bash
# Fetch latest rates into prices.beancount
python scripts/fetch_fx.py

# Audit conversions against market rates (flag >3%)
python scripts/check_fx_deviations.py
python scripts/check_fx_deviations.py --threshold 5    # custom threshold
python scripts/check_fx_deviations.py --verbose         # show all, not just deviations
```

### Email pipeline

```bash
python scripts/fetch_email.py scan --label _Investment       # scan for new emails
python scripts/fetch_email.py fetch --label _Investment      # download
python scripts/classify_emails.py                             # AI classify by type
python scripts/extract_distributions.py                       # extract distribution data
python scripts/extract_capital_calls.py                       # extract capital call data
python scripts/extract_reports_index.py                       # build report index
```

### Validation (run after every ledger change)

```bash
bean-check ledger/main.beancount
```

### Procedures sanity check

```bash
python scripts/check_procedures.py            # summary
python scripts/check_procedures.py --verbose   # full detail
```

Checks ledger compliance with conventions in this file and ASSUMPTIONS.md:
- FO-sourced metadata (`source:`, `fo-line:`)
- Classification metadata (`classification-source:` on Yield/Capital-Return entries)
- `#provisional` not on classified entries
- Account routing (distributions -> Receivable, payments -> Commitments)
- Folder naming (`<date>-<desc>[-<hash>]`)
- Over-drawn commitment balances (positive = investigate)
- Suspense balances (non-zero = unresolved counterparty)
- Non-zero receivable balances (pending reconciliation)
- `@@` total-cost usage (beancount v3 precision bug)
- `source:` metadata on every transaction
- Link tag format (lowercase-kebab-case)
- FO-reported uncleared (FO says distribution paid, no bank credit found - any currency)
- Link sequence gaps (`^prefix-1`, `^prefix-3` missing `^prefix-2`)
- Link singletons (^tag with only 1 entry - missing counterpart?)
- Link cross-investment (entries sharing a ^tag must reference the same investment)

## Script Limitations (known)

- `check_fo_assertions.py`: only matches against `Assets:Receivable:` accounts. Does not check `Liabilities:Commitments:` for investment payments. Needs updating.
- `generate_fo_entries.py`: currently routes all deposits to `Assets:Receivable` + `Assets:Suspense` rather than `Liabilities:Commitments`. Needs updating to use commitment accounts for payments and receivable accounts for distributions.

## Link Tag Conventions

Format: `^<investment>-<type>-<seq>`

- Bank credits: `^<beancount-name>-dist-<seq>` (auto-incremented per investment)
- Boligo-2 USD: uses `^boligo-2-usd-dist-N` to avoid clash with ILS distributions
- Sequence number determined by counting existing tags for that investment+type

## Ledger Folder Naming

Pattern: `ledger/YYYY/<filing-date>-<source>-<desc>-<hash>/`

- `<filing-date>`: when the document was processed (not transaction date)
- `<source>`: origin identifier (e.g., `electra`, `hsbc`, `fo`)
- `<desc>`: human-readable description (e.g., `dist-notice`, `credit-47k`)
- `<hash>`: first 6-8 chars of SHA-256 for uniqueness
- FO-sourced folders: `<filing-date>-<investment>-fo-sourced-<hash>/`
- Folders are never renamed after filing
