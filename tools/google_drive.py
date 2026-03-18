"""Upload DOCX to Google Drive as a native Google Doc.

Uses the same OAuth token as google_sheets.py (saved at ~/.config/contractsafe/google_token.json).
Uploads to a folder called "Claude Code articles" in Drive root, creating it if needed.
"""

from __future__ import annotations

from pathlib import Path

from tools.google_sheets import TOKEN_PATH, SCOPES


def _get_drive_service():
    """Build a Google Drive API service using saved OAuth credentials."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            "No Google OAuth token found. Run the pipeline with --sheet-url first "
            "to complete the OAuth flow, or run: python3 -c 'from tools.google_sheets import _get_credentials; _get_credentials()'"
        )

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def _get_or_create_folder(drive, folder_name: str) -> str:
    """Find or create a folder in Drive root. Returns folder ID."""
    # Search for existing folder
    results = drive.files().list(
        q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' "
          f"and 'root' in parents and trashed=false",
        fields="files(id)",
        pageSize=1,
    ).execute()

    files = results.get("files", [])
    if files:
        return files[0]["id"]

    # Create folder
    metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    folder = drive.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def upload_docx_to_drive(docx_path: str, title: str) -> str:
    """Upload a DOCX file to Google Drive as a Google Doc.

    - Uploads to "Claude Code articles" folder (created if missing)
    - Appends "CLAUDE CODE" to the filename
    - Converts DOCX to native Google Doc format
    - Returns the Google Doc URL
    """
    from googleapiclient.http import MediaFileUpload

    path = Path(docx_path)
    if not path.exists():
        raise FileNotFoundError(f"DOCX not found: {docx_path}")

    drive = _get_drive_service()

    # Get or create target folder
    folder_id = _get_or_create_folder(drive, "Claude Code articles")

    # Build filename: "Title CLAUDE CODE"
    doc_name = f"{title} CLAUDE CODE"

    file_metadata = {
        "name": doc_name,
        "parents": [folder_id],
        "mimeType": "application/vnd.google-apps.document",  # convert to Google Doc
    }
    media = MediaFileUpload(
        str(path),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    created = drive.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, webViewLink",
    ).execute()

    return created.get("webViewLink", f"https://docs.google.com/document/d/{created['id']}/edit")
