"""
demo_mode.py - In-memory guest demo sessions for QuMail.

Guest sessions are intentionally ephemeral: they live only in this process,
are isolated from real mailboxes, and disappear on restart or logout. Message
bodies and attachment bytes are encrypted with a per-session AES-256-GCM key
that never leaves the server. Nothing is sent to Gmail.
"""

import base64
import hashlib
import os
import secrets
import threading
import time
from typing import Dict, List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEMO_EMAIL = "guest@qumail.demo"
DEMO_NAME = "Guest Demo"
DEMO_NOTICE = "Demo only - not sent to Gmail"

SESSION_TTL_SECONDS = int(os.getenv("DEMO_SESSION_TTL_SECONDS", "7200"))
MAX_SESSIONS = int(os.getenv("DEMO_MAX_SESSIONS", "200"))
MAX_TEXT_BYTES = 32 * 1024
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 3

AAD_BODY = b"qumail-demo-body-v1"
AAD_FILE = b"qumail-demo-attachment-v1"

_SESSIONS: Dict[str, dict] = {}
_LOCK = threading.Lock()


class DemoError(Exception):
    """Base class for guest-session errors."""


class SessionNotFound(DemoError):
    pass


class ValidationError(DemoError):
    pass


_SAMPLE_INBOX = [
    {
        "id": "demo-inbox-1",
        "sender": "security@qumail.demo",
        "subject": "Welcome to QuMail Guest Demo",
        "body": (
            "Hello Guest,\n\nThis inbox is a self-contained demonstration. "
            "Each message body was encrypted with a random AES-256-GCM key "
            "created for this demo session when you signed in.\n\n"
            "Use the lock badge on a message to inspect the encryption metadata. "
            "Nothing here is linked to a real Gmail account.\n\n"
            "- QuMail Demo Team"
        ),
        "level": 0,
        "minutes_ago": 12,
        "starred": True,
    },
    {
        "id": "demo-inbox-2",
        "sender": "vault@qumail.demo",
        "subject": "Sample document shared securely",
        "body": (
            "A sample degree certificate from the Document Vault was encrypted "
            "and attached to this message.\n\n"
            "Open the Document Vault in the sidebar to attach a synthetic PDF "
            "to your own demo draft. The PDF is clearly marked as a sample and "
            "is not a government record."
        ),
        "level": 0,
        "minutes_ago": 47,
        "starred": False,
    },
    {
        "id": "demo-inbox-3",
        "sender": "notes@qumail.demo",
        "subject": "How the demo encryption works",
        "body": (
            "Each guest session gets its own 256-bit key from the operating "
            "system CSPRNG. Every body and attachment is sealed with AES-GCM "
            "using a fresh 96-bit nonce, so nonces are never reused.\n\n"
            "The server decrypts only for the session that owns the key. "
            "Logging out destroys the key and the demo data."
        ),
        "level": 0,
        "minutes_ago": 155,
        "starred": False,
    },
]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("utf-8")


def _ub64(value: str, field: str) -> bytes:
    try:
        return base64.b64decode(value.encode("utf-8"), validate=True)
    except Exception:
        raise ValidationError("Invalid base64 in field '%s'" % field)


def _seal(key: bytes, data: bytes, aad: bytes) -> dict:
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(key).encrypt(nonce, data, aad)
    ciphertext, tag = sealed[:-16], sealed[-16:]
    return {
        "algorithm": "AES-256-GCM",
        "nonce_b64": _b64(nonce),
        "ciphertext_b64": _b64(ciphertext),
        "tag_b64": _b64(tag),
        "aad": aad.decode("ascii"),
        "key_scope": "per-demo-session (server-side only)",
    }


def _open(key: bytes, payload: dict, aad: bytes) -> bytes:
    nonce = _ub64(payload.get("nonce_b64", ""), "nonce_b64")
    ciphertext = _ub64(payload.get("ciphertext_b64", ""), "ciphertext_b64")
    tag = _ub64(payload.get("tag_b64", ""), "tag_b64")
    if len(nonce) != 12 or len(tag) != 16:
        raise ValidationError("Malformed AES-GCM payload")
    try:
        return AESGCM(key).decrypt(nonce, ciphertext + tag, aad)
    except Exception:
        raise ValidationError("Decryption failed: wrong session or tampered payload")


def _time_label(minutes_ago: int) -> str:
    stamp = time.localtime(time.time() - minutes_ago * 60)
    return time.strftime("%I:%M %p", stamp)


def _purge_expired_locked() -> None:
    cutoff = time.time() - SESSION_TTL_SECONDS
    for token in [t for t, s in _SESSIONS.items() if s["last_seen"] < cutoff]:
        _SESSIONS.pop(token, None)


def _purge_to_limit_locked() -> None:
    overflow = len(_SESSIONS) - MAX_SESSIONS
    if overflow <= 0:
        return
    oldest = sorted(_SESSIONS.items(), key=lambda item: item[1]["last_seen"])
    for token, _ in oldest[:overflow]:
        _SESSIONS.pop(token, None)


def create_session() -> dict:
    token = secrets.token_urlsafe(32)
    key = secrets.token_bytes(32)
    now = time.time()

    inbox: List[dict] = []
    for index, template in enumerate(_SAMPLE_INBOX):
        body = template["body"]
        inbox.append({
            "id": template["id"],
            "sender": template["sender"],
            "subject": template["subject"],
            "preview": body.split("\n\n")[0][:180],
            "time": _time_label(template["minutes_ago"]),
            "level": template["level"],
            "starred": template["starred"],
            "read": index > 0,
            "demo": True,
            "attachments": [],
            "encryption": _seal(key, body.encode("utf-8"), AAD_BODY),
            "_plaintext": body,
        })

    session = {
        "token": token,
        "key": key,
        "created_at": now,
        "last_seen": now,
        "inbox": inbox,
        "sent": [],
    }
    with _LOCK:
        _purge_expired_locked()
        _purge_to_limit_locked()
        _SESSIONS[token] = session
    return session


def delete_session(token: str) -> bool:
    with _LOCK:
        return _SESSIONS.pop(token, None) is not None


def get_session(token: Optional[str]) -> dict:
    if not token:
        raise SessionNotFound("Missing demo session token")
    with _LOCK:
        _purge_expired_locked()
        session = _SESSIONS.get(token)
        if not session:
            raise SessionNotFound("Demo session expired or invalid. Start a new session.")
        session["last_seen"] = time.time()
        return session


def profile(session: dict) -> dict:
    return {
        "email": DEMO_EMAIL,
        "name": DEMO_NAME,
        "picture": None,
        "mode": "demo",
        "isGuest": True,
        "notice": DEMO_NOTICE,
    }


def inbox(session: dict) -> List[dict]:
    return [
        {k: v for k, v in msg.items() if k != "_plaintext"}
        for msg in session["inbox"]
    ]


def sent(session: dict) -> List[dict]:
    return list(session["sent"])


def message_detail(session: dict, message_id: str) -> dict:
    for msg in session["inbox"] + session["sent"]:
        if msg["id"] == message_id:
            detail = {k: v for k, v in msg.items() if k != "_plaintext"}
            if "_plaintext" in msg:
                detail["decrypted_text"] = msg["_plaintext"]
            elif msg.get("body_encryption"):
                detail["decrypted_text"] = _open(
                    session["key"], msg["body_encryption"], AAD_BODY
                ).decode("utf-8", errors="replace")
            return detail
    raise ValidationError("Demo message not found")


def encrypt_text(session: dict, text: str) -> dict:
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("Text is required")
    raw = text.encode("utf-8")
    if len(raw) > MAX_TEXT_BYTES:
        raise ValidationError("Text exceeds %d bytes" % MAX_TEXT_BYTES)
    return _seal(session["key"], raw, AAD_BODY)


def decrypt_text(session: dict, payload: dict) -> str:
    return _open(session["key"], payload, AAD_BODY).decode("utf-8", errors="replace")


def encrypt_file(session: dict, filename: str, data: bytes) -> dict:
    if not data:
        raise ValidationError("Empty file")
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError("File exceeds %d bytes" % MAX_FILE_BYTES)
    safe_name = os.path.basename(filename or "attachment")[:120]
    encryption = _seal(session["key"], data, AAD_FILE)
    return {
        "filename": safe_name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "encryption": encryption,
    }


def send_message(
    session: dict,
    recipient: str,
    subject: str,
    body: str,
    files: Optional[List[dict]] = None,
) -> dict:
    recipient = (recipient or "").strip()
    subject = (subject or "").strip()
    body = body or ""
    files = files or []

    if not recipient or "@" not in recipient or len(recipient) > 320:
        raise ValidationError("A valid recipient email is required")
    if len(subject) > 200:
        raise ValidationError("Subject is too long")
    if not body.strip() and not files:
        raise ValidationError("Add a message or at least one attachment")
    if len(body.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValidationError("Message is too long")
    if len(files) > MAX_FILES_PER_MESSAGE:
        raise ValidationError("At most %d attachments are allowed" % MAX_FILES_PER_MESSAGE)

    attachments = []
    for item in files:
        attachments.append({
            "name": item["filename"],
            "size": item["size"],
            "type": "application/pdf" if item["filename"].lower().endswith(".pdf") else "",
            "sha256": item["sha256"],
            "encryption": item["encryption"],
        })

    now = time.localtime()
    record = {
        "id": "demo-sent-%d-%s" % (int(time.time() * 1000), secrets.token_hex(3)),
        "sender": DEMO_EMAIL,
        "senderName": DEMO_NAME,
        "recipient": recipient,
        "subject": subject or "(No Subject)",
        "preview": body[:180] if body.strip() else "[%d encrypted attachment(s)]" % len(attachments),
        "time": time.strftime("%I:%M %p", now),
        "level": 0,
        "starred": False,
        "read": True,
        "demo": True,
        "delivered": False,
        "notice": DEMO_NOTICE,
        "attachments": attachments,
        "body_encryption": _seal(session["key"], body.encode("utf-8"), AAD_BODY),
    }

    with _LOCK:
        session["sent"].insert(0, record)

    return {k: v for k, v in record.items() if k != "body_encryption"}
