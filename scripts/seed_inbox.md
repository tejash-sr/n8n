# Seeding the test inbox with the 10 sample emails

You have two reliable ways to inject the 10 `.eml` files from [`../samples/emails/`](../samples/emails/) into your test Gmail inbox so the Gmail Trigger picks them up.

---

## Option A — Manual forward (no setup, ~5 minutes)

1. Open each `.eml` in any local mail client (Apple Mail, Thunderbird, even `cat sample_01_lead_rfp.eml`).
2. Copy the body, then in Gmail click *Compose* → paste → set `To:` to your test inbox → send from a **different** Google account (otherwise Gmail dedupes against itself and you won't see it in INBOX).
3. Repeat for all 10.

This is the recommended path for the first run because it forces you to read each email and confirm you understand why it should be classified the way it is.

---

## Option B — Python script via Gmail API

Uses the same OAuth client you already created for n8n.

### Prerequisites

```bash
pip install google-auth google-auth-oauthlib google-api-python-client
```

You need a downloaded `credentials.json` from Google Cloud Console (the OAuth 2.0 Client ID). **Never commit this file** — `.gitignore` already excludes it.

### Script

Save this as `scripts/seed_inbox.py` locally (NOT committed because it would tempt people to commit credentials):

```python
"""Inject the 10 .eml files in samples/emails/ into the authenticated Gmail
account's INBOX using the gmail.insert API (so they appear as if received).

WARNING: only run against a test inbox. Never against a real mailbox.
"""
import base64, glob, os, sys
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.insert",
          "https://www.googleapis.com/auth/gmail.labels"]
TOKEN_FILE = "token.json"
CREDS_FILE = "credentials.json"

def auth():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
        Path(TOKEN_FILE).write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)

def main():
    eml_dir = Path(__file__).resolve().parent.parent / "samples" / "emails"
    service = auth()
    for eml in sorted(eml_dir.glob("*.eml")):
        raw = base64.urlsafe_b64encode(eml.read_bytes()).decode()
        result = service.users().messages().insert(
            userId="me",
            internalDateSource="dateHeader",
            body={"raw": raw, "labelIds": ["INBOX", "UNREAD"]},
        ).execute()
        print(f"Injected {eml.name} → id={result['id']}")

if __name__ == "__main__":
    main()
```

### Run

```bash
cd lead-inbox-triage-bot
python3 scripts/seed_inbox.py
```

Within 60 seconds your n8n Executions list should show 10 new runs.

---

## After seeding

1. Open the `test_fixtures` tab in your CRM Sheet.
2. For each row, fill in `message_id` by copying it from the n8n execution input (or from the `leads/support/spam/other` tab that received the row — column A). This binds each sample file to its real Gmail Message-ID.
3. The `actual_category` and `match` formulas auto-fill.
