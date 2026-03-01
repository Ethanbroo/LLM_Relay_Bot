"""One-time Google OAuth authorization script.

Run this once to authorize access to Google Analytics and Search Console.
It opens a browser for consent, then saves the authorized credentials
(including refresh_token) for unattended use by the blog bot.

Usage:
    poetry run python credentials/authorize_google.py

After running, the bot's analytics sync will work automatically.
"""

import json
import sys
from pathlib import Path

# Scopes needed by analytics_sync.py
SCOPES = [
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]

CLIENT_CONFIG_PATH = Path(__file__).parent / "google_oauth_client.json"
TOKEN_PATH = Path(__file__).parent / "google_authorized_token.json"


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("ERROR: google-auth-oauthlib not installed.")
        print("  Run: poetry install")
        sys.exit(1)

    if not CLIENT_CONFIG_PATH.exists():
        print(f"ERROR: OAuth client config not found at {CLIENT_CONFIG_PATH}")
        print("  Download it from Google Cloud Console → Credentials → OAuth 2.0 Client IDs")
        sys.exit(1)

    if TOKEN_PATH.exists():
        print(f"Token file already exists at {TOKEN_PATH}")
        resp = input("Overwrite? (y/N): ").strip().lower()
        if resp != "y":
            print("Aborted.")
            return

    print("Opening browser for Google OAuth consent...")
    print(f"  Scopes: {', '.join(SCOPES)}")
    print()

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_CONFIG_PATH),
        scopes=SCOPES,
    )
    creds = flow.run_local_server(port=0)

    # Save as authorized user info (the format analytics_sync.py expects)
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or SCOPES),
    }

    TOKEN_PATH.write_text(json.dumps(token_data, indent=2))
    print(f"\nAuthorized credentials saved to: {TOKEN_PATH}")
    print("The blog bot's analytics sync will now use GA4 data automatically.")


if __name__ == "__main__":
    main()
