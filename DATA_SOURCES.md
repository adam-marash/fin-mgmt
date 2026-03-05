# Data Sources Matrix

This document maps **expected** data types by source, then shows per-investment which sources apply.

## 1. Data Types by Source

### Banks (HSBC Guernsey, HSBC Jersey, Bank Leumi, Vontobel)

| Data Type | Description | Frequency | Who Produces |
|---|---|---|---|
| Transaction log | Every debit/credit on the account | Continuous; exported on demand | Bank |
| Periodic statement | Monthly or quarterly summary with opening/closing balance | Monthly or quarterly | Bank |
| Asset/position statement | Holdings snapshot - what securities/deposits are held, at what value | Quarterly or annual | Bank (for managed portfolios) |
| Tax certificate | Interest earned, withholding tax deducted | Annual | Bank |
| FX confirmation | Details of currency conversions | Per transaction | Bank |
| Fee schedule / charges | Custody fees, transfer fees, account maintenance | Periodic | Bank |

### Family Office - The Service

| Data Type | Description | Frequency | Who Produces |
|---|---|---|---|
| Master transaction register | All capital calls, distributions, fees across all investments | Continuous; exported on demand | FO back office |
| Distribution notice | "Investment X distributed Y on date Z" with breakdown (principal, income, tax) | Per event | FO (Sharon) |
| Capital call coordination | "Investment X is calling Y, please wire to account Z by date W" | Per event | FO (Eran / operations) |
| Quarterly portfolio summary | Aggregated view across all investments for the quarter | Quarterly | FO (Omer) |
| Per-investment quarterly report | Fund-specific quarterly report, usually forwarded from fund manager | Quarterly | FO forwarding fund manager's report |
| Invoice (management fee) | FO's own fee for managing the relationship | Monthly | FO via Invoice4U |
| KYC/onboarding package | Subscription docs, W-8BEN, FATCA, trustee appointment | Per investment (at entry) | FO |
| Tax certificate (Israeli) | Per-investment annual Israeli tax withholding certificate | Annual | FO (Lia) |
| Reconciliation / valuation | NAV or fair value of each investment position | Quarterly or annual | FO (not yet received) |

### Trustee - Tzur Management / Apex Israel

| Data Type | Description | Frequency | Who Produces |
|---|---|---|---|
| Capital account statement | Formal position: committed, called, distributed, NAV, unfunded | Quarterly or semi-annual | Tzur via Apex portal |
| Distribution confirmation | Formal notice that distribution was wired | Per event | Tzur (Vanessa) |
| Capital call confirmation | Formal notice that capital call was processed | Per event | Tzur |
| Audited fund financials | Fund-level audited statements (IFRS/GAAP) | Annual | Tzur / fund auditor |

### Fund Managers (direct)

| Data Type | Description | Frequency | Who Produces |
|---|---|---|---|
| Quarterly/periodic report | Fund performance, portfolio updates, market commentary | Quarterly | Fund manager |
| Distribution notice | Amount, type (income vs return of capital), payment date | Per event | Fund manager |
| Capital call notice | Amount due, due date, wire instructions | Per event | Fund manager |
| Capital account statement | Investor's position in the fund | Quarterly or annual | Fund manager or administrator |
| K-1 / 1042-S (US funds) | US partnership tax form for foreign investors | Annual | Fund manager / tax preparer |
| Audited financial statements | Fund-level annual financials | Annual | Fund auditor |
| Investor letter | General updates, strategy changes, market outlook | Ad hoc | Fund manager |

### Tax Advisors (Virtu Tax)

| Data Type | Description | Frequency | Who Produces |
|---|---|---|---|
| Tax return (Israeli) | Annual Israeli income tax filing | Annual | Virtu Tax |
| Tax return (US) | US non-resident filing if required | Annual | Virtu Tax / US preparer |
| Tax planning memo | Advisory on structure, withholding, treaty benefits | Ad hoc | Virtu Tax |
| Filing confirmation | IRS/ITA acceptance of e-filed return | Annual | eFile Services / ITA |


## 2. Per-Investment Expected Data Sources

Legend:
- **FO-Reg** = FO master transaction register
- **FO-Dist** = FO distribution notice (email)
- **FO-QR** = FO quarterly report (forwarded)
- **FO-CC** = FO capital call coordination
- **Bank** = Bank transaction showing the actual cash movement
- **Tzur-CS** = Tzur/Apex capital account statement
- **FM-QR** = Fund manager quarterly report (direct)
- **FM-Dist** = Fund manager distribution notice (direct)
- **FM-CC** = Fund manager capital call (direct)
- **K1/Tax** = K-1, 1042-S, or Israeli tax certificate
- **Sub** = Subscription/onboarding docs

### HSBC Guernsey-settled (USD)

| Investment | FO-Reg | FO-Dist | FO-QR | FO-CC | Bank | Tzur-CS | FM-QR | FM-Dist | FM-CC | K1/Tax | Sub |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Liquidity Capital II | Yes | Yes | Yes | | HSBC GU | Yes (8160) | | Yes | | Yes | Yes |
| Electra EMIF II | Yes | Yes | Yes | | HSBC GU | Yes (7470) | | Yes | | Yes (K-1) | Yes |
| Pollen Street III | Yes | | Yes | Yes | HSBC GU | Yes (10060/9890) | | | | | Yes |
| Viola Credit ALF III | Yes | Yes | Yes | Yes | HSBC GU | | Yes (Forte) | Yes | Yes | | Yes |
| Faro-Point FRG-X | Yes | Yes | Yes | | HSBC GU | | Yes | Yes | | Yes (1042-S) | Yes |
| Caliber | Yes | Yes | Yes | | HSBC GU | | | Yes | | | |
| ISF III | Yes | | Yes | Yes | HSBC GU | Yes (10220) | | | | | Yes |
| Impact RE FOF | Yes | | Yes | Yes | HSBC GU | | | | | | Yes |
| KDC/Stardom | Yes | | Yes | Yes | HSBC GU | Yes (10140) | Yes (Keshet) | | Yes | | Yes |
| Residence Vienna 1 | Yes | | | Yes | HSBC GU | | | | | | Yes |
| Coller Capital VIII | Yes | | | Yes | HSBC GU? | | | | | | |

### Bank Leumi ILS-settled

| Investment | FO-Reg | FO-Dist | FO-QR | FO-CC | Bank | Tzur-CS | FM-QR | FM-Dist | FM-CC | K1/Tax | Sub |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Atlanta/Boligo 1 | Yes | Yes | Yes | | Leumi ILS | | | Yes (Fortress) | | | Yes |
| Atlanta/Boligo 2 | Yes | Yes | Yes | | Leumi ILS | | | Yes (Fortress) | | | |
| Netz New Haven | Yes | Yes | Yes | | Leumi ILS | | | | | | |
| Reality Germany 1 | Yes | Yes | Yes | | Leumi ILS | | | | | | |
| Electra BTR 1 | Yes | Yes | Yes | | Leumi ILS | | | | | | |
| Pelham Park | Yes | Yes | Yes | | Leumi ILS | | | | | Yes (K-1) | Yes |
| Gelfand Maryland Gatewater | Yes | Yes | Yes | | Leumi ILS | | | Yes (Fortress) | | Yes (K-1) | |
| Beit Mars (Adam) | Yes | | | Yes | Leumi ILS | | | | | | Yes |
| Carmel Credit | Yes | Yes | | | Leumi ILS | | | | | | |

### Bank Leumi USD-settled

| Investment | FO-Reg | FO-Dist | FO-QR | FO-CC | Bank | Tzur-CS | FM-QR | FM-Dist | FM-CC | K1/Tax | Sub |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Impact Debt FOF | Yes | Yes | Yes | | Leumi USD | Yes (9510) | Yes | Yes | | | Yes |

### Settlement route unknown or N/A

| Investment | FO-Reg | FO-Dist | FO-QR | FO-CC | Bank | Tzur-CS | FM-QR | FM-Dist | FM-CC | K1/Tax | Sub |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Harel Hamagen USA | Yes | | Yes | Yes | ? | Yes (8740) | Yes (Harel) | | | | Yes |
| Hartford CT | Yes | Yes | Yes | | ? | | | | | Yes (K-1) | |
| Vienna Apartment Hotel | Yes | Yes | Yes | | ? | | | Yes (Fortress) | | | |
| Data Center LA | Yes | | Yes | | N/A | | | | | | (Completed) |
| Alt Group Octo | | | | Yes | ? | | | | | | Yes |
| Cyprus Properties | No FO | | | | ? | | | | | | (Outside FO) |

### Managed Portfolios

| Portfolio | FO-Reg | Bank Txns | Asset Statement | Manager Reports | K1/Tax |
|---|---|---|---|---|---|
| IBI (HSBC 639B267960G) | Deposits/withdrawals only | HSBC GU (Income + Capital accounts) | Yes (HSBC EOY) | Expected from IBI directly | |
| Yalin (HSBC 639B576257B) | Deposits/withdrawals only | HSBC GU (Income + Capital accounts) | Yes (HSBC EOY) | Expected from Yalin directly | |
| Vontobel (CHF) | Not in FO | Vontobel quarterly statement | Yes (quarterly) | Vontobel is both bank and manager | |


## 3. Key Observations

**Multiple overlapping sources for verification**: Most investments have 3-4 independent data points for each event. A distribution should appear as: (1) FO register entry, (2) FO email notice, (3) bank credit, and optionally (4) Tzur capital statement update and/or (5) fund manager notice. This redundancy is the basis for reconciliation.

**The Service is the hub**: Nearly everything flows through The Service. They receive from fund managers, repackage for the client, coordinate with Tzur, and track in their register. The register is the single most valuable data source but only covers amounts/dates, not detailed breakdowns.

**Trustee coverage is partial**: Tzur/Apex covers 8 of 28 investments (those structured as Israeli LPs or with Israeli trustees). Direct real estate deals and non-Israeli funds don't have Tzur statements.

**Fund manager direct communication varies widely**: Some (Faropoint, Viola, Liquidity, Harel) communicate directly with detailed English-language reports. Others (Boligo, Gatewater, Vienna, Netz) communicate only through Fortress or The Service in Hebrew. Some (Pelham, Reality Germany, Electra BTR) have minimal direct communication.

**Tax reporting is split**: US partnerships produce K-1s (via US tax preparers). Israeli-structured funds produce Israeli tax certificates (via The Service). Virtu Tax coordinates both jurisdictions.

**Managed portfolios have two layers**: IBI and Yalin are portfolio *managers*, but HSBC Guernsey is the *custodian* that issues both transaction-level reports and periodic performance reports. The FO only tracks bulk deposits/withdrawals. We need the HSBC custodian reports for full visibility.

**IBI is also a trustee/pipe**: Many bank transactions that appear to come from IBI are actually distributions from underlying investments that IBI serves as trustee for. A large assignment exercise is needed: match unidentified bank credits to known investments by date/amount proximity against FO distribution notices and fund manager communications.

**FO portal has position statements**: The Service has a portal where position data can be downloaded periodically. These are cost-basis views (invested minus returned) rather than NAV/fair-value - they've only ever adjusted valuation on one investment.

**Harel Hamagen reinvests**: No cash distributions expected in bank accounts - returns are reinvested within the fund. Periodic performance reports from Harel exist and should be locatable.
