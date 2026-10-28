# decy.py (UPDATED WITH 4 OPTIONS)

import os
import base64
import requests
import hashlib
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import json
import urllib.parse
from typing import Optional  # Import Optional for fetch_key_from_kme


# Try to load quantcrypt ML-KEM (MLKEM_768)
_pqc_quantcrypt = None
_mlkem_768 = None
try:
    from quantcrypt import kem as _quantkem
    _mlkem_768 = _quantkem.MLKEM_768()
    _pqc_quantcrypt = "quantcrypt-mlkem"
    print(" PQC Backend (quantcrypt MLKEM_768) loaded for decryption.")
except Exception as e:
    _mlkem_768 = None
    _pqc_quantcrypt = None
    print(
        f" quantcrypt.MLKEM_768 not available for decryption: {e}. "
        "Will use KME symmetric fallback when necessary."
    )

# --------------------------
# Configuration
# --------------------------
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.getenv("QMAIL_DATA_DIR", BACKEND_DIR))
os.makedirs(DATA_DIR, exist_ok=True)
KME_URL = os.getenv("KME_URL", "http://127.0.0.1:8443")

# Legacy flat key files  always kept up-to-date for the active account
# by qumail_reciever._write_legacy_key_files()
DH_KEY_FILE  = os.path.join(DATA_DIR, "receiver_dh_private.txt")
PQC_PRIV_FILE = os.path.join(DATA_DIR, "receiver_pqc_private.bin")
PQC_PUB_FILE  = os.path.join(DATA_DIR, "receiver_pqc_public.bin")

BLOCK_SIZE = 16

# --------------------------
# Helpers
# --------------------------
def b64(e: bytes) -> str:
    return base64.b64encode(e).decode("utf-8")

def ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))

def fetch_key_from_kme(num_bytes: int, key_type: str, key_id_override: Optional[str] = None):
    """
    Fetch key material from KME.
    If key_id_override provided -> fetch stored key for that key_id.
    If not -> request 'count=num_bytes' bytes.
    """
    params = {"key_type": key_type}

    if key_id_override:
        params["key_id"] = key_id_override
        # Send count=0 to satisfy strict FastAPI validation when key_id is present
        params["count"] = 0
    else:
        params["count"] = num_bytes

    # Ensure this uses /GET_KEY
    url = urllib.parse.urljoin(KME_URL.rstrip('/') + '/', "GET_KEY")

    resp = requests.get(url, params=params, timeout=10.0)
    resp.raise_for_status()
    keys = resp.json().get("keys", [])
    if not keys:
        raise ValueError("KME returned no keys")
    key_info = keys[0]
    return ub64(key_info["key"]), key_info["key_id"]

def fetch_key_from_kme_for_decryption(key_id: str, method: str):
    """
    Fetch stored key material for decryption using key_id
    and SAME key_type as encryption.
    """
    if method in ("AES",):
        key_type = "aes"
    elif method in ("QKD-AES", "QKD-seeded-AES"):
        key_type = "qkd"
    elif method == "PQC-Hybrid-AES":
        key_type = "pqc"
    else:
        raise ValueError(f"Unknown method for key fetch: {method}")

    key_bytes, _ = fetch_key_from_kme(
        num_bytes=0,
        key_type=key_type,
        key_id_override=key_id
    )
    return key_bytes



def aes_decrypt_b64(iv_b64: str, ciphertext_b64: str, key_bytes: bytes):
    iv = ub64(iv_b64)
    ct = ub64(ciphertext_b64)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
    pt_padded = cipher.decrypt(ct)
    return unpad(pt_padded, BLOCK_SIZE)


def _dh_session_key(payload: dict) -> bytes:
    """Recompute the L4 Diffie-Hellman session key from the sender's public value."""
    A = int(base64.b64decode(payload["sender_public_hash"]).decode())
    P = int(base64.b64decode(payload["dh_p"]).decode())
    try:
        with open(DH_KEY_FILE, "r") as f:
            b = int(f.read().strip())
    except FileNotFoundError:
        raise FileNotFoundError(
            "DH private key not found. Sync an identity before decrypting L4 mail."
        )
    shared = pow(A, b, P)
    shared_bytes = shared.to_bytes((P.bit_length() + 7) // 8, "big")
    return hashlib.sha256(shared_bytes).digest()


def _pqc_session_key(payload: dict) -> bytes:
    """Recompute the L3 ML-KEM session key using the stored private key."""
    sender_ct = base64.b64decode(payload["sender_public_hash"])
    try:
        with open(PQC_PRIV_FILE, "rb") as f:
            sk_rec = f.read()
    except FileNotFoundError:
        if "receiver_private_demo" in payload:
            sk_rec = base64.b64decode(payload["receiver_private_demo"])
        else:
            raise FileNotFoundError("PQC private key not found. Please sync identity.")
    if _mlkem_768 is None:
        raise ValueError("ML-KEM backend not available for decryption")
    shared_secret = _mlkem_768.decaps(sk_rec, sender_ct)
    return hashlib.sha256(shared_secret).digest()


def _aes_gcm_decrypt(payload: dict, key: bytes, aad: bytes) -> bytes:
    nonce = ub64(payload["nonce"])
    ciphertext = ub64(payload["ciphertext"])
    tag = ub64(payload["tag"])
    return AESGCM(key).decrypt(nonce, ciphertext + tag, aad)

def pqc_decapsulate(payload):
    kem_ct_b64 = payload.get("kem_ciphertext")
    kem_priv_b64 = payload.get("kem_private_key_demo") or payload.get("private_key_b64")

    if not kem_ct_b64 or not kem_priv_b64:
        raise ValueError("Missing PQC KEM fields")

    kem_ct = ub64(kem_ct_b64)
    kem_priv = ub64(kem_priv_b64)

    if _mlkem_768 is None:
        raise ValueError("ML-KEM backend not available")

    shared_secret = _mlkem_768.decaps(kem_priv, kem_ct)
    return shared_secret[:32]


def decrypt_payload(payload):
    """
    Decrypt a QuMail payload for any level.
    Key files (DH_KEY_FILE, PQC_PRIV_FILE) are always kept current for the
    active account by qumail_reciever._write_legacy_key_files().
    """
    method = payload.get("method", "").strip()

    # --- Authenticated AES-GCM payloads (new encryption paths) ---
    if method in (
        "QKD-AES-GCM",
        "PQC-Hybrid-AES-GCM",
        "DH-AES-GCM",
        "QKD-DH-Fallback-GCM",
    ):
        if method == "DH-AES-GCM":
            return _aes_gcm_decrypt(payload, _dh_session_key(payload), b"qumail-l4-v1")

        if method == "PQC-Hybrid-AES-GCM" and "sender_public_hash" in payload:
            return _aes_gcm_decrypt(payload, _pqc_session_key(payload), b"qumail-l3-v1")

        if method == "PQC-Hybrid-AES-GCM":
            key_id = payload.get("key_id")
            if not key_id:
                raise ValueError("Missing key_id for PQC fallback payload")
            key, _ = fetch_key_from_kme(
                num_bytes=32, key_type="pqc-fallback", key_id_override=key_id
            )
            return _aes_gcm_decrypt(payload, key[:32], b"qumail-l3-fallback-v1")

        if method == "QKD-DH-Fallback-GCM":
            key_id = payload.get("key_id")
            if not key_id:
                raise ValueError("Missing key_id for L4 fallback payload")
            key, _ = fetch_key_from_kme(
                num_bytes=32, key_type="aes-256", key_id_override=key_id
            )
            return _aes_gcm_decrypt(payload, key, b"qumail-l4-fallback-v1")

        key_id = payload.get("key_id")
        if not key_id:
            raise ValueError("Missing key_id for AES-GCM payload")
        key, _ = fetch_key_from_kme(
            num_bytes=32, key_type="aes-256", key_id_override=key_id
        )
        return _aes_gcm_decrypt(payload, key, b"qumail-l2-v1")

    # --- OTP (Level 1) ---
    if method == "OTP":
        ciphertext = base64.b64decode(payload["ciphertext"])
        key_ids = payload.get("key_ids", [])
        full_key = b""
        for kid in key_ids:
            key, _ = fetch_key_from_kme(num_bytes=len(ciphertext), key_type="otp", key_id_override=kid)
            full_key += key
        return bytes([c ^ k for c, k in zip(ciphertext, full_key)])

    # --- Level 4: DH + AES ---
    if method == "AES" and "sender_public_hash" in payload:
        ciphertext = base64.b64decode(payload["ciphertext"])
        iv = base64.b64decode(payload["iv"])
        A = int(base64.b64decode(payload["sender_public_hash"]).decode())
        P = int(base64.b64decode(payload["dh_p"]).decode())
        try:
            with open(DH_KEY_FILE, "r") as f:
                b = int(f.read().strip())
        except FileNotFoundError:
            b = int(base64.b64decode(payload.get("receiver_private_demo", "")).decode())
        shared_secret_int = pow(A, b, P)
        print(f"?? DH Secret Derived: {str(shared_secret_int)[:10]}...")
        shared_bytes = shared_secret_int.to_bytes((P.bit_length() + 7) // 8, "big")
        session_key = hashlib.sha256(shared_bytes).digest()
        cipher = AES.new(session_key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(ciphertext), AES.block_size)

    # --- Level 2: QKD-AES & fallbacks ---
    elif method in ("QKD-AES", "QKD-seeded-AES", "QKD-DH-Fallback", "KME-Fallback"):
        ciphertext = base64.b64decode(payload["ciphertext"])
        iv = base64.b64decode(payload["iv"])
        key_id = payload.get("key_id")
        if not key_id:
            raise ValueError(f"Missing key_id for {method}")
        key, _ = fetch_key_from_kme(num_bytes=32, key_type="aes-256", key_id_override=key_id)
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(ciphertext), AES.block_size)

    # --- Level 3: PQC Hybrid (ML-KEM) ---
    elif method in ("PQC-Hybrid-AES", "Secure-Cloud-Session") and "sender_public_hash" in payload:
        ciphertext = base64.b64decode(payload["ciphertext"])
        iv = base64.b64decode(payload["iv"])
        sender_ct = base64.b64decode(payload["sender_public_hash"])
        try:
            with open(PQC_PRIV_FILE, "rb") as f:
                sk_rec = f.read()
        except FileNotFoundError:
            if "receiver_private_demo" in payload:
                sk_rec = base64.b64decode(payload["receiver_private_demo"])
            else:
                raise FileNotFoundError("PQC private key not found. Please sync identity.")
        if _mlkem_768 is None:
            raise ValueError("ML-KEM backend not available for decryption")
        shared_secret = _mlkem_768.decaps(sk_rec, sender_ct)
        session_key = hashlib.sha256(shared_secret).digest()
        cipher = AES.new(session_key, AES.MODE_CBC, iv)
        return unpad(cipher.decrypt(ciphertext), AES.block_size)

    # --- Unencrypted ---
    elif method == "NONE":
        return base64.b64decode(payload["data"]) if "data" in payload else b""

    # --- Fallback: key_id based AES ---
    else:
        key_id = payload.get("key_id")
        if key_id and "ciphertext" in payload and "iv" in payload:
            ciphertext = base64.b64decode(payload["ciphertext"])
            iv = base64.b64decode(payload["iv"])
            key, _ = fetch_key_from_kme(num_bytes=32, key_type="aes-256", key_id_override=key_id)
            cipher = AES.new(key, AES.MODE_CBC, iv)
            return unpad(cipher.decrypt(ciphertext), AES.block_size)
        raise ValueError(f"Unsupported or unrecognised method: '{method}'")

def handle_decrypted_data(decrypted_data: bytes, payload: dict):
    """
    Handles the final decrypted bytes based on the 'content_type' in the payload.
    """
    content_type = payload.get("content_type", "text")  # Default to 'text'

    if decrypted_data is None:
        print("Decryption failed: No data recovered.")
        return

    if content_type == "file":
        # --- File Logic: Use Save As Dialog (interactive CLI only) ---
        import tkinter as tk
        from tkinter.filedialog import asksaveasfilename

        original_filename = payload.get("filename", "decrypted_file_output.bin")

        # 1. Initialize Tkinter root window (and hide it)
        root = tk.Tk()
        root.withdraw()
        root.update()

        # 2. Open the Save As dialog
        save_path = asksaveasfilename(
            defaultextension=".*",
            initialfile=original_filename,
            title="Choose location to save decrypted file",
        )

        # 3. Clean up the Tkinter window
        root.destroy()

        if not save_path:
            print(" File save cancelled by user.")
            return

        try:
            print(f"Attempting to save decrypted file to: {save_path}")

            with open(save_path, "wb") as f:
                f.write(decrypted_data)

            print("--- Decryption Result ---")
            print(" Decryption successful: File saved!")
            print(f"Saved to: {save_path}")

        except OSError as e:
            print(" File system error saving file:")
            print(f"Error details: {e}")

        except Exception as e:
            print(f" Unexpected error during file save: {e}")

    elif content_type == "text":
        decrypted_text = decrypted_data.decode("utf-8")
        
        try:
            data = json.loads(decrypted_text)
            
            if "digilocker_token" in data:
                print("\n DigiLocker token found, fetching documents...")
                fetch_digilocker_docs_from_token(data["digilocker_token"])
                
            if "message" in data:
                print("\n Decrypted Message:")
                print(data["message"])

        except json.JSONDecodeError:
            # If it's normal text (not JSON)
            print(decrypted_text)


    else:
        # Fallback for unknown/unsupported content types
        print("--- Decryption Result (Unknown Type) ---")
        print(f"Content Type '{content_type}' is unknown. Printing raw bytes.")
        print(decrypted_data)


def interactive_json_input():
    """Prompts the user for payload fields line by line."""
    payload = {}
    print("\n--- One by One payload Input ---")

    # 1. Get required fields
    try:
        payload["level"] = int(input("Enter 'level' (1-4): ").strip())
    except ValueError:
        raise ValueError("Level must be an integer.")

    payload["method"] = input(
        "Enter 'method' (OTP, QKD-seeded-AES, PQC-Hybrid-AES, AES): "
    ).strip()
    payload["key_id"] = input("Enter 'key_id': ").strip().replace('"', "")
    payload["ciphertext"] = input(
        "Enter 'ciphertext' (Base64): "
    ).strip().replace('"', "")

    # 2. Get optional fields based on method
    if payload["method"] != "OTP":
        payload["iv"] = input("Enter 'iv' (Base64): ").strip().replace('"', "")

    # Optional fields for testing/demo (Level 4)
    local_key = input(
        "Enter 'local_key_b64' (optional, press Enter to skip): "
    ).strip()
    if local_key:
        payload["local_key_b64"] = local_key

    # Optional content_type / filename
    ctype = input("Enter 'content_type' (text/file) [optional]: ").strip()
    if ctype:
        payload["content_type"] = ctype

    if ctype == "file":
        fname = input("Enter 'filename' (original name) [optional]: ").strip()
        if fname:
            payload["filename"] = fname

    # Clean up empty strings from input
    payload = {k: v for k, v in payload.items() if v}

    return payload

def resolve_token(token: str):
    with open("token_store.json", "r") as f:
        for line in f:
            obj = json.loads(line)
            if obj["token"] == token:
                return obj["files"]
    raise ValueError(" Invalid token")


DIGILOCKER_BASE_URL = "http://127.0.0.1:8444"

def fetch_digilocker_docs_from_token(token: str):
    import tkinter as tk
    from tkinter.filedialog import asksaveasfilename

    access_token, doc_ids_part = token.split("::", 1)
    doc_ids = [d.strip() for d in doc_ids_part.split(",")]

    root = tk.Tk()
    root.withdraw()

    for doc_id in doc_ids:
        print(f" Fetching DigiLocker doc: {doc_id}")

        url = f"{DIGILOCKER_BASE_URL}/api/user/docs/{doc_id}"
        headers = {"Authorization": f"Bearer {access_token}"}

        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(f" Failed to fetch {doc_id}")
            continue

        save_path = asksaveasfilename(
            initialfile=f"{doc_id}.pdf",
            defaultextension=".pdf",
            title=f"Save {doc_id}"
        )

        if save_path:
            with open(save_path, "wb") as f:
                f.write(resp.content)
            print(f" Saved {doc_id}")

    root.destroy()



# --------------------------
# Decrypt CLI
# --------------------------
if __name__ == "__main__":
    print("=== Decrypt Demo ===")
    print("Options to load encrypted payload:")
    print("  (1) From file (payload.json)")
    print("  (2) Paste JSON")
    print("  (3) DigiLocker doc payload (from file)")
    print("  (4) Line by line (manual fields)")
    print("  (5) Fetch DigiLocker docs using Token")


    choice = input(
        "Choose input type (1=File, 2=JSON, 3=DigiLocker doc, 4=Line by line): "
    ).strip()

    try:
        if choice == "1":
            # Normal payload from file
            fname = input("Payload filename (e.g. payload.json): ").strip()
            with open(fname, "r") as f:
                payload = json.load(f)

        elif choice == "2":
            # Paste full JSON in terminal
            txt = input("Paste JSON payload here (single line if possible):\n")
            payload = json.loads(txt)

        elif choice == "3":
            # DigiLocker doc payload -> also JSON file, just labelled separately
            fname = input("DigiLocker payload filename (e.g. digilocker_payload.json): ").strip()
            with open(fname, "r") as f:
                payload = json.load(f)

        elif choice == "4":
            # Line-by-line manual entry
            payload = interactive_json_input()
        
        elif choice == "5":
            token = input("Enter DigiLocker token: ").strip()
            fetch_digilocker_docs_from_token(token)
            exit()



        else:
            print("Invalid Selection")
            exit()

    except Exception as e:
        print(f"Error loading payload: {e}")
        exit()

   # -------------------------------------------------
   # HANDLE LOCAL FILE BUNDLE PAYLOAD
   # -------------------------------------------------
    try:
        if payload.get("type") == "local_file_bundle":
            print(" Local file bundle detected")
            for file_payload in payload.get("files", []):
                decrypted_bytes = decrypt_payload(file_payload)
                handle_decrypted_data(decrypted_bytes, file_payload)
            if "message" in payload:
                print(f"\n Message: {payload['message']}")
        
        # Shift this ELSE block to the left to align with the first IF
        else:
            # This handles single messages or single files (Levels 1-4)
            decrypted_bytes = decrypt_payload(payload)
            handle_decrypted_data(decrypted_bytes, payload)

    except Exception as e:
        print(f" Decryption Failed: {e}")