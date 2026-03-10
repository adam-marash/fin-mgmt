# Procedures

Implementation patterns and usage guide. For design decisions and problem-space truths, see `ASSUMPTIONS.md`.

## Data Patterns

### Wire fees

Bank payment = FO amount + $45 (consistent across all matched investment payments). The $45 is a HSBC outgoing wire fee. When matching FO deposits to bank debits, allow for this difference.

### IBI-as-trustee payments

Investment payments routed through IBI as trustee are never bundled - each payment appears as a separate bank debit. Do not attempt to split or aggregate IBI-trusteed transactions.

### FO date vs bank date

FO records the fund's settlement date. Bank records the payment date. The bank date is typically 5-22 days earlier than FO. The FO-to-ledger matcher uses a tolerance window (default 5 days, 45 days for date-uncertain entries).

### FO deposit semantics

An FO "deposit" records the actual payment (Investment Lifecycle step 3b), not the capital call notice (step 2). FO-sourced entries from deposits are labeled "investment payment", not "capital call".

## Source Hierarchy and Demotion

Primary source (bank statement, investment agreement) supersedes secondary (FO).

### FO as announcement substitute only

FO-sourced entries stand in for the investment-side document only - either a distribution announcement (money in) or a capital call payment (money out). They never replace a bank leg. For distributions, the balance sheet leg goes to `Assets:Receivable`. For investment payments, the entry creates `Assets:Investments`, reduces `Liabilities:Commitments`, unwinds `Equity:Commitments`, and parks the cash outflow in `Assets:Suspense` (see Investment Lifecycle step 3b). FO entries never touch `Assets:Banks`.

### Creating FO-sourced entries

When no primary source exists, FO data serves as a provisional primary source:

```
2024-06-15 * "ISF-III - investment payment (FO-sourced)" #fo-sourced #provisional
  source: "data/2026-03-05-fo-transactions/tamar-transactions.csv"
  fo-line: "123"
  Assets:Investments:ISF-III        100,000.00 USD
  Liabilities:Commitments:ISF-III   100,000.00 USD
  Equity:Commitments               -100,000.00 USD
  Assets:Suspense                  -100,000.00 USD
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

## Investment Lifecycle

An investment goes through up to seven steps. Not all steps occur for every investment. FO-sourced variants use `Assets:Suspense` as a placeholder until matched to bank.

### Step 1: Commitment signed

Source: investment agreement.

```beancount
2019-06-01 * "Investment commitment - Boligo-1" #primary-source
  source: "inbox/commitments/boligo-1-commitment.pdf"
  Liabilities:Commitments:Boligo-1  -500,000.00 USD
  Equity:Commitments                 500,000.00 USD
```

`Liabilities:Commitments` = long-term callable obligation (negative = unfunded).
`Equity:Commitments` = balancing entry for the off-balance-sheet promise.

### Step 2: Capital call notice received

Source: call notice from fund administrator.

```beancount
2019-06-05 * "Boligo-1 - capital call notice" ^boligo-1-call-1
  source: "inbox/capital-calls/boligo-1-call-2019-06.pdf"
  Liabilities:Commitments:Boligo-1    200,000.00 USD
  Liabilities:Capital-Calls:Boligo-1 -200,000.00 USD
```

Moves the amount from long-term commitment to short-term payable ("due now").
If no call notice document exists, steps 2+3 collapse into step 3 alone.

### Step 3a: Capital call paid (bank known)

Source: bank statement.

```beancount
2019-06-07 * "Boligo-1 - investment payment" ^boligo-1-call-1
  source: "ledger/2019/.../entries.beancount"
  Assets:Investments:Boligo-1                          200,000.00 USD
  Liabilities:Capital-Calls:Boligo-1                   200,000.00 USD
  Equity:Commitments                                  -200,000.00 USD
  Assets:Banks:HSBC-GU:Tamar-Direct:USD-Capital-5637  -200,000.00 USD
```

Four legs: payable cleared, investment asset created, equity unwinds, cash leaves bank.
If step 2 was skipped, replace `Liabilities:Capital-Calls` with `Liabilities:Commitments`.

### Step 3b: Capital call paid (FO-sourced, bank unknown)

Source: FO transaction data.

```beancount
2019-06-07 * "Boligo-1 - investment payment (FO-sourced)" #fo-sourced
  source: "data/2026-03-05-fo-transactions/tamar-transactions.csv"
  fo-line: "42"
  Assets:Investments:Boligo-1                          200,000.00 USD
  Liabilities:Capital-Calls:Boligo-1                   200,000.00 USD
  Equity:Commitments                                  -200,000.00 USD
  Assets:Suspense                                     -200,000.00 USD
```

Same as 3a but `Assets:Suspense` stands in for the unknown bank account.

### Step 3c: Bank matched (clears FO suspense)

When the bank statement arrives for a previously FO-sourced payment:

```beancount
2019-06-07 * "Boligo-1 - bank match for FO payment"
  Assets:Banks:HSBC-GU:Tamar-Direct:USD-Capital-5637  -200,000.00 USD
  Assets:Suspense                                       200,000.00 USD
```

Steps 3b + 3c net to step 3a.

### Step 4a: Yield distribution announced

Source: distribution notice or FO data.

```beancount
2022-11-29 * "Boligo-1 - yield distribution" #fo-sourced ^boligo-1-dist-1
  source: "data/2026-03-05-fo-transactions/tamar-transactions.csv"
  Assets:Receivable:Boligo-1                5,000.00 USD
  Income:Distribution:Boligo-1:Yield       -5,000.00 USD
```

### Step 4b: Capital return announced

Source: distribution notice or FO data. Capital returns reduce cost basis (not income).

```beancount
2024-03-04 * "Boligo-1 - capital return" #fo-sourced ^boligo-1-dist-2
  source: "data/2026-03-05-fo-transactions/tamar-transactions.csv"
  Assets:Receivable:Boligo-1          10,000.00 USD
  Assets:Investments:Boligo-1        -10,000.00 USD
```

### Step 5: Distribution received (bank)

Source: bank statement.

```beancount
2022-12-05 * "Boligo-1 - distribution received" ^boligo-1-dist-1
  source: "ledger/2022/.../entries.beancount"
  Assets:Banks:Leumi:ILS           18,000.00 ILS
  Assets:Receivable:Boligo-1      -18,000.00 ILS
```

Clears the receivable. Link tag ties it to the announcement in step 4.

### Capital returns do not affect commitments

Capital returns are distributions (money coming back to us). They route through `Assets:Receivable` and reduce `Assets:Investments:<Investment>` (cost basis). Unlike yield distributions (which are income), capital returns are recovery of invested capital. They never touch `Liabilities:Commitments`.

The commitment tracks the contractual call schedule (money out) only. Capital returned does not re-open or reduce the commitment. The net balance on `Assets:Investments:<Investment>` shows cost basis minus capital returned.

When an investment concludes, any uncalled commitment is explicitly released:

```beancount
2026-06-30 * "Netz - commitment release on fund wind-down"
  Liabilities:Commitments:Netz  100,000 USD
  Equity:Commitments           -100,000 USD
```

### Balance signals

**Commitments** (`Liabilities:Commitments:X`):

| Balance | Meaning | Action |
|---------|---------|--------|
| Negative | Unfunded obligation remaining | Normal state |
| Zero | Fully funded | Done |
| Positive | Over-drawn | Investigate: duplicate entries, wrong commitment amount, or wire fees |

**Capital calls** (`Liabilities:Capital-Calls:X`):

| Balance | Meaning | Action |
|---------|---------|--------|
| Negative | Called but not yet paid | Pay or investigate |
| Zero | All calls paid | Normal |

**Investments** (`Assets:Investments:X`):

| Balance | Meaning | Action |
|---------|---------|--------|
| Positive | Cost basis of holdings | Normal |
| Zero | Not yet funded or fully returned | Check commitments |

**Receivables** (`Assets:Receivable:X`):

| Balance | Meaning | Action |
|---------|---------|--------|
| Positive | Announcement booked, bank credit pending | "Where's my money?" |
| Negative | Bank credit arrived, announcement pending | "What is this for?" |
| Zero | Fully reconciled | Done |

### Excluded from commitment tracking

- IBI-Portfolio and Yalin-Portfolio (managed portfolios, not commitment-based)

## Account Routing

| Flow | Account pattern | Example |
|------|----------------|---------|
| Investment cost basis | `Assets:Investments:<Investment>` | `Assets:Investments:Boligo-1` |
| Distribution announced | `Assets:Receivable:<Investment>` | `Assets:Receivable:Electra-MIF-II` |
| Long-term commitment | `Liabilities:Commitments:<Investment>` | `Liabilities:Commitments:Boligo-1` |
| Capital call due | `Liabilities:Capital-Calls:<Investment>` | `Liabilities:Capital-Calls:Boligo-1` |
| Commitment/equity offset | `Equity:Commitments` | multi-currency (USD, EUR, ILS) |
| Unknown bank (FO placeholder) | `Assets:Suspense` | `Assets:Suspense` |
| Unknown inflow | `Income:Suspense:<Currency>` | `Income:Suspense:ILS` |
| Personal expense | `Expenses:Personal:<Person>` | `Expenses:Personal:Tamar` |

### Investment metadata on cross-cutting accounts

Some accounts are organized by jurisdiction or cost type rather than by investment (e.g., `Expenses:Tax:Israel`, `Expenses:Professional:FO-Fees`). Transactions on these accounts that relate to a specific investment carry `investment:` metadata so they appear in investment-level queries:

```beancount
2021-10-21 * "Data Center LA - US withholding" ^data-center-la-dist-2
  investment: "Data-Center-LA"
  Expenses:Tax:US:NYS  -77,223.00 USD
  Assets:Receivable:Data-Center-LA  77,223.00 USD
```

Accounts that need `investment:` metadata (the account name does not contain the investment):
- `Expenses:Tax:<Jurisdiction>` - withholding tax
- `Expenses:Professional:FO-Fees` - family office fees attributable to a specific investment
- `Expenses:Professional:Tax-Advisory` - tax advisory fees for a specific investment

Accounts that do NOT need it (the investment name is already in the account path):
- `Assets:Investments:<Investment>`
- `Assets:Receivable:<Investment>`
- `Liabilities:Commitments:<Investment>`
- `Liabilities:Capital-Calls:<Investment>`
- `Income:Distribution:<Investment>:*`
- `Expenses:Carried-Interest:<Investment>`

A complete investment view:
```
WHERE account ~ '<Investment>' OR ANY_META('investment') = '<Investment>'
```

## Distribution Classification

Distributions are initially booked as `Income:Distribution:<Investment>:Unclassified` when the income type is unknown. Classification happens when a source provides the yield vs capital-return breakdown.

### Classification sources (in priority order)

1. **Annual tax certificate** from the fund - definitive, reclassify all distributions for that tax year
2. **FO transaction data** - FO often reports yield and capital-return as separate line items for the same distribution event
3. **Distribution notice** from investee or trustee - sometimes states the character of the payment

### Reclassification procedure

When reclassifying from `Unclassified` to `Yield`/`Capital-Return`/`Capital-Gain`:

1. **Yield**: change to `Income:Distribution:<Investment>:Yield`
2. **Capital-Return**: change to `Assets:Investments:<Investment>` (reduces cost basis, not income)
3. **Capital-Gain**: change to `Income:Distribution:<Investment>:Capital-Gain`
4. Add `classification-source:` metadata pointing to the document that provides the classification
5. Remove `#provisional` tag if present
6. If a single distribution splits into multiple types (e.g., part yield + part capital return), split into separate legs

```beancount
2023-10-12 * "Data Center LA - distribution announced" ^data-center-la-dist-2
  classification-source: "data/2026-03-05-fo-transactions/tamar-transactions.csv"
  Assets:Receivable:Data-Center-LA  859,646.00 USD
  Income:Distribution:Data-Center-LA:Yield  -277,682.00 USD
  Assets:Investments:Data-Center-LA  -581,964.00 USD
```

### When NOT to reclassify

- FO split is ambiguous or doesn't match the primary entry amount
- Annual tax certificate will arrive soon and override any interim classification
- The amounts don't reconcile (investigate first)

## Leumi Deposit Redemptions

Leumi rolling USD deposits (Branch 671, acc 245100) mature quarterly. The bank inconsistently reports interest:

- **Bundled**: redemption amount = principal + interest in a single line (פרעון פקדון). No separate interest line.
- **Separated**: redemption amount = principal only, with a separate interest line (ריבית מט"ח).
- **Corrected**: redemption includes interest, then bank issues a correction (תיקון ריבית) and re-credits interest separately.

When the redemption amount exceeds the preceding placement amount, the difference is interest income. Always split the redemption entry:

```beancount
2024-12-06 * "Leumi USD deposit redemption (principal + interest)"
  Assets:Banks:Leumi:USD      1,195,718.48 USD  ; bank credit (lump)
  Assets:Deposits:Leumi:USD  -1,181,345.55 USD  ; principal = placement amount
  Income:Interest:Leumi          -14,372.93 USD  ; interest = redemption - placement
```

The bank typically rolls over deposits: the full redemption amount (principal + interest) is re-placed as the new deposit. The new placement amount becomes the new principal. So the "preceding placement amount" for the next cycle's redemption is this rolled-over amount, not the original.

When ingesting new Leumi deposit redemptions, always compare the redemption amount to the most recent placement to detect embedded interest.

## FX Patterns

FX conversions are single multi-currency entries using `@ rate` syntax. Put the `@ rate` on the target currency posting (the currency being received), denominated in the source currency:

```beancount
2024-05-09 * "FX conversion USD to ILS (rate 3.6871)"
  source: "data/leumi-transactions/usd-account.csv"
  source-2: "data/leumi-transactions/ils-account.csv"
  Assets:Banks:Leumi:USD  -8,149.41 USD
  Assets:Banks:Leumi:ILS  30,000.00 ILS @ 0.271647 USD
```

When both sides come from different bank statements, use `source:` and `source-2:` to record both.

### Ingestion workflow for FX

- **Wise**: The debit-side CSV row has all FX data (`fx_from`, `fx_to`, `fx_rate`, `fx_amount`). Ingest emits a single merged entry. The credit-side row is skipped.
- **Leumi/HSBC**: Each side is ingested independently. The first leg goes to `Equity:FX-Conversion` as a temporary clearing account. Run `scripts/sweep_fx_conversion.py --commit` after ingesting both sides to merge paired entries.

### Rate precision

Use enough decimal places so that `target_amount * rate` is within 0.005 of `source_amount` (beancount's default tolerance). For large amounts, this may require 8-10 decimal places. Known beancount v3 bug: `@@` with non-terminating decimals causes precision errors - always use per-unit `@ rate` instead.

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

### Sweeping FX conversions

After ingesting both sides of FX conversions from separate bank statements:

```bash
python scripts/sweep_fx_conversion.py              # dry run - show proposed merges
python scripts/sweep_fx_conversion.py --commit      # apply changes
python scripts/sweep_fx_conversion.py --verbose      # show detail
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
- Account routing (distributions -> Receivable, payments -> Investments + Commitments + Equity)
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

## Script Behavior Notes

- `check_fo_assertions.py`: matches against both `Assets:Receivable:` (withdrawals) and `Liabilities:Commitments:` (deposits). Allows $45 HSBC wire fee difference on deposit matches.
- `generate_fo_entries.py`: generates 4-legged entries for deposits per Investment Lifecycle step 3b (`Assets:Investments` + `Liabilities:Commitments` + `Equity:Commitments` + `Assets:Suspense`). IBI-Portfolio and Yalin-Portfolio are excluded (managed portfolios, use `Assets:Receivable`). Matching checks both receivable and commitment accounts.

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

## TODO: Tax Deductibility Tagging

Currently `#us-deductible` is applied ad-hoc to Virtu Tax entries. Need to generalize:
- Define which tags exist (e.g., `#us-deductible`, `#uk-deductible`, `#il-deductible`)
- Document which expense categories are deductible in which jurisdictions
- Decide whether deductibility is tagged per-transaction or derived from account + rules
- Ensure ingestion scripts apply tags automatically where the mapping is known
