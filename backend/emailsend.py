import base64
import json
import mimetypes
import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from googleapiclient.discovery import build
from gmail_auth import load_credentials


def _clean_header(value: str) -> str:
    """Strip CR/LF so user input cannot inject extra MIME headers."""
    return " ".join(str(value or "").splitlines()).strip()


def _safe_filename(name: str) -> str:
    name = os.path.basename(str(name or "attachment"))
    name = "".join(ch for ch in name if ch not in '\r\n"\\')
    return name or "attachment"



def send_email(payload: dict, recipient: str, email: str = None):
    """
    Sends a QuMail encrypted payload to the recipient.
    For multi_file bundles: each encrypted file is a separate MIME attachment.
    The bundle metadata (including plain_text) goes in qumail_encrypted.json.
    """
    creds = load_credentials(email or None)
    service = build("gmail", "v1", credentials=creds)

    message = MIMEMultipart()
    message["to"] = _clean_header(recipient)
    message["subject"] = _clean_header(
        payload.get("subject") or "QuMail Secure Encrypted Message"
    )

    body_text = (
        "You have received a secure QuMail message.\n\n"
        "This email contains an encrypted payload attachment.\n"
        "Open it in QuMail to decrypt.\n"
    )
    message.attach(MIMEText(body_text, "plain"))

    if payload.get("type") == "multi_file":
        # For bundles: store each file payload as a separate attachment
        # Keep only metadata + plain_text in the main JSON
        file_payloads = payload.get("files", [])
        meta = {
            "type": "multi_file",
            "file_count": len(file_payloads),
            "subject": payload.get("subject", ""),
            "file_indices": list(range(len(file_payloads)))
        }
        # Main metadata JSON
        json_meta = json.dumps(meta, indent=2).encode()
        att = MIMEApplication(json_meta, _subtype="json")
        att.add_header("Content-Disposition", "attachment", filename="qumail_encrypted.json")
        message.attach(att)

        # Each file payload as a separate attachment
        for i, fp in enumerate(file_payloads):
            fp_json = json.dumps(fp, indent=2).encode()
            fp_att = MIMEApplication(fp_json, _subtype="json")
            fp_att.add_header("Content-Disposition", "attachment", filename=f"qumail_file_{i}.json")
            message.attach(fp_att)
    else:
        # Single payload — original behaviour
        json_payload = json.dumps(payload, indent=2).encode()
        attachment = MIMEApplication(json_payload, _subtype="json")
        attachment.add_header("Content-Disposition", "attachment", filename="qumail_encrypted.json")
        message.attach(attachment)

    return _execute_gmail_send(service, message)


def build_plain_message(recipient: str, subject: str, body: str,
                        attachments: list = None) -> MIMEMultipart:
    """Build an ordinary (unencrypted) Gmail MIME message with attachments."""
    message = MIMEMultipart()
    message["to"] = _clean_header(recipient)
    message["subject"] = _clean_header(subject or "(no subject)")
    message.attach(MIMEText(body or "", "plain", "utf-8"))

    for item in attachments or []:
        content = item.get("content") or b""
        filename = _safe_filename(item.get("filename"))
        mime_type = item.get("mime_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        maintype, _, subtype = mime_type.partition("/")
        part = MIMEBase(maintype or "application", subtype or "octet-stream")
        part.set_payload(content)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        message.attach(part)

    return message


def send_plain_message(recipient: str, subject: str, body: str,
                       attachments: list = None, email: str = None):
    """Send an ordinary email. The caller must have chosen plaintext explicitly."""
    creds = load_credentials(email or None)
    if not creds:
        raise RuntimeError("No authenticated Google account.")
    service = build("gmail", "v1", credentials=creds)
    message = build_plain_message(recipient, subject, body, attachments)
    return _execute_gmail_send(service, message)


def send_plain_file(recipient: str, file_content: bytes, filename: str, email: str = None):
    """
    Sends a file 'as is' via Gmail OAuth without encryption.
    """
    creds = load_credentials(email or None)
    service = build("gmail", "v1", credentials=creds)

    safe_name = _safe_filename(filename)
    message = MIMEMultipart()
    message["to"] = _clean_header(recipient)
    message["subject"] = _clean_header(f" QuMail Shared File: {safe_name}")

    body_text = f"You have received a file via QuMail: {safe_name}\nNote: This file was sent without additional Quantum encryption."
    message.attach(MIMEText(body_text, "plain"))

    # Determine MIME type or default to application/octet-stream
    attachment = MIMEApplication(file_content)
    attachment.add_header("Content-Disposition", "attachment", filename=safe_name)
    message.attach(attachment)

    return _execute_gmail_send(service, message)

def _execute_gmail_send(service, message):
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    result = service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
    return result
