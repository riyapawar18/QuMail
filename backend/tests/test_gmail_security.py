import base64
import json
from email import message_from_bytes

import pytest
from fastapi.testclient import TestClient

import backend as backend_module
import emailsend
import gmail_api


@pytest.fixture(scope="module")
def client():
    with TestClient(backend_module.app) as test_client:
        yield test_client


@pytest.fixture()
def demo(client):
    response = client.post("/api/demo/session")
    assert response.status_code == 200, response.text
    return {"headers": {"X-Demo-Session": response.json()["token"]}}


@pytest.fixture()
def google_session(monkeypatch, client):
    """Create a real server-side session cookie without touching Google tokens."""
    token = backend_module._create_google_session("sender@example.com")
    client.cookies.set(backend_module.SESSION_COOKIE_NAME, token)
    monkeypatch.setattr(
        backend_module,
        "require_google_session",
        lambda: {"email": "sender@example.com", "name": "Sender"},
    )
    monkeypatch.setattr(backend_module, "require_gmail_credentials", lambda: None)
    backend_module._send_receipts.clear()
    backend_module._send_inflight.clear()
    yield
    client.cookies.clear()
    backend_module._send_receipts.clear()
    backend_module._send_inflight.clear()


# ---------------------------------------------------------------------------
# Guest isolation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/api/gmail/status",
        "/api/gmail/messages",
        "/api/gmail/messages/abc123",
        "/api/gmail/messages/abc123/attachments/def456",
    ],
)
def test_guest_cannot_read_gmail(client, demo, path):
    response = client.get(path, headers=demo["headers"])
    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/api/gmail/send",
        "/api/gmail/disconnect",
        "/api/gmail/messages/abc123/read",
        "/api/gmail/messages/abc123/trash",
        "/api/gmail/messages/abc123/decrypt",
    ],
)
def test_guest_cannot_write_gmail(client, demo, path):
    response = client.post(path, json={}, headers=demo["headers"])
    assert response.status_code == 401


def test_protected_route_requires_a_browser_session(client):
    client.cookies.clear()
    response = client.get("/api/gmail/status")
    assert response.status_code == 401


def test_oauth_callback_sets_httponly_session_cookie(client, monkeypatch):
    client.cookies.clear()
    monkeypatch.setattr(
        backend_module,
        "complete_web_auth",
        lambda state, code: (None, {"email": "connected@example.com", "name": "Connected"}),
    )
    response = client.get(
        "/auth/google/callback?code=fake&state=unused",
        follow_redirects=False,
    )
    assert response.status_code == 302
    cookie = response.headers.get("set-cookie", "").lower()
    assert "qumail_google_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    client.cookies.clear()


def test_invalid_gmail_ids_are_rejected_before_gmail(client, google_session):
    assert client.get("/api/gmail/messages/bad%20id").status_code == 400
    assert client.get("/api/gmail/messages/abc/attachments/..%2F..%2Fetc").status_code in (400, 404)


# ---------------------------------------------------------------------------
# OAuth start / callback
# ---------------------------------------------------------------------------

def test_oauth_start_is_clean_503_without_client(client, monkeypatch):
    monkeypatch.setattr(backend_module, "gmail_configured", lambda: False)
    response = client.get("/auth/google/start", follow_redirects=False)
    assert response.status_code == 503
    assert "error" in response.json()


def test_oauth_start_redirects_when_configured(client, monkeypatch):
    monkeypatch.setattr(backend_module, "gmail_configured", lambda: True)
    monkeypatch.setattr(
        backend_module,
        "build_authorization_url",
        lambda redirect_uri: ("https://accounts.google.com/o/oauth2/auth?state=x", "x"),
    )
    response = client.get("/auth/google/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("https://accounts.google.com/")


def test_oauth_callback_rejects_unknown_state(client):
    response = client.get(
        "/auth/google/callback?code=abc&state=unknown-state",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "oauth_error=" in response.headers["location"]


def test_oauth_callback_reports_denied_consent(client):
    response = client.get(
        "/auth/google/callback?error=access_denied",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "oauth_error=" in response.headers["location"]


# ---------------------------------------------------------------------------
# HTML sanitisation / MIME parsing
# ---------------------------------------------------------------------------

def test_sanitizer_removes_active_content_and_remote_images():
    dirty = (
        '<p>Hello <script>alert(1)</script></p>'
        '<img src="https://tracker.example/pixel.png" onerror="alert(1)">'
        '<a href="javascript:alert(1)">click</a>'
        '<iframe src="https://evil.example"></iframe>'
        '<form action="https://evil.example"><input name="x"></form>'
    )
    clean = gmail_api.sanitize_html(dirty)
    lowered = clean.lower()
    assert "Hello" in clean
    assert "<script" not in lowered
    assert "<img" not in lowered
    assert "<iframe" not in lowered
    assert "<form" not in lowered
    assert "javascript:" not in lowered
    assert "onerror" not in lowered


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8")


def test_parse_full_message_extracts_safe_body_and_attachments():
    message = {
        "id": "msg-1",
        "threadId": "thread-1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Hello there",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "Bob <bob@example.com>"},
                {"name": "Subject", "value": "QuMail Secure Message"},
                {"name": "Date", "value": "Mon, 01 Jan 2024 10:00:00 +0000"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64url(b"Hello there")}},
                {
                    "mimeType": "text/html",
                    "body": {"data": _b64url(b"<p>Hello</p><script>x()</script>")},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "note.pdf",
                    "body": {"attachmentId": "att-1", "size": 12},
                },
            ],
        },
    }
    parsed = gmail_api.parse_full_message(message)
    assert parsed["sender_email"] == "alice@example.com"
    assert parsed["recipient_email"] == "bob@example.com"
    assert parsed["body_text"] == "Hello there"
    assert "<script" not in parsed["body_html"]
    assert parsed["unread"] is True
    assert parsed["has_attachments"] is True
    assert parsed["attachments"][0]["name"] == "note.pdf"
    assert parsed["attachments"][0]["download_url"] == (
        "/api/gmail/messages/msg-1/attachments/att-1"
    )


def test_plain_mime_builder_strips_header_injection():
    message = emailsend.build_plain_message(
        "bob@example.com",
        "Hello\r\nBcc: evil@example.com",
        "Body",
        [{"filename": "a\r\nBcc: evil.txt", "mime_type": "text/plain", "content": b"data"}],
    )
    parsed = message_from_bytes(message.as_bytes())
    assert parsed["To"] == "bob@example.com"
    assert parsed["Bcc"] is None
    attachment = parsed.get_payload()[1]
    assert "\r" not in (attachment.get_filename() or "")
    assert "\n" not in (attachment.get_filename() or "")


# ---------------------------------------------------------------------------
# Send validation and idempotency
# ---------------------------------------------------------------------------

def test_send_requires_explicit_mode(client, google_session):
    response = client.post(
        "/api/gmail/send",
        json={"recipient": "a@example.com", "subject": "s", "body": "b"},
    )
    assert response.status_code == 400
    assert "mode" in response.json()["error"].lower()


def test_send_rejects_invalid_recipient(client, google_session):
    response = client.post(
        "/api/gmail/send",
        json={"recipient": "not-an-email", "subject": "s", "body": "b", "mode": "plain"},
    )
    assert response.status_code == 400


def test_encrypted_send_requires_single_recipient(client, google_session):
    response = client.post(
        "/api/gmail/send",
        json={
            "recipient": "a@example.com, b@example.com",
            "subject": "s",
            "body": "b",
            "mode": "encrypted",
            "level": 2,
        },
    )
    assert response.status_code == 400
    assert "one recipient" in response.json()["error"].lower()


def test_plain_send_is_truthful_and_uses_requested_recipients(client, google_session, monkeypatch):
    calls = []

    def fake_send(recipient, subject, body, attachments, email=None):
        calls.append({"recipient": recipient, "subject": subject, "body": body,
                      "attachments": attachments, "email": email})
        return {"id": "gmail-msg-1", "threadId": "thread-1"}

    monkeypatch.setattr(backend_module, "send_plain_message", fake_send)
    response = client.post(
        "/api/gmail/send",
        json={
            "recipient": "a@example.com, b@example.com",
            "subject": "Status",
            "body": "Plain body",
            "mode": "plain",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted"
    assert body["delivery"] == "accepted-by-gmail"
    assert body["mode"] == "plain"
    assert body["level"] is None
    assert "not guaranteed delivery" in body["delivery_note"]
    assert body["id"] == "gmail-msg-1"
    assert calls[0]["recipient"] == "a@example.com, b@example.com"
    assert calls[0]["body"] == "Plain body"


def test_send_is_idempotent_for_same_request_id(client, google_session, monkeypatch):
    calls = []

    def fake_send(recipient, subject, body, attachments, email=None):
        calls.append(recipient)
        return {"id": "gmail-msg-2", "threadId": "thread-2"}

    monkeypatch.setattr(backend_module, "send_plain_message", fake_send)
    payload = {
        "recipient": "a@example.com",
        "subject": "Once",
        "body": "Only once",
        "mode": "plain",
        "client_request_id": "request-12345678",
    }
    first = client.post("/api/gmail/send", json=payload)
    second = client.post("/api/gmail/send", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers.get("x-idempotent-replay") == "true"
    assert second.json()["id"] == first.json()["id"]
    assert len(calls) == 1


def test_encrypted_send_never_includes_plaintext(client, google_session, monkeypatch):
    captured = {}

    def fake_encrypt(data, level, content_type, recipient_email=None, original_filename=None):
        return {
            "method": "QKD-AES-GCM",
            "level": level,
            "content_type": content_type,
            "nonce": "bm9uY2U=",
            "ciphertext": "Y2lwaGVy",
            "tag": "dGFn",
            "filename": original_filename,
        }

    def fake_send(payload, recipient, email=None):
        captured["payload"] = payload
        captured["recipient"] = recipient
        captured["email"] = email
        return {"id": "gmail-msg-3", "threadId": "thread-3"}

    monkeypatch.setattr(backend_module, "encrypt_data", fake_encrypt)
    monkeypatch.setattr(backend_module, "send_email", fake_send)

    response = client.post(
        "/api/gmail/send",
        json={
            "recipient": "a@example.com",
            "subject": "Secret",
            "body": "sensitive body",
            "mode": "encrypted",
            "level": 2,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["mode"] == "encrypted"
    assert response.json()["level"] == 2
    serialized = json.dumps(captured["payload"])
    assert "sensitive body" not in serialized
    assert "plain_text" not in captured["payload"]
    assert captured["payload"]["method"] == "QKD-AES-GCM"
    assert captured["payload"]["content_type"] == "text"
    assert captured["recipient"] == "a@example.com"


def test_level_one_rejects_attachments(client, google_session, monkeypatch):
    monkeypatch.setattr(
        backend_module, "send_email",
        lambda payload, recipient, email=None: {"id": "x", "threadId": "y"},
    )
    response = client.post(
        "/api/gmail/send",
        data={
            "recipient": "a@example.com",
            "subject": "s",
            "body": "b",
            "mode": "encrypted",
            "level": "1",
        },
        files={"files": ("note.txt", b"payload", "text/plain")},
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Encrypted receiving
# ---------------------------------------------------------------------------

def test_decrypt_reports_the_method_used(client, google_session, monkeypatch):
    monkeypatch.setattr(
        backend_module,
        "fetch_payload_from_email",
        lambda message_id, email=None: {"method": "QKD-AES-GCM", "level": 2, "content_type": "text"},
    )
    monkeypatch.setattr(backend_module, "decrypt_payload", lambda payload: b"decrypted body")
    response = client.post("/api/gmail/messages/abc123/decrypt")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "decrypted body"
    assert body["encryption"]["method"] == "QKD-AES-GCM"
    assert body["encryption"]["level"] == 2
    assert body["encryption"]["legacy_unencrypted"] is False


def test_decrypt_fails_safely_for_missing_metadata(client, google_session, monkeypatch):
    monkeypatch.setattr(
        backend_module, "fetch_payload_from_email", lambda message_id, email=None: {"subject": "no crypto"}
    )
    response = client.post("/api/gmail/messages/abc123/decrypt")
    assert response.status_code == 422
    assert "encrypted payload" in response.json()["error"].lower()


def test_decrypt_fails_safely_for_corrupt_payload(client, google_session, monkeypatch):
    monkeypatch.setattr(
        backend_module,
        "fetch_payload_from_email",
        lambda message_id, email=None: {"method": "QKD-AES-GCM", "level": 2, "content_type": "text"},
    )

    def broken(payload):
        raise ValueError("Authentication tag mismatch")

    monkeypatch.setattr(backend_module, "decrypt_payload", broken)
    response = client.post("/api/gmail/messages/abc123/decrypt")
    assert response.status_code == 422
    assert "decryption failed" in response.json()["error"].lower()
    assert "Traceback" not in response.text
    assert "text" not in response.json()


def test_decrypt_flags_legacy_plaintext_bundle(client, google_session, monkeypatch):
    monkeypatch.setattr(
        backend_module,
        "fetch_payload_from_email",
        lambda message_id, email=None: {
            "type": "multi_file",
            "files": [],
            "plain_text": "legacy body",
            "sender": "old@example.com",
        },
    )
    response = client.post("/api/gmail/messages/abc123/decrypt")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "legacy body"
    assert body["legacy_plaintext_text"] is True
    assert body["warning"]


def test_decrypt_rejects_non_utf8_text(client, google_session, monkeypatch):
    monkeypatch.setattr(
        backend_module,
        "fetch_payload_from_email",
        lambda message_id, email=None: {"method": "QKD-AES-GCM", "level": 2, "content_type": "text"},
    )
    monkeypatch.setattr(backend_module, "decrypt_payload", lambda payload: b"\xff\xfe")
    response = client.post("/api/gmail/messages/abc123/decrypt")
    assert response.status_code == 422
