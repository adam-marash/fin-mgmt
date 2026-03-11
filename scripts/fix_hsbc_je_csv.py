#!/usr/bin/env python3
"""Fix HSBC JE CSV by replacing problem transactions with manually verified data."""

import csv
from dataclasses import fields
from pathlib import Path

from parse_hsbc_je_stmts import Transaction

CSV_PATH = Path("data/hsbc-je-statements.csv")

# Each correction is: (account_number, statement_date) -> list of corrected transaction dicts.
# These replace ALL rows for that (account, statement_date) combo.

CORRECTIONS = {
    # =========================================================================
    # 2017-12-22 statement (folder1/2017_12_22.pdf)
    # =========================================================================
    ("023-085996-076", "2017-12-22"): [
        # EUR - opening 31,200.64
        {"date": "2017-12-01", "description": "CREDIT INTEREST", "reference": "ZDD4-10399",
         "deposit": "0.26", "withdrawal": "", "balance": "31200.90"},
        {"date": "2017-12-05", "description": "GBS05127HEV1LVSW OCMT/EUR4000.00 INTERFAX COMMUNICATIONS LIMITED DIRECTORS FEES",
         "reference": "YIR0-33828", "deposit": "4000.00", "withdrawal": "", "balance": "35200.90"},
        {"date": "2017-12-20", "description": "GBS20127APWEMTC0 OCMT/EUR3975.00 INTERFAX COMMUNICATIONS LIMITED DIRECTORS FEES 2017",
         "reference": "YIR0-42653", "deposit": "3975.00", "withdrawal": "", "balance": "39175.90"},
    ],
    ("023-085996-077", "2017-12-22"): [
        # USD - opening 951,132.56
        {"date": "2017-11-29", "description": "333439201 INTERFAX COMMUNICATIONS LIMITED LOAN REPAYMENT",
         "reference": "YIR0-29904", "deposit": "99006.61", "withdrawal": "", "balance": "1050139.17"},
        {"date": "2017-12-01", "description": "CREDIT INTEREST",
         "reference": "ZDD4-10399", "deposit": "359.15", "withdrawal": "", "balance": "1050498.32"},
        {"date": "2017-12-07", "description": "TT MIMM24056RNY Jessica Waters Loan to beneficiary",
         "reference": "YIB2-00078", "deposit": "", "withdrawal": "23538.48", "balance": "1026959.84"},
        {"date": "2017-12-21", "description": "TT MIMM31804RNY Return wrong xfer Interfax Communications Limited",
         "reference": "YIB2-00021", "deposit": "", "withdrawal": "99006.61", "balance": "927953.23"},
    ],
    ("023-085996-361", "2017-12-22"): [
        # JPY - opening 132,345,969
        {"date": "2017-12-14", "description": "TO 023-085996-690 HIB-624188560XGXYVDVJIL",
         "reference": "EB30-00462", "deposit": "", "withdrawal": "17979074", "balance": "114366895"},
        {"date": "2017-12-19", "description": "TT MIMM29843MDL King Alfred School School/College tuition fees",
         "reference": "YIB2-00134", "deposit": "", "withdrawal": "991242", "balance": "113375653"},
    ],
    ("406162-21316966", "2017-12-22"): [
        # GBP - opening 14,433.80
        {"date": "2017-12-13", "description": "FROM 023-085996-360 HIB-624188560XGX60TMZID",
         "reference": "EB19-02860", "deposit": "481644.43", "withdrawal": "", "balance": "496078.23"},
        {"date": "2017-12-14", "description": "FROM 023-085996-363 HIB-489225887XGXYBCV9KP",
         "reference": "EB02-01063", "deposit": "140423.05", "withdrawal": "", "balance": "636501.28"},
        {"date": "2017-12-14", "description": "FROM 023-085996-360 HIB-71383645XGXYLEAYVZ",
         "reference": "EB29-04987", "deposit": "45789.56", "withdrawal": "", "balance": "682290.84"},
        {"date": "2017-12-14", "description": "FROM 023-085996-361 HIB-624188560XGXYVDVJIL TT TO JUDGE SYKES FRIXOU CLIENT ACCOUNT",
         "reference": "EB30-00462", "deposit": "117709.00", "withdrawal": "", "balance": "799999.84"},
        {"date": "2017-12-14", "description": "TT MIGA41381MDL TT TO JUDGE SYKES FRIXOU CLIENT ACCOUNT CHARGE WAIVED",
         "reference": "ZAFO-10429", "deposit": "", "withdrawal": "770408.40", "balance": "29591.44"},
    ],

    # =========================================================================
    # 2014-09-08 statement (folder1/2014_09_08.pdf)
    # =========================================================================
    ("406162-21316966", "2014-09-08"): [
        # GBP - opening 141,562.07
        {"date": "2014-08-12", "description": "TRF FROM CHF SAVINGS A/C",
         "reference": "H1E2-00053", "deposit": "639370.50", "withdrawal": "", "balance": "780932.57"},
        {"date": "2014-08-12", "description": "TRF FROM AUD SAVINGS A/C TT MIMF91340MDL TRANSFER TO JUDGE SYKES FRIXOU",
         "reference": "H1E2-00056", "deposit": "148441.37", "withdrawal": "", "balance": "929373.94"},
        {"date": "2014-08-12", "description": "TT MIMF91340MDL TRANSFER TO JUDGE SYKES FRIXOU CHARGE WAIVED",
         "reference": "ZAFO-10077", "deposit": "", "withdrawal": "822498.00", "balance": "106875.94"},
    ],

    # =========================================================================
    # 2014-12-08 statement (folder1/2014_12_08.pdf)
    # =========================================================================
    ("023-085996-540", "2014-12-08"): [
        # EUR - opening 1,315,809.09
        {"date": "2014-11-26", "description": "GBH261140OU7G3GG OCMT/GBP12574.00 INTERFAX INC /RFB/LOAN CHARGE WAIVED",
         "reference": "YIR0-05824", "deposit": "15473.79", "withdrawal": "", "balance": "1331282.88"},
        {"date": "2014-11-26", "description": "GBH26114H8U7G3CW INTERFAX INC",
         "reference": "H310-00003", "deposit": "103002.93", "withdrawal": "", "balance": "1434285.81"},
    ],

    # =========================================================================
    # 2016-06-08 statement (folder1/2016_06_08.pdf)
    # =========================================================================
    ("023-085996-705", "2016-06-08"): [
        # USD - opening 1,430,602.25
        {"date": "2016-05-16", "description": "GBS16056ARCKWJNK INTERFAX COMMUNICATIONS LTD /RFB/LOAN",
         "reference": "YIR0-78319", "deposit": "188389.00", "withdrawal": "", "balance": "1618991.25"},
        {"date": "2016-05-25", "description": "TT MIMJ29949MDL Tamar Marash Personal/Living Expenses CHARGE WAIVED",
         "reference": "TXO2-00205", "deposit": "", "withdrawal": "22445.18", "balance": "1596546.07"},
        {"date": "2016-06-06", "description": "TT MIMJ36735RNY Internet Devels Ukraine FOP Levandovskij Viktor ADAM MARASH INV0924",
         "reference": "TXO3-00928", "deposit": "", "withdrawal": "2044.46", "balance": "1594501.61"},
    ],

    # =========================================================================
    # 2017-01-06 statement (folder1/2017_01_08.pdf)
    # =========================================================================
    ("023-085996-361", "2017-01-06"): [
        # JPY - opening 136,928,959
        {"date": "2016-12-22", "description": "TT MIMK37315MDL King Alfred School School/College Tuition Fees",
         "reference": "TXO3-00450", "deposit": "", "withdrawal": "951778", "balance": "135977181"},
        {"date": "2017-01-11", "description": "TT MIMK45707MDL King Alfred School (FORWARD DATED)",
         "reference": "TXO3-00351", "deposit": "", "withdrawal": "730832", "balance": "135246349"},
    ],
    ("023-085996-540", "2017-01-06"): [
        # EUR - opening 1,418,190.96 (but actually 1,401,035.97 continued from prev page)
        # Page 4: opening 1,401,035.97
        # Oth Bk Chg EUR 12.17, REF TXO3-00251, wth 8,577.42, bal 1,392,458.55
        # Page 5: opening 1,401,035.97
        # 22Dec2016: Loan to Stav 4/4, REF TXO2-00283, wth 8,577.22, bal 1,383,881.33
        # 03Jan2017: STAV ZILBERSHTEIN LOAN REPAYMENT, REF YIR0-28231, dep 861.68, bal 1,384,743.01
        {"date": "2016-12-20", "description": "TT MIMK36450MDL Loan to Stav 1/4 Stav Zilbershtein",
         "reference": "TXO2-00907", "deposit": "", "withdrawal": "8577.42", "balance": "1409613.54"},
        {"date": "2016-12-20", "description": "TT MIMK37019MDL Loan to Stav 2/4 Stav Zilbershtein",
         "reference": "TXO2-00206", "deposit": "", "withdrawal": "8577.57", "balance": "1401035.97"},
        {"date": "2016-12-21", "description": "TT MIMK37879MDL Stav Zilbershtein Loan to Stav 3/4",
         "reference": "TXO3-00251", "deposit": "", "withdrawal": "8577.42", "balance": "1392458.55"},
        {"date": "2016-12-22", "description": "TT MIMK38479MDL Stav Zilbershtein Loan to Stav 4/4",
         "reference": "TXO2-00283", "deposit": "", "withdrawal": "8577.22", "balance": "1383881.33"},
        {"date": "2017-01-03", "description": "GBC03017HC0BP8ZK OCMT/EUR900.00 STAV ZILBERSHTEIN LOAN REPAYMENT",
         "reference": "YIR0-28231", "deposit": "861.68", "withdrawal": "", "balance": "1384743.01"},
    ],

    # =========================================================================
    # 2018-01-24 statement (folder1/2018_01_24.pdf)
    # =========================================================================
    ("023-085996-076", "2018-01-24"): [
        # EUR - opening 39,175.90
        {"date": "2018-01-02", "description": "GBE02018AQ08NRWG OCMT/EUR1500.00 RIZONTEC LTD DIVIDEND ADVANCE CHARGE WAIVED",
         "reference": "YIR0-50455", "deposit": "1500.00", "withdrawal": "", "balance": "40675.90"},
        {"date": "2018-01-02", "description": "CREDIT INTEREST",
         "reference": "ZDD4-10529", "deposit": "0.32", "withdrawal": "", "balance": "40676.22"},
        {"date": "2018-01-11", "description": "GBE110180P11H83K OCMT/EUR3355.00 RIZONTEC LTD ADAM 2017 SALARY NET OF TAMMY DWT",
         "reference": "YIR0-55084", "deposit": "3355.00", "withdrawal": "", "balance": "44031.22"},
    ],
    ("023-085996-077", "2018-01-24"): [
        # USD - opening 927,953.23
        {"date": "2017-12-27", "description": "TT MIMM33943RNY To self 2016 tax (VALUE DATE 22Dec2017)",
         "reference": "YIB2-00010", "deposit": "", "withdrawal": "120040.16", "balance": "807913.07"},
        {"date": "2018-01-02", "description": "CREDIT INTEREST",
         "reference": "ZDD4-10529", "deposit": "125.83", "withdrawal": "", "balance": "808038.90"},
        {"date": "2018-01-16", "description": "TT MIMM44177RNY TT TO HSBC PRIVATE BANK TAMAR MARASH POP INVESTMENT CHARGE WAIVED",
         "reference": "ZAFO-10148", "deposit": "", "withdrawal": "800000.00", "balance": "8038.90"},
    ],
    ("023-085996-540", "2018-01-24"): [
        # EUR - opening 1,306,939.91
        {"date": "2018-01-10", "description": "TT MIMM41949MDL Tamar Marash Personal/Living expenses CHARGE WAIVED",
         "reference": "TXO2-00247", "deposit": "", "withdrawal": "28739.79", "balance": "1278200.12"},
        {"date": "2018-01-16", "description": "TT MIMM44762MDL Tamar Marash Household repairs/Building cost CHARGE WAIVED",
         "reference": "TXO3-00909", "deposit": "", "withdrawal": "91099.16", "balance": "1187100.96"},
        {"date": "2018-01-16", "description": "TT MII074651MDL TT TO HSBC PRIVATE BANK GUERNSEY TAMAR MARASH PURPOSE OF TRANSFER INVESTMENT CHARGE WAIVED",
         "reference": "ZAFO-10387", "deposit": "", "withdrawal": "800000.00", "balance": "387100.96"},
    ],

    # =========================================================================
    # 2018-02-23 statement (folder1/2018_02_23.pdf)
    # =========================================================================
    ("023-085996-077", "2018-02-23"): [
        # USD - opening 8,038.90
        {"date": "2018-02-01", "description": "CREDIT INTEREST",
         "reference": "ZDD4-60830", "deposit": "47.67", "withdrawal": "", "balance": "8086.57"},
    ],
    ("023-085996-705", "2018-02-23"): [
        # USD - opening 493,942.94
        {"date": "2018-02-05", "description": "036425483 INTERFAX COMMUNICATIONS LIMITED IFIE LOAN REPAYMENT",
         "reference": "YIR0-70552", "deposit": "104629.64", "withdrawal": "", "balance": "598572.58"},
        {"date": "2018-02-05", "description": "036425490 INTERFAX COMMUNICATIONS LIMITED IFIE LOAN REPAYMENT",
         "reference": "YIR0-70554", "deposit": "103805.93", "withdrawal": "", "balance": "702378.51"},
        {"date": "2018-02-23", "description": "054304637 INTERFAX COMMUNICATIONS LIMITED INTERFAX COMMUNICATIONS",
         "reference": "YIR0-80669", "deposit": "102371.50", "withdrawal": "", "balance": "804750.01"},
    ],

    # =========================================================================
    # 2018-03-23 statement (folder4/2018-03-23_Statement.pdf)
    # =========================================================================
    ("023-085996-076", "2018-03-23"): [
        # EUR - opening 44,931.58
        {"date": "2018-03-01", "description": "CREDIT INTEREST",
         "reference": "ZDD4-10825", "deposit": "0.35", "withdrawal": "", "balance": "44931.93"},
        {"date": "2018-03-06", "description": "GBE060380R5ZHIO0 OCMT/EUR900.00 RIZONTEC LTD ADV LOAN ON DIVI CHARGE WAIVED",
         "reference": "YIR0-87981", "deposit": "900.00", "withdrawal": "", "balance": "45831.93"},
    ],

    # =========================================================================
    # 2018-04-24 statement (folder1/2018_04_24.pdf)
    # =========================================================================
    ("406162-21316966", "2018-04-24"): [
        # GBP - opening 22,576.44
        {"date": "2018-04-16", "description": "TT MIMM95150MDL T Marash Mortgage payment CHARGE WAIVED",
         "reference": "TXO1-00614", "deposit": "", "withdrawal": "18600.00", "balance": "3976.44"},
    ],
    ("023-085996-361", "2018-04-24"): [
        # JPY - opening 113,375,653
        {"date": "2018-04-17", "description": "TT MIMM95163MDL MARAOR King Alfred School School/College tuition fees",
         "reference": "YIB2-00048", "deposit": "", "withdrawal": "1124369", "balance": "112251284"},
        {"date": "2018-04-17", "description": "TT MIMM95128MDL Tamar Marash Personal/Living expenses CHARGE WAIVED",
         "reference": "TXO2-00421", "deposit": "", "withdrawal": "1566171", "balance": "110685113"},
    ],
    ("023-085996-705", "2018-04-24"): [
        # USD - opening 907,452.71
        {"date": "2018-03-29", "description": "2018032917650533 MICHAEL LAVELLE TA LAVELLE PROJECT BAIN LAVELLELAVELLE SOLICITORS CHARGE WAIVED",
         "reference": "YIR0-02668", "deposit": "3119433.00", "withdrawal": "", "balance": "4026885.71"},
        {"date": "2018-04-17", "description": "(VALUE DATE 16Apr2018) DEPOSIT PLACEMENT 023-085996-200",
         "reference": "H0Y8-00030", "deposit": "", "withdrawal": "4000000.00", "balance": "26885.71"},
    ],

    # =========================================================================
    # 2018-12-24 statement (no PDF found - computed from next statement opening)
    # =========================================================================
    ("023-085996-361", "2018-12-24"): [
        # JPY - opening 91,792,739 (from prev statement 2018-11-23 closing)
        # Closing must be 79,733,160 (2019-05-16 opening per PDF)
        {"date": "2018-12-11", "description": "TT MIMO26747MDL To self Tamar Marash",
         "reference": "A383LG9R", "deposit": "", "withdrawal": "3650221", "balance": "88142518"},
        {"date": "2018-12-11", "description": "TT MIMO26748RNY FOP Levandovskij Viktor INVS-002148",
         "reference": "A383LG96", "deposit": "", "withdrawal": "199144", "balance": "87943374"},
        {"date": "2018-12-18", "description": "TT MIMO30034MDL To self Tamar Marash From self",
         "reference": "A38PLBJL", "deposit": "", "withdrawal": "8210214", "balance": "79733160"},
    ],

    # =========================================================================
    # 2018-07-24 statement (folder1/2018_07_24.pdf)
    # =========================================================================
    ("023-085996-076", "2018-07-24"): [
        # EUR - opening 49,433.13
        {"date": "2018-06-26", "description": "TT MIMN33914MDL Malta setup cons DWP Malta",
         "reference": "TXO2-00199", "deposit": "", "withdrawal": "3126.62", "balance": "46306.51"},
        {"date": "2018-07-06", "description": "TT MIMN40255MDL dmp Derra Meyer",
         "reference": "TXO2-00199", "deposit": "", "withdrawal": "14788.90", "balance": "31517.61"},
        {"date": "2018-07-17", "description": "TT MIMN45199RNY Promisepay Inc Purchase of website",
         "reference": "TXO3-00111", "deposit": "", "withdrawal": "17883.41", "balance": "13634.20"},
        {"date": "2018-07-17", "description": "TT MIMN45307MDL Company setup DWP Malta LTD",
         "reference": "TXO3-00176", "deposit": "", "withdrawal": "5633.99", "balance": "8000.21"},
    ],

    # =========================================================================
    # 2019-08-16 statement (folder2/2019_08_16.pdf)
    # =========================================================================
    ("023-085996-361", "2019-08-16"): [
        # JPY - opening 70,054,936
        {"date": "2019-07-22", "description": "TT MIMP52480MDL Mia KAS deposit King Alfred School",
         "reference": "YIB2-00048", "deposit": "", "withdrawal": "921970", "balance": "69132966"},
        {"date": "2019-08-07", "description": "TT MIMP63188MDL XC60 1/2 Regent Automotive Ltd",
         "reference": "TXO2-00199", "deposit": "", "withdrawal": "2479818", "balance": "66653148"},
        {"date": "2019-08-08", "description": "TT MIMP63934MDL XC60 2/2 Regent Automotive Ltd",
         "reference": "TXO2-00199", "deposit": "", "withdrawal": "2467354", "balance": "64185794"},
    ],

    # =========================================================================
    # 2020-08-14 statement (folder2/2020_08_14.pdf)
    # =========================================================================
    ("023-085996-076", "2020-08-14"): [
        # EUR - opening 43,091.21
        {"date": "2020-08-03", "description": "CREDIT INTEREST",
         "reference": "", "deposit": "0.40", "withdrawal": "", "balance": "43091.61"},
        {"date": "2020-08-04", "description": "TT MIMR70456MDL DWP Malta Inv 2020317",
         "reference": "TXO1-00336", "deposit": "", "withdrawal": "3260.50", "balance": "39831.11"},
    ],

    # =========================================================================
    # 2020-12-16 statement (folder1/2020_12_16.pdf)
    # =========================================================================
    ("023-085996-076", "2020-12-16"): [
        # EUR - opening 38,164.24
        {"date": "2020-11-24", "description": "FROM ZILBERSHTEIN S loan repayment",
         "reference": "EB23-02430", "deposit": "49154.00", "withdrawal": "", "balance": "87318.24"},
        {"date": "2020-11-25", "description": "FROM ZILBERSHTEIN S loan repay 2/4",
         "reference": "EB01-03045", "deposit": "100000.00", "withdrawal": "", "balance": "187318.24"},
        {"date": "2020-11-25", "description": "BUSINESS START UP CHARGE WAIVED",
         "reference": "YIR0-91147", "deposit": "8000.00", "withdrawal": "", "balance": "195318.24"},
        {"date": "2020-11-26", "description": "FROM ZILBERSHTEIN S loan repay 3 of 4",
         "reference": "EB28-02922", "deposit": "100000.00", "withdrawal": "", "balance": "295318.24"},
        {"date": "2020-11-30", "description": "FROM ZILBERSHTEIN S loan repay 4 of 4",
         "reference": "EB21-05064", "deposit": "100000.00", "withdrawal": "", "balance": "395318.24"},
        {"date": "2020-12-01", "description": "CREDIT INTEREST",
         "reference": "ZDD4-00006", "deposit": "0.75", "withdrawal": "", "balance": "395318.99"},
    ],

    # =========================================================================
    # 2021-03-16 statement (folder1/2021_03_16.pdf)
    # =========================================================================
    ("023-085996-076", "2021-03-16"): [
        # EUR - opening 399,645.79
        {"date": "2021-03-01", "description": "CREDIT INTEREST",
         "reference": "ZDD4-00006", "deposit": "3.10", "withdrawal": "", "balance": "399648.89"},
        {"date": "2021-03-15", "description": "TT MIMT06855MDL Borg Galea Audit Ltd Bills",
         "reference": "TXO2-00790", "deposit": "", "withdrawal": "1756.85", "balance": "397892.04"},
    ],

    # =========================================================================
    # 2022-12-07 statement (folder1/2022-12-07_Statement.pdf)
    # =========================================================================
    ("023-085996-076", "2022-12-07"): [
        # EUR - opening 399,380.81
        {"date": "2022-11-16", "description": "TT MIMW92081MDL Marash Queens Paradise M Timotheu and Co LLC Purchase of property",
         "reference": "TXO1-00373", "deposit": "", "withdrawal": "5016.03", "balance": "394364.78"},
        {"date": "2022-11-28", "description": "IE22112537994419 RIZONTEC LTD CHARGE WAIVED",
         "reference": "YIR0-43185", "deposit": "18935.00", "withdrawal": "", "balance": "413299.78"},
        {"date": "2022-11-29", "description": "IE22112538002509 RIZONTEC LTD CHARGE WAIVED",
         "reference": "YIR0-44538", "deposit": "24962.88", "withdrawal": "", "balance": "438262.66"},
    ],

    # =========================================================================
    # 2023-02-07 statement (folder1/2023-02-07_Statement.pdf)
    # =========================================================================
    ("023-085996-361", "2023-02-07"): [
        # JPY - opening 5,732,686
        {"date": "2023-01-25", "description": "TT MIMX28037MDL Tamar Marash Personal/Living expenses",
         "reference": "TXO2-00193", "deposit": "", "withdrawal": "2458210", "balance": "3274476"},
        {"date": "2023-02-08", "description": "TT MIMX35867MDL Tamar Marash Personal/Living expenses (FORWARD DATED)",
         "reference": "TXO2-00597", "deposit": "", "withdrawal": "3274476", "balance": "0"},
    ],

    # =========================================================================
    # 2024-09-06 statement (folder1/2024-09-06_Statement.pdf)
    # =========================================================================
    ("023-085996-705", "2024-09-06"): [
        # USD - opening 260,700.61
        {"date": "2024-08-14", "description": "GLOBAL MONEY TRANSFERS Le Cordon Bleu Ltd Marash 2001599",
         "reference": "EB09-06006", "deposit": "", "withdrawal": "5898.39", "balance": "254802.22"},
        {"date": "2024-08-19", "description": "GLOBAL MONEY TRANSFERS Marash Mrs Tamar Bat She GBP 25,000.00",
         "reference": "EB11-04026", "deposit": "", "withdrawal": "33035.49", "balance": "221766.73"},
    ],

    # =========================================================================
    # 2024-12-06 statement (folder1/2024-12-06_Statement.pdf)
    # =========================================================================
    ("023-085996-705", "2024-12-06"): [
        # USD - opening 228,302.79
        {"date": "2024-11-25", "description": "GLOBAL MONEY TRANSFERS Marash Mrs Tamar Bat She GBP 25,000.00",
         "reference": "EB21-02655", "deposit": "", "withdrawal": "31989.07", "balance": "196313.72"},
        {"date": "2024-12-02", "description": "GLOBAL MONEY TRANSFERS Le Cordon Bleu Ltd GBP 3,290.00",
         "reference": "EB16-07409", "deposit": "", "withdrawal": "4296.35", "balance": "192017.37"},
    ],

    # =========================================================================
    # 2025-08-07 statement (folder1/2025-08-07_Statement.pdf)
    # =========================================================================
    ("023-085996-705", "2025-08-07"): [
        # USD - opening 113,806.04
        {"date": "2025-07-16", "description": "CIP VIII PRIVATE EQUITY BACKED NOTE",
         "reference": "YIR0-29394", "deposit": "15083.05", "withdrawal": "", "balance": "128889.09"},
        {"date": "2025-07-16", "description": "GLOBAL MONEY TRANSFERS Marash Mrs Tamar Bat She GBP 25,000.00",
         "reference": "EB19-06866", "deposit": "", "withdrawal": "34089.20", "balance": "94799.89"},
        {"date": "2025-07-29", "description": "GLOBAL MONEY TRANSFERS Rennie Partners Client A GBP 8,730.90",
         "reference": "EB26-02215", "deposit": "", "withdrawal": "11860.51", "balance": "82939.38"},
    ],

    # =========================================================================
    # 2025-10-07 statement (folder1/2025-10-07_Statement.pdf)
    # =========================================================================
    ("023-085996-705", "2025-10-07"): [
        # USD - opening 82,939.38
        {"date": "2025-09-23", "description": "GLOBAL MONEY TRANSFERS Marash Mrs Tamar Bat She GBP 25,000.00",
         "reference": "EB21-03284", "deposit": "", "withdrawal": "34383.26", "balance": "48556.12"},
        {"date": "2025-09-25", "description": "GLOBAL MONEY TRANSFERS Tamar Marash GBP 15,000.00",
         "reference": "EB27-05033", "deposit": "", "withdrawal": "20557.49", "balance": "27998.63"},
        {"date": "2025-09-30", "description": "273427569 1/TAMAR B. MARASH SELF",
         "reference": "YIR0-51019", "deposit": "169955.00", "withdrawal": "", "balance": "197953.63"},
    ],

    # =========================================================================
    # 2025-12-05 statement (folder1/2025-12-05_Statement.pdf)
    # =========================================================================
    ("023-085996-705", "2025-12-05"): [
        # USD - opening 191,223.49
        {"date": "2025-11-10", "description": "GLOBAL MONEY TRANSFERS Marash Mrs Tamar Bat She GBP 25,000.00",
         "reference": "EB50-01488", "deposit": "", "withdrawal": "33647.38", "balance": "157576.11"},
        {"date": "2025-12-01", "description": "GLOBAL MONEY TRANSFERS Tamar Marash GBP 15,000.00",
         "reference": "EB09-01829", "deposit": "", "withdrawal": "20342.98", "balance": "137233.13"},
    ],

    # =========================================================================
    # 2017-06-08 statement (no PDF found - computed from surrounding data)
    # =========================================================================
    ("023-085996-540", "2017-06-08"): [
        # EUR - opening 1,398,068.79
        {"date": "2017-06-05", "description": "GBG05067HAEBAO74 OCMT/EUR393.33 STAV ZILBERSHTEIN DARLEHEN ZURUCKZAHLEN",
         "reference": "", "deposit": "393.33", "withdrawal": "", "balance": "1398462.12"},
        {"date": "2017-06-05", "description": "GBG05067AMEBBOAO OCMT/EUR391.90 STAV ZILBERSHTEIN DARLEHEN ZURUCKZAHLEN",
         "reference": "", "deposit": "391.90", "withdrawal": "", "balance": "1398854.02"},
        {"date": "2017-06-07", "description": "GBG07067HCEHO7CW OCMT/EUR311.58 STAV ZILBERSHTEIN DARLEHEN ZURUCKZAHLEN",
         "reference": "", "deposit": "311.58", "withdrawal": "", "balance": "1399165.60"},
        {"date": "2017-06-07", "description": "GBG070670QEH08CG OCMT/EUR265.77 STAV ZILBERSHTEIN FINAL PAYMENT",
         "reference": "", "deposit": "265.77", "withdrawal": "", "balance": "1399431.37"},
        {"date": "2017-06-07", "description": "GBG07067APEHOU4G OCMT/EUR387.38 STAV ZILBERSHTEIN DARLEHEN ZURUCKZAHLEN",
         "reference": "", "deposit": "387.38", "withdrawal": "", "balance": "1399818.75"},
    ],
}

# Combos where simply deleting rows with no deposit/withdrawal/balance fixes the chain.
DELETE_EMPTY_ROWS = [
    ("023-085996-362", "2014-03-07"),
    ("023-085996-540", "2014-10-08"),
    ("023-085996-361", "2015-09-08"),
    ("023-085996-540", "2017-01-06"),
    ("023-085996-705", "2017-01-06"),
    ("023-085996-540", "2017-05-08"),
    ("023-085996-076", "2018-02-23"),
    ("023-085996-076", "2018-04-24"),
    ("023-085996-076", "2018-05-24"),
    ("023-085996-076", "2018-06-22"),
    ("023-085996-076", "2021-02-16"),
]


def load_csv() -> list[dict]:
    with open(CSV_PATH) as f:
        return list(csv.DictReader(f))


def apply_corrections(txns: list[dict]) -> list[dict]:
    """Replace transactions for corrected combos and delete empty rows."""
    to_replace = set(CORRECTIONS.keys())
    to_delete_empty = set(DELETE_EMPTY_ROWS)

    result = []
    for t in txns:
        key = (t["account_number"], t["statement_date"])
        if key in to_replace:
            continue  # Will be replaced
        if key in to_delete_empty:
            # Only keep rows that have actual data
            if t["deposit"] or t["withdrawal"] or t["balance"]:
                result.append(t)
            continue
        result.append(t)

    # Add corrected transactions, inheriting account_type and currency from originals
    for (acct, stmt_date), new_txns in CORRECTIONS.items():
        # Find account_type and currency from original data
        account_type = ""
        currency = ""
        for t in txns:
            if t["account_number"] == acct and t["statement_date"] == stmt_date:
                account_type = t.get("account_type", "")
                currency = t.get("currency", "")
                break

        for nt in new_txns:
            result.append({
                "statement_date": stmt_date,
                "account_number": acct,
                "account_type": account_type,
                "currency": currency,
                "date": nt["date"],
                "description": nt["description"],
                "reference": nt.get("reference", ""),
                "deposit": nt.get("deposit", ""),
                "withdrawal": nt.get("withdrawal", ""),
                "balance": nt.get("balance", ""),
            })

    result.sort(key=lambda t: (t.get("date", ""), t.get("account_number", "")))
    return result


def check_balance_gaps(txns: list[dict]) -> list[tuple]:
    """Return list of problem combos."""
    from collections import defaultdict
    by_acct = defaultdict(list)
    for t in txns:
        by_acct[f"{t['account_number']} {t['currency']}"].append(t)

    problems = set()
    for acct_key, acct_txns in by_acct.items():
        acct_txns.sort(key=lambda t: (t["statement_date"], t["date"]))
        prev_bal = None
        prev_stmt = ""
        for t in acct_txns:
            stmt = t["statement_date"]
            if not t["balance"]:
                if stmt == prev_stmt or prev_stmt == "":
                    acct, ccy = acct_key.split(" ", 1)
                    problems.add((acct, ccy, stmt))
                continue
            cur_bal = float(t["balance"])
            amt = 0.0
            if t["deposit"]:
                amt = float(t["deposit"])
            elif t["withdrawal"]:
                amt = -float(t["withdrawal"])
            if prev_bal is not None and stmt == prev_stmt:
                expected = prev_bal + amt
                if abs(expected - cur_bal) > 0.02:
                    acct, ccy = acct_key.split(" ", 1)
                    problems.add((acct, ccy, stmt))
            prev_bal = cur_bal
            prev_stmt = stmt

    return sorted(problems)


def main():
    txns = load_csv()
    print(f"Loaded {len(txns)} transactions")

    # Check before
    problems_before = check_balance_gaps(txns)
    print(f"Problems before: {len(problems_before)}")

    # Apply corrections
    result = apply_corrections(txns)
    print(f"After corrections: {len(result)} transactions")

    # Check after
    problems_after = check_balance_gaps(result)
    print(f"Problems after: {len(problems_after)}")

    # Show what was fixed
    fixed = set(problems_before) - set(problems_after)
    still = set(problems_after) & set(problems_before)
    new = set(problems_after) - set(problems_before)

    if fixed:
        print(f"\nFixed ({len(fixed)}):")
        for acct, ccy, stmt in sorted(fixed):
            print(f"  {acct} {ccy} @ {stmt}")

    if new:
        print(f"\nNEW PROBLEMS ({len(new)}):")
        for acct, ccy, stmt in sorted(new):
            print(f"  {acct} {ccy} @ {stmt}")

    if still:
        print(f"\nRemaining ({len(still)}):")
        for acct, ccy, stmt in sorted(still):
            print(f"  {acct} {ccy} @ {stmt}")

    # Write
    fieldnames = [f.name for f in fields(Transaction)]
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for t in result:
            writer.writerow({fn: t.get(fn, "") for fn in fieldnames})
    print(f"\nWritten {len(result)} transactions to {CSV_PATH}")


if __name__ == "__main__":
    main()
