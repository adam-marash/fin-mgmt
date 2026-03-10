# Task: Retrieve Investment Commitment Documents

## Goal

For each investment listed below, find ONE authoritative document that confirms the total commitment amount. This is typically an investment agreement, subscription document, commitment letter, or side letter.

## Instructions

1. Search Dropbox (or wherever Adam directs you) for documents related to each investment
2. For each investment, retrieve the SINGLE most authoritative document - prefer:
   - Signed subscription agreement or investment agreement (best)
   - Commitment letter or side letter with amount
   - Capital account statement showing total commitment
   - Any document from the fund/trustee stating the committed amount
3. Save each document to `inbox/commitments/` with naming: `<investment-name>-commitment.<ext>`
   - e.g., `inbox/commitments/electra-mif-ii-commitment.pdf`
4. Create a manifest file `inbox/commitments/MANIFEST.md` with a table:

```
| Investment | File | Commitment Amount | Currency | Date Signed | Notes |
```

5. Do NOT book any ledger transactions - just retrieve and catalog the documents

## Investments to find (26 total)

### USD investments
- **Electra-MIF-II** (Electra USA 2 / EMIF II Feeder I LP) - FO says $604,000
- **Liquidity-Capital** (Liquidity Capital II, L.P) - FO says $400,000
- **Boligo-1** (senior living Atlanta) - FO says $500,000
- **Boligo-2** (senior living Atlanta 2) - FO says $300,000
- **Pelham-Park** (multi-family Philadelphia, Gelfand) - FO says $500,000
- **Netz** (multi-family/single-family New Haven) - FO says $500,000
- **Hartford-CT** (urban renewal, Connecticut) - FO says $475,000 but bank shows $600K paid
- **Coller-Capital** (Coller Capital VIII / CIP VIII) - FO says $1,000,000
- **Gatewater** (Gatewater Landing, Maryland, Gelfand) - FO says $500,000
- **Data-Center-LA** (data center, Los Angeles) - FO says $600,000
- **Impact-Debt** (Impact debt fund of funds) - FO says $1,000,000
- **FRG-X** (Faro-Point FRG-X) - FO says $591,495
- **KDC-Stardom** (KDC Media Fund / Stardom Ventures) - FO says $203,873
- **Pollen-Street** (Pollen Street Credit Fund III-USD / Phoenix Credit Strategies) - FO says $540,437
- **Electra-BTR** (Electra BTR 1) - FO says $500,000
- **Caliber** (Caliber) - FO says $500,000
- **Viola-Credit** (Viola Credit ALF III) - FO says $270,135
- **Harel-Hamagen** (Harel Finance Alternative Hamagen LP) - FO says $800,000
- **ISF-III** (ISF III) - FO says $137,570
- **Alt-Group-Octo** (Alt Group Octo Opportunities Fund) - FO says $1

### EUR investments
- **Reality-Germany** (Reality Germany supermarket portfolio) - FO says EUR 625,000
- **Vienna-Apartment** (Serviced Apartments Vienna) - FO says EUR 400,000
- **Vienna-Residence** (Residence Vienna 1) - FO says EUR 510,000
- **Impact-RE-FOF** (Impact Real Estate FOF) - FO says EUR 100,001

### ILS investments
- **Carmel-Credit** (Carmel Credit / A.B.G Planning) - FO says ILS 2,000,000
- **Beit-Mars** (Beit Mars, Adam's investment) - FO says ILS 1,881,200

## What to look for

The key data point from each document is the **total commitment amount** - the maximum the fund can call. This may differ from what FO recorded as deposits. Known discrepancies:
- Hartford-CT: bank paid $600K but FO only shows $475K commitment
- KDC-Stardom: 11 tranches suggest an open-ended commitment
- ISF-III: 5 tranches over 3 years, commitment likely larger than $137K funded

## Context

These documents will be used to replace provisional commitment entries in the beancount ledger. The current entries use FO deposit totals as commitment amounts (tagged #fo-sourced #provisional). Primary source documents will provide the actual contractual commitment amounts.

Family office: The Service (theservice.co.il)
Trustee: Tzur Capital Management
Investor name: Tamar Marash
