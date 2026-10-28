import os
import json
from tkinter import Tk
from tkinter.filedialog import askopenfilename
from gmail_auth import ensure_authenticated
ensure_authenticated()


# Importing the logic from your existing scripts
from sae_client import (
    encrypt_data,
    digilocker_choose_docs_and_create_token
)
from emailsend import send_email

def send_secure_message():
    print("\n=== QuMail Secure Message Sender ===\n")

    recipient = input("Enter recipient email: ").strip()
    message = input("Enter the message to encrypt and send: ").strip()

    print("\nChoose encryption level:")
    print("1 = OTP (One-Time Pad)")
    print("2 = QKD-seeded AES")
    print("3 = PQC-Hybrid AES")
    print("4 = Local AES")
    
    try:
        level = int(input("Enter level (1-4): ").strip())
        print("\nEncrypting message...")
        rec_hash = None
        if level in (3, 4):
            print(f" Searching registry for {recipient}...")
            from sae_client import auto_fetch_receiver_hash # Ensure this is imported
            rec_hash = auto_fetch_receiver_hash(recipient, level)
            if rec_hash:
                print(f" Found Public Hash automatically!")
            else:
                print(f" No key found for {recipient} in registry.")
                rec_hash = input("Please paste the Receiver's Public Hash manually: ").strip()
                # --- Update your existing encrypt_data call to include the hash ---
        payload = encrypt_data(
            message.encode(), 
            level, 
            "text", 
            recipient_email=recipient # <--- Pass the fetched hash
            )
        # content_type="text" handles raw strings

        print("Sending encrypted email...")
        result = send_email(payload, recipient)
        
        if result and "id" in result:
            print(f"\n Secure message sent! Message ID: {result['id']}")
    except Exception as e:
        print(f"\n Error: {e}")

def send_secure_file():
    print("\n=== QuMail Secure File Sender ===\n")

    # Hide the Tkinter main window for the file picker
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True) # Ensure dialog stays on top

    filepath = askopenfilename(title="Select a file to encrypt")
    root.destroy()

    if not filepath:
        print(" No file selected!")
        return

    recipient = input("Enter recipient email: ").strip()

    print("\nChoose encryption level:")
    print("2 = QKD-seeded AES")
    print("3 = PQC-Hybrid AES")
    print("4 = Local AES")
    
    try:
        level = int(input("Enter level (2-4): ").strip())
        rec_hash = None
        if level in (3, 4):
            from sae_client import auto_fetch_receiver_hash
            rec_hash = auto_fetch_receiver_hash(recipient, level)
        
        with open(filepath, "rb") as f:
            data = f.read()

        filename = os.path.basename(filepath)

        print(f"\nEncrypting file: {filename}...")
        payload = encrypt_data(data, level, "file", recipient_email=recipient,original_filename=filename)
        print("Sending encrypted email...")
        result = send_email(payload, recipient)
        
        if result and "id" in result:
            print("\n Secure file sent successfully!")
        else:
            print("\n Sending failed.")
    except Exception as e:
        print(f"\n Error during file encryption/send: {e}")

def send_secure_digilocker_docs():
    print("\n=== QuMail Secure DigiLocker Sender ===\n")

    recipient = input("Enter recipient email: ").strip()

    print("\nChoose encryption level (DigiLocker uses Level 2-4):")
    print("2 = QKD-seeded AES")
    print("3 = PQC-Hybrid AES")
    print("4 = Local AES")
    
    try:
        level = int(input("Enter level (2-4): ").strip())
        rec_hash = None
        if level in (3, 4):
            from sae_client import auto_fetch_receiver_hash
            rec_hash = auto_fetch_receiver_hash(recipient, level)

        print("\nFetching documents and generating secure access token...")
        # This matches the function in your sae_client.py
        digilocker_token = digilocker_choose_docs_and_create_token()

        # Wrap token in a message-style payload
        combined_data = {"digilocker_token": digilocker_token}
        
        # Optional: Add a note
        note = input("Add an optional note for the recipient? (Enter to skip): ")
        if note:
            combined_data["message"] = note

        combined_bytes = json.dumps(combined_data).encode("utf-8")

        print("\nEncrypting DigiLocker access payload...")
        payload = encrypt_data(combined_bytes, level, "text", recipient_email=recipient)

        print("Sending encrypted email...")
        result = send_email(payload, recipient)

        if result and "id" in result:
            print("\n DigiLocker document access sent securely!")
        else:
            print("\n Sending failed.")
    except Exception as e:
        print(f"\n DigiLocker flow error: {e}")

if __name__ == "__main__":
    print("########################################")
    print("       QUMAIL SENDER INTERFACE")
    print("########################################")
    print("Select mode:")
    print("1 = Send secure text message")
    print("2 = Send secure file/document")
    print("3 = Send secure DigiLocker document(s)")

    choice = input("\nEnter choice (1/2/3): ").strip()

    if choice == "1":
        send_secure_message()
    elif choice == "2":
        send_secure_file()
    elif choice == "3":
        send_secure_digilocker_docs()
    else:
        print("Invalid choice.")