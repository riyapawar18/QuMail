from pathlib import Path
from typing import Dict
import secrets
import time

from fastapi import FastAPI, Request, Form, HTTPException, Header
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# ------------------ PATH + FOLDER SETUP ------------------

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DOCS_DIR = STATIC_DIR / "docs"
DEMO_DOCS_DIR = STATIC_DIR / "demo_docs"

# Create static/ and static/docs/ if they don't exist
STATIC_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)
DEMO_DOCS_DIR.mkdir(parents=True, exist_ok=True)

print("BASE_DIR:", BASE_DIR)
print("STATIC_DIR:", STATIC_DIR, "exists:", STATIC_DIR.exists())
print("DEMO_DOCS_DIR:", DEMO_DOCS_DIR, "exists:", DEMO_DOCS_DIR.exists())

app = FastAPI(
    title="Mock DigiLocker",
    description="Educational mock DigiLocker API for QuMail demo"
)

# Mount static folder using ABSOLUTE path
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ------------------ IN-MEMORY DATA ------------------

AUTH_CODES: Dict[str, dict] = {}      # auth_code -> { user_id, client_id, redirect_uri, expires_at }
TOKENS: Dict[str, dict] = {}          # access_token -> { user_id, expires_at }
REFRESH_TOKENS: Dict[str, dict] = {}  # refresh_token -> { user_id, expires_at }

DEMO_USER_ID = "user_001"
DEMO_USER_EMAIL = "demo.user@digilocker.mock"

# Only synthetic, generated samples are exposed. Real personal documents that
# may exist under static/docs/ are never referenced by this registry.
USER_DOCS = {
    DEMO_USER_ID: [
        {
            "id": "doc_degree",
            "name": "Degree Certificate (Sample)",
            "file_name": "sample_degree_certificate.pdf",
            "type": "EDU",
            "issuer": "Demo University (sample)",
            "issue_date": "2026-01-15",
            "size_kb": 2,
            "is_sample": True,
            "sample_label": "Synthetic sample - not a university record",
        },
        {
            "id": "doc_marksheet",
            "name": "Academic Marksheet (Sample)",
            "file_name": "sample_marksheet.pdf",
            "type": "EDU",
            "issuer": "Demo State Board (sample)",
            "issue_date": "2026-01-10",
            "size_kb": 2,
            "is_sample": True,
            "sample_label": "Synthetic sample - not a board record",
        },
        {
            "id": "doc_identity",
            "name": "Identity Document (Sample)",
            "file_name": "sample_identity_card.pdf",
            "type": "ID",
            "issuer": "Demo Identity Issuer (sample)",
            "issue_date": "2026-01-05",
            "size_kb": 2,
            "is_sample": True,
            "sample_label": "Synthetic sample - not a government ID",
        },
    ]
}


def resolve_demo_doc_path(doc: dict) -> Path:
    """Return a path only if it resolves inside the trusted synthetic docs dir."""
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    file_name = doc.get("file_name", "")
    if not file_name or Path(file_name).name != file_name or not file_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid document filename")
    path = (DEMO_DOCS_DIR / file_name).resolve()
    if path.parent != DEMO_DOCS_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404, detail="Sample document file is not available")
    return path

# ------------------ UTILS ------------------

def generate_code(length: int = 32) -> str:
    return secrets.token_urlsafe(length)

def now() -> int:
    return int(time.time())

def validate_client(client_id: str, redirect_uri: str):
    if not client_id or not redirect_uri:
        raise HTTPException(status_code=400, detail="Invalid client or redirect URI")

def require_token(auth_header: str) -> str:
    """
    auth_header is the raw Authorization header that should look like:
    'Bearer <access_token>'
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    access_token = auth_header.split(" ", 1)[1].strip()
    token_data = TOKENS.get(access_token)
    if not token_data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if token_data["expires_at"] < now():
        raise HTTPException(status_code=401, detail="Token expired")

    return token_data["user_id"]

# ------------------ OAUTH AUTHORIZE ------------------

@app.get("/oauth/authorize", response_class=HTMLResponse)
async def oauth_authorize(
    request: Request,
    client_id: str,
    redirect_uri: str,
    state: str = "",
    response_type: str = "code"
):
    validate_client(client_id, redirect_uri)

    if response_type != "code":
        raise HTTPException(status_code=400, detail="Unsupported response_type")

    html_content = f"""
    <html>
        <head>
            <title>Mock DigiLocker - Authorize</title>
        </head>
        <body style="font-family: Arial; padding: 20px;">
            <h2>Mock DigiLocker - Authorization</h2>
            <p>You are logged in as: <b>{DEMO_USER_EMAIL}</b></p>
            <p>Application <b>{client_id}</b> is requesting access to your DigiLocker documents.</p>
            <form method="post" action="/oauth/authorize/confirm">
                <input type="hidden" name="client_id" value="{client_id}" />
                <input type="hidden" name="redirect_uri" value="{redirect_uri}" />
                <input type="hidden" name="state" value="{state}" />
                <button type="submit" name="action" value="allow">Allow</button>
                <button type="submit" name="action" value="deny">Deny</button>
            </form>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.post("/oauth/authorize/confirm")
async def oauth_authorize_confirm(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    action: str = Form(...)
):
    validate_client(client_id, redirect_uri)

    if action == "deny":
        return RedirectResponse(
            url=f"{redirect_uri}?error=access_denied&state={state}",
            status_code=302
        )

    auth_code = generate_code(16)
    AUTH_CODES[auth_code] = {
        "user_id": DEMO_USER_ID,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "expires_at": now() + 300  # 5 minutes
    }

    redirect_url = f"{redirect_uri}?code={auth_code}&state={state}"
    return RedirectResponse(url=redirect_url, status_code=302)

# ------------------ TOKEN ENDPOINT ------------------

@app.post("/oauth/token")
async def oauth_token(
    grant_type: str = Form(...),
    code: str = Form(None),
    refresh_token: str = Form(None),
    client_id: str = Form(...),
    client_secret: str = Form(None),
    redirect_uri: str = Form(None)
):
    if grant_type == "authorization_code":
        if not code:
            raise HTTPException(status_code=400, detail="Missing code")

        data = AUTH_CODES.get(code)
        if not data:
            raise HTTPException(status_code=400, detail="Invalid code")

        if data["expires_at"] < now():
            raise HTTPException(status_code=400, detail="Code expired")

        if data["client_id"] != client_id:
            raise HTTPException(status_code=400, detail="Client mismatch")

        if redirect_uri and data["redirect_uri"] != redirect_uri:
            raise HTTPException(status_code=400, detail="Redirect URI mismatch")

        user_id = data["user_id"]

        access_token = generate_code(24)
        refresh_token_val = generate_code(24)
        TOKENS[access_token] = {
            "user_id": user_id,
            "expires_at": now() + 3600  # 1 hour
        }
        REFRESH_TOKENS[refresh_token_val] = {
            "user_id": user_id,
            "expires_at": now() + 30 * 24 * 3600  # 30 days
        }

        del AUTH_CODES[code]

        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh_token_val
        })

    elif grant_type == "refresh_token":
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Missing refresh_token")

        data = REFRESH_TOKENS.get(refresh_token)
        if not data or data["expires_at"] < now():
            raise HTTPException(status_code=400, detail="Invalid or expired refresh_token")

        user_id = data["user_id"]

        access_token = generate_code(24)
        TOKENS[access_token] = {
            "user_id": user_id,
            "expires_at": now() + 3600
        }

        return JSONResponse({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": refresh_token
        })

    else:
        raise HTTPException(status_code=400, detail="Unsupported grant_type")

# ------------------ DOC LIST + DOWNLOAD ------------------

@app.get("/api/user/docs")
async def get_user_docs(Authorization: str = Header(...)):
    """
    Returns a list of user's documents.
    Authorization header is expected as: Bearer <access_token>
    """
    user_id = require_token(Authorization)
    docs = USER_DOCS.get(user_id, [])
    return docs


@app.get("/api/user/docs/{doc_id}")
async def get_single_doc(doc_id: str, Authorization: str = Header(...)):
    """
    Returns the requested document file (PDF) as FileResponse.
    """
    user_id = require_token(Authorization)
    docs = USER_DOCS.get(user_id, [])
    doc = next((d for d in docs if d["id"] == doc_id), None)

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = resolve_demo_doc_path(doc)

    return FileResponse(
        str(file_path),
        media_type="application/pdf",
        filename=doc["file_name"]
    )

# ------------------ ROOT ------------------

@app.get("/")
async def root():
    return {"message": "Mock DigiLocker is running", "user": DEMO_USER_EMAIL}
