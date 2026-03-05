#!/usr/bin/env python3
"""Extract an index of quarterly/monthly reports from inbox .classification files."""

import codecs
import csv
import json
import os
import re
import sys

# Register codec aliases for Hebrew email decoding
try:
    codecs.lookup("iso-8859-8-i")
except LookupError:
    try:
        iso8859_8 = codecs.lookup("iso-8859-8")
        codecs.register(
            lambda name: iso8859_8 if name == "iso-8859-8-i" else None
        )
    except LookupError:
        pass

try:
    codecs.lookup("unknown-8bit")
except LookupError:
    latin1 = codecs.lookup("latin-1")
    codecs.register(
        lambda name: latin1 if name == "unknown-8bit" else None
    )

INBOX_DIR = "/home/adam/life/fin-mgmt/inbox"
KNOWLEDGE_PATH = "/home/adam/life/fin-mgmt/knowledge.json"
OUTPUT_PATH = "/home/adam/life/fin-mgmt/data/email-reports-index.csv"

# Hebrew month names
HEBREW_MONTHS = {
    "ינואר": "January", "פברואר": "February", "מרץ": "March", "מרס": "March",
    "אפריל": "April", "מאי": "May", "יוני": "June",
    "יולי": "July", "אוגוסט": "August", "ספטמבר": "September",
    "אוקטובר": "October", "נובמבר": "November", "דצמבר": "December",
}

# Extra aliases not in knowledge.json but appearing in email subjects
EXTRA_ALIASES = {
    # Stardom/KDC variants
    "stardom ventures": "KDC Media Fund / Stardom Ventures",
    "stardom media": "KDC Media Fund / Stardom Ventures",
    "stardom": "KDC Media Fund / Stardom Ventures",
    "kdc media": "KDC Media Fund / Stardom Ventures",
    # Hartford variants
    "hartford": "Hartford, Connecticut Urban Renewal",
    "הרטפורד": "Hartford, Connecticut Urban Renewal",
    "הארטפורד": "Hartford, Connecticut Urban Renewal",
    "עירוב שימושים הרטפורד": "Hartford, Connecticut Urban Renewal",
    # FRG-X / Faropoint variants
    "frg-x": "Faro-Point FRG-X",
    "frg x": "Faro-Point FRG-X",
    "frg- x": "Faro-Point FRG-X",
    "frgx": "Faro-Point FRG-X",
    "logistic fund ii - frg x": "Faro-Point FRG-X",
    "logistic fund ii": "Faro-Point FRG-X",
    # Electra BTR
    "sfr electra": "Electra BTR 1",
    "electra btr": "Electra BTR 1",
    # Residential Portfolio CT = Netz New Haven
    "residntial portfolio ct": "Netz New Haven",
    "residential portfolio ct": "Netz New Haven",
    # Harel Hamagen
    "הראל מגן": "Harel Hamagen USA",
    "הראל המגן": "Harel Hamagen USA",
    "הראל מגן ארצות הברית": "Harel Hamagen USA",
    'קרן המגן ארה"ב': "Harel Hamagen USA",
    "קרן המגן": "Harel Hamagen USA",
    "הראל פיננסים אלטרנטיב המגן": "Harel Hamagen USA",
    # Pelham
    "פלהם": "Pelham Park",
    "plhm": "Pelham Park",
    # Boligo / Atlanta
    "assisted living": "Atlanta Senior Living / Boligo 1",
    "assisted living 2": "Atlanta Senior Living 2 / Boligo 2",
    "bulligo 2": "Atlanta Senior Living 2 / Boligo 2",
    "בוליגו": "Atlanta Senior Living / Boligo 1",
    "דיור מוגן": "Atlanta Senior Living / Boligo 1",
    # Viola
    "viola credit": "Viola Credit ALF III",
    # Pollen Street
    "pollen": "Pollen Street Credit Fund III - USD",
    "קרן pollen": "Pollen Street Credit Fund III - USD",
    # Hoch 33 = Vienna Apartment Hotel
    "hoch 33": "Vienna Apartment Hotel / Serviced Apartments",
    "הוך 33": "Vienna Apartment Hotel / Serviced Apartments",
    # Netz 2
    "קרן נץ 2": "Netz 2",
    "נץ 2": "Netz 2",
    "netz 2": "Netz 2",
    # Poplar Towers
    "poplar towers": "Poplar Towers",
    # REI West Texas
    "rei west texas": "REI West Texas",
    "אנרגיה הסולארית במערב טקסס": "REI West Texas",
    "פרויקט האנרגיה הסולארית": "REI West Texas",
}


def load_knowledge():
    """Load knowledge.json and build alias->name mapping."""
    with open(KNOWLEDGE_PATH, "r", encoding="utf-8") as f:
        kb = json.load(f)

    alias_map = {}
    for inv_key, inv in kb.get("investments", {}).items():
        canonical = inv["name"]
        for alias in inv.get("aliases", []):
            alias_map[alias.lower().strip()] = canonical
        alias_map[canonical.lower().strip()] = canonical
        if inv.get("hebrew_name"):
            alias_map[inv["hebrew_name"].lower().strip()] = canonical

    for ent_key, ent in kb.get("entities", {}).items():
        if ent.get("hebrew_name"):
            alias_map[ent["hebrew_name"].lower().strip()] = ent["name"]

    # Add extra aliases
    for alias, canonical in EXTRA_ALIASES.items():
        alias_map[alias.lower().strip()] = canonical

    return alias_map


def resolve_investment(name_raw, alias_map):
    """Try to resolve an investment name using the alias map."""
    if not name_raw:
        return name_raw
    key = name_raw.lower().strip()
    if key in alias_map:
        return alias_map[key]
    # Try partial matching
    for alias, canonical in sorted(alias_map.items(), key=lambda x: -len(x[0])):
        if alias in key or key in alias:
            return canonical
    return name_raw


def find_best_alias_match(text, alias_map):
    """Find the longest alias that appears in text."""
    text_lower = text.lower()
    best_match = None
    best_len = 0
    for alias, canonical in alias_map.items():
        if len(alias) >= 3 and alias in text_lower and len(alias) > best_len:
            best_match = canonical
            best_len = len(alias)
    return best_match


def extract_investment_name(subject, attachments, alias_map):
    """Extract investment name from subject line and resolve via aliases."""
    if not subject:
        return "Unknown"

    # Special patterns first

    # "עדכון רבעון X-YYYY <investment>" or "עדכון רבעון <investment>- QX YYYY"
    m = re.search(r'עדכון רבעון\s+(?:\d+[-,]?\s*\d{4}\s+)?(.+?)(?:\s*[-–]\s*Q\d.*)?$', subject)
    if m:
        inv = m.group(1).strip()
        # Remove leading Q/date info
        inv = re.sub(r'^(?:Q\d\s+\d{4}\s*[-–]\s*)', '', inv)
        resolved = resolve_investment(inv, alias_map)
        if resolved != inv:
            return resolved
        # Try alias match on the extracted part
        match = find_best_alias_match(inv, alias_map)
        if match:
            return match

    # "עדכון QX YYYY - <investment>"
    m = re.search(r'עדכון\s+Q\d\s+\d{4}\s*[-–]\s*(.+)', subject)
    if m:
        return resolve_investment(m.group(1).strip(), alias_map)

    # "סיכום רבעון X, YYYY <investment>"
    m = re.search(r'סיכום רבעון\s+\d+,?\s*\d{4}\s+(.+)', subject)
    if m:
        return resolve_investment(m.group(1).strip(), alias_map)

    # "דיווח רבעוני QX-YYYY ... <details>"
    # Fortress pattern: "פורטרס - עדכון רבעוני QX-YYYY <investment>"
    m = re.search(r'עדכון רבעוני\s+[QO]\d[-\s]*\d{4}\s*[-–0]?\s*(.+)', subject)
    if m:
        inv = m.group(1).strip()
        resolved = resolve_investment(inv, alias_map)
        if resolved != inv:
            return resolved
        match = find_best_alias_match(inv, alias_map)
        if match:
            return match

    # "דיווח חודשי" pattern (Harel)
    m = re.search(r'דיווח (?:חודשי|חודש)\s*(.+?)$', subject)
    if m:
        inv = m.group(1).strip()
        inv = re.sub(r'\s*[-–]\s*(?:ינואר|פברואר|מרץ|מרס|אפריל|מאי|יוני|יולי|אוגוסט|ספטמבר|אוקטובר|נובמבר|דצמבר).*$', '', inv)
        resolved = resolve_investment(inv, alias_map)
        if resolved != inv:
            return resolved
        match = find_best_alias_match(inv, alias_map)
        if match:
            return match

    # "הראל מגן ... | דיווח"
    if "הראל מגן" in subject or "הראל המגן" in subject:
        return "Harel Hamagen USA"

    # "דיווח <month> YYYY" (Harel monthly without explicit investment name)
    m = re.search(r'\|\s*(?:דיווח|עדכון)\s+(\S+)\s+(\d{4})', subject)
    if m:
        match = find_best_alias_match(subject, alias_map)
        if match:
            return match

    # English patterns: "<Investment> - QX YYYY" or "<Investment> QX YYYY"
    m = re.search(r'^(?:FW:|Fwd:|RE:)?\s*(?:Q\d\s+Update\s*[-–]\s*)(.+)', subject, re.I)
    if m:
        return resolve_investment(m.group(1).strip(), alias_map)

    # "Financial Report - QX YYYY <investment>"
    m = re.search(r'Financial Report\s*[-–]\s*Q\d\s+\d{4}\s+(.+)', subject, re.I)
    if m:
        return resolve_investment(m.group(1).strip(), alias_map)

    # "Quarterly Update QX YYYY - <investment>"  or "<investment> - Quarterly Update"
    m = re.search(r'Quarterly Update\s+Q\d\s+\d{4}\s*[-–]\s*(.+)', subject, re.I)
    if m:
        return resolve_investment(m.group(1).strip(), alias_map)

    # "<investment> - Quarterly Update QX YYYY"
    m = re.search(r'(.+?)\s*[-–]\s*Quarterly (?:Update|Report)', subject, re.I)
    if m:
        inv = re.sub(r'^(?:FW:|Fwd:|RE:)\s*', '', m.group(1), flags=re.I).strip()
        resolved = resolve_investment(inv, alias_map)
        if resolved != inv:
            return resolved

    # "<Investment> QX YYYY" at start
    m = re.match(r'(.+?)\s+Q\d\s+\d{4}', subject)
    if m:
        inv = re.sub(r'^(?:FW:|Fwd:|RE:)\s*', '', m.group(1), flags=re.I).strip()
        inv = re.sub(r'^(?:פורטרס|fortress)\s*[-–]\s*', '', inv, flags=re.I).strip()
        resolved = resolve_investment(inv, alias_map)
        if resolved != inv:
            return resolved
        match = find_best_alias_match(inv, alias_map)
        if match:
            return match

    # "דוח שנתי קרן <investment>"
    m = re.search(r'דוח שנתי\s+(?:קרן\s+)?(.+)', subject)
    if m:
        return resolve_investment(m.group(1).strip(), alias_map)

    # Generic: try alias match on full subject
    match = find_best_alias_match(subject, alias_map)
    if match:
        return match

    # Try alias match on attachment filenames
    if attachments:
        for att in attachments:
            if att.lower().endswith('.pdf'):
                match = find_best_alias_match(att, alias_map)
                if match:
                    return match

    # Fortress "Assisted Living" without number = Boligo 1
    if re.search(r'assisted living(?!\s*2)', subject, re.I):
        return "Atlanta Senior Living / Boligo 1"

    return "Unknown"


def extract_period(subject, dir_name):
    """Extract report period from subject line."""
    if not subject:
        return "Unknown"

    # Try QX YYYY (also handle O4 typo for Q4)
    m = re.search(r'[QO](\d)\s*[-–]?\s*(\d{4})', subject, re.I)
    if m:
        return f"Q{m.group(1)} {m.group(2)}"

    # Try QX without year (e.g. "Q2 Update")
    m = re.search(r'[QO](\d)\s+(?:Update|Report)', subject, re.I)
    if m:
        # Get year from directory name
        m2 = re.match(r'(\d{4})-', dir_name)
        year = m2.group(1) if m2 else ""
        return f"Q{m.group(1)} {year}".strip()

    # Try "רבעון X, YYYY" or "רבעון X YYYY"
    m = re.search(r'רבעון\s+(\d+),?\s*(\d{4})', subject)
    if m:
        return f"Q{m.group(1)} {m.group(2)}"

    # Try "רבעון X-YYYY"
    m = re.search(r'רבעון\s+(\d+)\s*[-–]\s*(\d{4})', subject)
    if m:
        return f"Q{m.group(1)} {m.group(2)}"

    # Try Hebrew month + year: "דיווח <month> YYYY"
    for heb, eng in HEBREW_MONTHS.items():
        m = re.search(rf'{heb}\s+(\d{{4}})', subject)
        if m:
            return f"{eng} {m.group(1)}"
        m = re.search(rf'{heb}\s*[-–]\s*(\d{{4}})', subject)
        if m:
            return f"{eng} {m.group(1)}"

    # Try English month + year
    m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', subject, re.I)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"

    # Try "H1/H2 YYYY"
    m = re.search(r'H(\d)\s+(\d{4})', subject, re.I)
    if m:
        return f"H{m.group(1)} {m.group(2)}"

    # Try MM/YYYY or M.YYYY
    m = re.search(r'(\d{1,2})[/.](\d{4})', subject)
    if m:
        month_num = int(m.group(1))
        year = m.group(2)
        if 1 <= month_num <= 12:
            month_names = ["January", "February", "March", "April", "May", "June",
                           "July", "August", "September", "October", "November", "December"]
            return f"{month_names[month_num - 1]} {year}"

    # Try "M-YY" or "M.YY" (e.g. "2.25" for Feb 2025)
    m = re.search(r'(\d{1,2})[-.](\d{2})(?:\b|\.pdf)', subject)
    if m:
        month_num = int(m.group(1))
        year_short = int(m.group(2))
        if 1 <= month_num <= 12 and 19 <= year_short <= 30:
            year = 2000 + year_short
            month_names = ["January", "February", "March", "April", "May", "June",
                           "July", "August", "September", "October", "November", "December"]
            return f"{month_names[month_num - 1]} {year}"

    # Try "דיווח חודשי" with no explicit period - check attachments
    # Try standalone year
    m = re.search(r'\b(20\d{2})\b', subject)
    if m:
        return m.group(1)

    # Fallback: year from directory
    m = re.match(r'(\d{4})-', dir_name)
    if m:
        return m.group(1)

    return "Unknown"


def extract_date_from_dir(dir_name):
    """Extract date from directory name."""
    m = re.match(r'(\d{4}-\d{2}-\d{2})', dir_name)
    return m.group(1) if m else "Unknown"


def extract_sender(from_field):
    """Clean up sender field."""
    if not from_field:
        return "Unknown"
    m = re.search(r'<([^>]+)>', from_field)
    if m:
        return m.group(1)
    return from_field.strip().strip('"')


def main():
    alias_map = load_knowledge()

    rows = []
    for dir_name in sorted(os.listdir(INBOX_DIR)):
        dir_path = os.path.join(INBOX_DIR, dir_name)
        if not os.path.isdir(dir_path):
            continue
        cls_path = os.path.join(dir_path, ".classification")
        if not os.path.exists(cls_path):
            continue

        try:
            with open(cls_path, "r", encoding="utf-8") as f:
                cls = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        category = cls.get("category", "")
        if category not in ("quarterly_report", "monthly_report"):
            continue

        subject = cls.get("subject", "")
        from_field = cls.get("from", "")
        attachments = cls.get("attachments", [])

        pdf_files = [a for a in attachments if a.lower().endswith(".pdf")]
        has_pdf = len(pdf_files) > 0

        investment = extract_investment_name(subject, attachments, alias_map)
        period = extract_period(subject, dir_name)
        date = extract_date_from_dir(dir_name)
        sender = extract_sender(from_field)

        rows.append({
            "date": date,
            "investment": investment,
            "period": period,
            "has_pdf": has_pdf,
            "pdf_files": "; ".join(pdf_files) if pdf_files else "",
            "sender": sender,
            "source_dir": dir_name,
        })

    # Write CSV
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "investment", "period", "has_pdf", "pdf_files", "sender", "source_dir"
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} reports to {OUTPUT_PATH}")

    # Summary
    investments = {}
    for r in rows:
        inv = r["investment"]
        investments[inv] = investments.get(inv, 0) + 1
    print(f"\nInvestments with reports ({len(investments)} unique):")
    for inv, count in sorted(investments.items(), key=lambda x: -x[1]):
        print(f"  {inv}: {count}")

    unknowns = [r for r in rows if r["investment"] == "Unknown"]
    if unknowns:
        print(f"\n{len(unknowns)} reports with unresolved investment name:")
        for r in unknowns:
            print(f"  {r['date']} | {r['source_dir']}")


if __name__ == "__main__":
    main()
