import base64

import pytest
from fastapi.testclient import TestClient

import backend as backend_module


@pytest.fixture(scope="module")
def client():
    with TestClient(backend_module.app) as test_client:
        yield test_client


@pytest.fixture()
def demo(client):
    response = client.post("/api/demo/session")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["token"]
    return {"token": data["token"], "profile": data["profile"], "headers": {"X-Demo-Session": data["token"]}}


# ---------------------------------------------------------------------------
# Health / config
# ---------------------------------------------------------------------------

def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["demo_mode"] is True
    assert "version" in body


def test_config(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    body = response.json()
    assert "gmail_available" in body
    assert body["notice"]


# ---------------------------------------------------------------------------
# Guest session
# ---------------------------------------------------------------------------

def test_create_demo_session(demo):
    profile = demo["profile"]
    assert profile["email"] == "guest@qumail.demo"
    assert profile["mode"] == "demo"
    assert profile["isGuest"] is True


def test_get_demo_session(client, demo):
    response = client.get("/api/demo/session", headers=demo["headers"])
    assert response.status_code == 200
    assert response.json()["profile"]["email"] == "guest@qumail.demo"


def test_demo_requires_session(client):
    assert client.get("/api/demo/inbox").status_code == 401
    assert client.get("/api/demo/sent").status_code == 401
    assert client.get("/api/demo/messages/demo-inbox-1").status_code == 401


def test_invalid_session_rejected(client):
    response = client.get("/api/demo/inbox", headers={"X-Demo-Session": "not-a-real-token"})
    assert response.status_code == 401


def test_inbox_samples(client, demo):
    response = client.get("/api/demo/inbox", headers=demo["headers"])
    assert response.status_code == 200
    messages = response.json()["messages"]
    assert len(messages) >= 3
    first = messages[0]
    assert first["demo"] is True
    assert first["encryption"]["algorithm"] == "AES-256-GCM"
    assert "_plaintext" not in first
    assert "body" not in first


def test_message_detail_is_decrypted(client, demo):
    response = client.get("/api/demo/messages/demo-inbox-1", headers=demo["headers"])
    assert response.status_code == 200
    message = response.json()["message"]
    assert "Hello Guest" in message["decrypted_text"]
    assert message["encryption"]["aad"] == "qumail-demo-body-v1"


# ---------------------------------------------------------------------------
# Real AES-GCM round trip
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_round_trip(client, demo):
    encrypted = client.post("/api/demo/encrypt", json={"text": "quantum round trip"}, headers=demo["headers"])
    assert encrypted.status_code == 200
    payload = encrypted.json()["text_encryption"]
    assert payload["algorithm"] == "AES-256-GCM"
    assert len(base64.b64decode(payload["nonce_b64"])) == 12

    decrypted = client.post("/api/demo/decrypt", json=payload, headers=demo["headers"])
    assert decrypted.status_code == 200
    assert decrypted.json()["text"] == "quantum round trip"


def test_nonce_is_unique_per_encryption(client, demo):
    first = client.post("/api/demo/encrypt", json={"text": "same text"}, headers=demo["headers"]).json()
    second = client.post("/api/demo/encrypt", json={"text": "same text"}, headers=demo["headers"]).json()
    assert first["text_encryption"]["nonce_b64"] != second["text_encryption"]["nonce_b64"]
    assert first["text_encryption"]["ciphertext_b64"] != second["text_encryption"]["ciphertext_b64"]


def test_decrypt_with_wrong_session_fails(client, demo):
    payload = client.post("/api/demo/encrypt", json={"text": "secret"}, headers=demo["headers"]).json()["text_encryption"]
    other = client.post("/api/demo/session").json()["token"]
    response = client.post("/api/demo/decrypt", json=payload, headers={"X-Demo-Session": other})
    assert response.status_code == 400


def test_tampered_ciphertext_fails(client, demo):
    payload = client.post("/api/demo/encrypt", json={"text": "secret"}, headers=demo["headers"]).json()["text_encryption"]
    raw = bytearray(base64.b64decode(payload["ciphertext_b64"]))
    raw[0] ^= 0x01
    payload["ciphertext_b64"] = base64.b64encode(bytes(raw)).decode()
    response = client.post("/api/demo/decrypt", json=payload, headers=demo["headers"])
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Demo send
# ---------------------------------------------------------------------------

def test_demo_send_with_attachment(client, demo):
    response = client.post(
        "/api/demo/send",
        headers=demo["headers"],
        data={
            "recipient": "friend@qumail.demo",
            "subject": "Hello guest",
            "body": "Demo body",
        },
        files={"files": ("note.txt", b"attachment payload", "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "demo-sent"
    assert body["delivered"] is False
    assert "not sent to Gmail" in body["notice"]
    message = body["message"]
    assert message["demo"] is True
    assert message["delivered"] is False
    assert len(message["attachments"]) == 1
    attachment = message["attachments"][0]
    assert attachment["encryption"]["algorithm"] == "AES-256-GCM"
    assert attachment["encryption"]["aad"] == "qumail-demo-attachment-v1"
    assert len(attachment["sha256"]) == 64

    sent = client.get("/api/demo/sent", headers=demo["headers"]).json()["messages"]
    assert any(m["id"] == message["id"] for m in sent)

    detail = client.get(f"/api/demo/messages/{message['id']}", headers=demo["headers"])
    assert detail.status_code == 200
    assert detail.json()["message"]["decrypted_text"] == "Demo body"


def test_demo_send_validates_recipient(client, demo):
    response = client.post(
        "/api/demo/send",
        headers=demo["headers"],
        data={"recipient": "not-an-email", "subject": "x", "body": "y"},
    )
    assert response.status_code == 400


def test_end_demo_session(client, demo):
    response = client.delete(f"/api/demo/session/{demo['token']}")
    assert response.status_code == 200
    assert client.get("/api/demo/inbox", headers=demo["headers"]).status_code == 401


# ---------------------------------------------------------------------------
# Guest isolation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/mail_store",
        "/accounts",
        "/me",
        "/verify_files",
        "/attachment/anything.pdf",
        "/attachment/..%2Fbackend.py",
    ],
)
def test_guest_cannot_access_authenticated_data(client, demo, path):
    response = client.get(path, headers=demo["headers"])
    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    ["/mail_store/sent", "/mail_store/sync_all", "/mail_store/move", "/mail_store/update", "/delete_message"],
)
def test_guest_cannot_post_to_authenticated_endpoints(client, demo, path):
    response = client.post(path, json={}, headers=demo["headers"])
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# DigiLocker-style vault
# ---------------------------------------------------------------------------

def test_digilocker_status_and_list(client):
    status = client.get("/api/digilocker/status")
    assert status.status_code == 200
    body = status.json()
    assert body["mode"].startswith("mock")
    assert body["real_integration"] is False

    listing = client.get("/api/digilocker/documents")
    assert listing.status_code == 200
    documents = listing.json()["documents"]
    assert {doc["id"] for doc in documents} == {"doc_degree", "doc_marksheet", "doc_identity"}
    assert all(doc["is_sample"] is True for doc in documents)


def test_digilocker_detail(client):
    response = client.get("/api/digilocker/documents/doc_degree")
    assert response.status_code == 200
    document = response.json()["document"]
    assert document["is_sample"] is True
    assert "sample" in document["sample_label"].lower()


def test_digilocker_download_is_pdf(client):
    response = client.get("/api/digilocker/documents/doc_degree/download")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    assert b"SAMPLE" in response.content


def test_digilocker_rejects_unknown_and_traversal_ids(client):
    assert client.get("/api/digilocker/documents/nope").status_code == 404
    traversal = client.get("/api/digilocker/documents/..%2F..%2Fbackend.py/download")
    assert traversal.status_code in (400, 404)
