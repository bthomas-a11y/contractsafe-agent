"""HubSpot CMS Blog Posts API wrapper.

Used by the pipeline for automated draft creation and by the MCP server.
Auth: HUBSPOT_ACCESS_TOKEN env var (Private App token with 'content' scope).
API docs: https://developers.hubspot.com/docs/api-reference/cms-posts-v3/guide
"""

from __future__ import annotations

import os

import httpx

HUBSPOT_BASE = "https://api.hubapi.com"


def _get_token() -> str:
    token = os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
    if not token:
        raise ValueError(
            "HUBSPOT_ACCESS_TOKEN not set. Create a HubSpot Private App with "
            "'content' scope at Settings > Integrations > Private Apps."
        )
    return token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
    }


def get_content_group_id(explicit_id: str = "") -> str:
    """Return the contentGroupId for the blog.

    Uses the explicit ID from config if set, otherwise auto-detects
    by listing existing blog posts.
    """
    if explicit_id:
        return explicit_id

    from_env = os.environ.get("HUBSPOT_CONTENT_GROUP_ID", "")
    if from_env:
        return from_env

    resp = httpx.get(
        f"{HUBSPOT_BASE}/cms/v3/blogs/posts",
        headers=_headers(),
        params={"limit": 1},
        timeout=15,
    )
    resp.raise_for_status()
    posts = resp.json().get("results", [])
    if not posts:
        raise ValueError(
            "No existing blog posts found — cannot auto-detect contentGroupId. "
            "Set HUBSPOT_CONTENT_GROUP_ID in .env manually."
        )
    return posts[0]["contentGroupId"]


def list_blogs(limit: int = 10) -> list[dict]:
    """List existing blog posts to discover contentGroupId values.

    Returns list of {contentGroupId, sample_title} dicts, deduplicated by ID.
    """
    resp = httpx.get(
        f"{HUBSPOT_BASE}/cms/v3/blogs/posts",
        headers=_headers(),
        params={"limit": limit},
        timeout=15,
    )
    resp.raise_for_status()
    posts = resp.json().get("results", [])
    seen = {}
    for post in posts:
        gid = post.get("contentGroupId")
        if gid and gid not in seen:
            seen[gid] = {"contentGroupId": gid, "sample_title": post.get("name", "")}
    return list(seen.values())


def create_blog_draft(
    name: str,
    post_body: str,
    slug: str = "",
    meta_description: str = "",
    content_group_id: str = "",
) -> dict:
    """Create a draft blog post in HubSpot CMS.

    Posts are created as DRAFT by default (no state field needed).

    Returns dict with 'id', 'preview_url', and 'state'.
    """
    cg_id = get_content_group_id(content_group_id)

    payload = {
        "name": name,
        "contentGroupId": cg_id,
        "postBody": post_body,
    }
    if slug:
        payload["slug"] = slug
    if meta_description:
        payload["metaDescription"] = meta_description

    resp = httpx.post(
        f"{HUBSPOT_BASE}/cms/v3/blogs/posts",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    post_id = data.get("id", "unknown")
    return {
        "id": post_id,
        "preview_url": f"https://app.hubspot.com/content/blog-posts/{post_id}",
        "state": data.get("state", "DRAFT"),
    }


def get_blog_post(post_id: str) -> dict:
    """Retrieve a blog post by ID for verification."""
    resp = httpx.get(
        f"{HUBSPOT_BASE}/cms/v3/blogs/posts/{post_id}",
        headers=_headers(),
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "id": data.get("id"),
        "name": data.get("name"),
        "state": data.get("state"),
        "slug": data.get("slug"),
        "content_length": len(data.get("postBody", "")),
    }


def _get_template_post() -> dict:
    """Fetch a recent published post to use as a template for cloning."""
    resp = httpx.get(
        f"{HUBSPOT_BASE}/cms/v3/blogs/posts",
        headers=_headers(),
        params={"limit": 10, "state": "PUBLISHED"},
        timeout=15,
    )
    resp.raise_for_status()
    posts = resp.json().get("results", [])
    if not posts:
        raise ValueError("No published blog posts found to use as template.")
    # Pick the most recent one with substantial content
    for post in posts:
        if len(post.get("postBody", "")) > 1000:
            return post
    return posts[0]


def clone_and_replace(
    title: str,
    article_md: str,
    slug: str = "",
    meta_description: str = "",
) -> dict:
    """Clone an existing blog post and replace its content with new article.

    Fetches a template post to inherit layout/settings, creates a new DRAFT
    with the article content converted to HubSpot-formatted HTML.

    Returns dict with 'id', 'preview_url', 'state', and 'edit_url'.
    """
    from tools.html_export import markdown_to_html

    template = _get_template_post()
    html_body = markdown_to_html(article_md)

    # Build payload from template, replacing content fields
    payload = {
        "name": title,
        "contentGroupId": template["contentGroupId"],
        "postBody": html_body,
    }

    # Inherit template settings where available
    for field in ["templatePath", "layoutSections", "widgetContainers",
                  "themeSettingsValues"]:
        if template.get(field):
            payload[field] = template[field]

    if slug:
        payload["slug"] = f"blog/{slug}"
    if meta_description:
        payload["metaDescription"] = meta_description

    resp = httpx.post(
        f"{HUBSPOT_BASE}/cms/v3/blogs/posts",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    post_id = data.get("id", "unknown")

    # Get portal ID for the edit URL
    portal_id = ""
    try:
        acct_resp = httpx.get(
            f"{HUBSPOT_BASE}/account-info/v3/details",
            headers=_headers(),
            timeout=10,
        )
        if acct_resp.status_code == 200:
            portal_id = str(acct_resp.json().get("portalId", ""))
    except Exception:
        pass

    edit_url = f"https://app.hubspot.com/content/{portal_id}/blog/{post_id}/edit" if portal_id else ""

    return {
        "id": post_id,
        "preview_url": data.get("url", ""),
        "edit_url": edit_url,
        "state": data.get("state", "DRAFT"),
    }
