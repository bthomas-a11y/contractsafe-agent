"""Web fetch tool for retrieving and parsing web page content."""

import httpx
from bs4 import BeautifulSoup
from config import WEB_FETCH_TIMEOUT, WEB_FETCH_MAX_CONTENT_LENGTH


def web_fetch(url: str) -> dict:
    """
    Fetch a web page and extract readable text content.

    Returns {url, status, content, error}.
    """
    try:
        resp = httpx.get(
            url,
            timeout=WEB_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text/html" in content_type:
            text = _extract_text_from_html(resp.text)
        else:
            text = resp.text

        # Truncate if too long
        if len(text) > WEB_FETCH_MAX_CONTENT_LENGTH:
            text = text[:WEB_FETCH_MAX_CONTENT_LENGTH] + "\n\n[Content truncated]"

        return {
            "url": str(resp.url),
            "status": resp.status_code,
            "content": text,
            "error": None,
        }
    except httpx.TimeoutException:
        return {"url": url, "status": 0, "content": "", "error": "Request timed out"}
    except httpx.HTTPStatusError as e:
        return {"url": url, "status": e.response.status_code, "content": "", "error": f"HTTP {e.response.status_code}"}
    except Exception as e:
        return {"url": url, "status": 0, "content": "", "error": str(e)}


def _extract_text_from_html(html: str) -> str:
    """Extract readable text from HTML, removing scripts, styles, nav, etc."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content elements
    for tag in soup.find_all(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()

    # Try to find main content area
    main = soup.find("main") or soup.find("article") or soup.find(role="main")
    if main:
        target = main
    else:
        target = soup.body or soup

    # Extract text with some structure preserved
    lines = []
    for element in target.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "blockquote"]):
        text = element.get_text(strip=True)
        if not text:
            continue

        tag_name = element.name
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_name[1])
            lines.append(f"\n{'#' * level} {text}\n")
        elif tag_name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)

    result = "\n".join(lines)

    # Fallback: if structured extraction got very little, use get_text
    if len(result.strip()) < 200:
        result = target.get_text(separator="\n", strip=True)

    return result
