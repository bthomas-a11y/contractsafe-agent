"""Convert pipeline markdown to HubSpot-compatible HTML."""

from __future__ import annotations

import markdown


def markdown_to_html(md_text: str) -> str:
    """Convert markdown article to HTML suitable for HubSpot postBody.

    Strips the H1 line (HubSpot uses the 'name' field for the title).
    Converts headings, bold/italic, links, lists, tables, and code blocks.
    """
    lines = md_text.split("\n")
    filtered = []
    for line in lines:
        # Skip H1 — HubSpot uses 'name' field for the title
        if line.strip().startswith("# ") and not line.strip().startswith("## "):
            continue
        filtered.append(line)
    body = "\n".join(filtered).strip()

    return markdown.markdown(body, extensions=["tables", "fenced_code"])
