import os
import base64
import json
import re
from googleapiclient.discovery import build
from gmail_auth import load_credentials, get_active_email

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.getenv("QMAIL_DATA_DIR", BACKEND_DIR))
os.makedirs(DATA_DIR, exist_ok=True)
SEEN_FILE = os.path.join(DATA_DIR, "seen_messages.json")

QUMAIL_SUBJECT = "QuMail Secure Message"


def _gmail_service(email: str = ""):
    creds = load_credentials(email or None)
    if not creds:
        raise RuntimeError(
            "No authenticated Google account. Configure Google OAuth or use Guest Demo."
        )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# -----------------------------------
# SEEN MESSAGE REGISTRY
# Tracks Gmail message IDs already processed. On every refresh we only
# fetch messages NOT in this set — so the loop stays O(new messages).
# Structure: { "email@gmail.com": ["id1", "id2", ...] }
# -----------------------------------

def _load_seen() -> dict:
    if not os.path.exists(SEEN_FILE):
        return {}
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_seen(store: dict):
    with open(SEEN_FILE, "w") as f:
        json.dump(store, f, indent=2)

def get_seen_ids(email: str) -> set:
    return set(_load_seen().get(email.strip().lower(), []))

def mark_seen(email: str, msg_id: str):
    """Mark a Gmail message ID as processed so it is never fetched again."""
    email = email.strip().lower()
    store = _load_seen()
    ids = store.get(email, [])
    if msg_id not in ids:
        ids.append(msg_id)
    store[email] = ids
    _save_seen(store)


# -----------------------------------
# FETCH ALL QU-MAIL ENCRYPTED EMAILS
# -----------------------------------

def fetch_qumail_emails(active_email: str = ""):
    service = _gmail_service(active_email)

    query = 'label:INBOX -from:me -label:SENT (subject:"QuMail Secure Encrypted Message" OR subject:"QuMail Shared File" OR filename:qumail_encrypted.json)'

    results = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=50
    ).execute()

    messages = results.get("messages", [])  # newest first

    if not active_email:
        active_email = get_active_email() or ""

    # Filter out already-seen/processed messages
    seen = get_seen_ids(active_email)
    unseen = [m for m in messages if m["id"] not in seen]

    if not unseen:
        print(f"DEBUG: All {len(messages)} messages already processed. Nothing new.")
        return []

    print(f"DEBUG: {len(messages) - len(unseen)} already-processed, {len(unseen)} unseen. Fetching newest 1.")
    return [unseen[0]]  # only the single newest unseen message


def archive_gmail_message(msg_id: str):
    """
    Remove INBOX label from a Gmail message (archive it) so it won't
    appear in inbox searches. This is the Gmail equivalent of 'delete from inbox'.
    """
    try:
        creds = load_credentials()
        service = build("gmail", "v1", credentials=creds)
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["INBOX"]}
        ).execute()
        print(f" Gmail message {msg_id} archived (removed from INBOX).")
        return True
    except Exception as e:
        print(f" Could not archive Gmail message {msg_id}: {e}")
        return False


def trash_gmail_message(msg_id: str):
    """
    Move a Gmail message to Gmail Trash permanently.
    Used when the user empties trash or permanently deletes.
    """
    try:
        creds = load_credentials()
        service = build("gmail", "v1", credentials=creds)
        service.users().messages().trash(userId="me", id=msg_id).execute()
        print(f" Gmail message {msg_id} moved to Gmail Trash.")
        return True
    except Exception as e:
        print(f" Could not trash Gmail message {msg_id}: {e}")
        return False


def fetch_payload_from_email(msg_id, email: str = ""):
    service = _gmail_service(email)

    try:
        msg = service.users().messages().get(
            userId="me",
            id=msg_id,
            format="full"
        ).execute()

        headers = msg.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), "")
        sender  = next((h['value'] for h in headers if h['name'].lower() == 'from'), "Unknown Sender")

        # Collect ALL attachments from the message
        def collect_attachments(payload_part, results=None):
            if results is None:
                results = {}
            filename = payload_part.get('filename')
            body = payload_part.get('body', {})
            att_id = body.get('attachmentId')
            if filename and att_id:
                att = service.users().messages().attachments().get(
                    userId='me', messageId=msg_id, id=att_id
                ).execute()
                results[filename] = base64.urlsafe_b64decode(att['data'])
            for part in payload_part.get('parts', []):
                collect_attachments(part, results)
            return results

        all_attachments = collect_attachments(msg.get('payload', {}))

        if not all_attachments:
            print("DEBUG ERROR: No attachments found.")
            return None

        # Get the main metadata JSON
        main_data = all_attachments.get('qumail_encrypted.json')
        if not main_data:
            # Fallback: use any attachment
            main_data = next(iter(all_attachments.values()))

        payload = json.loads(main_data.decode('utf-8', errors='ignore'))

        # If it's a multi_file bundle, reassemble file payloads from separate attachments
        if payload.get("type") == "multi_file":
            file_payloads = []
            i = 0
            while True:
                fp_data = all_attachments.get(f"qumail_file_{i}.json")
                if fp_data is None:
                    break
                fp = json.loads(fp_data.decode('utf-8', errors='ignore'))
                file_payloads.append(fp)
                i += 1
            if file_payloads:
                payload["files"] = file_payloads
            print(f"DEBUG: Bundle reassembled: {len(file_payloads)} files, plain_text='{payload.get('plain_text','')[:30]}'")

        payload["sender"] = sender
        if not payload.get("subject"):
            payload["subject"] = subject

        # Mark as read
        try:
            service.users().messages().modify(
                userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()
        except Exception:
            pass

        return payload

    except Exception as e:
        print(f"\nFATAL API/Extraction ERROR: {e}")
        return None
