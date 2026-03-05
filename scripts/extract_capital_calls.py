#!/usr/bin/env python3
"""Extract structured data from emails classified as capital_call."""

import codecs
import csv
import email
import email.policy
import json
import os
import re
from html.parser import HTMLParser

# Register codec aliases to avoid decode errors
try:
    codecs.lookup("iso-8859-8-i")
except LookupError:
    iso8859_8 = codecs.lookup("iso-8859-8")
    codecs.register(lambda name: iso8859_8 if name == "iso-8859-8-i" else None)

try:
    codecs.lookup("unknown-8bit")
except LookupError:
    latin1 = codecs.lookup("latin-1")
    def custom_search(name):
        if name == "unknown-8bit":
            return latin1
        return None
    codecs.register(custom_search)

INBOX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inbox")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUTPUT = os.path.join(DATA_DIR, "email-capital-calls.csv")
KNOWLEDGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge.json")

# Extra aliases not in knowledge.json (abbreviations used in emails)
EXTRA_ALIASES = {
    "סטארדום": "KDC Media Fund / Stardom Ventures",
    "stardom": "KDC Media Fund / Stardom Ventures",
    "stardom media ventures, l.p": "KDC Media Fund / Stardom Ventures",
    "stardom media ventures,l.p": "KDC Media Fund / Stardom Ventures",
    "stardom media ventures": "KDC Media Fund / Stardom Ventures",
    "אימפקט נדל\"ן": "Impact Real Estate FOF",
    "אימפקט נדל\"ן אגד קרנות": "Impact Real Estate FOF",
    "impact real estate fund of funds": "Impact Real Estate FOF",
    "impact real estate": "Impact Real Estate FOF",
    "אימפקט חוב": "Impact Debt FOF",
    "מגורים וינה 1": "Residence Vienna 1",
    "עסקת מגורים וינה 1": "Residence Vienna 1",
    "viola credit alf iii": "Viola Credit ALF III",
    "alt group opportunities fund, lp": "Alt Group Octo Opportunities Fund",
    "alt group opportunities fund": "Alt Group Octo Opportunities Fund",
    "alt group opportunity fund, lp": "Alt Group Octo Opportunities Fund",
    "alt group opportunity fund": "Alt Group Octo Opportunities Fund",
    "value isf feeder fund iii": "ISF III Feeder Fund, L.P",
    "value isf": "ISF III Feeder Fund, L.P",
    "הראל פיננסים": "Harel Hamagen USA",
    "הראל פיננסים אלטרנטיב": "Harel Hamagen USA",
    "harel finance": "Harel Hamagen USA",
    "harel finance alternatives": "Harel Hamagen USA",
}


class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []

    def handle_data(self, data):
        self.result.append(data)

    def get_text(self):
        return " ".join(self.result)


def load_knowledge():
    with open(KNOWLEDGE, encoding="utf-8") as f:
        return json.load(f)


def build_alias_map(knowledge):
    """Build mapping from Hebrew/alias names to canonical English investment names.

    Priority: extra aliases > investments > entities (later entries overwrite earlier).
    """
    alias_map = {}
    # Entities first (lowest priority)
    for key, ent in knowledge.get("entities", {}).items():
        if ent.get("hebrew_name"):
            alias_map[ent["hebrew_name"].lower()] = ent["name"]
    # Investments (higher priority, overwrite entity matches)
    for key, inv in knowledge.get("investments", {}).items():
        name = inv["name"]
        alias_map[key.lower()] = name
        alias_map[name.lower()] = name
        for alias in inv.get("aliases", []):
            alias_map[alias.lower()] = name
        if inv.get("hebrew_name"):
            alias_map[inv["hebrew_name"].lower()] = name
    # Extra aliases (highest priority)
    for alias, canonical in EXTRA_ALIASES.items():
        alias_map[alias.lower()] = canonical
    return alias_map


def get_email_body(eml_path):
    """Extract plain text body from .eml file, falling back to HTML text extraction."""
    with open(eml_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)

    body = msg.get_body(preferencelist=("plain",))
    if body:
        try:
            return body.get_content()
        except Exception:
            pass

    body = msg.get_body(preferencelist=("html",))
    if body:
        try:
            html_content = body.get_content()
            extractor = HTMLTextExtractor()
            extractor.feed(html_content)
            return extractor.get_text()
        except Exception:
            pass

    for part in msg.walk():
        ct = part.get_content_type()
        if ct == "text/plain":
            try:
                return part.get_content()
            except Exception:
                continue
        elif ct == "text/html":
            try:
                html_content = part.get_content()
                extractor = HTMLTextExtractor()
                extractor.feed(html_content)
                return extractor.get_text()
            except Exception:
                continue

    return ""


def resolve_name(name, alias_map):
    """Try to resolve a name via alias map with exact and partial matching."""
    if not name:
        return ""
    name_lower = name.lower().strip()
    # Exact match
    resolved = alias_map.get(name_lower)
    if resolved:
        return resolved
    # Partial match - longest alias first
    for alias, canonical in sorted(alias_map.items(), key=lambda x: -len(x[0])):
        if len(alias) >= 4 and (alias in name_lower or name_lower in alias):
            return canonical
    return name


def extract_investment_name(subject, body, alias_map):
    """Extract and resolve investment name from subject and body."""
    # Clean subject
    clean_subj = re.sub(r"^(?:RE:\s*|FW(?:D)?:\s*)+", "", subject, flags=re.IGNORECASE).strip()

    # Pattern: "Apex Israel Capital Call Notification - FUND_NAME"
    apex_match = re.search(
        r"Apex Israel (?:Capital Call|Document) Notification\s*[-–]\s*(.+?)(?:\s*[-–]\s*\d+)?$",
        clean_subj, re.IGNORECASE
    )
    if apex_match:
        name = apex_match.group(1).strip()
        resolved = resolve_name(name, alias_map)
        if resolved:
            return resolved

    # Pattern: Hebrew "קריאה לכסף X" or "קריאה ראשונה לכסף לקרן X"
    fund_call = re.search(r"קריאה\s+(?:\S+\s+)?לכסף\s+(?:לקרן\s+|בקרן\s+|עבור\s+|עסקת\s+)?(.+?)$", clean_subj)
    if fund_call:
        name = fund_call.group(1).strip()
        # Strip leading dash and person names
        name = re.sub(r"^[-–]\s*", "", name).strip()
        # Skip if the result is just a person name
        if name and "תמר מרש" not in name and "אדם מרש" not in name:
            resolved = resolve_name(name, alias_map)
            if resolved:
                return resolved

    # Pattern: "קריאה לכסף קרן X" (fund X)
    fund_match = re.search(r"קרן\s+(.+?)(?:\s*[-–]|\s*$)", clean_subj)
    if fund_match:
        name = fund_match.group(1).strip()
        resolved = resolve_name(name, alias_map)
        if resolved:
            return resolved

    # Pattern: "Person - Fund Name - Capital Call" or "Person - Fund - Nth Capital Call Notice"
    person_fund = re.search(
        r"(?:תמר\s+(?:ו?אדם\s+)?מרש|אדם\s+מרש|Tamar Marash)\s*[-–]\s*(.+?)(?:\s*[-–]\s*(?:\S+\s+)?Capital\s+Call|\s*$)",
        clean_subj, re.IGNORECASE
    )
    if person_fund:
        name = person_fund.group(1).strip()
        # Remove call notice text
        name = re.sub(r"\s*[-–]\s*(?:\w+\s+)?Capital\s+Call\s+Notice.*$", "", name, flags=re.IGNORECASE).strip()
        resolved = resolve_name(name, alias_map)
        if resolved:
            return resolved

    # Pattern: "FUND - Capital Call" or "FUND - First Close and First Drawdown"
    fund_cc = re.search(
        r"(.+?)\s*[-–]\s*(?:First\s+Close|Capital\s+Call|.*(?:drawdown|close))",
        clean_subj, re.IGNORECASE
    )
    if fund_cc:
        name = fund_cc.group(1).strip()
        # Strip person prefix
        name = re.sub(r"^(?:תמר\s+(?:ו?אדם\s+)?מרש|אדם\s+מרש)\s*[-–]\s*", "", name).strip()
        # Skip if the name is just a person name or "מכתב קריאה לכסף"
        if name and "מכתב קריאה לכסף" not in name and "תמר מרש" not in name:
            resolved = resolve_name(name, alias_map)
            if resolved:
                return resolved

    # Pattern: "הודעה לקריאה לכסף - Fund Name"
    notice_match = re.search(r"הודעה לקריאה לכסף\s*[-–]\s*(.+?)$", clean_subj)
    if notice_match:
        name = notice_match.group(1).strip()
        resolved = resolve_name(name, alias_map)
        if resolved:
            return resolved

    # Pattern: "מכתב קריאה לכסף - PERSON" (capital call letter - person name)
    # The fund name is not in the subject, fall through to body/from search
    if "מכתב קריאה לכסף" in clean_subj:
        pass  # Fall through to body search

    # Subject contains known investment name (longest first)
    subj_lower = clean_subj.lower()
    for alias, canonical in sorted(alias_map.items(), key=lambda x: -len(x[0])):
        if len(alias) >= 4 and alias in subj_lower:
            return canonical

    # Body patterns
    # "השקעתך בקרן X" (your investment in fund X)
    inv_match = re.search(r"השקעתך בקרן\s+(.+?)(?:\s*$|\n|,)", body)
    if inv_match:
        name = inv_match.group(1).strip()
        resolved = resolve_name(name, alias_map)
        if resolved:
            return resolved

    # "בהשקעתך בקרן X" (in your investment in fund X)
    inv_match2 = re.search(r"בהשקעתך בקרן\s+(.+?)(?:\s*,|\s*$|\n)", body)
    if inv_match2:
        name = inv_match2.group(1).strip()
        resolved = resolve_name(name, alias_map)
        if resolved:
            return resolved

    # Check body for known names (longest alias first)
    body_lower = body[:2000].lower()
    for alias, canonical in sorted(alias_map.items(), key=lambda x: -len(x[0])):
        if len(alias) >= 5 and alias in body_lower:
            return canonical

    return ""


def extract_amount_and_currency(subject, body):
    """Extract capital call amount and currency.

    Looks for the specific transfer amount, not the total commitment.
    Prioritizes patterns like 'יש להעביר $X' or 'סכום ההעברה הנדרש'.
    """
    combined = subject + "\n" + body

    # Hebrew: "יש להעביר $X" or "יש להעביר X,XXX $"
    transfer_match = re.search(r"יש להעביר\s+\$?\s*([\d,]+(?:\.\d+)?)\s*\$?", combined)
    if transfer_match:
        amount = transfer_match.group(1).replace(",", "")
        return amount, "USD"
    transfer_match2 = re.search(r"יש להעביר\s+\$([\d,]+(?:\.\d+)?)", combined)
    if transfer_match2:
        amount = transfer_match2.group(1).replace(",", "")
        return amount, "USD"

    # Hebrew: "סכום ההעברה הנדרש הינו : X אירו" or "סכום ההעברה הנדרש : X€"
    transfer_req = re.search(
        r"סכום ההעברה הנדרש\s*(?:הינו)?\s*:?\s*\$?\s*([\d,]+(?:\.\d+)?)\s*(?:אירו|€|EUR)",
        combined, re.IGNORECASE
    )
    if transfer_req:
        amount = transfer_req.group(1).replace(",", "")
        return amount, "EUR"
    transfer_req_usd = re.search(
        r"סכום ההעברה הנדרש\s*(?:הינו)?\s*:?\s*\$\s*([\d,]+(?:\.\d+)?)",
        combined
    )
    if transfer_req_usd:
        amount = transfer_req_usd.group(1).replace(",", "")
        return amount, "USD"

    # Hebrew: "יש להעביר X€" or "יש להעביר X אירו"
    transfer_eur = re.search(r"יש להעביר\s+(?:בהקדם\s+)?(?:את\s+)?(?:מלוא\s+)?(?:סכום\s+ה(?:השקעה|התחייבות))?", combined)
    # Look for the amount near "יש להעביר" for EUR
    transfer_eur2 = re.search(r"יש להעביר\s+.*?([\d,]+)\s*(?:אירו|€)", combined)
    if transfer_eur2:
        amount = transfer_eur2.group(1).replace(",", "")
        return amount, "EUR"

    # English: "capital call of FUND, due by DATE" - amount in attachment typically
    # English: "please find attached" - amount usually in PDF

    # Pattern: $X,XXX in body (but not fund size like $480M)
    # Skip amounts immediately followed by M/B (millions/billions)
    for m in re.finditer(r"\$\s*([\d,]+(?:\.\d+)?)\s*([MBmb]?)", combined):
        if m.group(2):  # Skip $480M etc.
            continue
        amount_str = m.group(1).replace(",", "")
        try:
            val = float(amount_str)
            if val > 0 and val < 10_000_000:
                return amount_str, "USD"
        except ValueError:
            continue

    # Pattern: X€ or X אירו
    eur_match = re.search(r"([\d,]+(?:\.\d+)?)\s*(?:€|אירו)", combined)
    if eur_match:
        amount = eur_match.group(1).replace(",", "")
        try:
            val = float(amount)
            if val > 0 and val < 10_000_000:
                return amount, "EUR"
        except ValueError:
            pass
    eur_match2 = re.search(r"€\s*([\d,]+(?:\.\d+)?)", combined)
    if eur_match2:
        amount = eur_match2.group(1).replace(",", "")
        try:
            val = float(amount)
            if val > 0 and val < 10_000_000:
                return amount, "EUR"
        except ValueError:
            pass

    return "", ""


def extract_call_number(subject, body):
    """Extract capital call number from subject or body."""
    combined = subject + "\n" + body[:500]

    # Pattern: CC #N or CC#N or Capital Call #N (in subject preferably)
    cc_num = re.search(r"(?:CC|Capital\s*Call)\s*#\s*(\d+)", subject, re.IGNORECASE)
    if cc_num:
        return cc_num.group(1)

    # Ordinal English in subject: "twelfth Capital Call"
    ordinals = {
        "first": "1", "second": "2", "third": "3", "fourth": "4",
        "fifth": "5", "sixth": "6", "seventh": "7", "eighth": "8",
        "ninth": "9", "tenth": "10", "eleventh": "11", "twelfth": "12",
        "thirteenth": "13", "thirteen": "13", "fourteenth": "14",
        "fifteenth": "15", "sixteenth": "16",
    }
    for word, num in ordinals.items():
        if re.search(rf"\b{word}\b", combined, re.IGNORECASE):
            return num

    # Hebrew ordinals
    hebrew_ordinals = {
        "ראשונה": "1", "שנייה": "2", "שלישית": "3", "רביעית": "4",
        "חמישית": "5", "שישית": "6", "שביעית": "7", "שמינית": "8",
        "תשיעית": "9", "עשירית": "10",
    }
    for word, num in hebrew_ordinals.items():
        if word in combined:
            return num

    # Pattern: CC #N in combined
    cc_num2 = re.search(r"(?:CC|Capital\s*Call)\s*#?\s*(\d+)", combined, re.IGNORECASE)
    if cc_num2:
        return cc_num2.group(1)

    # Pattern: "Xth call"
    nth = re.search(r"(\d+)(?:st|nd|rd|th)\s+(?:capital\s+)?call", combined, re.IGNORECASE)
    if nth:
        return nth.group(1)

    return ""


def extract_due_date(subject, body):
    """Extract due date from body text."""
    combined = subject + "\n" + body

    # Pattern: "עד ליום DD.MM.YYYY"
    he_date = re.search(r"עד\s+(?:ליום\s+)?(\d{1,2})[./](\d{1,2})[./](\d{4})", combined)
    if he_date:
        d, m, y = he_date.group(1), he_date.group(2), he_date.group(3)
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

    # Pattern: "יאוחר מDD.MM.YYYY"
    he_date2 = re.search(r"יאוחר\s+מ-?(\d{1,2})[./](\d{1,2})[./](\d{4})", combined)
    if he_date2:
        d, m, y = he_date2.group(1), he_date2.group(2), he_date2.group(3)
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

    # Pattern: "due by Month DDth, YYYY"
    en_date = re.search(
        r"due\s+(?:by\s+)?(\w+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})",
        combined, re.IGNORECASE
    )
    if en_date:
        month_name, day, year = en_date.group(1), en_date.group(2), en_date.group(3)
        months = {
            "january": "01", "february": "02", "march": "03", "april": "04",
            "may": "05", "june": "06", "july": "07", "august": "08",
            "september": "09", "october": "10", "november": "11", "december": "12"
        }
        m = months.get(month_name.lower(), "")
        if m:
            return f"{year}-{m}-{day.zfill(2)}"

    return ""


def extract_email_date(eml_path):
    """Extract the Date header from the email."""
    with open(eml_path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=email.policy.default)
    date_str = msg.get("Date", "")
    if date_str:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def determine_confidence(investment, amount, call_number):
    """Determine extraction confidence."""
    if investment and amount:
        return "high"
    elif investment:
        return "medium"
    else:
        return "low"


def main():
    knowledge = load_knowledge()
    alias_map = build_alias_map(knowledge)

    os.makedirs(DATA_DIR, exist_ok=True)

    results = []
    dirs = sorted(os.listdir(INBOX))

    for dirname in dirs:
        dirpath = os.path.join(INBOX, dirname)
        if not os.path.isdir(dirpath):
            continue
        cls_file = os.path.join(dirpath, ".classification")
        if not os.path.exists(cls_file):
            continue

        with open(cls_file, encoding="utf-8") as f:
            cls = json.load(f)

        if cls.get("category") != "capital_call":
            continue

        subject = cls.get("subject", "")
        from_addr = cls.get("from", "")

        # Skip fraud warnings - not actual capital calls
        if "fraud" in subject.lower() or "security notice" in subject.lower():
            continue

        eml_path = os.path.join(dirpath, "message.raw.eml")
        if not os.path.exists(eml_path):
            continue

        body = get_email_body(eml_path)
        email_date = extract_email_date(eml_path)

        investment = extract_investment_name(subject, body, alias_map)
        amount, currency = extract_amount_and_currency(subject, body)
        call_number = extract_call_number(subject, body)
        due_date = extract_due_date(subject, body)
        confidence = determine_confidence(investment, amount, call_number)

        results.append({
            "date": email_date,
            "investment": investment,
            "amount": amount,
            "currency": currency,
            "call_number": call_number,
            "due_date": due_date,
            "source_dir": dirname,
            "confidence": confidence,
        })

    # Sort by date
    results.sort(key=lambda r: r["date"])

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "investment", "amount", "currency",
            "call_number", "due_date", "source_dir", "confidence"
        ])
        writer.writeheader()
        writer.writerows(results)

    print(f"Extracted {len(results)} capital call records to {OUTPUT}")
    for r in results:
        inv = r["investment"] or "(unknown)"
        amt = f"{r['currency']} {r['amount']}" if r["amount"] else "(no amount)"
        call = f"call #{r['call_number']}" if r["call_number"] else ""
        due = f"due {r['due_date']}" if r["due_date"] else ""
        conf = r["confidence"]
        print(f"  {r['date']}  {inv:50s}  {amt:20s}  {call:10s}  {due:15s}  [{conf}]")


if __name__ == "__main__":
    main()
