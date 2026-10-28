import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import json
import hashlib
import requests
import base64
import random

from reciever_email import fetch_qumail_emails, fetch_payload_from_email
from decy import aes_decrypt_b64, ub64, fetch_key_from_kme, handle_decrypted_data
from gmail_auth import load_credentials, run_automatic_auth_flow, get_active_email

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend
from Crypto.Cipher import AES

# DH group parameters (RFC 3526 Group 14)
P = 0xFFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF
G = 2

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.getenv("QMAIL_DATA_DIR", BACKEND_DIR))
os.makedirs(DATA_DIR, exist_ok=True)
IDENTITY_SERVER_URL = os.getenv(
    "IDENTITY_URL", "https://qumail-identity-server.onrender.com"
).rstrip("/")

# Single multi-account key store file
KEYS_FILE = os.path.join(DATA_DIR, "receiver_keys.json")

# PQC backend
try:
    from quantcrypt import kem as _quantkem
    _mlkem_768 = _quantkem.MLKEM_768()
    HAS_PQC = True
except Exception:
    HAS_PQC = False
    _mlkem_768 = None


# --------------------------------------------------
# MULTI-ACCOUNT KEY STORE
# Structure of receiver_keys.json:
# {
#   "harshitmishra7762@gmail.com": {
#     "dh_private": "123456789...",          <- big int as string
#     "pqc_public":  "base64...",
#     "pqc_private": "base64..."
#   },
#   "harshitmishra1343@gmail.com": { ... }
# }
# --------------------------------------------------

def _load_keys() -> dict:
    if not os.path.exists(KEYS_FILE):
        return _migrate_flat_key_files()
    try:
        with open(KEYS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def _migrate_flat_key_files() -> dict:
    """
    One-time migration: if the old flat key files exist, read them into
    receiver_keys.json under the active account's email.
    """
    store = {}
    active_email = get_active_email()
    if not active_email:
        return store

    dh_file  = os.path.join(DATA_DIR, "receiver_dh_private.txt")
    pqc_priv = os.path.join(DATA_DIR, "receiver_pqc_private.bin")
    pqc_pub  = os.path.join(DATA_DIR, "receiver_pqc_public.bin")

    keys = {}
    if os.path.exists(dh_file):
        with open(dh_file) as f:
            keys["dh_private"] = f.read().strip()
    if os.path.exists(pqc_priv):
        with open(pqc_priv, "rb") as f:
            keys["pqc_private"] = base64.b64encode(f.read()).decode()
    if os.path.exists(pqc_pub):
        with open(pqc_pub, "rb") as f:
            keys["pqc_public"] = base64.b64encode(f.read()).decode()

    if keys:
        store[active_email.strip().lower()] = keys
        _save_keys(store)
        print(f" Migrated flat key files into receiver_keys.json for {active_email}")

    return store

def _save_keys(store: dict):
    with open(KEYS_FILE, "w") as f:
        json.dump(store, f, indent=2)

def _get_account_keys(email: str) -> dict:
    return _load_keys().get(email.strip().lower(), {})

def _set_account_keys(email: str, data: dict):
    store = _load_keys()
    store[email.strip().lower()] = data
    _save_keys(store)


def ensure_identity_exists(email: str = None):
    """Generate DH + PQC keys for this account if they don't exist yet."""
    if not email:
        email = get_active_email()
    if not email:
        print(" No active account  cannot create identity.")
        return

    email = email.strip().lower()
    keys = _get_account_keys(email)
    changed = False

    # --- DH key ---
    if not keys.get("dh_private"):
        print(f" Creating DH identity for {email}...")
        private_b = random.getrandbits(256)
        keys["dh_private"] = str(private_b)
        changed = True
        print(" DH identity created.")

    # --- PQC keys ---
    if not keys.get("pqc_private") or not keys.get("pqc_public"):
        if HAS_PQC and _mlkem_768:
            print(f" Creating ML-KEM (PQC) identity for {email}...")
            pk, sk = _mlkem_768.keygen()
            keys["pqc_public"]  = base64.b64encode(pk).decode()
            keys["pqc_private"] = base64.b64encode(sk).decode()
            changed = True
            print(" PQC identity created.")

    if changed:
        _set_account_keys(email, keys)

    # Also update the legacy flat files so decy.py fallback still works
    _write_legacy_key_files(email, keys)


def _write_legacy_key_files(email: str, keys: dict):
    """
    Write the active account's keys into the legacy flat files
    (receiver_dh_private.txt, receiver_pqc_private.bin, receiver_pqc_public.bin)
    so that decy.py  which reads those files  always gets the right keys
    for the currently active account.
    """
    if keys.get("dh_private"):
        with open(os.path.join(DATA_DIR, "receiver_dh_private.txt"), "w") as f:
            f.write(keys["dh_private"])

    if keys.get("pqc_private"):
        with open(os.path.join(DATA_DIR, "receiver_pqc_private.bin"), "wb") as f:
            f.write(base64.b64decode(keys["pqc_private"]))

    if keys.get("pqc_public"):
        with open(os.path.join(DATA_DIR, "receiver_pqc_public.bin"), "wb") as f:
            f.write(base64.b64decode(keys["pqc_public"]))


def sync_fresh_identity_to_cloud(email: str):
    """
    Rotate DH key for this account (Perfect Forward Secrecy),
    keep PQC key stable, push both public keys to the cloud registry.
    """
    email = email.strip().lower()
    print(f" Refreshing secure session for {email}...")
    try:
        keys = _get_account_keys(email)

        # Rotate DH private key
        new_b = random.getrandbits(256)
        new_B = pow(G, new_b, P)
        keys["dh_private"] = str(new_b)
        _set_account_keys(email, keys)

        # Update legacy flat files for decy.py
        _write_legacy_key_files(email, keys)

        B_b64 = base64.b64encode(str(new_B).encode()).decode()

        # PQC public key (stable)
        pqc_pub_b64 = keys.get("pqc_public", "")
        if not pqc_pub_b64:
            print(f" No PQC key for {email}  run ensure_identity_exists first.")
            return

        payload = {
            "email": email,
            "level3_pk": pqc_pub_b64,
            "level4_pk": B_b64
        }
        resp = requests.post(f"{IDENTITY_SERVER_URL}/register", json=payload, timeout=60)
        resp.raise_for_status()
        print(f" Identity synced for {email}. Quantum-Safe channels active.")
        return True
    except Exception as e:
        print(f" Registration failed: {e}")
        return False


def sync_identity_to_render(email: str) -> bool:
    """Bridge function called by the FastAPI backend on /sync."""
    try:
        ensure_identity_exists(email)
        return sync_fresh_identity_to_cloud(email) is True
    except Exception as e:
        print(f" Sync logic failed: {e}")
        return False


def decrypt_payload(payload):
    """Delegates to decy.decrypt_payload (reads legacy flat files written by _write_legacy_key_files)."""
    from decy import decrypt_payload as _decy_decrypt
    return _decy_decrypt(payload)


def main():
    ensure_identity_exists()
    if not load_credentials():
        print(" Could not load credentials.")
        return
    try:
        from gmail_auth import get_user_profile
        profile = get_user_profile()
        my_email = profile.get("email")
    except Exception as e:
        print(f" Could not get profile: {e}")
        return

    print("\n=== QuMail Receiver ===")
    print("1. Receive & decrypt latest secure message")
    print("2. Choose from list of received QuMail emails")
    choice = input("\nEnter choice (1/2): ").strip()

    emails = fetch_qumail_emails()
    if not emails:
        print("\n No QuMail messages found.")
        return

    if choice == "2":
        print("\n Found Encrypted Emails:")
        for idx, msg in enumerate(emails, 1):
            print(f"{idx}. ID: {msg['id']}")
        try:
            sel = int(input("\nSelect email number: "))
            message_id = emails[sel - 1]["id"]
        except (ValueError, IndexError):
            print("Invalid selection.")
            return
    else:
        message_id = emails[0]["id"]

    print(f"\n Fetching payload for: {message_id}")
    payload = fetch_payload_from_email(message_id)
    if payload:
        decrypt_payload(payload)
    else:
        print(" Failed to extract payload.")

    sync_fresh_identity_to_cloud(my_email)


if __name__ == "__main__":
    main()
