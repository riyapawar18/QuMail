import os
import json
import threading
import time
from typing import List, Optional, Tuple
from google_auth_oauthlib.flow import Flow, InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)
DATA_DIR = os.path.abspath(os.getenv("QMAIL_DATA_DIR", BACKEND_DIR))
os.makedirs(DATA_DIR, exist_ok=True)


def _resolve_runtime_path(value: str, default: str) -> str:
    """Resolve a relative path from .env regardless of the launch directory."""
    if not value:
        return default
    if os.path.isabs(value):
        return value
    for base in (os.getcwd(), PROJECT_DIR, BACKEND_DIR):
        candidate = os.path.join(base, value)
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    return os.path.abspath(os.path.join(PROJECT_DIR, value))


CLIENT_SECRET_FILE = _resolve_runtime_path(
    os.getenv("GOOGLE_CLIENT_SECRET_FILE", ""),
    os.path.join(BACKEND_DIR, "client_secret.json"),
)

# Single multi-account token store
TOKEN_FILE = _resolve_runtime_path(
    os.getenv("GOOGLE_TOKEN_FILE", ""),
    os.path.join(DATA_DIR, "token.json"),
)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid"
]

OAUTH_STATE_TTL_SECONDS = int(os.getenv("OAUTH_STATE_TTL_SECONDS", "600"))
_pending_states = {}
_state_lock = threading.Lock()


def _client_config() -> dict:
    """Load the OAuth client JSON from an env var or the configured file.

    Render and similar hosts can inject the whole JSON via
    GOOGLE_CLIENT_SECRET_JSON instead of mounting a file.
    """
    raw = os.getenv("GOOGLE_CLIENT_SECRET_JSON", "").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception as exc:
            raise RuntimeError("GOOGLE_CLIENT_SECRET_JSON is not valid JSON") from exc
    if not os.path.exists(CLIENT_SECRET_FILE):
        raise FileNotFoundError(
            "Google OAuth client is not configured. Set GOOGLE_CLIENT_SECRET_JSON "
            "or GOOGLE_CLIENT_SECRET_FILE."
        )
    with open(CLIENT_SECRET_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _remember_state(state: str, redirect_uri: str):
    now = time.time()
    with _state_lock:
        for key, (expiry, _) in list(_pending_states.items()):
            if expiry <= now:
                _pending_states.pop(key, None)
        _pending_states[state] = (now + OAUTH_STATE_TTL_SECONDS, redirect_uri)


def consume_oauth_state(state: str) -> Optional[str]:
    """Validate and consume a pending OAuth state, returning its redirect URI."""
    if not state:
        return None
    now = time.time()
    with _state_lock:
        entry = _pending_states.pop(state, None)
    if not entry:
        return None
    expiry, redirect_uri = entry
    if expiry <= now:
        return None
    return redirect_uri


def build_authorization_url(redirect_uri: str) -> Tuple[str, str]:
    """Create a Web-application OAuth URL with offline access and CSRF state."""
    flow = Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=redirect_uri
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account consent",
    )
    _remember_state(state, redirect_uri)
    return auth_url, state


def complete_web_auth(state: str, code: str) -> Tuple[Credentials, dict]:
    """Exchange an authorization code for tokens and persist the account."""
    redirect_uri = consume_oauth_state(state)
    if not redirect_uri:
        raise ValueError("OAuth state is missing or expired. Start the sign-in again.")
    if not code:
        raise ValueError("OAuth authorization code is missing.")

    flow = Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=redirect_uri, state=state
    )
    flow.fetch_token(code=code)
    creds = flow.credentials

    profile = build("oauth2", "v2", credentials=creds).userinfo().get().execute()
    email = (profile.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Google did not return an email address for this account.")

    _save_account_creds(email, creds)
    _set_active_email(email)
    print("Authenticated via web OAuth as %s" % email)
    return creds, profile


# --------------------------------------------------
# LOW-LEVEL: read/write the multi-account token.json
# --------------------------------------------------

def _load_store() -> dict:
    """Load the full token store. Structure:
    {
      "active": "email@gmail.com",
      "accounts": {
        "email@gmail.com": { ...google credentials json... }
      }
    }
    """
    if not os.path.exists(TOKEN_FILE):
        # Try to build from legacy tokens/ folder
        return _migrate_from_tokens_folder()

    try:
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)

        # Already in new multi-account format
        if "accounts" in data:
            return data

        # --- Migrate legacy flat token.json ---
        # The flat format has keys like: token, refresh_token, token_uri, client_id...
        # It also has an "account" field which is the email (set by google-auth)
        email = data.get("account", "").strip().lower()

        if not email:
            # account field empty  pull all accounts from tokens/ folder instead
            legacy = _migrate_from_tokens_folder()
            if legacy["accounts"]:
                return legacy
            email = "unknown"

        migrated = {
            "active": email,
            "accounts": {email: data}
        }

        # Also pull in any other accounts from tokens/ folder
        tokens_dir = os.path.join(BACKEND_DIR, "tokens")
        if os.path.exists(tokens_dir):
            for fname in os.listdir(tokens_dir):
                if not fname.startswith("token_") or not fname.endswith(".json"):
                    continue
                path = os.path.join(tokens_dir, fname)
                try:
                    with open(path) as f:
                        cdata = json.load(f)
                    acct_email = cdata.get("account", "").strip().lower()
                    if acct_email and acct_email not in migrated["accounts"]:
                        migrated["accounts"][acct_email] = cdata
                except Exception:
                    continue

        # Respect active_account.json if it exists
        active_file = os.path.join(BACKEND_DIR, "active_account.json")
        if os.path.exists(active_file):
            try:
                with open(active_file) as f:
                    active_email = json.load(f).get("email", "").strip().lower()
                if active_email and active_email in migrated["accounts"]:
                    migrated["active"] = active_email
            except Exception:
                pass

        _save_store(migrated)
        print(f" Migrated token.json  accounts: {list(migrated['accounts'].keys())}")
        return migrated

    except Exception as e:
        print(f" Could not load token store: {e}")
        return {"active": None, "accounts": {}}


def _migrate_from_tokens_folder() -> dict:
    """Build a multi-account store from the legacy tokens/ folder."""
    store = {"active": None, "accounts": {}}
    tokens_dir = os.path.join(BACKEND_DIR, "tokens")
    if not os.path.exists(tokens_dir):
        return store

    for fname in os.listdir(tokens_dir):
        if not fname.startswith("token_") or not fname.endswith(".json"):
            continue
        path = os.path.join(tokens_dir, fname)
        try:
            with open(path) as f:
                cdata = json.load(f)

            # Try "account" field first, then decode from filename
            email = cdata.get("account", "").strip().lower()
            if not email:
                # filename: token_user_at_gmail_com.json -> user@gmail.com
                raw = fname[len("token_"):-len(".json")]  # user_at_gmail_com
                email = raw.replace("_at_", "@").replace("_", ".", 1)
                # handle multi-dot domains: harshitmishra7762_at_gmail_com
                # raw = "harshitmishra7762_at_gmail_com"
                # after replace: "harshitmishra7762@gmail_com"  fix remaining _
                parts = email.split("@")
                if len(parts) == 2:
                    email = parts[0] + "@" + parts[1].replace("_", ".")

            if email:
                cdata["account"] = email  # stamp it
                store["accounts"][email] = cdata
        except Exception:
            continue

    # Set active from active_account.json
    active_file = os.path.join(BACKEND_DIR, "active_account.json")
    if os.path.exists(active_file):
        try:
            with open(active_file) as f:
                active_email = json.load(f).get("email", "").strip().lower()
            if active_email in store["accounts"]:
                store["active"] = active_email
        except Exception:
            pass

    # Fallback: first account
    if not store["active"] and store["accounts"]:
        store["active"] = next(iter(store["accounts"]))

    if store["accounts"]:
        _save_store(store)
        print(f" Built token.json from tokens/ folder  accounts: {list(store['accounts'].keys())}")

    return store

def _save_store(store: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(store, f, indent=2)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except Exception:
        pass

# --------------------------------------------------
# PUBLIC ACCOUNT HELPERS
# --------------------------------------------------

def get_active_email() -> Optional[str]:
    return _load_store().get("active")

def _set_active_email(email: str):
    store = _load_store()
    store["active"] = email
    _save_store(store)

def _save_account_creds(email: str, creds: Credentials):
    store = _load_store()
    creds_dict = json.loads(creds.to_json())
    # Always stamp the email so migration can identify this account later
    creds_dict["account"] = email
    store["accounts"][email] = creds_dict
    if not store.get("active"):
        store["active"] = email
    _save_store(store)

def _load_account_creds(email: str) -> Optional[Credentials]:
    store = _load_store()
    creds_data = store.get("accounts", {}).get(email)
    if not creds_data:
        return None
    try:
        creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
    except Exception as e:
        print(f" Token for {email} invalid: {e}")
        return None
    if creds and creds.expired and creds.refresh_token:
        try:
            print(f" Refreshing token for {email}...")
            creds.refresh(Request())
            _save_account_creds(email, creds)
        except Exception as e:
            print(f" Token refresh failed for {email}: {e}")
            return None
    return creds

def list_accounts() -> List[dict]:
    store = _load_store()
    active = store.get("active")
    accounts = []
    for email, creds_data in store.get("accounts", {}).items():
        try:
            creds = _load_account_creds(email)
            if not creds:
                continue
            svc = build("oauth2", "v2", credentials=creds)
            profile = svc.userinfo().get().execute()
            accounts.append({
                "email": profile.get("email", email),
                "name": profile.get("name", email),
                "picture": profile.get("picture"),
                "active": email == active
            })
        except Exception:
            # Still show the account even if profile fetch fails
            accounts.append({
                "email": email,
                "name": email,
                "picture": None,
                "active": email == active
            })
    return accounts

def switch_account(email: str) -> bool:
    store = _load_store()
    if email not in store.get("accounts", {}):
        return False
    _set_active_email(email)
    return True

def remove_account(email: str):
    """Remove an account from token.json and receiver_keys.json completely."""
    email = email.strip().lower()

    # 1. Remove from token store
    store = _load_store()
    store.get("accounts", {}).pop(email, None)
    if store.get("active") == email:
        remaining = [e for e in store.get("accounts", {}) if e != email]
        store["active"] = remaining[0] if remaining else None
    _save_store(store)

    # 2. Remove from receiver_keys.json
    keys_file = os.path.join(DATA_DIR, "receiver_keys.json")
    if os.path.exists(keys_file):
        try:
            with open(keys_file, "r") as f:
                keys_store = json.load(f)
            keys_store.pop(email, None)
            with open(keys_file, "w") as f:
                json.dump(keys_store, f, indent=2)
        except Exception as e:
            print(f" Could not clean receiver_keys.json: {e}")

    # 3. If the removed account was active, update flat key files for new active
    new_active = store.get("active")
    if new_active:
        _refresh_flat_key_files(new_active)
    else:
        # No accounts left  clear flat key files
        for fname in ["receiver_dh_private.txt", "receiver_pqc_private.bin", "receiver_pqc_public.bin"]:
            fpath = os.path.join(DATA_DIR, fname)
            if os.path.exists(fpath):
                os.remove(fpath)

    print(f" Account {email} removed. Active is now: {new_active}")


def _refresh_flat_key_files(email: str):
    """Write the given account's keys into the legacy flat files."""
    keys_file = os.path.join(DATA_DIR, "receiver_keys.json")
    if not os.path.exists(keys_file):
        return
    try:
        with open(keys_file) as f:
            keys_store = json.load(f)
        keys = keys_store.get(email.strip().lower(), {})
        import base64
        if keys.get("dh_private"):
            with open(os.path.join(DATA_DIR, "receiver_dh_private.txt"), "w") as f:
                f.write(keys["dh_private"])
        if keys.get("pqc_private"):
            with open(os.path.join(DATA_DIR, "receiver_pqc_private.bin"), "wb") as f:
                f.write(base64.b64decode(keys["pqc_private"]))
        if keys.get("pqc_public"):
            with open(os.path.join(DATA_DIR, "receiver_pqc_public.bin"), "wb") as f:
                f.write(base64.b64decode(keys["pqc_public"]))
    except Exception as e:
        print(f" Could not refresh flat key files: {e}")

# --------------------------------------------------
# CORE AUTH FUNCTIONS
# --------------------------------------------------

def run_automatic_auth_flow(prompt_account_chooser=True) -> Credentials:
    """Legacy local-desktop OAuth. Not used by the deployed web flow."""
    try:
        config = _client_config()
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "Google OAuth client is not configured. Download it from Google Cloud "
            "Console or set GOOGLE_CLIENT_SECRET_JSON."
        ) from exc
    flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)
    print("\n Opening browser for Google Authentication...")
    kwargs = {"port": 0}
    if prompt_account_chooser:
        kwargs["prompt"] = "select_account consent"
    creds = flow.run_local_server(**kwargs)

    svc = build("oauth2", "v2", credentials=creds)
    profile = svc.userinfo().get().execute()
    email = profile.get("email")

    _save_account_creds(email, creds)
    _set_active_email(email)
    print(f" Authenticated as {email}")
    return creds

def load_credentials(email: str = None) -> Optional[Credentials]:
    target = email or get_active_email()
    if not target:
        return None
    return _load_account_creds(target)


def has_credentials(email: str = None) -> bool:
    """True when a usable Google token is available (never starts OAuth)."""
    try:
        return load_credentials(email) is not None
    except Exception:
        return False


def gmail_configured() -> bool:
    """True when a Google OAuth client is present as a file or env JSON."""
    if os.getenv("GOOGLE_CLIENT_SECRET_JSON", "").strip():
        return True
    return os.path.exists(CLIENT_SECRET_FILE)


def get_gmail_service(email: str = None):
    creds = load_credentials(email)
    if not creds:
        raise RuntimeError(
            "No authenticated Google account. Configure Google OAuth or use Guest Demo."
        )
    return build("gmail", "v1", credentials=creds)


def get_user_profile(email: str = None, interactive: bool = False) -> dict:
    """
    Return the Google profile for the active account.

    `interactive=False` (the API default) never opens a browser: if no token is
    configured it raises a clear RuntimeError instead of blocking the server.
    """
    creds = load_credentials(email)
    if not creds:
        if not interactive:
            raise RuntimeError(
                "No authenticated Google account. Configure Google OAuth or use Guest Demo."
            )
        creds = run_automatic_auth_flow()
    svc = build("oauth2", "v2", credentials=creds)
    return svc.userinfo().get().execute()

def ensure_authenticated(email: str = None) -> Credentials:
    creds = load_credentials(email)
    if not creds:
        return run_automatic_auth_flow()
    return creds

if __name__ == "__main__":
    run_automatic_auth_flow()
