# QuMail

QuMail is a demo-ready, quantum-themed secure email interface built with React and FastAPI.
It keeps the original encryption workflow, adds authenticated AES-256-GCM paths, ships a
self-contained **Guest Demo** plus a **mock DigiLocker document vault**, and can connect to a
real Gmail mailbox through Google OAuth 2.0 when credentials are configured.

> **Demo status:** guest mode and the mock document vault are demonstrations. They do not send
> mail through Gmail, do not connect to the real DigiLocker, and do not validate real identity
> documents. Real Gmail send/receive works only after you create your own Google OAuth client and
> connect an account. See [Security limitations](#security-limitations).

---

## Features

- Welcome screen with **Continue as Guest** and **Continue with Google**.
- **Real Gmail integration (optional):** server-side OAuth 2.0 web flow, live inbox and Sent
  listing with pagination, message detail, read/unread and trash actions, attachment downloads,
  plain-text or QuMail-encrypted sending, and visible-account polling. OAuth tokens never reach
  the browser.
- Guest demo: isolated in-memory session, sample inbox/sent mail, real per-session AES-256-GCM
  encryption, tamper/wrong-session rejection, and an encryption-metadata inspector.
- Document Vault backed by an in-process mock DigiLocker: three synthetic PDFs (degree, marksheet,
  identity), preview metadata, validated download, and **Attach to Email**.
- Existing QuMail UI preserved: inbox, sent, drafts, search, compose, attachments, and security
  levels L1–L4.
- FastAPI serves the compiled React app in production, so the deployment is one web service.
- `/health` endpoint for platform health checks and consistent JSON errors.
- HTML email bodies are sanitized server-side; remote images, scripts, frames and forms are
  stripped before the frontend renders them.

## Architecture

```
React (CRA + Tailwind + Framer Motion)
        │  same-origin /api, /mail_store, /digilocker
        ▼
FastAPI  backend/backend.py  ── serves build/ (SPA) and JSON API
   ├── demo_mode.py            in-memory guest sessions + AES-256-GCM
   ├── mock_digilocker/        synthetic docs, in-process app at /mock-digilocker
   ├── sae_client.py           encryption (L1–L4)
   ├── decy.py                 decryption
   ├── gmail_auth.py           Google OAuth 2.0 web flow + multi-account token store
   ├── gmail_api.py            Gmail list/detail/attachment + HTML sanitisation
   ├── emailsend.py            MIME building and Gmail send
   ├── reciever_email.py       Gmail receive / QuMail payload fetch
   ├── mail_store.py           local JSON mail store
   └── kme_server.py           optional software KME (separate process)
```

The mock DigiLocker app is mounted into FastAPI and its metadata is also served through
`/api/digilocker/*`, so the vault does not depend on `localhost:8444` at runtime.

---

## Quick start (Windows)

### 1. Python and Node dependencies

```bat
cd C:\path\to\Qumail-main

python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt

npm install
```

Optional post-quantum ML-KEM-768 support (otherwise Level 3 uses the KME fallback):

```bat
pip install -r backend\requirements-pqc.txt
```

### 2. Build the frontend

```bat
npm run build
```

### 3. Start the API (serves the build)

```bat
cd backend
uvicorn backend:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and choose **Continue as Guest**.

### 4. Frontend development mode (optional)

The API must be running on port 8000. CRA's proxy (`package.json`) forwards relative API calls.

```bat
npm start
```

Open <http://localhost:3000>.

`run_project.bat` still starts KME (8443), the standalone mock DigiLocker (8444), the API (8000),
and the dev frontend. For the single-service production build you only need step 3.

### 5. Connect a real Gmail account (optional)

Guest mode works with no setup. Real Gmail needs your own Google OAuth **Web application** client;
no credentials are included in this repository.

1. Open <https://console.cloud.google.com/> and create (or select) a project.
2. **APIs & Services → Library → Gmail API → Enable**.
3. **APIs & Services → OAuth consent screen**:
   - User type: **External** (or Internal for a Workspace org).
   - While the app is in **Testing**, add the Google accounts you will use under **Test users**.
   - Scopes: `https://www.googleapis.com/auth/gmail.send`,
     `https://www.googleapis.com/auth/gmail.modify`,
     `https://www.googleapis.com/auth/userinfo.email`,
     `https://www.googleapis.com/auth/userinfo.profile`, `openid`.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID → Web application**.
   - **Authorized JavaScript origins:** `http://127.0.0.1:8000`, `http://localhost:3000`
     (add your deployed origin too).
   - **Authorized redirect URIs:** `http://127.0.0.1:8000/auth/google/callback` and, for the
     deployment, `https://YOUR-SERVICE.onrender.com/auth/google/callback`. They must match exactly.
5. Download the client JSON. Either save it as `backend/client_secret.json` (git-ignored) or paste
   the whole JSON into the `GOOGLE_CLIENT_SECRET_JSON` environment variable / platform secret.
6. Copy `.env.example` to `.env` and set at least:

```bat
OAUTH_REDIRECT_URI=http://127.0.0.1:8000/auth/google/callback
PUBLIC_BASE_URL=http://127.0.0.1:8000
GOOGLE_CLIENT_SECRET_FILE=./backend/client_secret.json
```

7. Install dependencies, build, and start the API as above. Click **Continue with Google**, grant
   consent, and you should return to the app with the connected address shown. Tokens are written
   to `GOOGLE_TOKEN_FILE` (`token.json`) on the server and are never sent to React.

Scopes are limited to Gmail send/modify plus basic profile/email. QuMail never asks for your Gmail
password. In **Testing** mode Google limits refresh tokens to test users and may expire them after
7 days; reconnecting is expected. Disconnect removes the active account's token from the server.

### Optional services

```bat
:: Level 2 QAES keys (software KME)
cd backend
python -m uvicorn kme_server:app --host 0.0.0.0 --port 8443

:: Standalone mock DigiLocker (the main API already mounts it in-process)
python -m uvicorn mock_digilocker.mock_digilocker:app --host 0.0.0.0 --port 8444
```

---

## Tests

Backend tests use FastAPI's `TestClient` with an isolated temporary data directory. They do not
need Google credentials or network access.

```bat
cd backend
python -m pytest tests -q
```

Last local run: **61 passed** (Python 3.8.0, FastAPI 0.115.14). The suite covers health/config,
guest session lifecycle, sample inbox, decrypted message details, AES-GCM round trip, unique
nonces, wrong-session and tamper rejection, demo send with encrypted attachment, guest isolation
from authenticated endpoints, vault listing/detail/download/path-traversal rejection, Gmail/OAuth
guest isolation, OAuth start/callback error handling, HTML sanitization, MIME parsing and header
injection, send validation/idempotency/no-plaintext bundles, and safe failure for corrupt or
legacy encrypted payloads.

These tests use stubs for Gmail and never call Google. **A live OAuth connection, real Gmail send,
real inbox retrieval and the deployed URL were not tested in this environment** because no Google
credentials or public deployment were available. See [Test report](#test-report).

Frontend production build:

```bat
npm run build
```

---

## Test report

Status of the acceptance checks in this environment (local Windows, Python 3.8, Node 24). Gmail
calls were stubbed in pytest; no Google account or public deployment was available.

| # | Check | Result |
| --- | --- | --- |
| 1 | Backend starts cleanly, `/health` responds | **Passed locally** |
| 2 | OAuth start validates config; callback rejects bad state | **Passed with stubs** (live consent **untested**) |
| 3 | Connected address displayed, disconnect works | **Untested live** (UI wired to `/api/gmail/status`) |
| 4 | Real Gmail send with recipient/subject/body/attachments | **Untested live** (validation + MIME tests pass) |
| 5 | Recipient actually receives the message | **Untested** (requires a real Gmail account) |
| 6 | Retrieve a real Gmail inbox message | **Untested live** (MIME parser tests pass) |
| 7 | Ordinary HTML email displayed safely | **Passed for sanitizer/parser**; live rendering **untested** |
| 8 | Attachments only through authorized routes | **Passed** for guest isolation and ID validation; live download **untested** |
| 9 | Refresh + pagination | **Untested live** (UI + page-token plumbing implemented) |
| 10 | Truthful status, no duplicate sends | **Passed** (idempotency + accepted-by-Gmail note tests) |
| 11 | QuMail decryption when keys/metadata exist | **Untested end-to-end** (error/legacy paths tested) |
| 12 | Corrupt/unsupported payload fails safely | **Passed** |
| 13 | Guest cannot access Gmail data or attachments | **Passed** |
| 14 | Frontend/deploy do not depend on hard-coded localhost | **Passed source review** (runtime uses relative/`REACT_APP_API_BASE`; only the CRA dev proxy is localhost); deployment **untested** |

`npm run build` completed successfully after the Gmail UI changes (**111 kB** main bundle, gzip).

---

## Environment variables

Copy `.env.example` to `.env` and adjust. `.env` is git-ignored. These variables are read by the
backend (except `REACT_APP_API_BASE`, which is compiled into the frontend):

| Variable | Default | Purpose |
| --- | --- | --- |
| `QMAIL_DEMO_MODE` | `1` | Enable the guest demo and demo APIs |
| `QMAIL_DATA_DIR` | `backend/` | Runtime JSON stores, tokens, key pool, attachments |
| `FRONTEND_BUILD_DIR` | `<project>/build` | Compiled React app served by FastAPI |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | CORS origins (comma-separated) |
| `DEMO_SESSION_TTL_SECONDS` | `7200` | Guest session lifetime |
| `DEMO_MAX_SESSIONS` | `200` | Guest session capacity |
| `KME_URL` | `http://127.0.0.1:8443` | Software KME for Level 2 |
| `KME_DEMO_KEY_COUNT` | `64` | Synthetic KME key pool size |
| `IDENTITY_URL` | hosted identity service | Level 4 DH public-key exchange |
| `DIGILOCKER_URL` | `http://127.0.0.1:8444` | Legacy mock DigiLocker URL |
| `GOOGLE_CLIENT_SECRET_FILE` | `backend/client_secret.json` | OAuth client JSON path (optional) |
| `GOOGLE_CLIENT_SECRET_JSON` | empty | Full OAuth client JSON; preferred on Render |
| `GOOGLE_TOKEN_FILE` | `<QMAIL_DATA_DIR>/token.json` | Multi-account token store (server-only) |
| `OAUTH_REDIRECT_URI` | derived | Exact redirect URI registered with Google |
| `PUBLIC_BASE_URL` | derived | Public backend origin; used to build redirect URIs |
| `FRONTEND_URL` | `PUBLIC_BASE_URL` | Where the OAuth callback redirects the browser |
| `OAUTH_STATE_TTL_SECONDS` | `600` | OAuth CSRF state lifetime |
| `GOOGLE_SESSION_TTL_SECONDS` | `43200` | HttpOnly Google session lifetime (in-memory) |
| `GMAIL_MAX_ATTACHMENTS` | `10` | Max attachments per send |
| `GMAIL_MAX_ATTACHMENT_BYTES` | `10485760` | Max single attachment size (upload/download) |
| `GMAIL_MAX_BODY_BYTES` | `1048576` | Max message body size |
| `GMAIL_MAX_RECIPIENTS` | `10` | Max recipients per send |
| `GMAIL_SEND_IDEMPOTENCY_TTL_SECONDS` | `600` | Duplicate-send receipt lifetime |
| `LOG_LEVEL` | `INFO` | API log level |
| `PORT` | `8000` | Platform-provided port |
| `REACT_APP_API_BASE` | empty (same-origin) | Frontend build-time API origin |
| `REACT_APP_GMAIL_POLL_MS` | `60000` | Inbox poll interval; `0` disables |

Relative paths in `.env` are resolved against the current directory, the project root, or
`backend/`, in that order. `.env` is loaded automatically by the backend when `python-dotenv` is
installed; real environment variables take precedence.

Never commit `.env`, `client_secret.json`, `token.json`, private keys, or real user documents.

---

## API overview

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| GET | `/health` | public | Status, demo mode, Gmail/KME/identity configuration |
| GET | `/api/config` | public | Frontend bootstrap flags |
| POST | `/api/demo/session` | public | Create guest session; returns token + profile |
| GET | `/api/demo/session` | guest header | Validate/resume a session |
| DELETE | `/api/demo/session/{token}` | public | End a guest session |
| GET | `/api/demo/inbox`, `/api/demo/sent` | guest header | Sample mail (ciphertext metadata + preview) |
| GET | `/api/demo/messages/{id}` | guest header | Decrypted body inside the session |
| POST | `/api/demo/encrypt`, `/api/demo/decrypt` | guest header | Real AES-256-GCM round trip |
| POST | `/api/demo/send` | guest header | Multipart demo send; `delivered: false` |
| GET | `/api/digilocker/status`, `/documents`, `/documents/{id}` | public | Synthetic sample metadata |
| GET | `/api/digilocker/documents/{id}/download` | public | Allowlisted synthetic PDF only |
| GET | `/auth/google/start`, `/login` | public | 302 to Google consent; 503 if OAuth is not configured |
| GET | `/auth/google/callback` | public | Validates CSRF state, stores tokens server-side, redirects to the app |
| GET | `/api/gmail/status` | Google session | Connected address, accounts, retrieval mode |
| POST | `/api/gmail/disconnect` | Google session | Remove the active account's server-side token |
| GET | `/api/gmail/messages?folder=inbox\|sent&page_token=&max_results=` | Google session | Paginated inbox/Sent list |
| GET | `/api/gmail/messages/{id}` | Google session | MIME detail, sanitized HTML, attachment metadata |
| GET | `/api/gmail/messages/{id}/attachments/{attachment_id}` | Google session | Authorized download, size-capped |
| POST | `/api/gmail/messages/{id}/read` | Google session | Persist read/unread state (`{"unread": true}`) |
| POST | `/api/gmail/messages/{id}/trash` | Google session | Move to Gmail Trash |
| POST | `/api/gmail/messages/{id}/decrypt` | Google session | Validate + decrypt a QuMail payload; 422 on failure |
| POST | `/api/gmail/send` | Google session | JSON or multipart; `mode=plain\|encrypted`; idempotent |
| Google endpoints | `/me`, `/mail_store*`, `/encrypt*`, `/send*`, `/attachment/*` | Google session | Return `401` without configured credentials |

Guest requests use the `X-Demo-Session` header. Google endpoints ignore it.

---

## Deployment (single Docker web service)

The included `Dockerfile` builds React in a Node stage, then runs FastAPI with the build copied to
`/app/build`. `render.yaml` describes a Render Docker web service with health check `/health`.

**Build command (Docker):** the image build runs `npm ci && npm run build` and installs
`backend/requirements.txt`; no separate platform build command is required.

**Start command:** `uvicorn backend:app --host 0.0.0.0 --port $PORT` (set in the Dockerfile).

**Steps (Render example):**

1. Push the repository to GitHub/GitLab.
2. Render → **New** → **Blueprint** and select `render.yaml`, or **New Web Service** → Docker.
3. Health check path: `/health`.
4. Environment variables: `QMAIL_DEMO_MODE=1`, `QMAIL_DATA_DIR=/app/data`,
   `FRONTEND_BUILD_DIR=/app/build`, `LOG_LEVEL=INFO`.
5. For Gmail, add these platform secrets (never commit them):
   - `GOOGLE_CLIENT_SECRET_JSON` = the full Web-application client JSON.
   - `OAUTH_REDIRECT_URI` = `https://YOUR-SERVICE.onrender.com/auth/google/callback`.
6. In Google Cloud Console, add `https://YOUR-SERVICE.onrender.com` under **Authorized JavaScript
   origins** and the same callback URL under **Authorized redirect URIs**. If the service URL
   changes, update both the Google client and `OAUTH_REDIRECT_URI`.
7. Deploy, then verify:

```bat
curl https://YOUR-SERVICE.onrender.com/health
```

```json
{"status":"ok","service":"qumail","demo_mode":true,...}
```

8. Open `https://YOUR-SERVICE.onrender.com`, confirm **Continue as Guest** works, then try
   **Continue with Google** in the same HTTPS origin.

> This repository was built and tested locally. **No public deployment was performed in this
> environment**, so no live URL is claimed. The Render/Docker configuration is provided but its
> live behavior (including Google's redirect-URI enforcement) is untested here.

**Hosting notes**

- Free/typical container filesystems are ephemeral: `QMAIL_DATA_DIR` JSON files, OAuth tokens,
  and attachments are lost on restart/redeploy. Attach a persistent disk (or use a paid plan with
  persistent storage) before relying on a connected Gmail account. Losing `token.json` simply means
  reconnecting; it never exposes credentials.
- OAuth uses a standard **Web application** redirect flow at `/auth/google/callback`; the server
  does not open a browser. The callback URI must be registered exactly and served over HTTPS in
  production. Render supplies `RENDER_EXTERNAL_URL`, which QuMail uses when `PUBLIC_BASE_URL` is
  unset.
- The refresh token is stored in `token.json` on the server only. Do not mount it into the
  frontend or commit it. Rotate/revoke access from your Google Account permissions page if needed.
- Gmail sync is **polling**, not push. Polling is opt-in via `REACT_APP_GMAIL_POLL_MS`, pauses when
  the tab is hidden, and never overlaps requests. No Gmail Pub/Sub push notification is claimed.
- The software KME (Level 2) and identity server (Level 4) are separate processes/services. Guest
  mode and Levels 1/3-fallback do not require them. Level 3 uses real ML-KEM only when
  `quantcrypt` is installed.

---

## Security limitations

- **Guest demo** is an in-memory demonstration. Each session gets a random 256-bit key and a fresh
  96-bit nonce per encryption; wrong-session and tampered ciphertext are rejected. Data disappears
  when the session ends or the server restarts.
- **L1 (OTP)** is the legacy XOR-based one-time-pad path. It is **not authenticated**; prefer L2–L4
  for new work.
- **L2 (QAES)** uses keys from a **software KME**, not real quantum key distribution. It does not
  prove QKD.
- **L3 (PQC)** uses ML-KEM-768 only when `quantcrypt` is installed. Without it, the app falls back
  to a KME-symmetric path and does not claim post-quantum protection.
- **L4 (DH)** uses Diffie–Hellman (RFC 3526 Group 14). It is **not post-quantum**.
- New encryption paths use AES-256-GCM with per-message nonces and AAD. Legacy AES-CBC payloads are
  still accepted for backward compatibility.
- If encryption fails, the API returns an error and nothing is sent; there is no plaintext fallback.
  Multi-file bundles encrypt the text body before sending.
- Private keys, OAuth tokens, and session keys stay server-side. The frontend receives only
  ciphertext, previews, and metadata.
- Authenticated attachment downloads require a Google session. Attachment IDs are validated
  against the requested message, size is capped, and no filesystem path is accepted. Vault
  downloads are allowlisted, path-validated synthetic PDFs only.
- **Gmail is not end-to-end encrypted by QuMail.** Ordinary messages are sent and displayed as
  ordinary Gmail mail and are explicitly labeled that way. Only messages carrying a QuMail
  encrypted payload use the decryption workflow. An encrypted send requires exactly one recipient;
  the UI never silently sends plaintext when encryption fails.
- QuMail reports Gmail's API acceptance (`delivery: accepted-by-gmail`), not guaranteed inbox
  delivery. Bounce/spam placement is outside this app's control.
- Gmail HTML is sanitized server-side with a narrow allowlist. Scripts, frames, forms, event
  handlers, `javascript:` URLs and remote images are removed before rendering.
- Gmail sync uses polling while the inbox is open; it is not push and can be delayed by the poll
  interval or Gmail rate limits. No Pub/Sub or webhook integration is claimed.
- OAuth refresh tokens are stored server-side in `token.json` (git-ignored). The browser never
  receives access tokens, refresh tokens, client secrets, or private keys. A random, HttpOnly,
  SameSite=Lax session cookie binds the connecting browser to the account; protected `/api/gmail/*`
  and legacy mail routes reject requests without it, so another visitor cannot read a connected
  mailbox. Sessions are in memory and end on server restart (the token remains; reconnect). Tokens
  are still long-lived bearer credentials for the configured scopes, so protect the data directory
  and use least-privilege scopes.
- Legacy QuMail payloads that stored the body as plaintext are flagged in the decrypt response
  (`legacy_unencrypted_text` / `warning`); the app does not present them as encrypted content.
- The mock DigiLocker is **not** a real DigiLocker integration. Documents are explicitly labeled
  synthetic samples, and no Aadhaar data is collected.
- Local JSON storage is single-process and unsuitable for concurrent multi-user production. Use a
  database and durable object storage for real deployments.

---

## Interview explanation

**Guest Login.** The welcome screen offers Google OAuth or a guest session. Guest mode calls
`POST /api/demo/session`; the server creates an isolated in-memory session with a random 32-byte
key and returns only a session token. Sample mail bodies and attachments are sealed with
AES-256-GCM using unique 96-bit nonces and purpose-specific AAD. The frontend sends the token in
`X-Demo-Session`; every Google/Gmail endpoint still requires OAuth, so a guest cannot read another
mailbox. Exiting the demo deletes the session and its key.

**DigiLocker Vault.** The vault reuses the original mock DigiLocker metadata but serves it through
`/api/digilocker/*`. Documents are synthetic PDFs under a trusted directory; the download endpoint
resolves a fixed allowlist, rejects path separators/traversal, and returns `application/pdf`.
"Attach to Email" downloads the validated PDF into the composer as a normal attachment, which is
then encrypted by the chosen send path. The UI labels everything as a sample and states that no
real DigiLocker or government verification is involved.

**Real Gmail.** `GET /auth/google/start` builds a Google consent URL with offline access and a
single-use CSRF state; Google returns to `/auth/google/callback`, where the server exchanges the
code and writes the refresh token to `token.json`. React only learns the connected address from
`/api/gmail/status`. The inbox calls `users.messages.list` + `get`, parses MIME parts, sanitizes
HTML with bleach, and exposes attachments through authorized, ID-validated routes. Compose sends
either an explicitly chosen plain email (`mode=plain`) or a QuMail payload (`mode=encrypted`); the
latter requires one recipient, encrypts body/attachments before MIME construction, and returns an
idempotent `accepted-by-gmail` receipt. The UI labels ordinary mail as unencrypted and never
claims delivery confirmation.

---

## Two-minute demo script

1. **Start** at the welcome screen. Point out **Continue as Guest** and the disclosure that guest
   mail is never sent to Gmail. Click **Continue as Guest**.
2. **Inbox.** Three sample messages load, each badged **AES-GCM**. Open the first message: show the
   decrypted body and the **Encryption metadata** panel (algorithm, AAD, nonce, key scope).
3. **Vault.** Open **Document Vault**. Show the mock DigiLocker badge and the synthetic disclaimer.
   Select the degree certificate, point out issuer/date/status fields, then click **Attach to Email**.
4. **Compose.** Add a recipient and subject, show the attached `Degree Certificate (Sample).pdf`,
   and point out the amber guest-demo banner. Send with **L2 QAES**.
5. **Sent.** The message appears in Sent with `delivered: false`. Open it: the decrypted body, demo
   notice, attachment, and encryption metadata are visible.
6. **Exit.** Open the profile menu → **Exit guest demo**. You return to the welcome screen, and the
   previous session token no longer works.
7. **Health.** Visit `/health` to show `status: ok`, `demo_mode: true`, and Gmail availability flags.

---

## Project layout

```
backend/
  backend.py                 FastAPI app, auth guards, guest + vault endpoints
  demo_mode.py               In-memory guest sessions and AES-256-GCM
  sae_client.py / decy.py    Encryption and decryption (L1–L4)
  gmail_auth.py              Google OAuth web flow and multi-account tokens
  gmail_api.py               Gmail list/detail/attachment and HTML sanitisation
  emailsend.py               Gmail MIME building and send
  reciever_email.py          Gmail receive / QuMail payload fetch
  mail_store.py              Local JSON mail store
  kme_server.py              Optional software KME
  tests/test_demo_api.py     Guest/vault API and isolation tests
  tests/test_gmail_security.py  Gmail/OAuth isolation, send, sanitizer, decrypt tests
  mock_digilocker/           Mock vault app and synthetic PDFs
src/
  App.js                     Splash → login → app flow
  LoginScreen.jsx            Google / Guest welcome screen
  QuMailApp.jsx              Mail UI, compose, demo integration
  DocumentVault.jsx          Vault UI, download, attach
  api.js                     API base + guest session header
Dockerfile                   Single-service production image
render.yaml                  Render Blueprint
.env.example                 Environment template
```
