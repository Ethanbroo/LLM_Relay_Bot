"""
Real Google Docs connector for multi_agent_v2.

Uses OAuth 2.0 installed-app flow — the same flow described in Google's
"Configure OAuth consent" guide. No service account needed. You authorize
the app once in your browser; the token is cached locally after that.

Setup (one time):
  1. Google Cloud Console → APIs & Services → Enable:
       - Google Docs API
       - Google Drive API
  2. Google Auth Platform → Branding → configure consent screen
       - App name: LLM Relay Bot (or anything)
       - User type: External  (or Internal if you have Workspace)
       - Add your email as a test user (under Audience)
       - Data Access → Add scopes:
           https://www.googleapis.com/auth/documents
           https://www.googleapis.com/auth/drive.file
  3. APIs & Services → Credentials → Create Credentials → OAuth client ID
       - Application type: Desktop app
       - Download the JSON file (called something like client_secret_....json)
  4. Set in your .env:
       GOOGLE_OAUTH_CLIENT_JSON=/path/to/client_secret_....json
       GOOGLE_DOCS_FOLDER_ID=<optional Drive folder ID>

First run: a browser window opens for you to approve access.
After that: token is saved to google_token.json in the project root.
Subsequent runs: fully automatic, no browser needed.
"""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from typing import Optional

# Token cache location — project root, gitignored via .gitignore entry
_TOKEN_PATH = os.path.join(os.path.dirname(__file__), "..", "google_token.json")
_TOKEN_PATH = os.path.normpath(_TOKEN_PATH)

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


@dataclass
class DocResult:
    success: bool
    doc_id: Optional[str] = None
    doc_url: Optional[str] = None
    error: Optional[str] = None

    def __str__(self) -> str:
        if self.success:
            return f"Created: {self.doc_url}"
        return f"Failed: {self.error}"


class GoogleDocsRealConnector:
    """
    Real Google Docs connector using OAuth 2.0 installed-app flow.

    First call opens a browser for one-time authorization.
    Token is cached in google_token.json afterward.
    Falls back gracefully if credentials are not configured.
    """

    def __init__(
        self,
        oauth_client_json: Optional[str] = None,
        folder_id: Optional[str] = None,
        token_path: Optional[str] = None,
    ) -> None:
        """
        Args:
            oauth_client_json: Path to the OAuth Desktop client JSON downloaded
                               from Google Cloud Console. Falls back to the
                               GOOGLE_OAUTH_CLIENT_JSON environment variable.
            folder_id:         Google Drive folder ID to place new documents in.
                               Falls back to GOOGLE_DOCS_FOLDER_ID env var.
            token_path:        Where to cache the OAuth token. Defaults to
                               google_token.json in the project root.
        """
        self._client_json = oauth_client_json or os.environ.get(
            "GOOGLE_OAUTH_CLIENT_JSON", ""
        )
        self._folder_id = folder_id or os.environ.get("GOOGLE_DOCS_FOLDER_ID", "")
        self._token_path = token_path or _TOKEN_PATH

        self._docs_service = None
        self._drive_service = None
        self._ready = False
        self._setup_error: Optional[str] = None

        self._try_connect()

    def _try_connect(self) -> None:
        if not self._client_json:
            self._setup_error = (
                "GOOGLE_OAUTH_CLIENT_JSON not set. "
                "Google Docs upload disabled — summary saved locally only."
            )
            return

        if not os.path.exists(self._client_json):
            self._setup_error = (
                f"OAuth client JSON not found: {self._client_json}. "
                "Google Docs upload disabled."
            )
            return

        try:
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = None

            # Load cached token if it exists
            if os.path.exists(self._token_path):
                creds = Credentials.from_authorized_user_file(
                    self._token_path, SCOPES
                )

            # Refresh or run the OAuth flow
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    # Opens browser for one-time authorization
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self._client_json, SCOPES
                    )
                    creds = flow.run_local_server(port=0)

                # Cache the token for future runs
                with open(self._token_path, "w") as token_file:
                    token_file.write(creds.to_json())

            self._docs_service = build("docs", "v1", credentials=creds)
            self._drive_service = build("drive", "v3", credentials=creds)
            self._ready = True

        except ImportError as e:
            self._setup_error = (
                f"Missing Google SDK package: {e}. "
                "Run: pip install google-api-python-client google-auth-oauthlib"
            )
        except Exception as e:
            self._setup_error = f"Google OAuth connection failed: {e}"

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def setup_error(self) -> Optional[str]:
        return self._setup_error

    def create_document(self, title: str, content: str) -> DocResult:
        """
        Create a new Google Doc with the given title and content.

        Args:
            title:   Document title shown in Google Drive.
            content: Document body (Markdown preserved as plain text).

        Returns:
            DocResult with doc_id and doc_url on success.
        """
        if not self._ready:
            return DocResult(
                success=False,
                error=self._setup_error or "Google Docs connector not initialized.",
            )

        try:
            # 1. Create empty document
            doc = self._docs_service.documents().create(
                body={"title": title}
            ).execute()
            doc_id = doc["documentId"]

            # 2. Insert content
            self._docs_service.documents().batchUpdate(
                documentId=doc_id,
                body={"requests": [{"insertText": {"location": {"index": 1}, "text": content}}]},
            ).execute()

            # 3. Move to folder if configured
            if self._folder_id:
                file_meta = self._drive_service.files().get(
                    fileId=doc_id, fields="parents"
                ).execute()
                previous_parents = ",".join(file_meta.get("parents", []))
                self._drive_service.files().update(
                    fileId=doc_id,
                    addParents=self._folder_id,
                    removeParents=previous_parents,
                    fields="id, parents",
                ).execute()

            doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
            return DocResult(success=True, doc_id=doc_id, doc_url=doc_url)

        except Exception as e:
            return DocResult(success=False, error=str(e))

    def share_document(self, doc_id: str, email: str, role: str = "reader") -> DocResult:
        """
        Share a document with a specific email address.

        Args:
            doc_id: The Google Doc ID to share.
            email:  Email address to share with.
            role:   "reader", "commenter", or "writer".
        """
        if not self._ready:
            return DocResult(
                success=False,
                error=self._setup_error or "Google Docs connector not initialized.",
            )

        try:
            self._drive_service.permissions().create(
                fileId=doc_id,
                body={"type": "user", "role": role, "emailAddress": email},
                sendNotificationEmail=False,
            ).execute()
            doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
            return DocResult(success=True, doc_id=doc_id, doc_url=doc_url)

        except Exception as e:
            return DocResult(success=False, error=str(e))
