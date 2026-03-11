# fin-mgmt

AI-native financial management system with beancount double-entry ledger.

## Key Files

- `ASSUMPTIONS.md` - problem-space truths and design decisions. Read at session start.
- `PROCEDURES.md` - implementation patterns, account routing, script usage, and operational how-tos.
- `knowledge.json` - persistent domain knowledge (entities, investments, aliases, counterparty mappings).
- `ledger/` - the book of record (beancount files + source documents), git-tracked.
- `inbox/` - landing zone for incoming documents. Not part of the ledger.

## Scripts

All in `scripts/`. See `scripts/PREPROCESSING.md` for the full data pipeline.

| Script | Purpose |
|--------|---------|
| `fetch_fx.py` | Fetch FX rates (Frankfurter API) into `ledger/prices.beancount`. Merges and sorts. |
| `ingest.py` | Main ingestion pipeline. Modes: `hsbc-credit`, `scan`, `leumi-ils`, `leumi-usd`, `wise`, `report`. |
| `normalize_wise.py` | Normalize Wise CSV statement exports to clean CSVs. |
| `fetch_email.py` | Gmail fetcher. `scan`/`fetch` subcommands, `--label`, `--limit`. |
| `parse_hsbc_stmts.py` | Parse HSBC GU individual PDF statements (pdfplumber). |
| `normalize_hsbc.py` | Normalize HSBC GU XLSX export to clean CSV. |
| `normalize_hsbc_all.py` | Normalize all HSBC accounts (multi-sheet). |
| `normalize_fo.py` | Normalize The Service FO XLSX export to CSV. |
| `normalize_leumi.py` | Normalize Leumi HTML-as-XLS export to CSV. |
| `check_fx_deviations.py` | Compare FX conversions in ledger against market rates, flag >3% deviations. |
| `check_fo_assertions.py` | Cross-check FO transactions against ledger entries. Reports matched/FO-only/ledger-only. |
| `generate_fo_entries.py` | Create `#fo-sourced` ledger entries for unmatched FO transactions. Dry run by default. |
| `classify_distributions.py` | Classify distribution entries as Yield vs Capital-Return from FO CSV. |
| `check_procedures.py` | Sanity-check ledger against PROCEDURES.md conventions. Run with `--verbose` for full detail. |
| `sweep_fx_conversion.py` | Merge paired `Equity:FX-Conversion` entries into single multi-currency entries. Dry run by default. |
| `classify_emails.py` | AI-classify emails by type (distribution, capital call, report, etc.). |
| `extract_distributions.py` | AI-extract distribution data from classified emails. |
| `extract_capital_calls.py` | AI-extract capital call data from classified emails. |
| `extract_reports_index.py` | AI-build report index from classified emails. |

## Knowledge Base

`knowledge.json` is the persistent domain knowledge store. It contains entities, investments, people, aliases, categorization patterns, and relationships learned from processing real documents.

- **Always read `knowledge.json` at session start** before processing any financial data.
- **Update `knowledge.json` whenever you learn something new** about entities, investments, aliases, categorization, or relationships. Do not wait until end of session.
- When encountering a new sender, counterparty, or investment, check knowledge.json first for existing entries before creating new ones.
- Entries should be factual and verified (confirmed by data or by Adam), not speculative.

## Ledger

- Beancount plaintext double-entry. `ledger/main.beancount` is the entry point.
- Each source document lives in `ledger/YYYY/<folder>/` alongside its `entries.beancount`.
- Primary sources create ledger entries. Secondary sources (FO) create assertions only.
- `^link-tags` group entries into events (e.g., `^electra-dist-36`). `#hash-tags` mark reporting dimensions (e.g., `#uk-reportable`).
- **After every change to ledger content**, run `bean-check ledger/main.beancount` to validate. Do not proceed if it fails.
- **After completing a unit of work**, commit without asking. Do not ask "want me to commit?" - just do it.

## Web UI

See [`WEB-UI.md`](WEB-UI.md) for full documentation. Single-file PHP + Alpine.js + Tailwind app at `web/public/index.php`.

## Conventions

- Python for scripts, venv at `.venv/`
- No em-dashes; use space-hyphen-space or restructure
- American English
