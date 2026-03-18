#!/usr/bin/env python3
"""HubSpot CMS Blog Posts MCP Server.

Exposes tools for creating blog post drafts in HubSpot CMS.
Runs as a stdio-transport MCP server for Claude Code.

Implements the MCP JSON-RPC protocol directly (no SDK dependency)
so it works with Python 3.9+.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to path so we can import tools/
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.hubspot_cms import clone_and_replace, create_blog_draft, get_blog_post, list_blogs

SERVER_NAME = "hubspot-cms"
SERVER_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "hubspot_list_blogs",
        "description": (
            "List existing HubSpot blogs to find contentGroupId values. "
            "Use this to discover the blog ID needed for creating drafts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max number of posts to scan (default 10)",
                    "default": 10,
                },
            },
        },
    },
    {
        "name": "hubspot_create_blog_draft",
        "description": (
            "Create a draft blog post in HubSpot CMS. Posts are created as "
            "unpublished drafts by default. Returns the post ID and preview URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Blog post title (required)"},
                "post_body": {"type": "string", "description": "HTML content of the blog post (required)"},
                "content_group_id": {
                    "type": "string",
                    "description": "Parent blog ID. Use hubspot_list_blogs to find this. Auto-detected if omitted.",
                },
                "slug": {"type": "string", "description": "URL slug (auto-generated from name if empty)"},
                "meta_description": {"type": "string", "description": "SEO meta description"},
            },
            "required": ["name", "post_body"],
        },
    },
    {
        "name": "hubspot_get_blog_post",
        "description": "Retrieve a blog post by ID to verify it was created correctly.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "post_id": {"type": "string", "description": "The HubSpot blog post ID"},
            },
            "required": ["post_id"],
        },
    },
]


def handle_request(method: str, params: dict | None, req_id) -> dict:
    """Handle a single JSON-RPC request and return a response dict."""
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "notifications/initialized":
        return None  # Notification, no response

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        try:
            result_text = _dispatch_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            }

    # Unknown method
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _dispatch_tool(name: str, args: dict) -> str:
    """Dispatch a tool call to the appropriate function."""
    if name == "hubspot_list_blogs":
        blogs = list_blogs(limit=args.get("limit", 10))
        if not blogs:
            return "No blog posts found in this HubSpot portal."
        lines = []
        for b in blogs:
            lines.append(f"contentGroupId: {b['contentGroupId']}  (sample: {b['sample_title']})")
        return "\n".join(lines)

    if name == "hubspot_create_blog_draft":
        result = create_blog_draft(
            name=args["name"],
            post_body=args["post_body"],
            slug=args.get("slug", ""),
            meta_description=args.get("meta_description", ""),
            content_group_id=args.get("content_group_id", ""),
        )
        return (
            f"Draft created successfully.\n"
            f"  ID: {result['id']}\n"
            f"  State: {result['state']}\n"
            f"  Preview: {result['preview_url']}"
        )

    if name == "hubspot_get_blog_post":
        info = get_blog_post(args["post_id"])
        return (
            f"Title: {info['name']}\n"
            f"State: {info['state']}\n"
            f"Slug: {info['slug']}\n"
            f"Content length: {info['content_length']} chars"
        )

    raise ValueError(f"Unknown tool: {name}")


def main():
    """Run the MCP server on stdio."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = request.get("method", "")
        params = request.get("params")
        req_id = request.get("id")

        response = handle_request(method, params, req_id)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
