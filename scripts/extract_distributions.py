#!/usr/bin/env python3
"""Extract structured distribution data from emails classified as distribution_notice."""

import codecs
import csv
import email
import json
import os
import re
import sys
from email import policy
from html.parser import HTMLParser
from pathlib import Path

# Register iso-8859-8-i as alias for iso-8859-8
try:
    codecs.lookup('iso-8859-8-i')
except LookupError:
    _iso8 = codecs.lookup('iso-8859-8')
    codecs.register(lambda name: _iso8 if name == 'iso-8859-8-i' else None)

BASE_DIR = Path(__file__).parent
INBOX_DIR = BASE_DIR / 'inbox'
KNOWLEDGE_FILE = BASE_DIR / 'knowledge.json'
OUTPUT_FILE = BASE_DIR / 'data' / 'email-distributions.csv'


class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return text content."""
    def __init__(self):
        super().__init__()
        self.fed = []

    def handle_data(self, d):
        self.fed.append(d)

    def get_data(self):
        return ''.join(self.fed)


def strip_html(html_text):
    s = HTMLTextExtractor()
    s.feed(html_text)
    return s.get_data()


def load_knowledge():
    with open(KNOWLEDGE_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_alias_map(knowledge):
    """Build a mapping from Hebrew/alias names to canonical English investment names."""
    alias_map = {}
    for key, inv in knowledge.get('investments', {}).items():
        name = inv['name']
        # Map all aliases
        for alias in inv.get('aliases', []):
            alias_map[alias.lower()] = name
        # Map Hebrew name if present
        if 'hebrew_name' in inv:
            alias_map[inv['hebrew_name'].lower()] = name
        # Map the canonical name itself (lowercase)
        alias_map[name.lower()] = name
        # Map the key
        alias_map[key] = name
    return alias_map


def get_email_body(eml_path):
    """Parse .eml and return plain text body (or HTML stripped to text)."""
    with open(eml_path, 'rb') as f:
        msg = email.message_from_bytes(f.read(), policy=policy.default)

    text_body = ''
    html_body = ''

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain' and not text_body:
                try:
                    text_body = part.get_content()
                except Exception:
                    pass
            elif ct == 'text/html' and not html_body:
                try:
                    html_body = part.get_content()
                except Exception:
                    pass
    else:
        ct = msg.get_content_type()
        try:
            content = msg.get_content()
        except Exception:
            content = ''
        if ct == 'text/plain':
            text_body = content
        elif ct == 'text/html':
            html_body = content

    if text_body:
        return text_body
    if html_body:
        return strip_html(html_body)
    return ''


def get_email_date(eml_path):
    """Get the Date header from the email."""
    with open(eml_path, 'rb') as f:
        msg = email.message_from_bytes(f.read(), policy=policy.default)
    date_str = msg.get('Date', '')
    return date_str


# --- Investment name recognition ---

# Subject-based patterns mapping to investment names
SUBJECT_INVESTMENT_PATTERNS = [
    # English names in subjects
    (r'Liquidity Capital II', 'Liquidity Capital II, L.P.'),
    (r'Viola Credit ALF III', 'Viola Credit ALF III'),
    (r'EMIF II|Electra Multifamily Investment Fund II', 'Electra Multifamily Investment Fund II (EMIF II)'),
    (r'Logistic Fund II \(FRG-X\)|FRG.?X', 'Faro-Point FRG-X'),
    (r'ISF.?III|Israel Secondary Fund', 'ISF III Feeder Fund, L.P'),
    (r'Impact Debt', 'Impact Debt FOF'),
    (r'Impact Real Estate', 'Impact Real Estate FOF'),
    (r'Pollen Street|Phoenix Credit Strategies', 'Pollen Street Credit Fund III - USD'),
    (r'Coller Capital VIII', 'Coller Capital VIII'),
    (r'KDC Media|Stardom', 'KDC Media Fund / Stardom Ventures'),
    # Hebrew names in subjects
    (r'כרמל קרדיט', 'Carmel Credit'),
    (r'אלקטרה.*נדל"ן|קרן אלקטרה 2|אלקטרה 2|Electra.*BTR', 'Electra Multifamily Investment Fund II (EMIF II)'),
    (r'אלקטרה.*BTR|אלקטרה בי\.?\s*טי\.?\s*אר', 'Electra BTR 1'),
    (r'הראל המגן|Harel.*Hamagen', 'Harel Hamagen USA'),
    (r'ריאליטי\s*גרמניה|Reality Germany', 'Reality Germany 1'),
    (r'דיור מוגן.*אטלנטה|Assisted Living.*Georgia|בוליגו\s*1', 'Atlanta Senior Living / Boligo 1'),
    (r'דיור מוגן.*2.*אטלנטה|בוליגו\s*2', 'Atlanta Senior Living 2 / Boligo 2'),
    (r'Pelham Park|פלהם פארק|פלהם.*פילדלפיה|פלהם \(גלפנד\)|גלפנד-?\s*פלהם|עסקת פלהם', 'Pelham Park'),
    (r'גייטווטר|Gatewater|גלפנד.*מרילנד|מרילנד.*גלפנד', 'Gelfand Maryland Gatewater'),
    (r'ניו הייבן.*נץ|נץ.*ניו הייבן|New Haven|Residential Portfolio.*New Haven|Residential.*Portfolio.*CT', 'Netz New Haven'),
    (r'הרטפורד|Hartford', 'Hartford, Connecticut Urban Renewal'),
    (r'וינה.*מלון|מלון.*וינה|Vienna.*Apartment|Serviced Apartments.*Vienn?a', 'Vienna Apartment Hotel / Serviced Apartments'),
    (r'מגורים.*וינה|Residence Vienna', 'Residence Vienna 1'),
    (r'בית מרס|Beit Mars', 'Beit Mars'),
    (r'קאליבר|Caliber', 'Caliber'),
    (r'דאטה סנטר|Data Center', 'Data Center LA'),
    (r'קרן נץ\s*2|Netz\s*2', 'Netz 2'),
    (r'Octo', 'Alt Group Octo Opportunities Fund'),
    (r'Zoo Hotel Schonbrunn|Zoo Hotel.*Vienna', 'Vienna Apartment Hotel / Serviced Apartments'),
    (r'Reality RG1|RG1.*Supermarket', 'Reality Germany 1'),
    (r'Residential Portfolio.*SFR.*Ball Ground|SFR.*Ball Ground.*GA', 'Atlanta Senior Living 2 / Boligo 2'),
]

# Body-based patterns for when subject is vague
BODY_INVESTMENT_PATTERNS = SUBJECT_INVESTMENT_PATTERNS + [
    (r'Liquidity Capital II L\.?P', 'Liquidity Capital II, L.P.'),
    (r'EMIF II Feeder', 'Electra Multifamily Investment Fund II (EMIF II)'),
    (r'עסקת גלפנד-?\s*פלהם|עסקת פלהם', 'Pelham Park'),
    (r'עסקת גלפנד-?\s*גייטווטר|עסקת גייטווטר', 'Gelfand Maryland Gatewater'),
    (r'עסקת ניו הייבן', 'Netz New Haven'),
    (r'עסקת הרטפורד', 'Hartford, Connecticut Urban Renewal'),
    (r'עסקת ריאליטי\s*גרמניה', 'Reality Germany 1'),
    (r'יזמות דיור מוגן אטלנטה|דיור מוגן באטלנטה|פרויקט דיור מוגן', 'Atlanta Senior Living / Boligo 1'),
]


def identify_investment(subject, body, alias_map):
    """Identify the investment from subject and body text."""
    # Try subject patterns first
    for pattern, name in SUBJECT_INVESTMENT_PATTERNS:
        if re.search(pattern, subject, re.IGNORECASE):
            return name

    # Handle ambiguous subjects like "Fwd: חלוקה רבעונית" - try body
    for pattern, name in BODY_INVESTMENT_PATTERNS:
        if re.search(pattern, body, re.IGNORECASE):
            return name

    # Special disambiguation: Pelham vs Maryland/Gatewater
    # Subject says "גלפנד- פלהם" but body mentions "עסקת פלהם"
    # Subject says "גלפנד- מרילנד" -> Gatewater
    if re.search(r'גלפנד.*מרילנד|מרילנד', subject, re.IGNORECASE):
        return 'Gelfand Maryland Gatewater'
    if re.search(r'גלפנד.*פלהם|פלהם', subject, re.IGNORECASE):
        return 'Pelham Park'

    return None


def extract_amounts(body, subject):
    """Extract distribution amount(s) and currency from body text."""
    amounts = []

    # Pattern: amount with currency symbol inline
    # $X,XXX or X,XXX$ or X,XXX₪ or €X,XXX or X,XXX€
    # Also handles amounts like 17,000₪

    # ILS amounts with ₪ suffix
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*₪', body):
        val = m.group(1).replace(',', '')
        try:
            amounts.append((float(val), 'ILS'))
        except ValueError:
            pass

    # USD amounts: $X,XXX or X,XXX$
    for m in re.finditer(r'\$\s*([\d,]+(?:\.\d+)?)', body):
        val = m.group(1).replace(',', '')
        try:
            amounts.append((float(val), 'USD'))
        except ValueError:
            pass
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*\$', body):
        val = m.group(1).replace(',', '')
        try:
            amounts.append((float(val), 'USD'))
        except ValueError:
            pass

    # EUR amounts
    for m in re.finditer(r'€\s*([\d,]+(?:\.\d+)?)', body):
        val = m.group(1).replace(',', '')
        try:
            amounts.append((float(val), 'EUR'))
        except ValueError:
            pass
    for m in re.finditer(r'([\d,]+(?:\.\d+)?)\s*€', body):
        val = m.group(1).replace(',', '')
        try:
            amounts.append((float(val), 'EUR'))
        except ValueError:
            pass

    return amounts


def extract_distribution_number(subject, body):
    """Extract distribution number like 'Dist #7' or 'Distribution 19'."""
    # From subject
    m = re.search(r'(?:Dist(?:ribution)?\.?\s*#?\s*(\d+))', subject, re.IGNORECASE)
    if m:
        return f"Dist #{m.group(1)}"

    m = re.search(r'Distribution\s+(\d+)', subject, re.IGNORECASE)
    if m:
        return f"Dist #{m.group(1)}"

    m = re.search(r'ה-?(\d+)', subject)
    if m and 'חלוקה' in subject:
        return f"Dist #{m.group(1)}"

    # From body
    m = re.search(r'(?:Dist(?:ribution)?\.?\s*(?:Notice\s*)?#?\s*(\d+))', body, re.IGNORECASE)
    if m:
        return f"Dist #{m.group(1)}"

    m = re.search(r'(\d+)(?:th|st|nd|rd)\s+distribution', body, re.IGNORECASE)
    if m:
        return f"Dist #{m.group(1)}"

    m = re.search(r'החלוקה\s+ה-?(\d+)', body)
    if m:
        return f"Dist #{m.group(1)}"

    # "חלוקה שנייה" = 2nd distribution
    hebrew_ordinals = {
        'ראשונה': 1, 'שנייה': 2, 'שלישית': 3, 'רביעית': 4,
        'חמישית': 5, 'שישית': 6, 'שביעית': 7
    }
    for heb, num in hebrew_ordinals.items():
        if heb in body or heb in subject:
            return f"Dist #{num}"

    return ''


def extract_period(subject, body):
    """Extract the quarter/period covered."""
    combined = subject + ' ' + body

    # Q1-Q4 2024 style
    m = re.search(r'Q([1-4])\s*(\d{4})', combined, re.IGNORECASE)
    if m:
        return f"Q{m.group(1)} {m.group(2)}"

    # "רבעון הראשון/השני/השלישי/הרביעי של/לשנת 2022"
    heb_quarters = {
        'ראשון': '1', 'השני': '2', 'שני': '2',
        'השלישי': '3', 'שלישי': '3',
        'הרביעי': '4', 'רביעי': '4',
        'הראשון': '1',
    }

    # "לרבעון הX של/לשנת YYYY" or "רבעון X YYYY"
    for heb, qnum in heb_quarters.items():
        pattern = rf'(?:ל?רבעון\s+(?:ה)?{heb}|רבעון\s+{heb})\s+(?:של\s+|לשנת\s+)?(\d{{4}})'
        m = re.search(pattern, combined)
        if m:
            return f"Q{qnum} {m.group(1)}"

    # "רבעון 1" style with year nearby
    m = re.search(r'רבעון\s+(\d)\s*(?:,\s*|\s+)(\d{4})', combined)
    if m:
        return f"Q{m.group(1)} {m.group(2)}"

    m = re.search(r'רבעון\s+(\d)\s+', combined)
    if m:
        # Try to find year nearby
        year_m = re.search(r'20\d{2}', combined)
        if year_m:
            return f"Q{m.group(1)} {year_m.group(0)}"

    # Hebrew quarter names in table format: "רבעון 1\nרבעון 2\n..."
    # The last quarter with an amount is the current one

    return ''


def extract_date_from_dir(dirname):
    """Extract date from directory name like 2025-11-04-..."""
    m = re.match(r'(\d{4}-\d{2}-\d{2})', dirname)
    if m:
        return m.group(1)
    return ''


def pick_distribution_amount(amounts, investment_name, body):
    """From a list of (amount, currency) tuples, pick the most likely distribution amount.

    Heuristics:
    - Skip investment commitment amounts (large round numbers that match known commitments)
    - For The Service emails with table format (Q1/Q2/Q3/Q4), pick the last filled amount
    - Skip zero amounts
    - For Netz-style emails with breakdown (principal + interest + total ILS), prefer the breakdown
    """
    if not amounts:
        return None, None

    # Known commitment amounts to skip
    skip_amounts = {
        500000, 600000, 475000, 400000, 300000, 800000, 335000,
        1000000, 250000, 407701, 510000, 2000000, 150000, 625000
    }

    # Also detect "סכום השקעתך בפרויקט" amounts and remove them
    investment_amount_pattern = re.compile(
        r'(?:סכום השקעת|השקעתכם בעסקה|סכום ההשקעה)\S*\s*:?\s*([\d,]+(?:\.\d+)?)\s*([₪$€])',
    )
    investment_vals = set()
    for m in investment_amount_pattern.finditer(body):
        val = float(m.group(1).replace(',', ''))
        investment_vals.add(val)

    filtered = [(a, c) for a, c in amounts
                if a > 0 and a not in skip_amounts and a not in investment_vals]

    if not filtered:
        return None, None

    # For emails with table format containing multiple quarters,
    # we have multiple amounts - they represent different quarters.
    # The email is reporting on the *last* filled quarter.
    # But we want all of them actually, or just the latest?
    # Per the task, we extract what we can. For table-format emails,
    # pick the most recently filled value(s).

    # If there's only one amount after filtering, use it
    if len(filtered) == 1:
        return filtered[0]

    # If body has breakdown pattern (principal + interest + total),
    # look for total
    if re.search(r'סה"כ|סה״כ|total', body, re.IGNORECASE):
        # The largest ILS amount is likely the total
        ils_amounts = [(a, c) for a, c in filtered if c == 'ILS']
        if ils_amounts:
            return max(ils_amounts, key=lambda x: x[0])

    # For Netz-style with "סכום החזר הקרן" + "סכום הריבית" + "סה"כ חלוקה בש"ח"
    # Return total ILS or sum of USD components
    principal_m = re.search(r'(?:החזר ה?קרן|principal).*?([\d,]+(?:\.\d+)?)\s*\$', body, re.IGNORECASE)
    interest_m = re.search(r'(?:ריבית|interest).*?([\d,]+(?:\.\d+)?)\s*\$', body, re.IGNORECASE)
    total_ils_m = re.search(r'(?:סה"כ|סה״כ).*?([\d,]+(?:\.\d+)?)\s*₪', body)

    if principal_m and interest_m:
        p = float(principal_m.group(1).replace(',', ''))
        i = float(interest_m.group(1).replace(',', ''))
        return (p + i, 'USD')

    # For table format emails: multiple amounts of same currency
    # Return the last non-zero amount (most recent quarter)
    if len(filtered) > 1:
        # Check if they're all same currency
        currencies = set(c for _, c in filtered)
        if len(currencies) == 1:
            return filtered[-1]  # Last amount in the table
        else:
            # Prefer USD over ILS, unless all are ILS
            usd = [(a, c) for a, c in filtered if c == 'USD']
            if usd:
                return usd[-1]
            return filtered[-1]

    return filtered[0] if filtered else (None, None)


def determine_confidence(investment, amount, period, body, subject):
    """Determine extraction confidence."""
    if not investment:
        return 'low'
    if not amount:
        return 'medium'  # Investment identified but no amount

    # Check for corrections
    if re.search(r'תיקון|correction|corrected', body + subject, re.IGNORECASE):
        return 'low'

    # High confidence if both investment and amount are clear
    if investment and amount and period:
        return 'high'
    if investment and amount:
        return 'medium'
    return 'low'


def is_no_amount_email(body, subject):
    """Check if this email is just a notification without actual amounts.

    Emails that say 'attached is the distribution notice' or are about
    bank details or portal links, not actual distribution data.
    """
    # Fortress emails: just say "attached is the distribution notice for Q..."
    # These have amounts in the PDF, not the email body
    # We still want to record them with what info we can extract from subject

    # Reply/confirmation emails without amounts
    skip_patterns = [
        r'following up',
        r'confirm.*bank.*details',
        r'החלוקה שלך חזרה',  # "your distribution was returned" (bank issue)
        r'היה שינוי בחשבון',  # "was there a change in account"
        r'הצגת סכום חלוקה',  # meta-discussion about how amount is shown
        r'במכתב מוצגת החלוקה',  # "in the letter, the distribution is shown"
    ]
    for pattern in skip_patterns:
        if re.search(pattern, body, re.IGNORECASE):
            return True
    return False


def extract_period_from_fortress(subject, body, dirname):
    """Fortress emails typically say 'רבעון הX של YYYY' in the body."""
    combined = subject + ' ' + body

    # "לרבעון הראשון/השני/השלישי/הרביעי של 2025"
    heb_quarters = {
        'הראשון': '1', 'ראשון': '1',
        'השני': '2', 'שני': '2',
        'השלישי': '3', 'שלישי': '3',
        'הרביעי': '4', 'רביעי': '4',
    }

    for heb, qnum in heb_quarters.items():
        m = re.search(rf'רבעון\s+{heb}\s+(?:של\s+)?(\d{{4}})', combined)
        if m:
            return f"Q{qnum} {m.group(1)}"

    # "Q1 2025" style
    m = re.search(r'Q([1-4])\s*(\d{4})', combined, re.IGNORECASE)
    if m:
        return f"Q{m.group(1)} {m.group(2)}"

    return ''


def process_email(dir_path, alias_map):
    """Process a single distribution_notice email directory."""
    classification_path = dir_path / '.classification'
    eml_path = dir_path / 'message.raw.eml'

    if not eml_path.exists():
        return None

    with open(classification_path, 'r', encoding='utf-8') as f:
        cls = json.load(f)

    if cls.get('category') != 'distribution_notice':
        return None

    subject = cls.get('subject', '')
    from_addr = cls.get('from', '')
    dirname = dir_path.name
    email_date = extract_date_from_dir(dirname)

    body = get_email_body(eml_path)

    # Skip emails that are just replies/confirmations without amounts
    if is_no_amount_email(body, subject):
        return None

    # Identify investment
    investment = identify_investment(subject, body, alias_map)

    # Extract amounts
    amounts = extract_amounts(body, subject)
    amount, currency = pick_distribution_amount(amounts, investment, body)

    # Extract distribution number
    dist_num = extract_distribution_number(subject, body)

    # Extract period
    period = extract_period(subject, body)
    if not period:
        period = extract_period_from_fortress(subject, body, dirname)

    # Determine confidence
    confidence = determine_confidence(investment, amount, period, body, subject)

    # For Fortress/forwarded emails that just say "attached is distribution notice",
    # we record them even without amounts - the info is in the PDF
    if not investment:
        confidence = 'low'

    return {
        'date': email_date,
        'investment': investment or f'UNKNOWN ({subject[:60]})',
        'amount': f'{amount:.2f}' if amount else '',
        'currency': currency or '',
        'distribution_number': dist_num,
        'period': period,
        'source_dir': dirname,
        'confidence': confidence,
    }


def main():
    knowledge = load_knowledge()
    alias_map = build_alias_map(knowledge)

    # Find all distribution_notice directories
    results = []
    skipped = 0
    errors = []

    for d in sorted(INBOX_DIR.iterdir()):
        if not d.is_dir():
            continue
        cls_path = d / '.classification'
        if not cls_path.exists():
            continue

        try:
            with open(cls_path, 'r', encoding='utf-8') as f:
                cls = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        if cls.get('category') != 'distribution_notice':
            continue

        try:
            result = process_email(d, alias_map)
            if result:
                results.append(result)
            else:
                skipped += 1
        except Exception as e:
            errors.append((d.name, str(e)))

    # Sort by date
    results.sort(key=lambda r: r['date'])

    # Write CSV
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'date', 'investment', 'amount', 'currency',
            'distribution_number', 'period', 'source_dir', 'confidence'
        ])
        writer.writeheader()
        writer.writerows(results)

    # Summary
    print(f"Processed {len(results)} distribution emails -> {OUTPUT_FILE}")
    print(f"Skipped {skipped} (no-amount replies, portal links, bank issues)")
    if errors:
        print(f"Errors ({len(errors)}):")
        for name, err in errors:
            print(f"  {name}: {err}")

    # Stats
    investments = set(r['investment'] for r in results)
    with_amounts = sum(1 for r in results if r['amount'])
    high_conf = sum(1 for r in results if r['confidence'] == 'high')
    med_conf = sum(1 for r in results if r['confidence'] == 'medium')
    low_conf = sum(1 for r in results if r['confidence'] == 'low')

    print(f"\nInvestments found: {len(investments)}")
    print(f"With amounts: {with_amounts}")
    print(f"Confidence: {high_conf} high, {med_conf} medium, {low_conf} low")

    # Show UNKNOWN investments for debugging
    unknowns = [r for r in results if r['investment'].startswith('UNKNOWN')]
    if unknowns:
        print(f"\nUnidentified investments ({len(unknowns)}):")
        for r in unknowns:
            print(f"  {r['date']} - {r['investment']} - {r['source_dir']}")


if __name__ == '__main__':
    main()
