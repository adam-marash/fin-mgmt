"""Fetch labeled emails from Gmail and save to inbox/ as raw files."""

import base64
import email
import hashlib
import logging
import sys
import time
from dataclasses import dataclass, field
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

from slugify import slugify

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = PROJECT_ROOT / "config" / "google-service-account.json"
IMPERSONATE_EMAIL = "adam@marash.net"
GMAIL_LABEL = "Accounting Inbox"  # default, overridden by --label
INBOX_DIR = PROJECT_ROOT / "inbox"


# --- Data classes ---

@dataclass
class Attachment:
    filename: str
    data: bytes

@dataclass
class Email:
    msg_id: str
    subject: str
    sender: str
    date: str  # YYYY-MM-DD
    body_text: str | None = None
    body_html: str | None = None
    raw_bytes: bytes = b""
    attachments: list[Attachment] = field(default_factory=list)


# --- Gmail client ---

class GmailClient:
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    def __init__(self, credentials_path: Path, impersonate: str):
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_service_account_file(
            str(credentials_path), scopes=self.SCOPES
        ).with_subject(impersonate)
        self._service = build("gmail", "v1", credentials=creds)

    def list_message_ids(self, label: str) -> list[str]:
        label_id = self._get_label_id(label)
        msg_ids = []
        request = (
            self._service.users()
            .messages()
            .list(userId="me", labelIds=[label_id], maxResults=500)
        )
        while request is not None:
            response = request.execute()
            msg_ids.extend(m["id"] for m in response.get("messages", []))
            request = self._service.users().messages().list_next(request, response)
        return msg_ids

    def fetch_headers(self, msg_id: str) -> dict:
        """Fetch only metadata (subject, from, date) - no body or attachments."""
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=msg_id, format="metadata",
                 metadataHeaders=["Subject", "From", "Date"])
            .execute()
        )
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        return {
            "msg_id": msg_id,
            "subject": _decode_header(headers.get("Subject", "(no subject)")),
            "sender": _extract_email(headers.get("From", "")),
            "date": _parse_date(headers.get("Date", "")),
            "size_estimate": msg.get("sizeEstimate", 0),
        }

    def fetch_message(self, msg_id: str) -> Email:
        msg = (
            self._service.users()
            .messages()
            .get(userId="me", id=msg_id, format="raw")
            .execute()
        )
        raw_bytes = base64.urlsafe_b64decode(msg["raw"])
        return self._parse(msg_id, raw_bytes)

    def _get_label_id(self, label_name: str) -> str:
        results = self._service.users().labels().list(userId="me").execute()
        for lbl in results.get("labels", []):
            if lbl["name"] == label_name:
                return lbl["id"]
        raise ValueError(f"Gmail label not found: {label_name}")

    def _parse(self, msg_id: str, raw_bytes: bytes) -> Email:
        parsed = email.message_from_bytes(raw_bytes)
        sender = _extract_email(parsed.get("From", ""))
        date_str = _parse_date(parsed.get("Date", ""))
        subject = _decode_header(parsed.get("Subject", "(no subject)"))

        body_text = body_html = None
        attachments = []

        for part in parsed.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))

            if "attachment" in disp:
                fname = _decode_header(part.get_filename() or "attachment")
                data = part.get_payload(decode=True)
                if data:
                    attachments.append(Attachment(filename=fname, data=data))
            elif ctype == "text/plain" and body_text is None:
                payload = part.get_payload(decode=True)
                if payload:
                    body_text = payload.decode("utf-8", errors="replace")
            elif ctype == "text/html" and body_html is None:
                payload = part.get_payload(decode=True)
                if payload:
                    body_html = payload.decode("utf-8", errors="replace")

        return Email(
            msg_id=msg_id, subject=subject, sender=sender, date=date_str,
            body_text=body_text, body_html=body_html,
            raw_bytes=raw_bytes, attachments=attachments,
        )


# --- Helpers ---

def _decode_header(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            # Normalize charset aliases Python doesn't recognize
            cs = (charset or "utf-8").lower().replace("-i", "").replace("-e", "")
            if cs == "iso-8859-8":
                cs = "iso-8859-8"
            try:
                decoded.append(part.decode(cs, errors="replace"))
            except (LookupError, UnicodeDecodeError):
                decoded.append(part.decode("utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)

def _extract_email(from_header) -> str:
    from_header = str(from_header)
    if "<" in from_header and ">" in from_header:
        return from_header.split("<")[1].split(">")[0].strip().lower()
    return from_header.strip().lower()

def _parse_date(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        return parsedate_to_datetime(date_str).strftime("%Y-%m-%d")
    except Exception:
        return ""

def _sender_domain(addr: str) -> str:
    if "@" not in addr:
        return "unknown"
    domain = addr.split("@")[1].lower()
    parts = domain.split(".")
    return "-".join(parts[:-1]) if len(parts) > 1 else parts[0]

def _strip_attachments(raw_bytes: bytes) -> bytes:
    parsed = email.message_from_bytes(raw_bytes)
    if not parsed.is_multipart():
        return raw_bytes
    for part in parsed.walk():
        if "attachment" in str(part.get("Content-Disposition", "")):
            part.set_payload("[attachment stripped]")
            del part["Content-Transfer-Encoding"]
    return parsed.as_bytes()

def _file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


# --- Main ---

def _already_fetched(inbox: Path) -> set[str]:
    """Scan inbox folders for a .gmail_id marker file and return known IDs."""
    ids = set()
    for marker in inbox.rglob(".gmail_id"):
        ids.add(marker.read_text().strip())
    return ids

def scan(label: str = GMAIL_LABEL, limit: int = 0):
    """Headers-only scan - show what's on the server vs what we have locally."""
    gmail = GmailClient(CREDENTIALS_PATH, IMPERSONATE_EMAIL)
    all_ids = gmail.list_message_ids(label)
    already = _already_fetched(INBOX_DIR)

    pending = [mid for mid in all_ids if mid not in already]
    local_only = already - set(all_ids)

    print(f"Remote: {len(all_ids)} | Local: {len(already)} | New: {len(pending)} | Local-only: {len(local_only)}")
    print()

    to_scan = pending if not limit else pending[:limit]
    if not to_scan:
        print("Nothing new to scan.")
        return

    print(f"Fetching headers for {len(to_scan)} new emails...")
    for i, mid in enumerate(to_scan):
        h = gmail.fetch_headers(mid)
        size_kb = h['size_estimate'] // 1024
        att_indicator = f" ({size_kb}KB)" if size_kb > 10 else ""
        print(f"  {h['date']}  {h['sender']:<40s}  {h['subject'][:80]}{att_indicator}")
        if i < len(to_scan) - 1:
            time.sleep(0.2)


def fetch(label: str = GMAIL_LABEL, limit: int = 5):
    """Fetch full emails (with attachments) and save to inbox/."""
    gmail = GmailClient(CREDENTIALS_PATH, IMPERSONATE_EMAIL)
    all_ids = gmail.list_message_ids(label)
    already = _already_fetched(INBOX_DIR)
    pending = [mid for mid in all_ids if mid not in already]

    logger.info("%d labeled, %d already fetched, %d pending", len(all_ids), len(already), len(pending))

    if limit:
        pending = pending[:limit]

    for i, mid in enumerate(pending):
        eml = gmail.fetch_message(mid)
        logger.info("[%d/%d] %s - %s", i + 1, len(pending), eml.date, eml.subject)

        # Build folder name
        domain = _sender_domain(eml.sender)
        att_slug = ""
        if eml.attachments:
            att_slug = "-" + slugify(Path(eml.attachments[0].filename).stem, max_length=40, word_boundary=True)
        h = _file_hash(eml.raw_bytes)
        folder_name = f"{eml.date}-{domain}{att_slug}-{h}"
        folder = INBOX_DIR / folder_name
        folder.mkdir(parents=True, exist_ok=True)

        # Save stripped email
        (folder / "message.raw.eml").write_bytes(_strip_attachments(eml.raw_bytes))

        # Save attachments
        for att in eml.attachments:
            (folder / att.filename).write_bytes(att.data)

        # Marker so we know this gmail ID is already fetched
        (folder / ".gmail_id").write_text(eml.msg_id)

        # Brief pause between fetches
        if i < len(pending) - 1:
            time.sleep(0.5)

    print(f"Fetched {len(pending)} emails to {INBOX_DIR}/")


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Headers-only scan of new emails")
    scan_p.add_argument("--label", default=GMAIL_LABEL)
    scan_p.add_argument("--limit", type=int, default=0, help="Max to scan (0=all)")

    fetch_p = sub.add_parser("fetch", help="Fetch full emails to inbox/")
    fetch_p.add_argument("--label", default=GMAIL_LABEL)
    fetch_p.add_argument("--limit", type=int, default=5, help="Max to fetch (0=all)")

    args = parser.parse_args()
    if args.command == "scan":
        scan(label=args.label, limit=args.limit)
    elif args.command == "fetch":
        fetch(label=args.label, limit=args.limit)
