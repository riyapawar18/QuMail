"""Gmail API helpers: listing, MIME parsing, sanitisation, attachments.

This module never exposes OAuth tokens. It builds a Gmail client from the
server-side token store on each call and returns JSON-safe data only.
"""

import base64
import email.utils
import os
from typing import Optional

import bleach
from googleapiclient.discovery import build

from gmail_auth import load_credentials


class GmailError(RuntimeError):
    """Raised for a clean, user-facing Gmail failure."""


MAX_ATTACHMENT_BYTES = int(
    os.getenv("GMAIL_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024))
)

ALLOWED_TAGS = [
    "a", "b", "blockquote", "br", "code", "div", "em", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "i", "li", "ol", "p", "pre", "span", "strong",
    "table", "tbody", "td", "th", "thead", "tr", "u", "ul",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "td": ["colspan", "rowspan"],
    "th": ["colspan", "rowspan"],
}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

_PROFILE_HEADERS = ["From", "To", "Cc", "Subject", "Date", "Message-ID"]


def _service(email: Optional[str] = None):
    creds = load_credentials(email)
    if not creds:
        raise GmailError(
            "No authenticated Google account. Sign in with Google or use Guest Demo."
        )
    try:
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as exc:
        raise GmailError("Could not initialise the Gmail client: %s" % exc) from exc


def _decode(data: str) -> bytes:
    if not data:
        return b""
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding)
    except Exception:
        return b""


def sanitize_html(html: str) -> str:
    """Allow harmless formatting, strip scripts, frames, forms and remote images."""
    if not html:
        return ""
    cleaned = bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    # Remove empty anchor targets that could navigate the app itself.
    return cleaned.replace('href="#"', 'href=""')


def _strip_tags(html: str) -> str:
    return bleach.clean(html or "", tags=[], attributes={}, strip=True)


def _headers(headers) -> dict:
    out = {}
    for item in headers or []:
        name = (item.get("name") or "").lower()
        if name and name not in out:
            out[name] = item.get("value") or ""
    return out


def _iso_date(raw: str) -> str:
    if not raw:
        return ""
    try:
        return email.utils.parsedate_to_datetime(raw).isoformat()
    except Exception:
        return raw


def _walk_parts(part, collected: dict, message_id: str):
    mime_type = (part.get("mimeType") or "").lower()
    filename = (part.get("filename") or "").strip()
    body = part.get("body") or {}
    body_data = body.get("data")
    attachment_id = body.get("attachmentId")

    if filename or attachment_id:
        collected["attachments"].append({
            "id": attachment_id,
            "name": filename or "attachment",
            "mime_type": mime_type or "application/octet-stream",
            "size": int(body.get("size") or 0),
            "encrypted": filename.lower() == "qumail_encrypted.json",
            "download_url": (
                "/api/gmail/messages/%s/attachments/%s"
                % (message_id, attachment_id or "")
            ),
        })
    elif mime_type == "text/plain" and body_data:
        text = _decode(body_data).decode("utf-8", errors="replace")
        if text.strip():
            collected["text"].append(text)
    elif mime_type == "text/html" and body_data:
        collected["html"].append(_decode(body_data).decode("utf-8", errors="replace"))

    for child in part.get("parts") or []:
        _walk_parts(child, collected, message_id)


def _parse_address(raw: str) -> dict:
    name, addr = email.utils.parseaddr(raw or "")
    return {"name": name or addr, "email": addr}


def parse_full_message(message: dict, mark_unread: bool = False) -> dict:
    """Convert a Gmail `format=full` message into safe JSON."""
    message_id = message.get("id", "")
    payload = message.get("payload") or {}
    hdrs = _headers(payload.get("headers"))

    collected = {"text": [], "html": [], "attachments": []}
    _walk_parts(payload, collected, message_id)

    subject = hdrs.get("subject", "(No subject)")
    body_html = sanitize_html("\n".join(collected["html"]))
    body_text = "\n".join(collected["text"]).strip()
    if not body_text and body_html:
        body_text = _strip_tags(body_html)

    labels = message.get("labelIds") or []
    qumail = any(a.get("encrypted") for a in collected["attachments"]) or subject.lower().startswith("qumail")
    sender = _parse_address(hdrs.get("from", ""))
    recipient = _parse_address(hdrs.get("to", ""))

    return {
        "id": message_id,
        "thread_id": message.get("threadId"),
        "sender": hdrs.get("from", ""),
        "sender_name": sender["name"],
        "sender_email": sender["email"],
        "recipient": hdrs.get("to", ""),
        "recipient_name": recipient["name"],
        "recipient_email": recipient["email"],
        "cc": hdrs.get("cc", ""),
        "subject": subject,
        "date": _iso_date(hdrs.get("date", "")),
        "snippet": message.get("snippet", ""),
        "unread": "UNREAD" in labels and not mark_unread,
        "labels": labels,
        "body_text": body_text,
        "body_html": body_html,
        "attachments": collected["attachments"],
        "has_attachments": bool(collected["attachments"]),
        "qumail": qumail,
        "html_sanitized": True,
        "remote_images_blocked": True,
    }


def list_messages(folder: str = "inbox", page_token: Optional[str] = None,
                  max_results: int = 20, search: Optional[str] = None,
                  email: Optional[str] = None) -> dict:
    label = {"inbox": "INBOX", "sent": "SENT"}.get(folder)
    if not label:
        raise GmailError("folder must be 'inbox' or 'sent'")

    try:
        size = max(1, min(int(max_results), 50))
    except Exception:
        size = 20

    service = _service(email)
    kwargs = {
        "userId": "me",
        "labelIds": [label],
        "maxResults": size,
    }
    if page_token:
        kwargs["pageToken"] = page_token
    if search:
        kwargs["q"] = search

    try:
        result = service.users().messages().list(**kwargs).execute()
    except Exception as exc:
        raise GmailError("Gmail could not list messages: %s" % exc) from exc

    messages = []
    for ref in result.get("messages") or []:
        message_id = ref.get("id")
        if not message_id:
            continue
        try:
            meta = service.users().messages().get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=_PROFILE_HEADERS,
            ).execute()
        except Exception:
            continue
        hdrs = _headers((meta.get("payload") or {}).get("headers"))
        labels = meta.get("labelIds") or []
        subject = hdrs.get("subject", "(No subject)")
        sender = _parse_address(hdrs.get("from", ""))
        recipient = _parse_address(hdrs.get("to", ""))
        messages.append({
            "id": message_id,
            "thread_id": meta.get("threadId"),
            "sender": hdrs.get("from", ""),
            "sender_name": sender["name"],
            "sender_email": sender["email"],
            "recipient": hdrs.get("to", ""),
            "recipient_name": recipient["name"],
            "recipient_email": recipient["email"],
            "subject": subject,
            "date": _iso_date(hdrs.get("date", "")),
            "snippet": meta.get("snippet", ""),
            "unread": "UNREAD" in labels,
            "qumail": subject.lower().startswith("qumail"),
        })

    return {
        "folder": folder,
        "messages": messages,
        "next_page_token": result.get("nextPageToken"),
        "result_size_estimate": result.get("resultSizeEstimate"),
    }


def get_message(message_id: str, email: Optional[str] = None,
                mark_read: bool = True) -> dict:
    service = _service(email)
    try:
        message = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
    except Exception as exc:
        raise GmailError("Gmail could not load this message: %s" % exc) from exc

    parsed = parse_full_message(message, mark_unread=not mark_read)

    if mark_read and "UNREAD" in (message.get("labelIds") or []):
        try:
            service.users().messages().modify(
                userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()
        except Exception:
            pass
    return parsed


def _find_attachment_part(part, attachment_id: str):
    body = part.get("body") or {}
    if body.get("attachmentId") == attachment_id:
        return part
    for child in part.get("parts") or []:
        found = _find_attachment_part(child, attachment_id)
        if found:
            return found
    return None


def get_attachment(message_id: str, attachment_id: str,
                   email: Optional[str] = None) -> dict:
    if not attachment_id or "/" in attachment_id or "\\" in attachment_id:
        raise GmailError("Invalid attachment identifier")

    service = _service(email)
    try:
        message = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
    except Exception as exc:
        raise GmailError("Gmail could not load the message for this attachment.") from exc

    part = _find_attachment_part(message.get("payload") or {}, attachment_id)
    if not part:
        raise GmailError("Attachment not found on this message.")

    declared_size = int((part.get("body") or {}).get("size") or 0)
    if declared_size > MAX_ATTACHMENT_BYTES:
        raise GmailError("Attachment exceeds the configured download limit.")

    try:
        data = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
    except Exception as exc:
        raise GmailError("Gmail could not download this attachment.") from exc

    raw = _decode(data.get("data", ""))
    if not raw:
        raise GmailError("Gmail returned an empty attachment.")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise GmailError("Attachment exceeds the configured download limit.")

    return {
        "data": raw,
        "name": (part.get("filename") or "attachment").strip() or "attachment",
        "mime_type": (part.get("mimeType") or "application/octet-stream").lower(),
    }


def mark_read(message_id: str, email: Optional[str] = None):
    service = _service(email)
    service.users().messages().modify(
        userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()


def set_unread(message_id: str, unread: bool = True, email: Optional[str] = None) -> dict:
    """Apply the Gmail UNREAD label so the state survives a refresh."""
    service = _service(email)
    body = (
        {"addLabelIds": ["UNREAD"]} if unread else {"removeLabelIds": ["UNREAD"]}
    )
    try:
        result = service.users().messages().modify(
            userId="me", id=message_id, body=body
        ).execute()
    except Exception as exc:
        raise GmailError("Gmail could not update the read state: %s" % exc) from exc
    return {"id": message_id, "unread": unread, "labels": result.get("labelIds") or []}


def trash_message(message_id: str, email: Optional[str] = None) -> dict:
    """Move a message to Gmail Trash (the message remains recoverable in Gmail)."""
    service = _service(email)
    try:
        result = service.users().messages().trash(userId="me", id=message_id).execute()
    except Exception as exc:
        raise GmailError("Gmail could not move this message to Trash: %s" % exc) from exc
    return {"id": message_id, "labels": result.get("labelIds") or []}

