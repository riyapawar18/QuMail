# receiver_request.py

import requests
from tkinter import Tk
from tkinter.filedialog import asksaveasfilename

MOCK_BASE_URL = "http://127.0.0.1:8444"

def download_documents_with_key(share_key: str):
    """
    share_key format: <access_token>::<doc_id1,doc_id2,...>
    Example:
        u18meS-BP...::doc_001,doc_003
    """
    try:
        access_token, doc_ids_part = share_key.split("::", 1)
    except ValueError:
        raise Exception("Invalid share key format. Expected: <access_token>::<doc_id or doc_id1,doc_id2,...>")

    # Split by comma to get individual document IDs
    doc_ids = [d.strip() for d in doc_ids_part.split(",") if d.strip()]

    if not doc_ids:
        raise Exception("No document IDs found in share key.")

    # Prepare GUI for Save As dialogs
    root = Tk()
    root.withdraw()  # Hide main Tk window

    for doc_id in doc_ids:
        print(f"[*] Trying to download document: {doc_id}")

        url = f"{MOCK_BASE_URL}/api/user/docs/{doc_id}"
        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            print(f"[-] Error downloading {doc_id}: {resp.status_code}, body={resp.text}")
            continue  # Skip this doc, go to next

        # Ask user where to save this specific document
        default_filename = f"{doc_id}.pdf"
        file_path = asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=default_filename,
            title=f"Save DigiLocker Document: {doc_id}"
        )

        if not file_path:
            print(f"[-] Save cancelled for {doc_id}.")
            continue

        with open(file_path, "wb") as f:
            f.write(resp.content)

        print(f"[+] Receiver: Document {doc_id} saved at:\n    {file_path}")

    # Destroy hidden root window when done
    root.destroy()

if __name__ == "__main__":
    print("[*] Receiver: ready to download using share key.")
    share_key = input("Paste the share key you got from the sender:\n> ").strip()
    download_documents_with_key(share_key)
