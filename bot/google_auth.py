"""
Google OAuth for the bot (acts as YOU — sheets land in your own Drive).

One-time setup, then it's automatic:
  1. In Google Cloud Console: enable the Sheets API + Drive API, configure the
     OAuth consent screen (External, add your own email as a Test user), and
     create an OAuth client ID of type "Desktop app".
  2. Download its JSON and save it as  bot/credentials.json
  3. Run:  cd bot && python google_auth.py
     A browser opens → you approve → token.json is cached. Done.

After that the bot refreshes the token automatically; you never re-auth.
"""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Least-privilege: edit spreadsheets + manage only files THIS app creates.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

_BASE = os.path.dirname(__file__)
CREDENTIALS_FILE = os.path.join(_BASE, "credentials.json")   # you download this
TOKEN_FILE = os.path.join(_BASE, "token.json")               # auto-created


def get_credentials():
    """Return valid OAuth credentials, refreshing or running the flow as needed."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not os.path.exists(CREDENTIALS_FILE):
            raise FileNotFoundError(
                f"Missing {CREDENTIALS_FILE}. Download an OAuth 'Desktop app' "
                "client JSON from Google Cloud Console and save it there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(port=0)
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    return creds


def is_ready() -> bool:
    """True if we already have a usable token (used to gate the Sheets feature)."""
    if not os.path.exists(TOKEN_FILE):
        return False
    try:
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        return bool(creds and (creds.valid or creds.refresh_token))
    except Exception:
        return False


if __name__ == "__main__":
    get_credentials()
    print("✅ token.json created — Google Sheets auth is ready.")
