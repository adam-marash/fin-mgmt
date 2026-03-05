# fin-mgmt

AI-native financial management system with beancount double-entry ledger.

## Key Files

- `ASSUMPTIONS.md` - problem-space truths and design decisions. Read at session start.
- `knowledge.json` - persistent domain knowledge (entities, investments, aliases, counterparty mappings).
- `ledger/` - the book of record (beancount files + source documents), git-tracked.
- `inbox/` - landing zone for incoming documents. Not part of the ledger.

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

## Conventions

- Python for scripts, venv at `.venv/`
- No em-dashes; use space-hyphen-space or restructure
- American English
