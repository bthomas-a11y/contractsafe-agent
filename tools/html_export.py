"""Convert pipeline markdown to HubSpot-compatible HTML.

Matches the formatting conventions used in existing ContractSafe blog posts:
- <p> wrappers for all prose
- <li aria-level="1"><p>text</p></li> for list items
- rel="noopener" target="_blank" on links
- <!--more--> after the first paragraph
- TL;DR styled as a boxed section between <hr> tags
"""

from __future__ import annotations

import re


def markdown_to_html(md_text: str) -> str:
    """Convert markdown article to HubSpot-ready HTML.

    Strips the H1 (HubSpot uses the 'name' field for the title) and applies
    HubSpot blog formatting conventions.
    """
    lines = md_text.split("\n")
    html_parts: list[str] = []
    i = 0
    first_para_done = False
    in_list = False  # "ul" or "ol" or False
    in_table = False
    table_rows: list[str] = []

    while i < len(lines):
        line = lines[i].strip()

        # Skip H1
        if line.startswith("# ") and not line.startswith("## "):
            i += 1
            continue

        # Blank line — close open list, skip
        if not line:
            if in_list:
                html_parts.append(f"</{in_list}>")
                in_list = False
            if in_table:
                html_parts.append(_build_table(table_rows))
                table_rows = []
                in_table = False
            i += 1
            continue

        # Table rows
        if line.startswith("|"):
            # Skip separator rows like |---|---|
            if re.match(r'^\|[\s\-:|]+\|$', line):
                i += 1
                continue
            table_rows.append(line)
            in_table = True
            i += 1
            continue
        elif in_table:
            html_parts.append(_build_table(table_rows))
            table_rows = []
            in_table = False
            # Don't increment — process current line below

        # Headings
        heading_match = re.match(r'^(#{2,6})\s+(.+)$', line)
        if heading_match:
            if in_list:
                html_parts.append(f"</{in_list}>")
                in_list = False
            level = len(heading_match.group(1))
            text = _inline(heading_match.group(2))
            html_parts.append(f"<h{level}>{text}</h{level}>")
            i += 1
            continue

        # Horizontal rules
        if re.match(r'^---+$', line):
            html_parts.append("<hr>")
            i += 1
            continue

        # Bullet list items
        bullet_match = re.match(r'^[-*]\s+(.+)$', line)
        if bullet_match:
            if in_list != "ul":
                if in_list:
                    html_parts.append(f"</{in_list}>")
                html_parts.append("<ul>")
                in_list = "ul"
            text = _inline(bullet_match.group(1))
            html_parts.append(f'<li aria-level="1"><p>{text}</p></li>')
            i += 1
            continue

        # Numbered list items
        num_match = re.match(r'^(\d+)[.)]\s+(.+)$', line)
        if num_match:
            if in_list != "ol":
                if in_list:
                    html_parts.append(f"</{in_list}>")
                html_parts.append("<ol>")
                in_list = "ol"
            text = _inline(num_match.group(2))
            html_parts.append(f'<li aria-level="1"><p>{text}</p></li>')
            i += 1
            continue

        # Blockquotes (TL;DR sections)
        if line.startswith(">"):
            if in_list:
                html_parts.append(f"</{in_list}>")
                in_list = False
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote_lines.append(lines[i].strip().lstrip("> "))
                i += 1
            quote_html = " ".join(_inline(q) for q in quote_lines)
            html_parts.append(f"<blockquote><p>{quote_html}</p></blockquote>")
            continue

        # Regular paragraph
        if in_list:
            html_parts.append(f"</{in_list}>")
            in_list = False

        # Check for TL;DR paragraph
        if line.startswith("**TL;DR") or line.startswith("TL;DR"):
            tl_dr_text = _inline(re.sub(r'^\*?\*?TL;DR\*?\*?:?\s*', '', line))
            html_parts.append('<div><hr><span style="font-size: 20px;"><strong>TL;DR</strong></span></div>')
            if tl_dr_text:
                html_parts.append(f"<p><strong>{tl_dr_text}</strong></p>")
                html_parts.append("<hr>")
            if not first_para_done:
                first_para_done = True
            i += 1
            continue

        text = _inline(line)
        html_parts.append(f"<p>{text}</p>")

        # Insert <!--more--> after first paragraph
        if not first_para_done:
            html_parts.append("<!--more-->")
            first_para_done = True

        i += 1

    # Close any open structures
    if in_list:
        html_parts.append(f"</{in_list}>")
    if in_table:
        html_parts.append(_build_table(table_rows))

    return "\n".join(html_parts)


def _inline(text: str) -> str:
    """Convert inline markdown (bold, italic, links) to HTML."""
    # Links: [text](url) → <a href="url" rel="noopener" target="_blank">text</a>
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href="\2" rel="noopener" target="_blank">\1</a>',
        text,
    )
    # Bold+italic: ***text*** or ___text___
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    # Bold: **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic: *text* or _text_
    text = re.sub(r'(?<!\w)\*(.+?)\*(?!\w)', r'<em>\1</em>', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<em>\1</em>', text)
    return text


def _build_table(rows: list[str]) -> str:
    """Convert markdown table rows to an HTML table."""
    if not rows:
        return ""
    html = '<table style="border-collapse: collapse; width: 100%;">\n'

    # First row is header
    header_cells = [c.strip() for c in rows[0].strip("|").split("|")]
    html += "<thead><tr>"
    for cell in header_cells:
        html += f'<th style="border: 1px solid #ddd; padding: 8px; text-align: left;">{_inline(cell)}</th>'
    html += "</tr></thead>\n<tbody>\n"

    # Remaining rows
    for row in rows[1:]:
        cells = [c.strip() for c in row.strip("|").split("|")]
        html += "<tr>"
        for cell in cells:
            html += f'<td style="border: 1px solid #ddd; padding: 8px;">{_inline(cell)}</td>'
        html += "</tr>\n"

    html += "</tbody></table>"
    return html
