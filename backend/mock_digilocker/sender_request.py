# sender_request.py

import requests
from urllib.parse import urlparse, parse_qs
from tkinter import Tk, Listbox, MULTIPLE, Button, END, Scrollbar, RIGHT, Y, LEFT, BOTH

# ----------------- CONFIG -----------------

MOCK_BASE_URL = "http://127.0.0.1:8444"

CLIENT_ID = "QUMAIL_DEMO"
REDIRECT_URI = "http://localhost:8000/digilocker/callback"
STATE = "xyz123"

# ----------------- 1) GET AUTH CODE -----------------

def get_auth_code():
    url = f"{MOCK_BASE_URL}/oauth/authorize/confirm"
    data = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "state": STATE,
        "action": "allow",
    }

    resp = requests.post(url, data=data, allow_redirects=False)
    if resp.status_code != 302:
        raise Exception(f"Unexpected status from /oauth/authorize/confirm: {resp.status_code}, body={resp.text}")

    redirect_url = resp.headers.get("Location")
    print("[+] Redirect URL from authorize:", redirect_url)

    parsed = urlparse(redirect_url)
    qs = parse_qs(parsed.query)
    code_list = qs.get("code")
    if not code_list:
        raise Exception("No 'code' found in redirect Location")

    code = code_list[0]
    print("[+] Got auth code:", code)
    return code

# ----------------- 2) GET ACCESS TOKEN -----------------

def get_access_token(auth_code: str):
    url = f"{MOCK_BASE_URL}/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "client_id": CLIENT_ID,
        "client_secret": "demo-secret",
        "redirect_uri": REDIRECT_URI,
    }

    resp = requests.post(url, data=data)
    if resp.status_code != 200:
        raise Exception(f"Error from /oauth/token: {resp.status_code}, body={resp.text}")

    token_data = resp.json()

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")

    if not access_token:
        raise Exception("No access_token returned by /oauth/token")

    print("[+] Got access token:", access_token)
    print("[+] Got refresh token:", refresh_token)

    return access_token, refresh_token

# ----------------- 3) FETCH DOCUMENT LIST -----------------

def fetch_documents(access_token: str):
    url = f"{MOCK_BASE_URL}/api/user/docs"
    headers = {"Authorization": f"Bearer {access_token}"}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"Error from /api/user/docs: {resp.status_code}, body={resp.text}")

    docs = resp.json()

    print("\n[+] Documents fetched from Mock DigiLocker (for info/log):")
    for idx, d in enumerate(docs, start=1):
        print(f"   {idx}) {d['id']} - {d['name']} ({d['file_name']}) from {d['issuer']}")

    return docs

# ----------------- 4) GUI POPUP TO CHOOSE DOCUMENTS -----------------

def choose_documents_gui(docs):
    """
    Opens a Tkinter popup window showing all docs from server.
    User can multi-select docs. Returns a list of selected doc dicts.
    """
    selected_indices = []

    def on_ok():
        nonlocal selected_indices
        # Get selected indices from listbox
        selected_indices = list(listbox.curselection())
        root.destroy()

    def on_cancel():
        # User cancelled selection
        selected_indices.clear()
        root.destroy()

    root = Tk()
    root.title("Select DigiLocker Documents to Share")

    # Create listbox with scrollbar
    scrollbar = Scrollbar(root)
    scrollbar.pack(side=RIGHT, fill=Y)

    global listbox  # to access inside on_ok
    listbox = Listbox(root, selectmode=MULTIPLE, width=80, height=10, yscrollcommand=scrollbar.set)

    for idx, d in enumerate(docs):
        display_text = f"{idx+1}) {d['id']} - {d['name']} ({d['file_name']}) from {d['issuer']}"
        listbox.insert(END, display_text)

    listbox.pack(side=LEFT, fill=BOTH, expand=True)
    scrollbar.config(command=listbox.yview)

    # OK and Cancel buttons
    btn_ok = Button(root, text="OK", command=on_ok)
    btn_ok.pack()

    btn_cancel = Button(root, text="Cancel", command=on_cancel)
    btn_cancel.pack()

    root.mainloop()

    if not selected_indices:
        print("[-] No documents selected in popup.")
        return []

    selected_docs = [docs[i] for i in selected_indices]
    return selected_docs

# ----------------- MAIN FLOW (SENDER SIDE) -----------------

if __name__ == "__main__":
    print("[*] Sender: Starting DigiLocker authorization...")

    # Step 1: Get Auth Code (user 'allows' access)
    code = get_auth_code()

    # Step 2: Get Access Token
    access_token, refresh_token = get_access_token(code)

    # Step 3: Fetch docs from server
    docs = fetch_documents(access_token)

    if not docs:
        print("[-] No documents found for this user!")
        exit()

    # Step 4: GUI selection of one or more documents
    selected_docs = choose_documents_gui(docs)

    if not selected_docs:
        print("[-] No docs selected. Exiting.")
        exit()

    # Step 5: Create ONE share key for all selected docs
    doc_ids = ",".join([doc["id"] for doc in selected_docs])
    share_key = f"{access_token}::{doc_ids}"

    print("\n====== GENERATED SHARE KEY (ONE KEY FOR ALL SELECTED DOCS) ======")
    print(share_key)
    print("==================================================================")
    print("\nSend this share key to the receiver.")
