from fastapi import FastAPI, HTTPException, Query,Form
from pydantic import BaseModel
from typing import List, Optional
import base64
import json
import os
import secrets
import threading
import itertools 

# --- Configuration ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.getenv("QMAIL_DATA_DIR", BASE_DIR))
os.makedirs(DATA_DIR, exist_ok=True)
KEY_DB_FILE = os.path.join(DATA_DIR, "kme_keys.json")
CHUNK_KEYS_FILE = os.path.join(DATA_DIR, "chunk_keys.json")
MAX_KEY_COUNT = 30 
DEMO_KEY_COUNT = int(os.getenv("KME_DEMO_KEY_COUNT", "64"))

# --- Global Key Storage ---
KEY_STORE = {}
KEY_SERIAL_COUNTER = 0 
STORE_LOCK = threading.Lock() 

# --- Utility Functions ---

def b64(b: bytes) -> str:
    """Encode bytes to a Base64 string."""
    return base64.b64encode(b).decode("utf-8")

def ub64(s: str) -> bytes:
    """Decode a Base64 string back to bytes."""
    return base64.b64decode(s.encode("utf-8"))

# --- Persistence Functions ---

def load_keys():
    """Load keys from the JSON file on startup."""
    global KEY_STORE, KEY_SERIAL_COUNTER
    if os.path.exists(KEY_DB_FILE):
        print(f"Loading keys from {KEY_DB_FILE}...")
        try:
            with open(KEY_DB_FILE, "r") as f:
                data = json.load(f)
                KEY_STORE = data.get("keys", {})
                KEY_SERIAL_COUNTER = data.get("counter", 0)
                
                # Convert base64 keys back to bytes
                for k in KEY_STORE:
                    # NOTE: Key is stored as base64 string in the file, decode it here
                    KEY_STORE[k]["key"] = ub64(KEY_STORE[k]["key"]) 
            
            print(f"Loaded {len(KEY_STORE)} pre-generated keys. Counter at {KEY_SERIAL_COUNTER}.")
        except Exception as e:
            print(f"Error loading keys: {e}. Starting with an empty store.")
            KEY_STORE = {}
            KEY_SERIAL_COUNTER = 0
    else:
        # This is the message you saw, now addressed by the bootstrap call below
        print(f"Key file {KEY_DB_FILE} not found. Starting with an empty store. Distribution will fail.")


def bootstrap_kme_from_chunks():
    """
    Initializes kme_keys.json from the raw keys in chunk_keys.json 
    if the KME database file does not already exist.

    In demo mode (the default) a fresh set of random keys is generated when no
    chunk file is present, so the demo can exercise levels 1/2 without setup.
    """
    if os.path.exists(KEY_DB_FILE):
        return

    if os.path.exists(CHUNK_KEYS_FILE):
        print(f"Bootstrapping {KEY_DB_FILE} from {CHUNK_KEYS_FILE} ...")
        with open(CHUNK_KEYS_FILE, "r") as f:
            chunk_data = json.load(f)
        keys_list = chunk_data.get("keys", [])
        key_type = "QKD"
    else:
        demo_env = os.getenv("QMAIL_DEMO_MODE", "1").strip().lower()
        if demo_env in ("0", "false", "no"):
            print(" chunk_keys.json not found and demo mode is disabled. KME cannot bootstrap.")
            return
        print(f"No key source found - generating {DEMO_KEY_COUNT} ephemeral demo keys.")
        keys_list = [b64(secrets.token_bytes(32)) for _ in range(DEMO_KEY_COUNT)]
        key_type = "DEMO"

    kme_data = {
        "counter": len(keys_list),
        "keys": {}
    }

    # Assign sequential IDs (starting at 1) and status to each key
    for idx, key_b64 in enumerate(keys_list, start=1):
        kme_data["keys"][str(idx)] = {
            "key": key_b64,
            "key_type": key_type,
            "status": "available"
        }

    with open(KEY_DB_FILE, "w") as f:
        json.dump(kme_data, f, indent=4)

    print(f" Created {KEY_DB_FILE} with {len(keys_list)} keys.")

# NOTE: save_keys() is omitted as key status updates are not persisted in this simplified demo.
# For a production system, a save_keys() function would be required here.


# --- Pydantic Models ---

class KeyInfo(BaseModel):
    key_id: str
    key: str  # base64

class KeysResponse(BaseModel):
    keys: List[KeyInfo]

# --- Initialization ---

# 1. Ensure the key database file exists (if not, create it from chunks)
bootstrap_kme_from_chunks() 

# 2. Load the key pool into memory for the application
load_keys()

# 3. Initialize the FastAPI app
app = FastAPI(title="Static Pool KME Server (demo)")

# --- FastAPI Endpoints ---

@app.get("/GET_KEY", response_model=KeysResponse)
def get_key(
    count: Optional[int] = Query(None, ge=0), 
    key_type: str = Query(..., description="e.g., QKD, AES"), 
    key_id: Optional[str] = None
):
    """
    GET_KEY endpoint: 
    1. Fetches an existing key by ID (for decryption access).
    2. Distributes the next available key (for encryption consumption).
    """
    
    # --- 1. Key Fetch (by key_id) - Decryption Access ---
    if key_id:
        with STORE_LOCK:
            entry = KEY_STORE.get(key_id)
        if not entry:
            # Key not found in the pool
            raise HTTPException(status_code=404, detail=f"key_id '{key_id}' not found")
        
        # Key is returned for decryption access, regardless of its 'status'
        return {"keys": [{"key_id": key_id, "key": b64(entry["key"])}]}

    # --- 2. Key Distribution (New Key Request) - Consumption Logic ---
    
    if count is None:
        raise HTTPException(status_code=400, detail="count must be provided for a distribution request.")
    
    # Logic to find the next available key ID to distribute
    with STORE_LOCK:
        
        # 1. Find all available key IDs
        available_ids = [
            k_id for k_id, entry in KEY_STORE.items() 
            if entry.get("status", "available") == "available"
        ]
        
        if not available_ids:
            # If the pool is exhausted.
            raise HTTPException(
                status_code=507, 
                detail=f"Key pool is exhausted. Max count: {len(KEY_STORE)}. No more keys available for distribution."
            )
        
        # 2. Select the key to distribute (e.g., the lowest ID)
        distribute_id = min(available_ids, key=int) 
        
        # 3. Retrieve the entry and update its status (CONSUME the key)
        entry = KEY_STORE[distribute_id]
        entry["status"] = "consumed"
        
        # NOTE: In a real system, you would call save_keys() here to persist the "consumed" status.
        
        
    # 4. Return the distributed key
    return {"keys": [{"key_id": distribute_id, "key": b64(entry["key"])}]}

@app.get("/LIST_KEYS")
def list_keys():
    """Return list of currently stored key ids and their status."""
    with STORE_LOCK:
        # Prepare a more informative list for the demo
        status_counts = {}
        for entry in KEY_STORE.values():
            status = entry.get("status", "available")
            status_counts[status] = status_counts.get(status, 0) + 1
            
        return {
            "total_keys": len(KEY_STORE), 
            "status_summary": status_counts,
            "key_ids": list(KEY_STORE.keys())
        }

