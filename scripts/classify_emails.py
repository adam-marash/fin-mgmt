"""Classify inbox emails into categories and write .classification JSON files.

Usage:
    python classify_emails.py [--sender PATTERN] [--dry-run]

Categories:
    distribution_notice - Distribution/dividend payment notification
    capital_call - Capital call / drawdown request
    quarterly_report - Quarterly/periodic report or financial statement
    monthly_report - Monthly update/report
    tax_form - K-1, tax certificate, tax filing confirmation
    kyc_onboarding - KYC, subscription docs, W8-BEN, onboarding forms
    invoice - Invoice or receipt (חשבונית, קבלה)
    capital_statement - Capital account statement / position summary
    wire_confirmation - Wire/transfer confirmation
    portal_notification - Investor portal link/notification (no substance)
    correspondence - General correspondence, replies, FYIs
    marketing - Newsletter, marketing, general updates
    pitch_presentation - Investment pitch, presentation, meeting summary
    signing_request - Forms to sign, compliance forms
    meeting_scheduling - Zoom/meeting coordination
    investment_update - Investment-specific operational update
    fee_payment - Fee/payment notice
    unknown - Cannot classify
"""

import email
import email.header
import codecs
import json
import os
import sys
from pathlib import Path

# Register Hebrew encoding aliases
for alias in ('iso-8859-8-i', 'unknown-8bit'):
    try:
        codecs.lookup(alias)
    except LookupError:
        fallback = 'iso-8859-8' if 'hebrew' in alias or '8859-8' in alias else 'utf-8'
        codecs.register(lambda name, a=alias, fb=fallback: codecs.lookup(fb) if name == a else None)


def safe_decode_subject(msg):
    raw = msg.get('Subject', '')
    if not raw:
        return ''
    try:
        parts = email.header.decode_header(raw)
        decoded = []
        for part, enc in parts:
            if isinstance(part, bytes):
                try:
                    decoded.append(part.decode(enc or 'utf-8', errors='replace'))
                except (LookupError, UnicodeDecodeError):
                    decoded.append(part.decode('utf-8', errors='replace'))
            else:
                decoded.append(part)
        return ' '.join(decoded).strip()
    except Exception:
        return str(raw)[:200]


def safe_decode_from(msg):
    raw = msg.get('From', '')
    try:
        parts = email.header.decode_header(raw)
        decoded = []
        for part, enc in parts:
            if isinstance(part, bytes):
                try:
                    decoded.append(part.decode(enc or 'utf-8', errors='replace'))
                except (LookupError, UnicodeDecodeError):
                    decoded.append(part.decode('utf-8', errors='replace'))
            else:
                decoded.append(part)
        return ' '.join(decoded).strip()
    except Exception:
        return str(raw)[:200]


def get_body_preview(msg, max_chars=500):
    """Extract plain text body preview."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == 'text/plain':
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or 'utf-8'
                    try:
                        return payload.decode(charset, errors='replace')[:max_chars]
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode('utf-8', errors='replace')[:max_chars]
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or 'utf-8'
            try:
                return payload.decode(charset, errors='replace')[:max_chars]
            except (LookupError, UnicodeDecodeError):
                return payload.decode('utf-8', errors='replace')[:max_chars]
    return ''


CATEGORY_KEYWORDS = {
    'distribution_notice': [
        'distribution', 'חלוקה', 'dist', 'dividend', 'דיבידנד',
        'distribution wire', 'distribution notice',
    ],
    'quarterly_report': [
        'quarterly report', 'דוח רבעוני', 'סיכום רבעון', 'q1 ', 'q2 ', 'q3 ', 'q4 ',
        'financial report', 'דיווח רבעוני', 'quarterly update',
        'q1-', 'q2-', 'q3-', 'q4-', 'executive summary', 'update - stardom',
        'financial statements as of', 'דוח שנתי', 'year in review',
        'דיווח למשקיעים', 'עדכון רבעון',
    ],
    'monthly_report': [
        'דיווח חודשי', 'monthly report', 'monthly update', 'דיווח חודש',
        'דיווח יוני', 'דיווח יולי', 'עדכון אוגוסט', 'עדכון אוקטובר',
        'עדכון נובמבר', 'עדכון דצמבר', 'עדכון ינואר', 'עדכון פברואר',
        'עדכון מרץ', 'עדכון אפריל', 'עדכון מאי', 'עדכון ספטמבר',
    ],
    'tax_form': [
        'k-1', 'k1', 'tax return', 'tax certificate', 'אישור מס',
        'efileservices', 'electronically filed', 'irs', '1065',
        'אישור ניכוי מס', 'נספח מס', '1042s', '1042-s', 'tax package',
        'tax report', 'ניכויים במקור', 'אישור רואה חשבון',
        'אישור תושב זר', 'הצהרת מעמד מס', 'הגשת דוח המס',
        'דוחות מס', 'מיסוי', 'מיסויים',
    ],
    'kyc_onboarding': [
        'kyc', 'הכר את הלקוח', 'w8-ben', 'w8ben', 'subscription',
        'טפסים להחתמה', 'מסמכי הצטרפות', 'הצטרפות', 'onboarding',
        'מינוי נאמן', 'הסכם שותפות', 'fatca', 'signature page',
        'welcome to corpro', 'final close', 'final closing',
        'מכתב למשקיעים',
    ],
    'invoice': [
        'חשבונית', 'קבלה', 'invoice', 'receipt', 'חשבון עיסקה',
        'invoice4u',
    ],
    'capital_statement': [
        'capital statement', 'capital account', 'דוח הון', 'position statement',
        'confirmation letter',
    ],
    'wire_confirmation': [
        'wire confirmation', 'אישור העברה', 'אישור הפקדה',
        'distribution wire', 'אישור תשלום', 'פרטי העברה',
    ],
    'portal_notification': [
        'magic link', 'apex israel document notification',
        'apex israel capital', 'investor portal',
    ],
    'marketing': [
        'in the loop', 'newsletter', 'special edition',
        'annual general meeting', 'introducing forte',
        'מדיניות הפרטיות',
    ],
    'pitch_presentation': [
        'מצגת', 'presentation', 'מצגות לעיונך', 'דף מוצר',
        'סיכום פגישה', 'לקראת סיום', 'market update',
        'סקירת חציון', 'סיכום שנת',
    ],
    'capital_call': [
        'capital call', 'drawdown', 'קריאה לכסף', 'קריאת הון',
        'first close', 'first drawdown', 'קריאה לכספי ההשקעה',
        'catch up', 'קריאה ראשונה',
    ],
    'signing_request': [
        'טפסים לחתימה', 'טפסים להחתמה', 'חתומים', 'לטיפולך',
        'טפסי כשירות', 'כשירות לקוח', 'הצהרת לקוח',
    ],
    'meeting_scheduling': [
        'זום', 'תיאום זום', 'תיאום פגישה', 'פגישה', 'zoom',
    ],
    'investment_update': [
        'עסקת', 'עדכון בעלי מניות', 'יצאה לדרך', 'סיכום תלת',
        'שינוי מסלול', 'יתרות מ', 'חשבון בנק להשקעה',
        'גאנט השקעות', 'קבלת מידע מקרן',
    ],
    'fee_payment': [
        'שכ"ט', 'תשלום',
    ],
}


def classify_email(subject, from_addr, body, attachments, dirname):
    """Rule-based classification. Returns (category, confidence, reason)."""
    subj_lower = subject.lower()
    from_lower = from_addr.lower()
    body_lower = body.lower()
    att_names = [a.lower() for a in attachments]
    all_text = f'{subj_lower} {from_lower} {" ".join(att_names)}'

    # Invoice4u is always invoice
    if 'invoice4u' in dirname or 'invoice4u' in from_lower:
        return 'invoice', 'high', 'invoice4u sender'

    # efileservices is always tax
    if 'efileservices' in dirname or 'efileservices' in from_lower:
        return 'tax_form', 'high', 'efileservices sender'

    # Apex portal notifications
    if 'apex israel document notification' in subj_lower:
        if any(a for a in attachments if a.endswith('.pdf')):
            return 'capital_statement', 'medium', 'Apex notification with PDF attachment'
        return 'portal_notification', 'high', 'Apex notification without attachment'

    if 'apex israel capital call' in subj_lower:
        return 'capital_call', 'high', 'Apex capital call notification'

    # Magic link = portal
    if 'magic link' in subj_lower:
        return 'portal_notification', 'high', 'portal magic link'

    # Check keyword matches
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        matched = []
        for kw in keywords:
            if kw in subj_lower:
                score += 3
                matched.append(f'subj:{kw}')
            elif kw in all_text:
                score += 1
                matched.append(f'text:{kw}')
        if score > 0:
            scores[cat] = (score, matched)

    if scores:
        best_cat = max(scores, key=lambda k: scores[k][0])
        score, matched = scores[best_cat]
        confidence = 'high' if score >= 3 else 'medium' if score >= 2 else 'low'
        return best_cat, confidence, f'keywords: {", ".join(matched)}'

    # Fallback heuristics
    if not subject and not attachments:
        return 'correspondence', 'low', 'no subject, no attachments'

    # Fortress with PDF = quarterly report typically
    if 'fortress' in dirname and any(a.endswith('.pdf') for a in attachments):
        return 'quarterly_report', 'medium', 'fortress sender with PDF'

    # Replies are correspondence
    if subj_lower.startswith('re:') or subj_lower.startswith('fw:'):
        return 'correspondence', 'low', 'reply/forward'

    return 'unknown', 'low', 'no matching rules'


def process_inbox(sender_pattern=None, dry_run=False):
    inbox = Path('inbox')
    processed = 0
    skipped = 0
    results = {}

    for d in sorted(inbox.iterdir()):
        if not d.is_dir():
            continue
        if (d / '.flags').exists():
            skipped += 1
            continue
        if (d / '.classification').exists():
            skipped += 1
            continue
        if sender_pattern and sender_pattern not in d.name:
            continue

        eml_path = d / 'message.raw.eml'
        if not eml_path.exists():
            continue

        with open(eml_path, 'rb') as f:
            msg = email.message_from_bytes(f.read())

        subject = safe_decode_subject(msg)
        from_addr = safe_decode_from(msg)
        body = get_body_preview(msg)
        attachments = [f.name for f in d.iterdir()
                       if f.name != 'message.raw.eml' and not f.name.startswith('.')]

        category, confidence, reason = classify_email(
            subject, from_addr, body, attachments, d.name)

        classification = {
            'category': category,
            'confidence': confidence,
            'reason': reason,
            'subject': subject[:200],
            'from': from_addr[:200],
            'attachments': attachments,
        }

        if not dry_run:
            with open(d / '.classification', 'w') as f:
                json.dump(classification, f, ensure_ascii=False, indent=2)

        results.setdefault(category, []).append(d.name)
        processed += 1

    print(f'Processed: {processed}, Skipped (already done): {skipped}')
    print()
    for cat in sorted(results, key=lambda k: -len(results[k])):
        print(f'{cat}: {len(results[cat])}')
    print()
    return results


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sender', help='Filter by sender pattern in dirname')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    process_inbox(sender_pattern=args.sender, dry_run=args.dry_run)
