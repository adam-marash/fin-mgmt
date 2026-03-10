# Zilbershtein / Prodigy / Betegy Scratchpad

## Context

Tamar and Z co-invested in Betegy sp. z o.o. (Poland) via Prodigy Investments Ltd (Malta, C-87506).
Prodigy was later dissolved; shares distributed equally to Z and Tamar.
All cash came from Tamar. Half of each investment payment and related fees are loans to Z.

## Current ledger state (after 2026-03-10 corrections)

### Balances
| Account | Balance | Notes |
|---------|---------|-------|
| Assets:Business:Betegy | 2,659,877.58 PLN | Equity investment (shares + premium + loan assignments) |
| Assets:Loans:Betegy | 73,545.00 EUR | Tamar-only loan to Betegy (non-performing) |
| Expenses:Professional:Prodigy-MT | 44,464.56 EUR + 3,629.70 CHF | Half is Z's loan |
| Assets:Loans:Zilbershtein | -357,154.00 EUR | Z's repayments (no originating entry yet) |

### Tamar's outflows INTO Betegy (the investment)

| Date | Description | Amount | Currency | Source account |
|------|------------|--------|----------|---------------|
| 2018-07-31 | Share purchase from Yatsunyk (220 shares) | 299,367.82 | PLN | HSBC-GU PLN-Income |
| 2018-08-02 | Tranche 1 - nominal (par) | 8,917.75 | PLN | HSBC-GU PLN-Income |
| 2018-08-02 | Tranche 1 - share premium (agio) | 1,101,397.75 | PLN | HSBC-GU PLN-Income |
| 2018-09-21 | Tranche 2 - nominal (par) | 6,919.89 | PLN | HSBC-GU PLN-Income |
| 2018-09-21 | Tranche 2 - share premium (agio) | 849,689.89 | PLN | HSBC-GU PLN-Income |
| 2018-12-17 | Loan assignment - nominal | 3,267.24 | PLN | HSBC-GU PLN-Capital |
| 2018-12-17 | Loan assignment - share premium | 390,317.24 | PLN | HSBC-GU PLN-Capital |
| **Total PLN** | | **2,659,877.58** | **PLN** | |

### EUR loan to Betegy (Tamar only, Z NOT involved)
- 2019-10-30: 73,545.00 EUR - non-performing, relinquishment doc likely exists
- Now in `Assets:Loans:Betegy` (was in Assets:Suspense)
- Write off when relinquishment doc is found

### Prodigy professional fees (paid by Tamar via HSBC JE EUR)

| Date | Provider | Amount |
|------|----------|--------|
| 2018-06-26 | DWP Malta - setup consultancy | 3,126.62 EUR |
| 2018-07-06 | Derra Meyer - legal services | 14,788.90 EUR |
| 2018-07-17 | DWP Malta - company/newco setup | 5,633.99 EUR |
| 2018-08-01 | Derra Meyer - legal FV/180/07/2018 | 10,969.85 EUR |
| 2019-08-06 | DWP Malta - Inv 2019310 | 3,260.64 EUR |
| 2020-08-04 | DWP Malta - Inv 2020317 | 3,260.50 EUR |
| 2020-09-15 | Borg Galea Audit - 2018-2019 | 1,667.21 EUR |
| 2021-03-15 | Borg Galea Audit - RFP675 | 1,756.85 EUR |
| 2025-11-12 | Libertas Treuhand - accountancy | 3,629.70 CHF (paid 4,015.16 EUR) |
| **Total** | | **44,464.56 EUR + 3,629.70 CHF** |

### Z's repayments (received via HSBC JE EUR)

| Date | Description | Amount EUR |
|------|------------|-----------|
| 2020-11-24 | Loan repayment 1/5 | 49,154.00 |
| 2020-11-25 | Loan repayment 2/5 | 100,000.00 |
| 2020-11-26 | Loan repayment 3/5 | 8,000.00 |
| 2020-11-26 | Loan repayment 4/5 | 100,000.00 |
| 2020-11-30 | Loan repayment 5/5 | 100,000.00 |
| **Total received** | | **357,154.00** |

## Z loan calculation (2026-03-10)

Half of all Tamar's payments into Betegy/Prodigy and related fees = Z's loan.
EUR Betegy loan (73,545 EUR) is Tamar only, excluded.

### PLN investments converted to EUR (Frankfurter mid-rates)

| Date | Description | PLN | Rate | EUR |
|------|------------|-----|------|-----|
| 2018-07-31 | Share purchase (Yatsunyk) | 299,367.82 | 0.23381 | 69,995.19 |
| 2018-08-02 | Tranche 1 nominal | 8,917.75 | 0.23354 | 2,082.65 |
| 2018-08-02 | Tranche 1 share premium | 1,101,397.75 | 0.23354 | 257,220.43 |
| 2018-09-21 | Tranche 2 nominal | 6,919.89 | 0.23285 | 1,611.30 |
| 2018-09-21 | Tranche 2 share premium | 849,689.89 | 0.23285 | 197,850.29 |
| 2018-12-17 | Loan assignment nominal | 3,267.24 | 0.23345 | 762.74 |
| 2018-12-17 | Loan assignment premium | 390,317.24 | 0.23345 | 91,119.56 |
| **Total** | | **2,659,877.58** | | **620,642.16** |

### Z's debt summary

| Component | EUR |
|-----------|-----|
| Half of PLN investments | 310,321.08 |
| Half of EUR professional fees (44,464.56 / 2) | 22,232.28 |
| Half of CHF professional fees (~3,800 EUR / 2) | ~1,900.00 |
| **Z total debt (principal)** | **~334,453** |
| Z repaid | -357,154.00 |
| **Net** | **~-22,700 (Z overpaid)** |

### Interpretation

The ~22,700 EUR overpayment likely represents **interest** on the loan. This would make the accounting clean:
- Principal: ~334,453 EUR
- Interest: ~22,700 EUR
- Outstanding: zero

### Caveats
- PLN/EUR rates are Frankfurter mid-rates, not actual bank conversion rates
- CHF component approximated (~3,800 EUR for CHF 3,630)
- "Business start up" €8,000 included in repayments - confirm this is a repayment
- Actual bank spreads on PLN conversions could shift the total by several thousand EUR

## Ledger corrections made (2026-03-10)

1. Reclassified three "fees" entries as nominal/par value (paired with share premium/agio)
2. Reclassified EUR 73,545 Betegy payment: split into 73,500 loan + 45 wire fee
3. Narrations updated to reflect nominal/share premium (agio) structure
4. **Betegy loan written off** per Debt Release Agreement (signed 2024-08-31):
   - Accrued interest: 35,723.01 EUR (73,500 -> 109,223.01)
   - Full write-off to Expenses:Losses:Betegy
   - Assets:Loans:Betegy now **zero**
5. Source document filed: `ledger/2024/2026-03-10-betegy-debt-release-199c9fb6/`

## Still to do

- [ ] Book originating Z loan entry (~334K EUR, split into principal components)
- [ ] Split Z repayments into principal (~334K) and interest (~22K)
- [ ] Create `Income:Interest:Zilbershtein` or similar for the interest component
- [x] ~~Find Betegy loan relinquishment doc and write off Assets:Loans:Betegy~~ DONE
- [x] ~~Reclassify Assets:Suspense:Betegy (2.66M PLN) to proper investment asset account~~ DONE - now Assets:Business:Betegy
- [ ] Reclassify Expenses:Professional:Prodigy-MT - half is Z's loan, half is Tamar's cost
- [ ] Confirm "business start up" €8K is a loan repayment
- [ ] Confirm interest interpretation of ~€22,700 overpayment
- [ ] Determine if there was a stated interest rate or back into it from repayment

## Answers from Adam (2026-03-10)

1. **"Business start up" €8K** - Yes, it's loan repayment 3/5. Fixed in ledger.
2. **~€22,700 overpayment = interest** - Yes, Z paid interest on the loan.
3. **Loan agreement** - No formal loan agreement exists. Informal arrangement.
4. **Actual PLN/EUR rates** - N/A. The loan to Z is denominated in EUR (half of Tamar's EUR-equivalent outflows). The PLN->EUR conversion happened at the bank when Tamar funded the PLN payments; those actual bank rates are what matters for the EUR loan amount but we only have market mid-rates.
5. **Settlement on Prodigy dissolution** - No separate settlement. Betegy shares distributed 50/50 to Z and Tamar.

## Remaining questions

- What EUR amounts did the bank actually convert for the PLN payments? (Would give exact loan principal vs our mid-rate estimate of ~334K)
- Should we back into the interest rate from the repayment figures, or just book the ~22.7K as interest without a stated rate?
