# Web UI

Single-file PHP + Alpine.js + Tailwind app at `web/public/index.php`. No build step, no npm, no database.

## Architecture

- **Backend**: Pure PHP. Serves JSON APIs by shelling out to `bean-query` (from `.venv/bin/`). Also serves PDFs from ledger folders with path-traversal protection.
- **Frontend**: Alpine.js for reactivity, Tailwind CSS (CDN) for styling. All HTML/JS/CSS in one file.
- **Data flow**: Browser fetches `?api=events|entry|pnl|balsheet|trialbal|account|fxrates` endpoints. Backend runs BQL queries, parses CSV output, returns JSON.

## Pages

| Page | Description |
|------|-------------|
| **Ledger** | Sidebar with year/event tree, search, j/k navigation. Detail panel shows beancount entries alongside source PDFs (split/entries/PDF tabs). |
| **P&L** | Income/Expenses by year with multi-currency columns + USD equivalent. Expandable account groups. |
| **Balance Sheet** | Assets/Liabilities/Equity cumulative through selected year. Same layout as P&L. |
| **Trial Balance** | All accounts, all time. Grand total should be zero. |

## Key Features

- **Account drill-down modal**: Click any account name to see its journal with running balance, debit/credit columns, and contra accounts. Clickable contra accounts for navigation.
- **Year-bound modals**: P&L and Balance Sheet drill-downs filter to the selected year. Trial balance shows all time.
- **Stateful modals**: Account modal state persists in URL hash (e.g., `#pnl/2025/Assets:Bank:HSBC`). Survives page refresh.
- **Calendar/UK tax year toggle**: Slider in header switches between Jan-Dec and 6 Apr-5 Apr. Persisted in URL hash (`/uk` suffix). Affects all report queries and drill-downs.
- **Reconciled indicator**: Green "R" badge on transactions tagged `#reconciled`.
- **Keyboard shortcuts**: `/` to search, `j`/`k` to navigate, `1`/`2`/`3` to switch views.
- **Client-side caching**: Entry, report, and account data cached to avoid redundant API calls.

## URL Hash Formats

| Hash | Meaning |
|------|---------|
| `#folder-name` | Ledger page, event selected |
| `#folder-name/Account:Name` | Ledger page with account modal open |
| `#pnl/2025` | P&L for calendar year 2025 |
| `#pnl/2025/uk` | P&L for UK tax year 2025/26 |
| `#pnl/2025/uk/Income:Interest` | P&L UK tax year with account modal |
| `#balsheet/2024` | Balance Sheet through calendar year 2024 |
| `#trialbal` | Trial Balance |
| `#trialbal/Assets:Bank:HSBC` | Trial Balance with account modal |

## API Endpoints

All via `?api=` query params. Optional params: `year` (int), `taxyr=uk` (UK tax year mode).

| Endpoint | Params | Returns |
|----------|--------|---------|
| `events` | - | All ledger events (folder, date, title, links, tags, PDFs) |
| `entry` | `folder` | Raw beancount text for one event |
| `pnl` | `year`, `taxyr?` | Income/Expense rows grouped by account/currency |
| `balsheet` | `year`, `taxyr?` | Asset/Liability/Equity rows cumulative through year |
| `trialbal` | - | All accounts with non-zero balances |
| `account` | `name`, `year?`, `taxyr?` | Journal with running balance, tags, contra accounts |
| `fxrates` | - | Year-end FX rates from `prices.beancount` |
| `pdf` | `pdf` (path) | Serves PDF file from ledger directory |
