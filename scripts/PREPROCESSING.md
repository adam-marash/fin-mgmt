# Data Preprocessing Pipeline

Each data source requires specific steps to go from raw export to usable input CSV.

## Source: HSBC Guernsey

**Raw format**: XLSX (downloaded from HSBC online banking)
**Output**: `data/2026-03-05-hsbc-gu-transactions/all-transactions.csv`

Steps:
1. `python scripts/normalize_hsbc.py <raw.xlsx>` - deterministic
   - Parses XLSX, normalizes dates, amounts, account numbers
   - Handles multi-sheet workbook (one sheet per sub-account)
   - Output: clean CSV with columns: date, account, description, amount, balance, currency

## Source: HSBC GU Individual PDF Statements

**Raw format**: PDF (individual transaction confirmations)
**Output**: `data/2026-03-05-hsbc-gu-statements/individual-statements.csv`

Steps:
1. `python scripts/parse_hsbc_stmts.py <pdf_dir>` - deterministic
   - Uses pdfplumber to extract transaction details from each PDF
   - Extracts counterparty names (critical for credit assignment)
   - Output: CSV with date, account, counterparty, amount, reference

## Source: HSBC GU Credit Assignment

**Raw format**: `all-transactions.csv` (filtered to credits on USD income account)
**Output**: `data/2026-03-05-hsbc-gu-credit-assignment/hsbc-gu-credit-assignment.csv`

Steps:
1. Filter all-transactions.csv to credits on MA6Z9LVT/MB37A4EW accounts
2. **Generative**: Match each credit to an investment using counterparty names from PDF statements, FO data, and knowledge.json
3. Human review of matches
4. Output: CSV with date, amount, status (matched/probable/unidentified), investment, fee

## Source: The Service FO Transactions

**Raw format**: XLSX (exported from The Service portal)
**Output**: `data/2026-03-05-fo-transactions/tamar-transactions.csv`

Steps:
1. `python scripts/normalize_fo.py <raw.xlsx>` - deterministic
   - Parses FO export, normalizes dates and amounts
   - Flags dates that may have been auto-parsed by Excel (`date_uncertain`)
   - Output: CSV with date, investment, type, amount, currency, date_uncertain

## Source: Bank Leumi ILS Account

**Raw format**: HTML-as-XLS (downloaded from Leumi online banking)
**Output**: `data/2026-03-05-leumi-transactions/ils-account.csv` (Hebrew)
**Actionable**: `data/2026-03-05-leumi-transactions/ils-account-en.csv` (English)

Steps:
1. `python scripts/normalize_leumi.py <raw.html>` - deterministic
   - Parses HTML table, normalizes dates, amounts
   - Preserves Hebrew descriptions
   - Output: CSV with date, value_date, description, extended, reference, amount, balance
2. **Generative**: Translate Hebrew to English, extract counterparty names and account numbers, categorize transactions
   - Input: ils-account.csv (Hebrew)
   - Output: ils-account-en.csv with columns: date, value_date, description, counterparty, counterparty_account, amount, balance, category, note
   - Categories: investment_income, fo_fees, tax_advisory, tax_payment, credit_card, bank_fee, personal_transfer, fx_conversion, cash_withdrawal, professional_services, insurance, other_income

## Source: Bank Leumi USD Account

**Raw format**: HTML-as-XLS (same Leumi export, FX account tab)
**Output**: `data/2026-03-05-leumi-transactions/usd-account.csv` (partially Hebrew)
**Actionable**: `data/2026-03-05-leumi-transactions/usd-account-en.csv` (English)

Steps:
1. `python scripts/normalize_leumi.py <raw.html>` - deterministic (same script)
2. **Generative**: Translate remaining Hebrew, standardize counterparty names, categorize
   - Output: usd-account-en.csv with columns: date, description, counterparty, counterparty_account, reference, amount, balance, category, note

## Source: Tzur/Apex Distribution Notices

**Raw format**: PDF (downloaded from Apex portal)
**Output**: `data/2026-03-05-tzur-distributions/tzur-distributions.csv`

Steps:
1. **Generative**: Parse PDFs using pdfplumber, extract distribution details
   - Investor number, distribution number, date, gross amount, withholding tax, carried interest, net amount
   - Match investor numbers to investments via knowledge.json

## Source: Gmail (_Investment label)

**Raw format**: Email (fetched via Gmail API)
**Output**: Multiple CSVs in `data/2026-03-05-email-*/`

Steps:
1. `python scripts/fetch_email.py scan --label _Investment` - deterministic (API call)
2. `python scripts/fetch_email.py fetch --label _Investment` - deterministic (downloads)
3. `python scripts/classify_emails.py` - **generative** (AI classifies email type)
4. `python scripts/extract_distributions.py` - **generative** (extracts distribution data from emails)
5. `python scripts/extract_capital_calls.py` - **generative** (extracts capital call data)
6. `python scripts/extract_reports_index.py` - **generative** (builds report index)

## Step Types

- **Deterministic**: Script produces identical output from identical input. No AI/LLM involved. Can be re-run safely.
- **Generative**: Requires AI/LLM for translation, classification, or extraction. Output should be reviewed by human. Once reviewed, the output file is the source of truth (not the script).
