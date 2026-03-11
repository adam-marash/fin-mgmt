# HSBC JE <-> UK Suspense Reconciliation Scratchpad

## Setup

- **UK side (9564)**: 45 entries in `Assets:Suspense:JE-UK` totalling -£1,126,277.09 (credits to 9564)
- **JE side**: 15 GBP entries currently in `Expenses:Personal:Living` totalling £323,548.44 (debits from JE)
- Action: reclassify JE-side from `Expenses:Personal:Living` to `Assets:Suspense:JE-UK`

## Match Results (±5 days, exact amount)

| JE date | Amount | UK date | UK Amount | Days | Status |
|---------|-------:|---------|----------:|-----:|--------|
| 2019-12-11 | 25,000 | - | - | - | NO MATCH |
| 2020-09-11 | 25,000 | - | - | - | NO MATCH |
| 2022-06-13 | 3,548.44 | 2022-06-13 | 3,548.44 | 0 | MATCH |
| 2024-02-05 | 15,000 | 2024-02-05 | 15,000 | 0 | MATCH |
| 2024-05-08 | 25,000 | 2024-05-08 | 25,000 | 0 | MATCH |
| 2024-08-19 | 25,000 | 2024-08-17 | 25,000 | 2 | MATCH |
| 2024-11-25 | 25,000 | 2024-11-23 | 25,000 | 2 | MATCH |
| 2025-02-24 | 25,000 | 2025-02-24 | 25,000 | 0 | MATCH |
| 2025-05-12 | 25,000 | 2025-05-12 | 25,000 | 0 | MATCH |
| 2025-07-29 | 25,000 | - | - | - | NO MATCH (UK has 07-16, 13 days) |
| 2025-09-23 | 25,000 | 2025-09-23 | 25,000 | 0 | MATCH |
| 2025-09-25 | 15,000 | - | - | - | NO MATCH |
| 2025-11-10 | 25,000 | 2025-11-09 | 25,000 | 1 | MATCH |
| 2025-12-01 | 15,000 | - | - | - | NO MATCH |
| 2026-01-06 | 25,000 | - | - | - | NO MATCH |

**9/15 matched, 6 unmatched JE entries, 36 unmatched UK entries**

## Unmatched JE entries (6, £155K)

1. **2019-12-11** £25K - may go to joint account not 9564, or UK date differs > 5 days
2. **2020-09-11** £25K - same
3. **2025-07-29** £25K - UK has £25K on 07-16 (13 day gap, likely same transfer)
4. **2025-09-25** £15K - may arrive after 9564 statement cutoff, or different account
5. **2025-12-01** £15K - same
6. **2026-01-06** £25K - after Mar 3 2026 cutoff? No, should be in 2026 statements

## Unmatched UK entries (36, £933K)

These are JE->UK transfers received in 9564 where the JE outbound hasn't been imported yet.
Mostly 2018-2023 (before JE import coverage started). Will resolve when full JE history is imported.

## Next Steps

1. Reclassify the 15 JE entries from `Expenses:Personal:Living` to `Assets:Suspense:JE-UK`
2. Investigate the 6 unmatched JE entries (especially 2019/2020 - different destination?)
3. Import remaining JE history to resolve the 36 unmatched UK entries
4. After full JE import, sweep matched suspense pairs to zero
