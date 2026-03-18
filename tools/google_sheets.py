"""Upload article content to a Google Sheet.

Uses OAuth (browser-based login) so the tool accesses Sheets as YOU —
no need to share sheets with a service account email.

First run: opens a browser for Google login. Saves a token to
~/.config/contractsafe/google_token.json for subsequent runs.

Requires:
- A Google Cloud OAuth client ID (GOOGLE_OAUTH_CLIENT_ID in .env or env var)
  pointing to a desktop app OAuth client JSON file.
  Create one at: https://console.cloud.google.com/apis/credentials
  (Application type: Desktop app, download the JSON)
- pip install gspread google-auth google-auth-oauthlib

Usage from pipeline:
    from tools.google_sheets import upload_to_sheet
    upload_to_sheet(sheet_url, article_md, meta_description, linkedin_post, twitter_post, topic)
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
TOKEN_DIR = Path.home() / ".config" / "contractsafe"
TOKEN_PATH = TOKEN_DIR / "google_token.json"


def _get_oauth_client_path() -> str:
    """Find the OAuth client secrets JSON file."""
    key_path = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    if not key_path:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith("GOOGLE_OAUTH_CLIENT_ID="):
                    key_path = line.split("=", 1)[1].strip()
                    break

    if not key_path or not Path(key_path).exists():
        raise FileNotFoundError(
            f"OAuth client secrets not found at '{key_path}'. "
            "Set GOOGLE_OAUTH_CLIENT_ID in .env to the path of your OAuth client JSON file.\n"
            "Create one at: https://console.cloud.google.com/apis/credentials\n"
            "  → Create Credentials → OAuth client ID → Desktop app → Download JSON"
        )
    return key_path


def _get_credentials():
    """Get OAuth credentials, prompting for browser login if needed."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    import gspread

    creds = None

    # Load saved token
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    # Refresh or get new token
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            client_path = _get_oauth_client_path()
            flow = InstalledAppFlow.from_client_secrets_file(client_path, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save token for next time
        TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json())

    return gspread.authorize(creds)


def upload_to_sheet(
    sheet_url: str,
    article_md: str,
    meta_description: str = "",
    linkedin_post: str = "",
    twitter_post: str = "",
    topic: str = "",
) -> str:
    """Upload article content to a Google Sheet.

    Creates/updates a worksheet named after the topic slug.
    Layout:
    - Row 1: Headers
    - Row 2+: Article content split into sections (one H2 section per row)
    - Separate columns for: Section Heading, Section Content, Word Count
    - Additional rows at bottom for meta description, social copy

    Returns the sheet URL on success.
    """
    gc = _get_credentials()

    # Open the sheet
    sheet = gc.open_by_url(sheet_url)

    # Create worksheet name from topic
    ws_name = re.sub(r'[^\w\s-]', '', topic)[:30].strip() or "Article"

    # Delete existing worksheet with same name if it exists
    try:
        existing = sheet.worksheet(ws_name)
        sheet.del_worksheet(existing)
    except Exception:
        pass

    # Create new worksheet
    ws = sheet.add_worksheet(title=ws_name, rows=100, cols=5)

    # Build rows
    rows = []
    rows.append(["Section", "Content", "Word Count", "Type", "Notes"])

    # Split article by H2 headings
    sections = re.split(r'^(## .+)$', article_md, flags=re.MULTILINE)

    # First chunk is intro (before any H2)
    intro = sections[0].strip()
    if intro:
        # Extract H1 if present
        h1_match = re.match(r'^# (.+)$', intro, re.MULTILINE)
        heading = h1_match.group(1) if h1_match else "Introduction"
        content = re.sub(r'^# .+\n*', '', intro, count=1).strip()
        wc = len(content.split())
        rows.append([heading, content, str(wc), "intro", ""])

    # Remaining sections come in pairs: heading, content
    for i in range(1, len(sections), 2):
        heading = sections[i].replace("## ", "").strip()
        content = sections[i + 1].strip() if i + 1 < len(sections) else ""
        wc = len(content.split())
        rows.append([heading, content, str(wc), "section", ""])

    # Add meta and social copy
    rows.append(["", "", "", "", ""])  # blank separator
    if meta_description:
        rows.append(["Meta Description", meta_description, str(len(meta_description)), "meta", f"{len(meta_description)} chars"])
    if linkedin_post:
        rows.append(["LinkedIn Post", linkedin_post, str(len(linkedin_post.split())), "social", ""])
    if twitter_post:
        rows.append(["X/Twitter Post", twitter_post, str(len(twitter_post.split())), "social", ""])

    # Write all rows at once
    ws.update(rows, "A1")

    # Format header row bold
    ws.format("A1:E1", {"textFormat": {"bold": True}})

    return sheet_url


def test_connection(sheet_url: str) -> bool:
    """Test that we can connect to and write to the given Google Sheet.

    Returns True on success, raises on failure with a clear error message.
    """
    gc = _get_credentials()
    sheet = gc.open_by_url(sheet_url)
    _ = sheet.title
    return True
