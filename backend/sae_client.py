import os
import base64
import requests
import uuid
import time
import hashlib
import random
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
import json
import typing
from typing import Dict, Any
from urllib.parse import urlparse, parse_qs

# --------------------------
# PQC Configuration (Using pqcrypto / quantcrypt)
# --------------------------

_pqc_quantcrypt = None
_mlkem_768 = None
try:
    from quantcrypt import kem as _quantkem
    _mlkem_768 = _quantkem.MLKEM_768()
    _pqc_quantcrypt = "quantcrypt-mlkem"
    print(" PQC Backend (quantcrypt MLKEM_768) loaded.")
except Exception as e:
    _mlkem_768 = None
    _pqc_quantcrypt = None
    print(f"quantcrypt.MLKEM_768 not available: {e}. Will use KME symmetric fallback for Level 3.")

# --------------------------
# Configuration
# --------------------------
KME_URL = os.getenv("KME_URL", "http://127.0.0.1:8443")
IDENTITY_SERVER_URL = os.getenv(
    "IDENTITY_URL", "https://qumail-identity-server.onrender.com"
).rstrip("/")
BLOCK_SIZE = 16

#  DigiLocker / Mock-Digilocker configuration
DIGILOCKER_BASE_URL = os.getenv("DIGILOCKER_URL", "http://127.0.0.1:8444")
DIGILOCKER_CLIENT_ID = "QUMAIL_DEMO"
DIGILOCKER_REDIRECT_URI = "http://localhost/digilocker/callback"
DIGILOCKER_STATE = "xyz123"


# --------------------------
# Helper functions
# --------------------------

def b64(e: bytes) -> str:
    return base64.b64encode(e).decode("utf-8")


def ub64(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))

def _cloud_identity_lookup(email: str, label: str = "Identity"):
    """
    Helper to fetch recipient keys from Cloud with a retry for Render spin-up.
    """
    max_retries = 2
    for attempt in range(max_retries):
        try:
            print(f" {label} lookup for {email} (Attempt {attempt+1}/{max_retries})...")
            resp = requests.get(f"{IDENTITY_SERVER_URL}/lookup/{email}", timeout=30.0)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f" Recipient {email} not found in QuMail Cloud Registry (Checked: {IDENTITY_SERVER_URL})")
                raise RuntimeError(f"Recipient {email} has not registered their Quantum Identity. Please ask them to use the 'Sync Identity' button.")
            if e.response.status_code >= 500:
                print(f" Cloud Server Error ({e.response.status_code}): The Identity Server on Render ({IDENTITY_SERVER_URL}) is currently unavailable.")
                raise RuntimeError(f"The Cloud Identity Server is currently offline. Status: {e.response.status_code}")
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt == 0:
                print(f" Cloud server is warming up (Render cold-start). Retrying in 2s...")
                time.sleep(2)
                continue
            print(f" Cloud server timed out after {max_retries} attempts.")
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f" Encryption Error: {str(e)}")
            raise

def auto_fetch_receiver_hash(email: str, level: int):
    """Pings the KME Server to see if the recipient has a registered public key."""
    try:
        keys = _cloud_identity_lookup(email, f"Level {level}")
        return keys.get(str(level))
    except Exception:
        return None

def fetch_key_from_kme(num_bytes: int, key_type: str, key_id_override: str = None):
    """
    Fetch key material from KME.
    If key_id_override provided -> fetch stored key for that key_id.
    If not -> request 'count=num_bytes' bytes.
    """
    params = {"key_type": key_type}

    if key_id_override:
        params["key_id"] = key_id_override
        # ADDED: Send count=0 to satisfy strict FastAPI validation
        params["count"] = 0
    else:
        params["count"] = num_bytes

    # Ensure this is /GET_KEY as per the most stable server configuration
    import urllib.parse
    url = urllib.parse.urljoin(KME_URL.rstrip('/') + '/', "GET_KEY")

    resp = requests.get(url, params=params, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    keys = data.get("keys", [])
    if not keys:
        raise ValueError("KME returned no keys")
    key_info = keys[0]
    return ub64(key_info["key"]), key_info["key_id"]


def aes_encrypt(data: bytes, key_bytes: bytes):
    iv = get_random_bytes(16)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(data, BLOCK_SIZE))
    return b64(iv), b64(ciphertext)


# --------------------------
# DigiLocker helper functions
# --------------------------

def digilocker_get_auth_code() -> str:
    """
    Simulate user clicking 'Allow' on /oauth/authorize by directly calling
    /oauth/authorize/confirm on the Mock DigiLocker server.
    """
    url = f"{DIGILOCKER_BASE_URL}/oauth/authorize/confirm"
    data = {
        "client_id": DIGILOCKER_CLIENT_ID,
        "redirect_uri": DIGILOCKER_REDIRECT_URI,
        "state": DIGILOCKER_STATE,
        "action": "allow",
    }

    resp = requests.post(url, data=data, allow_redirects=False)
    if resp.status_code != 302:
        raise RuntimeError(
            f"Unexpected status from /oauth/authorize/confirm: "
            f"{resp.status_code}, body={resp.text}"
        )

    redirect_url = resp.headers.get("Location")
    if not redirect_url:
        raise RuntimeError("No Location header from /oauth/authorize/confirm")

    parsed = urlparse(redirect_url)
    qs = parse_qs(parsed.query)
    code_list = qs.get("code")
    if not code_list:
        raise RuntimeError("No 'code' found in redirect Location")

    return code_list[0]


def digilocker_get_access_token(auth_code: str) -> str:
    """
    Exchange auth_code for access_token using /oauth/token.
    """
    url = f"{DIGILOCKER_BASE_URL}/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": DIGILOCKER_CLIENT_ID,
        "client_secret": "demo-secret",  # mock only
        "redirect_uri": DIGILOCKER_REDIRECT_URI,
    }

    resp = requests.post(url, data=data)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Error from /oauth/token: {resp.status_code}, body={resp.text}"
        )

    token_data = resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("No access_token returned by /oauth/token")

    return access_token


def digilocker_fetch_docs(access_token: str):
    """
    Get list of available docs from /api/user/docs.
    """
    url = f"{DIGILOCKER_BASE_URL}/api/user/docs"
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Error from /api/user/docs: {resp.status_code}, body={resp.text}"
        )

    return resp.json()


def digilocker_download_doc(doc_id: str, access_token: str) -> bytes:
    """
    Download a single doc's raw bytes from /api/user/docs/{doc_id}.
    """
    url = f"{DIGILOCKER_BASE_URL}/api/user/docs/{doc_id}"
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Error downloading doc {doc_id}: {resp.status_code}, body={resp.text}"
        )

    return resp.content


def digilocker_choose_doc_and_get_bytes() -> (bytes, str):
    """
    Full DigiLocker flow:
      1) Get auth_code
      2) Exchange for access_token
      3) List docs
      4) Let user pick one
      5) Download file bytes
    Returns: (file_bytes, original_filename)
    """
    print(" Contacting Mock DigiLocker...")

    # 1) Authorization code
    auth_code = digilocker_get_auth_code()
    print(f" Got auth_code: {auth_code}")

    # 2) Access token
    access_token = digilocker_get_access_token(auth_code)
    print(f" Got access_token: {access_token[:10]}...")

    # 3) List docs
    docs = digilocker_fetch_docs(access_token)
    if not docs:
        raise RuntimeError("No documents returned by DigiLocker.")

    print("\n Available DigiLocker Documents:")
    for idx, doc in enumerate(docs, start=1):
        name = doc.get("name", "")
        doc_id = doc.get("id", "")
        file_name = doc.get("file_name", "")
        print(f"  {idx}) {name} [{doc_id}] ({file_name})")

    # 4) Let user choose one
    while True:
        try:
            choice_str = input("\nSelect document number to encrypt: ").strip()
            choice_idx = int(choice_str)
            if 1 <= choice_idx <= len(docs):
                break
            else:
                print(f"Please enter a number between 1 and {len(docs)}.")
        except ValueError:
            print("Please enter a valid integer.")

    selected_doc = docs[choice_idx - 1]
    doc_id = selected_doc.get("id")
    original_filename = selected_doc.get("file_name") or f"{doc_id}.pdf"

    print(f"\n Downloading '{original_filename}' from DigiLocker...")
    data_bytes = digilocker_download_doc(doc_id, access_token)
    print(f" Downloaded {len(data_bytes)} bytes from DigiLocker.")

    return data_bytes, original_filename

def digilocker_choose_docs_and_create_token():
    # Step 1: Auth
    auth_code = digilocker_get_auth_code()
    access_token = digilocker_get_access_token(auth_code)

    # Step 2: Fetch docs
    docs = digilocker_fetch_docs(access_token)

    print("\n Available DigiLocker Documents:")
    for i, d in enumerate(docs, 1):
        print(f"{i}) {d['id']} - {d['name']} ({d['file_name']})")

    # Step 3: Multi-select
    choice = input("Enter document numbers (comma separated): ")
    indices = [int(x.strip()) - 1 for x in choice.split(",")]

    selected_doc_ids = [docs[i]["id"] for i in indices]

    # Step 4: Create ONE token
    token = f"{access_token}::{','.join(selected_doc_ids)}"
    return token

def derive_aes_key(shared_secret: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"qumail-session",
        backend=default_backend()
    ).derive(shared_secret)


def aes_gcm_encrypt(key: bytes, data: bytes):
    nonce = get_random_bytes(12)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(data)
    return b64(nonce), b64(ciphertext), b64(tag)


def aes_gcm_encrypt_aad(key: bytes, data: bytes, aad: bytes):
    """AES-256-GCM with additional authenticated data (used for new payloads)."""
    nonce = get_random_bytes(12)
    aesgcm = AESGCM(key)
    sealed = aesgcm.encrypt(nonce, data, aad)
    ciphertext, tag = sealed[:-16], sealed[-16:]
    return b64(nonce), b64(ciphertext), b64(tag)

# --------------------------
# Encryption logic by level
# --------------------------
def encrypt_data(data_bytes: bytes, level: int, content_type: str,recipient_email: str = None,original_filename: str = None):

    # 1. Initialize Base Payload
    payload = {
        "level": level,
        "method": None,
        "content_type": content_type
    }

    if content_type == "file" and original_filename:
        payload["filename"] = original_filename

    # 2. Add Encryption Data based on Level

    if level == 1 and content_type == "file":
        raise ValueError("Level 1 (OTP) only supports text messages. Please upgrade to L2, L3, or L4 for files.")

    if level == 1:
        # OTP  fetch enough key material in chunks
        remaining = len(data_bytes)
        key_bytes = b""
        key_ids = []

        print(f"DEBUG: Message length is {remaining} bytes. Fetching OTP chunks...")

        while remaining > 0:
            # Fetch a 32-byte chunk from KME
            chunk, key_id = fetch_key_from_kme(32, "one_time_pad")
            key_bytes += chunk
            key_ids.append(key_id)
            remaining -= len(chunk)
            print(f"DEBUG: Fetched key_id {key_id}. Remaining: {max(0, remaining)} bytes.")

        # Perform XOR encryption
        ciphertext_bytes = bytes(
            p ^ k for p, k in zip(data_bytes, key_bytes)
        )

        payload.update({
            "method": "OTP",
            "key_ids": key_ids,  #  List of IDs for the receiver
            "ciphertext": b64(ciphertext_bytes)
        })
        print(f" OTP Encryption complete using {len(key_ids)} chunks.")

    elif level == 2:
        # QKD-seeded AES-256-GCM (authenticated encryption)
        key_bytes, key_id = fetch_key_from_kme(32, "aes-256")
        nonce, ciphertext, tag = aes_gcm_encrypt_aad(
            key_bytes, data_bytes, b"qumail-l2-v1"
        )
        payload.update({
            "method": "QKD-AES-GCM",
            "key_id": key_id,
            "nonce": nonce,
            "ciphertext": ciphertext,
            "tag": tag
        })

    elif level == 3:
        try:
            if _mlkem_768:
                # Using our robust cloud lookup with retry
                B_data = _cloud_identity_lookup(recipient_email, "PQC (L3)")
                pk_rec_b64 = B_data.get("3")
                
                if not pk_rec_b64:
                     raise ValueError(f"Recipient {recipient_email} has no registered ML-KEM key.")
                pk_rec = base64.b64decode(pk_rec_b64)
                sender_hint_ct, shared_secret = _mlkem_768.encaps(pk_rec)
                
                # Derive 32-byte key
                session_key = hashlib.sha256(shared_secret).digest()

                nonce, ciphertext, tag = aes_gcm_encrypt_aad(
                    session_key, data_bytes, b"qumail-l3-v1"
                )
                payload.update({
                    "method": "PQC-Hybrid-AES-GCM",
                    "sender_public_hash": b64(sender_hint_ct),
                    "nonce": nonce,
                    "ciphertext": ciphertext,
                    "tag": tag
                })
                return payload # Success return
        except Exception as e:
            print(f" ML-KEM failed: {e}. Falling back to KME.")

        # Fallback: KME key is not post-quantum, but is authenticated AES-GCM
        key_bytes, key_id = fetch_key_from_kme(64, "pqc-fallback")
        nonce, ciphertext, tag = aes_gcm_encrypt_aad(
            key_bytes[:32], data_bytes, b"qumail-l3-fallback-v1"
        )
        payload.update({
            "method": "PQC-Hybrid-AES-GCM",
            "key_id": key_id,
            "nonce": nonce,
            "ciphertext": ciphertext,
            "tag": tag
        })

    elif level == 4:
        # Normalize email
        recipient_email = recipient_email.strip().lower() if recipient_email else None
        if not recipient_email:
            raise ValueError("Recipient email is required for Level 4 encryption.")

        print(f" Initiating Diffie-Hellman Handshake for {recipient_email}...")
        
        try:
            # Step 4: Exchange - Fetch Receiver's Public Key 'B' from Cloud Identity Server
            B_data = _cloud_identity_lookup(recipient_email, "DH (L4)")
            if "4" not in B_data:
                raise ValueError(f"Recipient {recipient_email} has no registered DH key.")
            
            B = int(base64.b64decode(B_data["4"]).decode())

            # Step 1: Agree on Public Numbers (Standard RFC 3526 Group 14)
            P = 0xFFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF
            G = 2

            # Step 2: Choose Private Key (a)
            a = random.getrandbits(256)

            # Step 3: Create Public Key (A = G^a mod P)
            A = pow(G, a, P)

            # Step 5: Compute Shared Secret (S = B^a mod P)
            S = pow(B, a, P)
            print(f" [DEBUG] DH Secret Derived: {str(S)[:10]}...")

            # Step 6: Derive Session Key = SHA256(S_bytes)
            S_bytes = S.to_bytes((P.bit_length() + 7) // 8, 'big')
            session_key = hashlib.sha256(S_bytes).digest()

            # Step 7: Encrypt with AES-256-GCM
            nonce, ciphertext, tag = aes_gcm_encrypt_aad(
                session_key, data_bytes, b"qumail-l4-v1"
            )
            
            payload.update({
                "method": "DH-AES-GCM",
                "sender_public_hash": base64.b64encode(str(A).encode()).decode(), # Share A
                "nonce": nonce,
                "ciphertext": ciphertext,
                "tag": tag,
                "dh_p": base64.b64encode(str(P).encode()).decode() # Share P
            })
        except Exception as e:
            print(f" DH Handshake failed: {e}. Falling back to QKD-seeded AES.")
            # Fallback to Level 2 style authenticated encryption
            key_bytes, key_id = fetch_key_from_kme(32, "aes-256")
            nonce, ciphertext, tag = aes_gcm_encrypt_aad(
                key_bytes, data_bytes, b"qumail-l4-fallback-v1"
            )
            payload.update({
                "method": "QKD-DH-Fallback-GCM",
                "key_id": key_id,
                "nonce": nonce,
                "ciphertext": ciphertext,
                "tag": tag
            })
    else:
        raise ValueError("Invalid level. Must be 1..4")

    return payload
    
# --------------------------
# CLI (Text + File + DigiLocker)
# --------------------------
digilocker_token = None
message = None
combined_bytes = None

if __name__ == "__main__":
    recipient_email = input("Enter recipient email: ").strip()
    print("=== Quantum/Hybrid Encryption Demo (client) ===")
    print("\nOptions:")
    print("  (1) Message")
    print("  (2) Local Document/File")
    print("  (3) DigiLocker Document")

    choice = input(
        "Encrypt (1) Message, (2) Local File, or (3) DigiLocker Document? : "
    ).strip()

    digilocker_token = None
    message = None
    combined_bytes = None

    # -------------------------------
    # OPTION 1: MESSAGE ONLY
    # -------------------------------
    if choice == "1":
        message = input("Enter message: ")
        combined_bytes = message.encode("utf-8")

    # -------------------------------
    # OPTION 2: LOCAL FILE(S)
    # -------------------------------
    elif choice == "2":
        # tkinter is only needed for this interactive CLI path, never for the API.
        import tkinter as tk
        from tkinter.filedialog import askopenfilenames

        root = tk.Tk()
        root.withdraw()
        root.update()

        file_paths = askopenfilenames(title="Select one or more files to encrypt")
        root.destroy()

        if not file_paths:
            print(" No files selected")
            exit()

        files_data = []
        for path in file_paths:
            with open(path, "rb") as f:
                files_data.append({
                    "filename": os.path.basename(path),
                    "data": f.read()
                })
         # Optional message for Local Files
        send_msg = input("\nDo you also want to send a message? (y/N): ").strip().lower()
        if send_msg == "y":
            message = input("Enter message: ")
        

    # -------------------------------
    # OPTION 3: DIGILOCKER
    # -------------------------------
    elif choice == "3":
        digilocker_token = digilocker_choose_docs_and_create_token()

        print("\n DIGILOCKER TOKEN:")
        print(digilocker_token)

        send_msg = input("\nDo you also want to send a message? (y/N): ").strip().lower()
        if send_msg == "y":
            message = input("Enter message: ")

        combined_data = {
            "digilocker_token": digilocker_token
        }

        if message:
            combined_data["message"] = message

        combined_bytes = json.dumps(combined_data).encode("utf-8")

    else:
        print(" Invalid choice")
        exit()
    


    # -------------------------------
    # ENCRYPTION LEVEL SELECTION
    # -------------------------------
    if choice == "1":  # Message only  OTP allowed
        level = int(input(
            "Enter encryption level (1=OTP, 2=QKD-AES, 3=PQC-Hybrid-AES, 4=AES): "
            ))
    
    elif choice in ("2", "3"):  # Local file or DigiLocker  OTP NOT allowed
        level = int(input(
            "Enter encryption level (2=QKD-AES, 3=PQC-Hybrid-AES, 4=AES): "
            ))
    
        
        if level == 1:
            print(" OTP is not allowed for file or DigiLocker encryption")
            exit()


    try:
        # -------------------------------
        # ENCRYPT
        # -------------------------------
        if choice in ("1", "3"):
            final_payload = encrypt_data(
                combined_bytes,
                level,
                content_type="text",
                recipient_email=recipient_email
            )

            print("\n--- ENCRYPTED PAYLOAD ---")
            print(json.dumps(final_payload, indent=2))

            save = input("\nSave encrypted payload to file? (y/N): ").strip().lower()
            if save == "y":
                fname = input("Filename (e.g. payload.json): ").strip() or "payload.json"
                with open(fname, "w") as f:
                    json.dump(final_payload, f, indent=2)
                print(f" Saved to {fname}")

        elif choice == "2":
            encrypted_files = []
            
            for f in files_data:
                encrypted_files.append(
                    encrypt_data(
                        f["data"],
                        level,
                        content_type="file",
                        recipient_email=recipient_email,
                        original_filename=f["filename"]
                        )
                 )

            # -------------------------------
            # BUILD FINAL PAYLOAD (FILES + OPTIONAL MESSAGE)
            # -------------------------------
            final_payload = {
            "type": "local_file_bundle",
            "level": level,
            "files": encrypted_files
            }
            
            if message:
                final_payload["message"] = message

            # -------------------------------
            # SHOW RESULT
            # -------------------------------
            print("\n--- ENCRYPTED PAYLOAD (LOCAL FILES) ---")
            print(json.dumps(final_payload, indent=2))

            # -------------------------------
            # ASK TO SAVE
            # -------------------------------
            save = input("\nSave encrypted payload to file? (y/N): ").strip().lower()
            if save == "y":
                fname = input("Filename (e.g. local_payload.json): ").strip() or "local_payload.json"
                with open(fname, "w") as f:
                    json.dump(final_payload, f, indent=2)
                print(f" Payload saved to {fname}")


    except Exception as e:
        print(" Encryption failed:", e)