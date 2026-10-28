import base64
import contextvars
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import uuid
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import quote

# Ensure the directory containing this script is in the Python search path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Load a local .env if present (environment variables always win).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(current_dir), ".env"), override=False)
except Exception:
    pass

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("qumail")

# --- IMPORT SHARED LOGIC ---
try:
    from qumail_reciever import sync_identity_to_render
except Exception as exc:  # pragma: no cover - only if optional deps are missing
    log.exception("Critical import error while loading qumail_reciever: %s", exc)

    def sync_identity_to_render(email):
        return False

import requests

from sae_client import encrypt_data
from decy import decrypt_payload
from emailsend import send_email, send_plain_file, send_plain_message
from reciever_email import (
    fetch_qumail_emails, fetch_payload_from_email,
    mark_seen
)
from gmail_api import (
    GmailError, list_messages as gmail_list_messages,
    get_message as gmail_get_message,
    get_attachment as gmail_get_attachment,
    set_unread as gmail_set_unread,
    trash_message as gmail_trash_message,
    MAX_ATTACHMENT_BYTES,
)
from mail_store import (
    get_all_folders, get_folder, append_message,
    update_message, move_message,
    delete_message as store_delete_message,
    delete_account as store_delete_account
)
from gmail_auth import (
    get_gmail_service, load_credentials, get_user_profile,
    run_automatic_auth_flow, list_accounts, switch_account,
    remove_account, get_active_email, has_credentials, gmail_configured,
    build_authorization_url, complete_web_auth,
)

import demo_mode
from mock_digilocker.mock_digilocker import (
    USER_DOCS as DIGILOCKER_USER_DOCS,
    DEMO_USER_ID as DIGILOCKER_USER_ID,
    resolve_demo_doc_path as digilocker_doc_path,
    app as mock_digilocker_app,
)

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
FRONTEND_BUILD_DIR = Path(
    os.getenv("FRONTEND_BUILD_DIR", str(PROJECT_ROOT / "build"))
).resolve()
ATTACHMENT_DIR = Path(os.getenv("QMAIL_DATA_DIR", str(BACKEND_DIR))) / "attachments"
ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)

DEMO_MODE = os.getenv("QMAIL_DEMO_MODE", "1").strip().lower() not in ("0", "false", "no")
APP_VERSION = "1.2.0"
DIGILOCKER_DISCLAIMER = (
    "Mock DigiLocker integration for demonstration. Documents are synthetic "
    "samples, not government-issued records, and are not verified by DigiLocker."
)

MAX_GMAIL_ATTACHMENTS = int(os.getenv("GMAIL_MAX_ATTACHMENTS", "10"))
MAX_GMAIL_BODY_BYTES = int(os.getenv("GMAIL_MAX_BODY_BYTES", str(1024 * 1024)))
MAX_GMAIL_RECIPIENTS = int(os.getenv("GMAIL_MAX_RECIPIENTS", "10"))
EMAIL_RE = re.compile(r"^[^@\s,;\"<>]+@[^@\s,;\"<>]+\.[^@\s,;\"<>]+$")
GMAIL_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

_send_lock = threading.Lock()
_send_receipts = {}
_send_inflight = set()
SEND_RECEIPT_TTL_SECONDS = int(os.getenv("GMAIL_SEND_IDEMPOTENCY_TTL_SECONDS", "600"))


def _parse_recipients(raw: str):
    """Validate a comma/semicolon separated recipient list."""
    parts = [p.strip() for p in re.split(r"[,;]", raw or "") if p.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail="At least one recipient is required.")
    if len(parts) > MAX_GMAIL_RECIPIENTS:
        raise HTTPException(
            status_code=400,
            detail="Too many recipients (limit %s)." % MAX_GMAIL_RECIPIENTS,
        )
    cleaned = []
    for part in parts:
        _, addr = parseaddr(part)
        if not addr or not EMAIL_RE.match(addr):
            raise HTTPException(status_code=400, detail="Invalid recipient address: %s" % part)
        cleaned.append(addr)
    return cleaned


def _validate_gmail_id(value: str, label: str = "message") -> str:
    if not value or not GMAIL_ID_RE.match(value):
        raise HTTPException(status_code=400, detail="Invalid %s identifier." % label)
    return value


def _clean_header(value: str) -> str:
    return " ".join(str(value or "").splitlines()).strip()


def _safe_download_name(name: str) -> str:
    name = os.path.basename(str(name or "attachment"))
    name = "".join(ch for ch in name if ch not in '\r\n"\\')
    return name or "attachment"


def _request_base_url(request: Request) -> str:
    configured = os.getenv("PUBLIC_BASE_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        return render_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def _oauth_redirect_uri(request: Request) -> str:
    configured = os.getenv("OAUTH_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return _request_base_url(request) + "/auth/google/callback"


def _frontend_base(request: Request) -> str:
    configured = os.getenv("FRONTEND_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return _request_base_url(request)


def _send_receipt_key(request_id: str, fingerprint: str) -> str:
    return ("rid:" + request_id) if request_id else ("fp:" + fingerprint)


def _purge_send_receipts_locked():
    now = time.time()
    for key, (expiry, _) in list(_send_receipts.items()):
        if expiry <= now:
            _send_receipts.pop(key, None)



def clean_payload(data):
    """Recursively ensures all binary data in a dictionary is Base64 encoded."""
    if isinstance(data, dict):
        return {k: clean_payload(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_payload(v) for v in data]
    elif isinstance(data, bytes):
        return base64.b64encode(data).decode('utf-8', errors='ignore')
    return data


app = FastAPI(title="QuMail API", version=APP_VERSION)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "Invalid request", "details": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"error": "Internal server error"})


# ------------------------------------------
# ENABLE CORS FOR THE CONFIGURED FRONTEND ORIGINS
# ------------------------------------------
_default_origins = "http://localhost:3000,http://127.0.0.1:3000"
_allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")
    if origin.strip()
]
_allow_wildcard = "*" in _allowed_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _allow_wildcard else _allowed_origins,
    # Browsers reject wildcard origins combined with credentials.
    allow_credentials=not _allow_wildcard,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ------------------------------------------
# AUTH / DEMO HELPERS
# ------------------------------------------
SESSION_COOKIE_NAME = "qumail_google_session"
GOOGLE_SESSION_TTL_SECONDS = int(os.getenv("GOOGLE_SESSION_TTL_SECONDS", "43200"))

_google_sessions = {}
_google_session_lock = threading.Lock()
_session_email_var = contextvars.ContextVar("qumail_session_email", default=None)


def _purge_google_sessions_locked():
    now = time.time()
    for token, entry in list(_google_sessions.items()):
        if entry["expiry"] <= now:
            _google_sessions.pop(token, None)


def _create_google_session(email: str) -> str:
    token = secrets.token_urlsafe(32)
    with _google_session_lock:
        _purge_google_sessions_locked()
        _google_sessions[token] = {
            "email": email.strip().lower(),
            "expiry": time.time() + GOOGLE_SESSION_TTL_SECONDS,
        }
    return token


def _session_from_request(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    with _google_session_lock:
        entry = _google_sessions.get(token)
        if not entry:
            return None
        if entry["expiry"] <= time.time():
            _google_sessions.pop(token, None)
            return None
        return dict(entry)


def _destroy_google_session(token):
    if not token:
        return
    with _google_session_lock:
        _google_sessions.pop(token, None)


def _drop_sessions_for_email(email: str):
    target = (email or "").strip().lower()
    with _google_session_lock:
        for token, entry in list(_google_sessions.items()):
            if entry["email"] == target:
                _google_sessions.pop(token, None)


def _set_session_email(request: Request, email: str):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return
    with _google_session_lock:
        entry = _google_sessions.get(token)
        if entry:
            entry["email"] = email.strip().lower()


def _cookie_secure(request: Request) -> bool:
    return _request_base_url(request).startswith("https://")


def _set_session_cookie(response: Response, request: Request, token: str):
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=GOOGLE_SESSION_TTL_SECONDS,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request):
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
    )


def _is_google_protected(path: str) -> bool:
    exact = {
        "/me", "/accounts", "/sync", "/verify_files", "/logout",
        "/remove_account", "/add_account", "/switch_account",
        "/receive_email", "/delete_message", "/decrypt_email",
    }
    prefixes = (
        "/api/gmail/", "/mail_store", "/attachment/", "/encrypt",
        "/send_", "/send_email", "/send_plain",
    )
    return path in exact or any(path.startswith(prefix) for prefix in prefixes)


@app.middleware("http")
async def google_session_guard(request: Request, call_next):
    """Reject protected data routes unless this browser has a Google session."""
    protected = _is_google_protected(request.url.path) and request.method != "OPTIONS"
    entry = _session_from_request(request) if protected else None
    if protected and not entry:
        return JSONResponse(
            status_code=401,
            content={
                "error": "Sign in with Google to access this resource.",
                "status_code": 401,
            },
        )
    context_token = _session_email_var.set(entry["email"] if entry else None)
    try:
        return await call_next(request)
    finally:
        _session_email_var.reset(context_token)


def require_google_session() -> dict:
    """Return the Google profile bound to this browser's session, or a clean 401."""
    email = _session_email_var.get()
    if not email or not has_credentials(email):
        raise HTTPException(
            status_code=401,
            detail="Sign in with Google to access this resource. Guest mode stays isolated.",
        )
    try:
        return get_user_profile(email=email, interactive=False)
    except Exception as exc:
        log.warning("Google profile unavailable: %s", exc)
        raise HTTPException(
            status_code=401,
            detail="Google session is unavailable. Sign in again or use Guest Demo.",
        )


def require_gmail_credentials() -> None:
    email = _session_email_var.get()
    if not email or not has_credentials(email):
        raise HTTPException(
            status_code=401,
            detail="Gmail is not available for this session. Continue as Guest for the offline demo.",
        )


def demo_session_from_request(request: Request) -> dict:
    token = request.headers.get("X-Demo-Session") or request.query_params.get("session")
    try:
        return demo_mode.get_session(token)
    except demo_mode.SessionNotFound as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except demo_mode.DemoError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def digilocker_documents() -> list:
    docs = DIGILOCKER_USER_DOCS.get(DIGILOCKER_USER_ID, [])
    return [dict(doc, disclaimer=DIGILOCKER_DISCLAIMER) for doc in docs]


def digilocker_document(document_id: str) -> dict:
    for doc in digilocker_documents():
        if doc["id"] == document_id:
            return doc
    raise HTTPException(status_code=404, detail="Document not found")


# ------------------------------------------
# HEALTH / CONFIG
# ------------------------------------------
@app.get("/health")
def health():
    active_email = None
    try:
        active_email = get_active_email()
    except Exception:
        active_email = None
    return {
        "status": "ok",
        "service": "qumail",
        "version": APP_VERSION,
        "demo_mode": DEMO_MODE,
        "gmail_client_configured": gmail_configured(),
        "gmail_token_present": bool(active_email),
        "frontend_build_present": (FRONTEND_BUILD_DIR / "index.html").is_file(),
        "digilocker": "mock-in-process",
        "kme_configured": bool(os.getenv("KME_URL")),
        "identity_configured": bool(os.getenv("IDENTITY_URL")),
    }


@app.get("/api/config")
def api_config():
    active_email = None
    try:
        active_email = bool(get_active_email())
    except Exception:
        active_email = False
    return {
        "service": "qumail",
        "version": APP_VERSION,
        "demo_mode": DEMO_MODE,
        "gmail_client_configured": gmail_configured(),
        "gmail_available": gmail_configured() and active_email,
        "authenticated": active_email,
        "oauth_redirect_uri": os.getenv("OAUTH_REDIRECT_URI", ""),
        "notice": demo_mode.DEMO_NOTICE,
    }


# ------------------------------------------
# GOOGLE OAUTH 2.0 (web application flow)
# ------------------------------------------
@app.get("/auth/google/start")
def oauth_start(request: Request):
    """Redirect the browser to Google's consent screen."""
    if not gmail_configured():
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_SECRET_JSON "
                   "or GOOGLE_CLIENT_SECRET_FILE, then restart the server.",
        )
    try:
        auth_url, _state = build_authorization_url(_oauth_redirect_uri(request))
    except Exception as exc:
        log.warning("Could not build Google authorization URL: %s", exc)
        raise HTTPException(status_code=503, detail="Could not start Google sign-in.")
    return RedirectResponse(url=auth_url, status_code=302)


@app.get("/auth/google/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle Google's redirect, store the token server-side, return to the app."""
    frontend = _frontend_base(request)
    if error:
        log.info("Google OAuth was denied or failed: %s", error)
        return RedirectResponse(
            url=frontend + "/?oauth_error=" + quote("Google sign-in was cancelled."),
            status_code=302,
        )
    try:
        _creds, profile = complete_web_auth(state, code)
    except ValueError as exc:
        log.info("OAuth callback rejected: %s", exc)
        return RedirectResponse(
            url=frontend + "/?oauth_error=" + quote(str(exc)), status_code=302
        )
    except Exception as exc:
        log.warning("OAuth token exchange failed: %s", exc)
        return RedirectResponse(
            url=frontend + "/?oauth_error=" + quote(
                "Google sign-in failed. Check the OAuth client redirect URI and try again."
            ),
            status_code=302,
        )

    email = profile.get("email", "")
    try:
        from qumail_reciever import ensure_identity_exists
        ensure_identity_exists(email)
    except Exception as exc:
        log.warning("Identity key setup skipped for %s: %s", email, exc)

    # Bind this browser to the account so other visitors cannot read the mailbox.
    _destroy_google_session(request.cookies.get(SESSION_COOKIE_NAME))
    session_token = _create_google_session(email)
    response = RedirectResponse(url=frontend + "/?connected=1", status_code=302)
    _set_session_cookie(response, request, session_token)
    return response



# ------------------------------------------
# GUEST DEMO SESSION (isolated, in-memory)
# ------------------------------------------
@app.post("/api/demo/session")
def create_demo_session():
    if not DEMO_MODE:
        raise HTTPException(status_code=403, detail="Demo mode is disabled on this deployment.")
    session = demo_mode.create_session()
    return {
        "status": "ok",
        "token": session["token"],
        "profile": demo_mode.profile(session),
        "inbox": demo_mode.inbox(session),
        "sent": demo_mode.sent(session),
        "notice": demo_mode.DEMO_NOTICE,
        "expires_in_seconds": demo_mode.SESSION_TTL_SECONDS,
    }


@app.get("/api/demo/session")
def get_demo_session(request: Request):
    session = demo_session_from_request(request)
    return {
        "status": "ok",
        "profile": demo_mode.profile(session),
        "notice": demo_mode.DEMO_NOTICE,
    }


@app.delete("/api/demo/session/{token}")
def end_demo_session(token: str):
    demo_mode.delete_session(token)
    return {"status": "ended"}


@app.get("/api/demo/inbox")
def demo_inbox(request: Request):
    session = demo_session_from_request(request)
    return {
        "status": "ok",
        "messages": demo_mode.inbox(session),
        "notice": demo_mode.DEMO_NOTICE,
    }


@app.get("/api/demo/sent")
def demo_sent(request: Request):
    session = demo_session_from_request(request)
    return {
        "status": "ok",
        "messages": demo_mode.sent(session),
        "notice": demo_mode.DEMO_NOTICE,
    }


@app.get("/api/demo/messages/{message_id}")
def demo_message(message_id: str, request: Request):
    session = demo_session_from_request(request)
    try:
        return {"status": "ok", "message": demo_mode.message_detail(session, message_id)}
    except demo_mode.ValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/demo/encrypt")
async def demo_encrypt(request: Request):
    """Encrypt demo text and/or one file with the session AES-GCM key."""
    session = demo_session_from_request(request)
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            text = (form.get("text") or "").strip()
            upload = form.get("file")
            result = {"status": "encrypted", "mode": "demo", "notice": demo_mode.DEMO_NOTICE}
            if text:
                result["text_encryption"] = demo_mode.encrypt_text(session, text)
            if upload is not None and hasattr(upload, "read"):
                data = await upload.read()
                result["file"] = demo_mode.encrypt_file(
                    session, upload.filename, data
                )
            if "text_encryption" not in result and "file" not in result:
                raise demo_mode.ValidationError("Provide text or a file")
            return result

        body = await request.json()
        text = body.get("text", "")
        return {
            "status": "encrypted",
            "mode": "demo",
            "notice": demo_mode.DEMO_NOTICE,
            "text_encryption": demo_mode.encrypt_text(session, text),
        }
    except demo_mode.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/demo/decrypt")
async def demo_decrypt(request: Request):
    session = demo_session_from_request(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON body required")
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else body
    try:
        text = demo_mode.decrypt_text(session, payload)
    except demo_mode.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "decrypted", "mode": "demo", "text": text}


@app.post("/api/demo/send")
async def demo_send(request: Request):
    """Simulate a send. Nothing leaves the server or reaches Gmail."""
    session = demo_session_from_request(request)
    try:
        form = await request.form()
        recipient = str(form.get("recipient") or "")
        subject = str(form.get("subject") or "")
        body = str(form.get("body") or "")

        encrypted_files = []
        for upload in form.getlist("files"):
            if not hasattr(upload, "read"):
                continue
            data = await upload.read()
            encrypted_files.append(demo_mode.encrypt_file(session, upload.filename, data))

        message = demo_mode.send_message(
            session, recipient, subject, body, encrypted_files
        )
        return {
            "status": "demo-sent",
            "delivered": False,
            "notice": demo_mode.DEMO_NOTICE,
            "message": message,
        }
    except demo_mode.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------
# DIGILOCKER-STYLE DOCUMENT VAULT (mock)
# ------------------------------------------
@app.get("/api/digilocker/status")
def digilocker_status():
    return {
        "status": "ok",
        "mode": "mock-in-process",
        "real_integration": False,
        "document_count": len(digilocker_documents()),
        "disclaimer": DIGILOCKER_DISCLAIMER,
    }


@app.get("/api/digilocker/documents")
def digilocker_documents_list():
    documents = [
        {
            "id": doc["id"],
            "name": doc["name"],
            "type": doc["type"],
            "issuer": doc["issuer"],
            "issue_date": doc.get("issue_date"),
            "size_kb": doc.get("size_kb"),
            "is_sample": True,
            "sample_label": doc.get("sample_label"),
            "download_url": "/api/digilocker/documents/%s/download" % doc["id"],
        }
        for doc in digilocker_documents()
    ]
    return {
        "status": "ok",
        "mode": "mock",
        "real_integration": False,
        "disclaimer": DIGILOCKER_DISCLAIMER,
        "documents": documents,
    }


@app.get("/api/digilocker/documents/{document_id}")
def digilocker_document_detail(document_id: str):
    doc = digilocker_document(document_id)
    return {
        "status": "ok",
        "disclaimer": DIGILOCKER_DISCLAIMER,
        "document": {
            "id": doc["id"],
            "name": doc["name"],
            "type": doc["type"],
            "issuer": doc["issuer"],
            "issue_date": doc.get("issue_date"),
            "size_kb": doc.get("size_kb"),
            "is_sample": True,
            "sample_label": doc.get("sample_label"),
            "download_url": "/api/digilocker/documents/%s/download" % doc["id"],
        },
    }


@app.get("/api/digilocker/documents/{document_id}/download")
def digilocker_document_download(document_id: str):
    doc = digilocker_document(document_id)
    path = digilocker_doc_path(doc)
    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=doc["file_name"],
        headers={"Cache-Control": "no-store", "X-QuMail-Sample-Document": "true"},
    )


# Expose the original mock DigiLocker OAuth/doc routes in one deployment.
app.mount("/mock-digilocker", mock_digilocker_app)


# ------------------------------------------
# SERVE FRONTEND BUILD (portable, optional)
# ------------------------------------------
if (FRONTEND_BUILD_DIR / "static").is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_BUILD_DIR / "static")),
        name="static",
    )


@app.get("/")
def frontend_root():
    index_file = FRONTEND_BUILD_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(index_file)
    return {
        "service": "QuMail API",
        "version": APP_VERSION,
        "message": "Frontend build not found. Run `npm run build` or use the dev server.",
        "health": "/health",
    }

# ------------------------------------------
# SYNC ENDPOINT (The Bridge to qumail_reciever)
# ------------------------------------------
# ------------------------------------------
# SYNC ENDPOINT (The Bridge to qumail_reciever)
# ------------------------------------------
@app.post("/sync")
async def sync_secure_endpoint(request: Request):
    profile = require_google_session()
    try:
        data = await request.json()
    except Exception:
        data = {}

    email = (data.get("email") or profile.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email required")

    log.info("Triggering identity sync for %s", email)
    success = sync_identity_to_render(email)
    if not success:
        raise HTTPException(
            status_code=503,
            detail="Identity sync failed. The identity service may be unavailable.",
        )
    return {"status": "success", "message": "Identity synced successfully"}


@app.get("/me")
def me():
    profile = require_google_session()
    return {
        "email": profile.get("email"),
        "name": profile.get("name"),
        "picture": profile.get("picture"),
        "mode": "google",
        "isGuest": False,
    }

# ------------------------------------------
# MULTI-ACCOUNT MANAGEMENT
# ------------------------------------------

@app.get("/accounts")
def get_accounts():
    """Return all saved accounts."""
    require_google_session()
    try:
        accounts = list_accounts()
        return {"accounts": accounts}
    except Exception as e:
        log.warning("Could not list accounts: %s", e)
        raise HTTPException(status_code=503, detail="Could not list Google accounts.")

@app.post("/switch_account")
async def switch_account_endpoint(request: Request):
    """Switch the active account."""
    require_google_session()
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="email required")
        ok = switch_account(email)
        if ok:
            _set_session_email(request, email)
            return {"status": "switched", "email": email}
        raise HTTPException(status_code=404, detail="Account not found. Please add it first.")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/add_account")
def add_account(request: Request):
    """Return the Google consent URL; the browser navigates to it."""
    if not gmail_configured():
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured on this deployment.",
        )
    try:
        auth_url, _state = build_authorization_url(_oauth_redirect_uri(request))
    except Exception as exc:
        log.warning("Could not build add-account URL: %s", exc)
        raise HTTPException(status_code=503, detail="Could not start Google sign-in.")
    return {"status": "redirect", "auth_url": auth_url}

@app.post("/logout")
async def logout(request: Request):
    """Sign out: remove the account token, invalidate sessions, and clear the cookie."""
    require_gmail_credentials()
    try:
        body = await request.json()
        email = body.get("email")
    except Exception:
        email = None
    if not email:
        email = _session_email_var.get() or get_active_email()
    if email:
        remove_account(email)
        _drop_sessions_for_email(email)
    _destroy_google_session(request.cookies.get(SESSION_COOKIE_NAME))
    response = JSONResponse({"status": "logged_out", "email": email})
    _clear_session_cookie(response, request)
    return response

@app.post("/remove_account")
async def remove_account_endpoint(request: Request):
    """
    Permanently remove an account:
    - Deletes its entry from token.json
    - Deletes its keys from receiver_keys.json
    - Updates flat key files for the new active account
    Returns the new active account email (or None if no accounts left).
    """
    require_google_session()
    try:
        body = await request.json()
        email = body.get("email", "").strip().lower()
    except Exception:
        email = ""
    if not email:
        raise HTTPException(status_code=400, detail="email required")

    current_email = _session_email_var.get()
    remove_account(email)
    _drop_sessions_for_email(email)

    response = JSONResponse({
        "status": "removed",
        "removed_email": email,
        "new_active": get_active_email(),
    })
    if email == current_email:
        _clear_session_cookie(response, request)
    return response

@app.get("/verify_files")
def verify_files():
    """
    Check that all required credential and key files exist.
    Returns a status dict so the frontend can show warnings.
    """
    require_google_session()
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    files = {
        "token.json":                  os.path.join(backend_dir, "token.json"),
        "receiver_keys.json":          os.path.join(backend_dir, "receiver_keys.json"),
        "receiver_dh_private.txt":     os.path.join(backend_dir, "receiver_dh_private.txt"),
        "receiver_pqc_private.bin":    os.path.join(backend_dir, "receiver_pqc_private.bin"),
        "receiver_pqc_public.bin":     os.path.join(backend_dir, "receiver_pqc_public.bin"),
    }
    result = {}
    for name, path in files.items():
        exists = os.path.exists(path)
        size = os.path.getsize(path) if exists else 0
        result[name] = {"exists": exists, "size_bytes": size}

    # Also report token.json format
    token_path = files["token.json"]
    if os.path.exists(token_path):
        try:
            with open(token_path) as f:
                data = json.load(f)
            result["token.json"]["format"] = "multi-account" if "accounts" in data else "legacy-flat"
            result["token.json"]["accounts"] = list(data.get("accounts", {}).keys())
            result["token.json"]["active"] = data.get("active")
        except Exception as e:
            result["token.json"]["format"] = f"error: {e}"

    # Report receiver_keys.json accounts
    keys_path = files["receiver_keys.json"]
    if os.path.exists(keys_path):
        try:
            with open(keys_path) as f:
                kdata = json.load(f)
            result["receiver_keys.json"]["accounts"] = list(kdata.keys())
        except Exception as e:
            result["receiver_keys.json"]["accounts"] = f"error: {e}"

    all_ok = all(v["exists"] for v in result.values())
    return {"all_ok": all_ok, "files": result}

@app.get("/login")
def login(request: Request):
    """Start the web OAuth flow (does not block the server on a local browser)."""
    if not gmail_configured():
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured on this deployment.",
        )
    try:
        auth_url, _state = build_authorization_url(_oauth_redirect_uri(request))
    except Exception as exc:
        log.warning("Could not build login URL: %s", exc)
        raise HTTPException(status_code=503, detail="Could not start Google sign-in.")
    return RedirectResponse(url=auth_url, status_code=302)

# ------------------------------------------
# REAL GMAIL INBOX, DETAIL, ATTACHMENTS, SEND
# ------------------------------------------
@app.get("/api/gmail/status")
def gmail_status():
    profile = require_google_session()
    accounts = []
    try:
        accounts = [
            {"email": a.get("email"), "name": a.get("name"), "active": a.get("active")}
            for a in list_accounts()
        ]
    except Exception as exc:
        log.warning("Could not list Gmail accounts: %s", exc)
    return {
        "status": "ok",
        "connected": True,
        "email": profile.get("email"),
        "name": profile.get("name"),
        "picture": profile.get("picture"),
        "accounts": accounts,
        "retrieval": "gmail-api",
        "push_notifications": False,
    }


@app.post("/api/gmail/disconnect")
def gmail_disconnect(request: Request):
    """Remove the signed-in account's stored token and invalidate its sessions."""
    profile = require_google_session()
    email = (profile.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(status_code=401, detail="No connected Google account.")
    remove_account(email)
    _drop_sessions_for_email(email)
    response = JSONResponse({
        "status": "disconnected",
        "email": email,
        "new_active": get_active_email(),
    })
    _clear_session_cookie(response, request)
    return response


@app.get("/api/gmail/messages")
def gmail_messages(
    folder: str = "inbox",
    page_token: str = None,
    max_results: int = 20,
    search: str = None,
):
    profile = require_google_session()
    require_gmail_credentials()
    account = (profile.get("email") or "").lower()
    try:
        data = gmail_list_messages(
            folder=folder,
            page_token=page_token,
            max_results=max_results,
            search=search,
            email=account,
        )
    except GmailError as exc:
        log.warning("Gmail list failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        log.exception("Unexpected Gmail list failure")
        raise HTTPException(status_code=502, detail="Could not load Gmail messages.")
    return {"status": "ok", "account": account, **data}


@app.get("/api/gmail/messages/{message_id}")
def gmail_message(message_id: str):
    _validate_gmail_id(message_id)
    profile = require_google_session()
    require_gmail_credentials()
    account = (profile.get("email") or "").lower()
    try:
        message = gmail_get_message(message_id, email=account, mark_read=True)
    except GmailError as exc:
        log.warning("Gmail detail failed for %s: %s", message_id, exc)
        status = 404 if "404" in str(exc) else 502
        detail = "Message not found in Gmail." if status == 404 else str(exc)
        raise HTTPException(status_code=status, detail=detail)
    except Exception:
        log.exception("Unexpected Gmail detail failure")
        raise HTTPException(status_code=502, detail="Could not load this Gmail message.")
    return {"status": "ok", "account": account, "message": message}


@app.post("/api/gmail/messages/{message_id}/read")
async def gmail_read_state(message_id: str, request: Request):
    _validate_gmail_id(message_id)
    profile = require_google_session()
    require_gmail_credentials()
    account = (profile.get("email") or "").lower()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="A JSON body with 'unread' is required.")
    unread = body.get("unread", True)
    if not isinstance(unread, bool):
        raise HTTPException(status_code=400, detail="'unread' must be true or false.")
    try:
        result = gmail_set_unread(message_id, unread=unread, email=account)
    except GmailError as exc:
        log.info("Gmail read-state update failed for %s: %s", message_id, exc)
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        log.exception("Unexpected Gmail read-state failure")
        raise HTTPException(status_code=502, detail="Could not update the read state.")
    return {"status": "ok", "account": account, **result}


@app.post("/api/gmail/messages/{message_id}/trash")
def gmail_trash(message_id: str):
    _validate_gmail_id(message_id)
    profile = require_google_session()
    require_gmail_credentials()
    account = (profile.get("email") or "").lower()
    try:
        result = gmail_trash_message(message_id, email=account)
    except GmailError as exc:
        log.info("Gmail trash failed for %s: %s", message_id, exc)
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        log.exception("Unexpected Gmail trash failure")
        raise HTTPException(status_code=502, detail="Could not move the message to Trash.")
    return {"status": "trashed", "account": account, **result}


@app.get("/api/gmail/messages/{message_id}/attachments/{attachment_id}")
def gmail_attachment(message_id: str, attachment_id: str):
    _validate_gmail_id(message_id)
    _validate_gmail_id(attachment_id, "attachment")
    profile = require_google_session()
    require_gmail_credentials()
    account = (profile.get("email") or "").lower()
    try:
        data = gmail_get_attachment(message_id, attachment_id, email=account)
    except GmailError as exc:
        log.info("Gmail attachment unavailable: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception:
        log.exception("Unexpected Gmail attachment failure")
        raise HTTPException(status_code=502, detail="Could not download this attachment.")

    filename = _safe_download_name(data["name"])
    quoted = quote(filename)
    return Response(
        content=data["data"],
        media_type=data["mime_type"],
        headers={
            "Content-Disposition": "attachment; filename=\"%s\"; filename*=UTF-8''%s"
            % (filename.replace('"', ""), quoted),
            "Content-Length": str(len(data["data"])),
            "Cache-Control": "no-store",
        },
    )


def _encryption_info(payload: dict) -> dict:
    """Describe the path that was actually used, without exposing key material."""
    method = payload.get("method") or "unknown"
    return {
        "method": method,
        "level": payload.get("level"),
        "content_type": payload.get("content_type") or "text",
        "legacy_unencrypted": method == "NONE",
    }


def _decode_text_bytes(raw: bytes) -> str:
    if raw is None:
        raise ValueError("Decryption returned no content.")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Decrypted content is not valid UTF-8 text.")


def _decrypt_gmail_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("The QuMail payload is not a JSON object.")

    if payload.get("type") == "multi_file":
        text = None
        legacy_plaintext = False
        text_encryption = None
        text_payload = payload.get("text_payload")
        if text_payload:
            if not isinstance(text_payload, dict):
                raise ValueError("The QuMail text payload is malformed.")
            text = _decode_text_bytes(decrypt_payload(text_payload))
            text_encryption = _encryption_info(text_payload)
        elif payload.get("plain_text"):
            # Older QuMail bundles embedded the body as plaintext. Never hide that.
            text = str(payload["plain_text"])
            legacy_plaintext = True

        file_payloads = payload.get("files") or []
        if not isinstance(file_payloads, list):
            raise ValueError("The QuMail attachment bundle is malformed.")
        if not file_payloads and not text_payload and not legacy_plaintext:
            raise ValueError("The QuMail bundle contains no message or attachments.")

        files = []
        file_encryption = []
        for index, file_payload in enumerate(file_payloads):
            if not isinstance(file_payload, dict):
                raise ValueError("Attachment %s in the QuMail bundle is malformed." % (index + 1))
            file_payload["sender"] = payload.get("sender", "Unknown")
            raw = decrypt_payload(file_payload)
            if raw is None:
                raise ValueError("Attachment %s could not be decrypted." % (index + 1))
            files.append({
                "name": file_payload.get("filename", "file_%s" % (index + 1)),
                "size": len(raw),
                "data_b64": (
                    base64.b64encode(raw).decode("utf-8")
                    if len(raw) <= 5 * 1024 * 1024 else None
                ),
            })
            file_encryption.append(_encryption_info(file_payload))

        warning = None
        if legacy_plaintext:
            warning = (
                "This legacy QuMail bundle stored the message body as plaintext. "
                "Only the attachments were encrypted."
            )
        return {
            "status": "decrypted",
            "text": text,
            "attachments": files,
            "legacy_plaintext_text": legacy_plaintext,
            "encryption": {
                "container": "multi_file",
                "text": text_encryption,
                "files": file_encryption,
                "legacy_unencrypted_text": legacy_plaintext,
            },
            "warning": warning,
        }

    method = payload.get("method")
    if not method:
        raise ValueError("This message does not contain a QuMail encrypted payload.")

    if method == "NONE":
        raw = base64.b64decode(payload.get("data", ""))
    else:
        raw = decrypt_payload(payload)
    if raw is None:
        raise ValueError("Decryption returned no content.")

    info = _encryption_info(payload)
    warning = (
        "This legacy payload is only Base64-encoded, not encrypted."
        if info["legacy_unencrypted"] else None
    )

    if payload.get("content_type") == "file":
        return {
            "status": "decrypted",
            "text": None,
            "attachments": [{
                "name": payload.get("filename", "decrypted_file"),
                "size": len(raw),
                "data_b64": (
                    base64.b64encode(raw).decode("utf-8")
                    if len(raw) <= 5 * 1024 * 1024 else None
                ),
            }],
            "encryption": info,
            "warning": warning,
        }

    return {
        "status": "decrypted",
        "text": _decode_text_bytes(raw),
        "attachments": [],
        "encryption": info,
        "warning": warning,
    }


@app.post("/api/gmail/messages/{message_id}/decrypt")
def gmail_decrypt(message_id: str):
    _validate_gmail_id(message_id)
    profile = require_google_session()
    require_gmail_credentials()
    account = (profile.get("email") or "").lower()
    try:
        payload = fetch_payload_from_email(message_id, email=account)
    except Exception as exc:
        log.warning("QuMail payload fetch failed for %s: %s", message_id, exc)
        raise HTTPException(status_code=502, detail="Could not retrieve the encrypted payload from Gmail.")
    if not payload:
        raise HTTPException(
            status_code=422,
            detail="No QuMail encrypted payload was found on this message.",
        )
    try:
        return _decrypt_gmail_payload(payload)
    except Exception as exc:
        log.warning("QuMail decryption failed for %s: %s", message_id, exc)
        raise HTTPException(
            status_code=422,
            detail="Decryption failed: %s" % exc,
        )


@app.post("/api/gmail/send")
async def gmail_send(request: Request):
    """Send an explicitly chosen plaintext or QuMail-encrypted email via Gmail."""
    profile = require_google_session()
    require_gmail_credentials()
    account = (profile.get("email") or "").lower()

    content_type = request.headers.get("content-type", "")
    attachments = []
    try:
        if "application/json" in content_type:
            body = await request.json()
            recipient_raw = str(body.get("recipient") or body.get("recipients") or "")
            subject = str(body.get("subject") or "")
            text_body = str(body.get("body") or "")
            mode = str(body.get("mode") or "").strip().lower()
            level_raw = body.get("level")
            request_id = str(body.get("client_request_id") or "").strip()
        else:
            form = await request.form()
            recipient_raw = str(form.get("recipient") or form.get("recipients") or "")
            subject = str(form.get("subject") or "")
            text_body = str(form.get("body") or "")
            mode = str(form.get("mode") or "").strip().lower()
            level_raw = form.get("level")
            request_id = str(form.get("client_request_id") or "").strip()

            uploads = [u for u in form.getlist("files") if hasattr(u, "read")]
            if len(uploads) > MAX_GMAIL_ATTACHMENTS:
                raise HTTPException(
                    status_code=413,
                    detail="Too many attachments (limit %s)." % MAX_GMAIL_ATTACHMENTS,
                )
            total = 0
            for upload in uploads:
                data = await upload.read()
                total += len(data)
                if len(data) > MAX_ATTACHMENT_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Attachment '%s' exceeds the size limit." % upload.filename,
                    )
                if total > 20 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Attachments exceed 20 MB in total.")
                attachments.append({
                    "filename": upload.filename or "attachment",
                    "mime_type": upload.content_type or "application/octet-stream",
                    "content": data,
                })
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read the send request.")

    recipients = _parse_recipients(recipient_raw)
    if mode not in ("plain", "encrypted"):
        raise HTTPException(
            status_code=400,
            detail="Choose an explicit mode: 'plain' or 'encrypted'.",
        )
    subject = _clean_header(subject)
    if len(subject) > 998:
        raise HTTPException(status_code=400, detail="Subject is too long.")
    if len(text_body.encode("utf-8")) > MAX_GMAIL_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Message body is too large.")

    level = None
    if mode == "encrypted":
        if len(recipients) != 1:
            raise HTTPException(
                status_code=400,
                detail="Encrypted sends require exactly one recipient.",
            )
        try:
            level = int(level_raw)
        except Exception:
            raise HTTPException(status_code=400, detail="Encryption level is required (1-4).")
        if level not in (1, 2, 3, 4):
            raise HTTPException(status_code=400, detail="Encryption level must be 1-4.")
        if level == 1 and attachments:
            raise HTTPException(
                status_code=400,
                detail="Level 1 (OTP) is text-only. Remove attachments or choose L2-L4.",
            )
        if not text_body and not attachments:
            raise HTTPException(status_code=400, detail="Message body or attachments required.")

    if request_id and not re.match(r"^[A-Za-z0-9_-]{8,128}$", request_id):
        raise HTTPException(status_code=400, detail="Invalid client_request_id.")

    digest = hashlib.sha256()
    digest.update(account.encode())
    digest.update(mode.encode())
    digest.update(str(level).encode())
    digest.update("|".join(recipients).encode())
    digest.update(subject.encode())
    digest.update(text_body.encode())
    for item in attachments:
        digest.update(item["filename"].encode())
        digest.update(hashlib.sha256(item["content"]).digest())
    key = _send_receipt_key(request_id, digest.hexdigest())

    with _send_lock:
        _purge_send_receipts_locked()
        cached = _send_receipts.get(key)
        if cached:
            return JSONResponse(content=cached[1], headers={"X-Idempotent-Replay": "true"})
        if key in _send_inflight:
            raise HTTPException(
                status_code=409,
                detail="A send with this request id is already in progress.",
            )
        _send_inflight.add(key)

    try:
        if mode == "encrypted":
            recipient = recipients[0]
            if attachments:
                encrypted_files = [
                    clean_payload(encrypt_data(
                        item["content"],
                        level,
                        "file",
                        recipient_email=recipient,
                        original_filename=item["filename"],
                    ))
                    for item in attachments
                ]
                bundle = {
                    "type": "multi_file",
                    "file_count": len(encrypted_files),
                    "files": encrypted_files,
                    "subject": subject,
                }
                if text_body:
                    bundle["text_payload"] = clean_payload(encrypt_data(
                        text_body.encode("utf-8"),
                        level,
                        "text",
                        recipient_email=recipient,
                    ))
                result = send_email(bundle, recipient, email=account)
            else:
                payload = clean_payload(encrypt_data(
                    text_body.encode("utf-8"),
                    level,
                    "text",
                    recipient_email=recipient,
                ))
                payload["subject"] = subject
                result = send_email(payload, recipient, email=account)
        else:
            result = send_plain_message(
                ", ".join(recipients), subject, text_body, attachments, email=account
            )

        response = {
            "status": "accepted",
            "id": result.get("id"),
            "thread_id": result.get("threadId"),
            "account": account,
            "recipients": recipients,
            "mode": mode,
            "level": level,
            "delivery": "accepted-by-gmail",
            "delivery_note": (
                "Gmail accepted this send request. This confirms API acceptance, "
                "not guaranteed delivery to the recipient's inbox."
            ),
        }
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("Gmail send failed for %s: %s", account, exc)
        raise HTTPException(
            status_code=502,
            detail="Gmail did not accept the message, so nothing was sent: %s" % exc,
        )
    finally:
        with _send_lock:
            _send_inflight.discard(key)

    with _send_lock:
        _send_receipts[key] = (time.time() + SEND_RECEIPT_TTL_SECONDS, response)
    return response


# ------------------------------------------
# ENCRYPTION & SENDING
# ------------------------------------------
@app.post("/encrypt")
async def encrypt_message(
    level: int = Form(...), 
    message: str = Form(None),
    subject: str = Form(None),
    recipient_email: str = Form(None),
    file: UploadFile = File(None)
):
    require_gmail_credentials()
    try:
        if file:
            log.info("Encrypting file for recipient (level %s)", level)
            data = await file.read()
            payload = encrypt_data(data, level, "file", recipient_email, file.filename)
        else:
            if not message:
                raise HTTPException(status_code=400, detail="message or file required")
            payload = encrypt_data(message.encode(), level, "text", recipient_email=recipient_email)
        
        if subject:
            payload["subject"] = subject

        return clean_payload(payload)

    except HTTPException:
        raise
    except Exception as e:
        log.warning("Encryption failed at level %s: %s", level, e)
        raise HTTPException(
            status_code=500,
            detail=f"Encryption failed at Level {level}: {str(e)}",
        )

@app.post("/encrypt_multi")
async def encrypt_multi(request: Request):
    """
    Encrypt multiple files at once and return an array of payloads.
    The frontend sends all files in one multipart request.
    """
    require_gmail_credentials()
    try:
        form = await request.form()
        level = int(form.get("level", 2))
        subject = form.get("subject", "")
        recipient_email = form.get("recipient_email", "")
        files = form.getlist("files")

        payloads = []
        for f in files:
            data = await f.read()
            payload = encrypt_data(data, level, "file", recipient_email, f.filename)
            if subject:
                payload["subject"] = subject
            payloads.append(clean_payload(payload))

        return {"status": "ok", "payloads": payloads, "count": len(payloads)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send_email_bundle")
async def send_email_bundle(request: Request):
    """Send encrypted file payloads (+ optional encrypted text) as ONE Gmail email."""
    require_gmail_credentials()
    try:
        body = await request.json()
        recipient = body.get("recipient", "")
        payloads = body.get("payloads", [])
        subject = body.get("subject", "")
        plain_text = body.get("plain_text", "")
        if not recipient or not payloads:
            raise HTTPException(status_code=400, detail="recipient and payloads required")

        level = body.get("level") or (payloads[0].get("level") if payloads else 2)

        # Encrypt the message body too - never ship a plaintext copy alongside
        # the encrypted attachments. If encryption fails, the request fails and
        # nothing is sent.
        text_payload = None
        if plain_text:
            text_payload = clean_payload(
                encrypt_data(
                    plain_text.encode("utf-8"),
                    int(level),
                    "text",
                    recipient_email=recipient,
                )
            )

        bundle = {
            "type": "multi_file",
            "file_count": len(payloads),
            "files": payloads,
            "subject": subject,
        }
        if text_payload is not None:
            bundle["text_payload"] = text_payload

        log.info("Sending encrypted bundle: %s file(s)", len(payloads))
        result = send_email(bundle, recipient)
        return {"status": "sent", "id": result.get("id"), "file_count": len(payloads)}
    except HTTPException:
        raise
    except Exception as e:
        log.warning("Bundle send failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send_email")
async def send_encrypted(request: Request):
    """Accept either JSON body or form data for flexibility with large payloads."""
    require_gmail_credentials()
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            payload_dict = body.get("payload")
            recipient = body.get("recipient")
            if isinstance(payload_dict, str):
                payload_dict = json.loads(payload_dict)
        else:
            form = await request.form()
            payload_str = form.get("payload", "")
            recipient = form.get("recipient", "")
            payload_dict = json.loads(payload_str)

        if not payload_dict or not recipient:
            raise HTTPException(status_code=400, detail="payload and recipient required")

        result = send_email(payload_dict, recipient)
        return {"status": "sent", "id": result.get("id")}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/send_plain")
async def send_plain(recipient: str = Form(...), file: UploadFile = File(...)):
    require_gmail_credentials()
    try:
        file_content = await file.read()
        result = send_plain_file(recipient, file_content, file.filename)
        return {"status": "sent", "id": result.get("id")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ------------------------------------------
# RECEIVE & DECRYPT
# ------------------------------------------
@app.get("/receive_email")
def receive_all():
    try:
        profile = require_google_session()
        user_email = profile.get("email", "").lower()

        # Fetch only unseen messages — newest first, stop at first success
        msgs = fetch_qumail_emails(active_email=user_email)
        if not msgs:
            return {"status": "no-new-messages", "messages": []}

        log.info("Syncing inbox for %s — %s new message(s) to check.", user_email, len(msgs))

        results = []
        for msg in msgs:
            msg_id = msg["id"]
            try:
                payload = fetch_payload_from_email(msg_id, email=user_email)
                if not payload:
                    mark_seen(user_email, msg_id)
                    continue

                sender_raw = payload.get("sender", "").lower()

                # Skip self-sent
                if user_email and user_email in sender_raw:
                    mark_seen(user_email, msg_id)
                    continue

                method = payload.get("method", "")

                # --- MULTI-FILE BUNDLE ---
                if payload.get("type") == "multi_file":
                    file_payloads = payload.get("files", [])
                    plain_text = payload.get("plain_text", "")
                    text_payload = payload.get("text_payload")
                    if text_payload:
                        try:
                            plain_text = decrypt_payload(text_payload).decode(
                                "utf-8", errors="replace"
                            )
                        except Exception as te:
                            log.warning("Could not decrypt bundle text for %s: %s", msg_id, te)
                            plain_text = ""
                    attachments = []
                    attachment_dir = ATTACHMENT_DIR
                    attachment_dir.mkdir(parents=True, exist_ok=True)

                    for i, fp in enumerate(file_payloads):
                        fp["sender"] = payload.get("sender", "Unknown")
                        try:
                            file_bytes = decrypt_payload(fp)
                            if not file_bytes:
                                continue
                            fname = fp.get("filename", f"file_{i+1}")
                            att_path = os.path.join(attachment_dir, f"{msg_id}_{i}_{fname}")
                            with open(att_path, "wb") as af:
                                af.write(file_bytes)
                            attachments.append({
                                "name": fname,
                                "size": len(file_bytes),
                                "type": "",
                                "download_url": f"/attachment/{msg_id}_{i}",
                                "file_data_b64": base64.b64encode(file_bytes).decode("utf-8")
                            })
                        except Exception as fe:
                            log.warning("Failed to decrypt file %s in bundle: %s", i, fe)

                    import time as _time
                    preview = plain_text[:200] if plain_text else f"[{len(attachments)} encrypted file(s)]"
                    msg_data = {
                        "id": msg_id,
                        "key_id": "Bundle",
                        "level": file_payloads[0].get("level") if file_payloads else 2,
                        "sender": payload.get("sender", "Unknown"),
                        "subject": payload.get("subject", "No Subject"),
                        "content_type": "multi_file",
                        "time": _time.strftime("%I:%M %p"),
                        "starred": False,
                        "read": False,
                        "preview": preview,
                        "decrypted_text": plain_text,
                        "attachments": attachments
                    }
                    store_msg = {k: v for k, v in msg_data.items() if k != "file_data_b64"}
                    store_msg["attachments"] = [{k: v for k, v in a.items() if k != "file_data_b64"} for a in attachments]
                    try:
                        append_message(user_email, "inbox", store_msg)
                    except Exception as se:
                        print(f"Failed to save bundle message: {se}")
                    mark_seen(user_email, msg_id)
                    results.append(msg_data)
                    continue

                if not method:
                    mark_seen(user_email, msg_id)
                    continue

                key_id = "Session-Key"
                try:
                    plaintext_bytes = decrypt_payload(payload)
                    if not plaintext_bytes:
                        mark_seen(user_email, msg_id)
                        continue

                    if "key_id" in payload:
                        key_id = payload["key_id"]
                    elif "key_ids" in payload:
                        key_id = ",".join(payload["key_ids"])
                    elif "sender_public_hash" in payload:
                        key_id = "Secure-Handshake-Key"

                except Exception as de:
                    if "padding" not in str(de).lower():
                        print(f"Decryption error for {msg_id}: {de}")
                    # Mark undecryptable messages as seen — stop retrying
                    mark_seen(user_email, msg_id)
                    continue

                content_type = payload.get("content_type", "text")
                import time as _time
                msg_data = {
                    "id": msg_id,
                    "key_id": key_id,
                    "level": payload.get("level"),
                    "sender": payload.get("sender", "Unknown"),
                    "subject": payload.get("subject", "No Subject"),
                    "content_type": content_type,
                    "time": _time.strftime("%I:%M %p"),
                    "starred": False,
                    "read": False
                }

                if content_type == "file":
                    filename = payload.get("filename", "decrypted_file")
                    msg_data["filename"] = filename
                    msg_data["preview"] = f"[Encrypted file: {filename}]"

                    # Save file to disk for persistent download access
                    attachment_dir = ATTACHMENT_DIR
                    attachment_dir.mkdir(parents=True, exist_ok=True)
                    att_path = os.path.join(attachment_dir, f"{msg_id}_{filename}")
                    with open(att_path, "wb") as af:
                        af.write(plaintext_bytes)

                    # Build attachments array so the UI renders the download card
                    msg_data["attachments"] = [{
                        "name": filename,
                        "size": len(plaintext_bytes),
                        "type": "",
                        "download_url": f"/attachment/{msg_id}",
                        "file_data_b64": base64.b64encode(plaintext_bytes).decode("utf-8")
                    }]
                else:
                    text = plaintext_bytes.decode("utf-8", errors="ignore")
                    msg_data["decrypted_text"] = text
                    msg_data["text"] = text
                    msg_data["preview"] = text[:200]

                # Persist to disk mail store — strip binary and path fields
                store_msg = {k: v for k, v in msg_data.items() 
                             if k not in ("file_data_b64", "attachment_path")}
                try:
                    added = append_message(user_email, "inbox", store_msg)
                    if added:
                        print(f"Saved message {msg_id} to inbox for {user_email}")
                    else:
                        print(f"Message {msg_id} already in store (duplicate)")
                except Exception as store_err:
                    print(f"Failed to save message {msg_id} to mail store: {store_err}")
                mark_seen(user_email, msg_id)
                results.append(msg_data)

            except Exception as e:
                print(f"Error processing {msg_id}: {e}")
                continue

        if not results:
            return {"status": "no-new-messages", "messages": []}

        latest = results[0]
        return {"status": "success", "messages": results, **latest}

    except HTTPException:
        raise
    except Exception as e:
        log.exception("Inbox sync failed")
        raise HTTPException(status_code=502, detail="Could not sync Gmail inbox: %s" % e)


# ------------------------------------------
# MAIL STORE ENDPOINTS
# ------------------------------------------

@app.get("/mail_store")
def get_mail_store():
    """Return all folders for the active account from disk storage."""
    profile = require_google_session()
    try:
        target_email = profile.get("email", "").lower()
        folders = get_all_folders(target_email)
        return {"status": "ok", "email": target_email, "folders": folders}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/attachment/{att_id:path}")
def get_attachment(att_id: str):
    """Serve a previously decrypted attachment. Requires an authenticated account."""
    require_google_session()
    if "/" in att_id or "\\" in att_id or ".." in att_id or not att_id.strip():
        raise HTTPException(status_code=404, detail="Attachment not found")

    if not ATTACHMENT_DIR.is_dir():
        raise HTTPException(status_code=404, detail="Attachment not found")

    prefix = att_id + "_"
    for entry in ATTACHMENT_DIR.iterdir():
        if entry.is_file() and entry.name.startswith(prefix):
            original_name = entry.name[len(prefix):]
            return FileResponse(
                str(entry),
                filename=original_name,
                media_type="application/octet-stream",
            )
    raise HTTPException(status_code=404, detail="Attachment not found")


@app.post("/mail_store/sync_all")
async def sync_all_folders(request: Request):
    """
    Bulk-save all folders from the frontend into disk store.
    Called on first load to migrate any data the frontend has in memory/localStorage.
    Only saves messages that aren't already in the store (deduplicates by id).
    """
    profile = require_google_session()
    try:
        body = await request.json()
        email = body.get("email", "").strip().lower()
        folders_data = body.get("folders", {})
        if not email:
            email = profile.get("email", "").lower()

        saved_counts = {}
        for folder, messages in folders_data.items():
            if not isinstance(messages, list):
                continue
            count = 0
            for msg in messages:
                if append_message(email, folder, msg):
                    count += 1
            saved_counts[folder] = count

        return {"status": "synced", "email": email, "saved": saved_counts}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mail_store/sent")
async def save_sent_message(request: Request):
    """Persist a sent message to disk mail store."""
    profile = require_google_session()
    try:
        msg = await request.json()
        email = profile.get("email", "").lower()
        added = append_message(email, "sent", msg)
        return {"status": "saved" if added else "duplicate"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mail_store/move")
async def move_mail_message(request: Request):
    """Move a message between folders in disk store."""
    profile = require_google_session()
    try:
        body = await request.json()
        email = profile.get("email", "").lower()
        ok = move_message(email, body["from"], body["to"], body["id"])
        return {"status": "moved" if ok else "not_found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mail_store/update")
async def update_mail_message(request: Request):
    """Patch fields on a message (e.g. starred, read)."""
    profile = require_google_session()
    try:
        body = await request.json()
        email = profile.get("email", "").lower()
        update_message(email, body["folder"], body["id"], body["updates"])
        return {"status": "updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/mail_store/{folder}/{msg_id}")
def delete_mail_message(folder: str, msg_id: str):
    """
    Permanently delete a message from mail_store.json.
    For real Gmail IDs (inbox messages): also marks seen so it is never re-fetched.
    """
    profile = require_google_session()
    try:
        email = profile.get("email", "").lower()
        ok = store_delete_message(email, folder, msg_id)
        # If it's a real Gmail ID, mark seen so sync never re-delivers it
        s = str(msg_id).strip()
        if len(s) > 13 and not s.isdigit():
            mark_seen(email, msg_id)
        return {"status": "deleted" if ok else "not_found"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete_message")
async def delete_message(request: Request):
    """
    Delete a message: removes from mail_store.json and marks seen in Gmail.
    Called by the frontend for inbox messages with real Gmail IDs.
    """
    profile = require_google_session()
    try:
        body = await request.json()
        msg_id = str(body.get("id", "")).strip()
        folder = body.get("folder", "inbox")
        if not msg_id:
            raise HTTPException(status_code=400, detail="id required")
        email = profile.get("email", "").lower()
        store_delete_message(email, folder, msg_id)
        mark_seen(email, msg_id)
        return {"status": "deleted", "id": msg_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/decrypt_email")
def decrypt_email(message_id: str = Form(...)):
    profile = require_google_session()
    try:
        payload = fetch_payload_from_email(
            message_id, email=(profile.get("email") or "").lower()
        )
        if not payload:
            raise HTTPException(status_code=404, detail="Message payload not found")

        method = payload.get("method", "")
        content_type = payload.get("content_type", "text")

        if method == "NONE":
            plaintext_bytes = base64.b64decode(payload.get("data", ""))
        else:
            plaintext_bytes = decrypt_payload(payload)

        if plaintext_bytes is None:
            raise HTTPException(status_code=400, detail="Decryption failed")

        if content_type == "file":
            return {
                "content_type": "file",
                "filename": payload.get("filename", "decrypted_file"),
                "message": base64.b64encode(plaintext_bytes).decode("utf-8"),
            }

        return {
            "content_type": "text",
            "message": plaintext_bytes.decode("utf-8", errors="ignore"),
        }

    except HTTPException:
        raise
    except Exception as e:
        log.warning("Decrypt error for %s: %s", message_id, e)
        raise HTTPException(status_code=400, detail="Could not decrypt message: %s" % e)


# ------------------------------------------
# SPA FALLBACK (must be registered last)
# ------------------------------------------
@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    reserved = (
        "api/", "auth/", "mail_store", "attachment", "accounts", "send_",
        "encrypt", "receive_email", "mock-digilocker", "static",
        "health", "me", "login", "logout", "sync", "verify_files",
        "switch_account", "add_account", "remove_account",
        "delete_message", "decrypt_email", "docs", "redoc", "openapi.json",
    )
    if full_path.startswith(reserved):
        raise HTTPException(status_code=404, detail="Not found")

    index_file = FRONTEND_BUILD_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="Frontend build not available")

    candidate = (FRONTEND_BUILD_DIR / full_path).resolve()
    if FRONTEND_BUILD_DIR in candidate.parents and candidate.is_file():
        media_type, _ = mimetypes.guess_type(str(candidate))
        return FileResponse(candidate, media_type=media_type)

    return FileResponse(index_file)