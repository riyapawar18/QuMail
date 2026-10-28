"""
mail_store.py — Persistent per-account mail storage on disk.

Structure of mail_store.json:
{
  "harshitmishra7762@gmail.com": {
    "inbox": [ { id, sender, subject, preview, time, level, keyId, ... }, ... ],
    "sent":  [ { id, recipient, subject, preview, time, level, ... }, ... ],
    "drafts": [...],
    "trash":  [...],
    "snoozed": [...],
    "spam":    [...],
    "scheduled": [...],
    "purchases": [...]
  },
  "harshitmishra1343@gmail.com": { ... }
}
"""

import os
import json

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.getenv("QMAIL_DATA_DIR", BACKEND_DIR))
os.makedirs(DATA_DIR, exist_ok=True)
STORE_FILE = os.path.join(DATA_DIR, "mail_store.json")

FOLDERS = ["inbox", "sent", "drafts", "trash", "snoozed", "spam", "scheduled", "purchases"]


# --------------------------------------------------
# LOW-LEVEL
# --------------------------------------------------

def _load() -> dict:
    if not os.path.exists(STORE_FILE):
        return {}
    try:
        with open(STORE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _strip_binary(store: dict) -> dict:
    """Strip file_data_b64 from messages before saving — files are on disk in attachments/."""
    import copy
    clean = copy.deepcopy(store)
    for email_data in clean.values():
        if not isinstance(email_data, dict):
            continue
        for folder_msgs in email_data.values():
            if isinstance(folder_msgs, list):
                for msg in folder_msgs:
                    if isinstance(msg, dict):
                        msg.pop("file_data_b64", None)
                        # Strip file_data_b64 from attachments array too, keep other fields
                        if "attachments" in msg and isinstance(msg["attachments"], list):
                            for att in msg["attachments"]:
                                if isinstance(att, dict):
                                    att.pop("file_data_b64", None)
    return clean

def _save(store: dict):
    # Always strip large binary fields before writing
    clean = _strip_binary(store)
    try:
        with open(STORE_FILE, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[mail_store] ERROR saving store: {e}")

def _account(store: dict, email: str) -> dict:
    email = email.strip().lower()
    if email not in store:
        store[email] = {folder: [] for folder in FOLDERS}
    # Ensure all folders exist (migration safety)
    for folder in FOLDERS:
        store[email].setdefault(folder, [])
    return store[email]


# --------------------------------------------------
# PUBLIC API
# --------------------------------------------------

def get_all_folders(email: str) -> dict:
    """Return all folders for this account. Returns empty folders if account not found — does NOT save."""
    store = _load()
    email = email.strip().lower()
    if email in store:
        # Ensure all folder keys exist (migration safety) without overwriting data
        for folder in FOLDERS:
            store[email].setdefault(folder, [])
        return store[email]
    # Account not in store yet — return empty structure without saving
    return {folder: [] for folder in FOLDERS}


def get_folder(email: str, folder: str) -> list:
    store = _load()
    return _account(store, email).get(folder, [])


def append_message(email: str, folder: str, message: dict) -> bool:
    """
    Append a message to a folder. Deduplicates by id.
    Strips large binary fields (file_data_b64) before saving to keep the file small.
    Returns True if added, False if duplicate.
    """
    store = _load()
    acct  = _account(store, email)
    msgs  = acct.get(folder, [])

    # Deduplicate by id
    if any(str(m.get("id")) == str(message.get("id")) for m in msgs):
        return False

    # Strip large binary data — files are stored separately in attachments/
    save_msg = {k: v for k, v in message.items() if k != "file_data_b64"}

    acct[folder] = [save_msg] + msgs   # newest first
    _save(store)
    return True


def update_message(email: str, folder: str, msg_id, updates: dict):
    """Patch fields on an existing message."""
    store = _load()
    acct  = _account(store, email)
    acct[folder] = [
        {**m, **updates} if str(m.get("id")) == str(msg_id) else m
        for m in acct.get(folder, [])
    ]
    _save(store)


def move_message(email: str, from_folder: str, to_folder: str, msg_id) -> bool:
    """Move a message between folders."""
    store = _load()
    acct  = _account(store, email)
    src   = acct.get(from_folder, [])
    msg   = next((m for m in src if str(m.get("id")) == str(msg_id)), None)
    if not msg:
        return False
    acct[from_folder] = [m for m in src if str(m.get("id")) != str(msg_id)]
    dest = acct.get(to_folder, [])
    if not any(m.get("id") == msg.get("id") for m in dest):
        acct[to_folder] = [msg] + dest
    _save(store)
    return True


def delete_message(email: str, folder: str, msg_id) -> bool:
    """Permanently remove a message from a folder."""
    store = _load()
    acct  = _account(store, email)
    before = len(acct.get(folder, []))
    acct[folder] = [m for m in acct.get(folder, []) if str(m.get("id")) != str(msg_id)]
    changed = len(acct[folder]) < before
    if changed:
        _save(store)
    return changed


def delete_account(email: str):
    """Remove all mail data for an account."""
    store = _load()
    store.pop(email.strip().lower(), None)
    _save(store)
