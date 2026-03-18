"""Asana REST API wrapper for automated task updates.

Used by the pipeline for post-publish Asana updates and by the MCP server.
Auth: ASANA_ACCESS_TOKEN env var (Personal Access Token).
API docs: https://developers.asana.com/reference
"""

from __future__ import annotations

import os

import httpx

ASANA_BASE = "https://app.asana.com/api/1.0"


def _get_token() -> str:
    token = os.environ.get("ASANA_ACCESS_TOKEN", "")
    if not token:
        raise ValueError(
            "ASANA_ACCESS_TOKEN not set. Create a Personal Access Token at "
            "https://app.asana.com/0/my-apps"
        )
    return token


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
    }


def search_tasks(workspace_gid: str, query: str) -> list[dict]:
    """Search for tasks by text query within a workspace.

    Returns list of {gid, name, completed} dicts.
    """
    resp = httpx.get(
        f"{ASANA_BASE}/workspaces/{workspace_gid}/tasks/search",
        headers=_headers(),
        params={"text": query, "opt_fields": "name,gid,completed"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_task(task_gid: str) -> dict:
    """Get full task details by GID."""
    resp = httpx.get(
        f"{ASANA_BASE}/tasks/{task_gid}",
        headers=_headers(),
        params={"opt_fields": "name,notes,completed,assignee.name,due_on,custom_fields"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def update_task(task_gid: str, data: dict) -> dict:
    """Update an Asana task.

    Supported data keys: name, notes, completed, assignee, due_on, custom_fields.
    """
    resp = httpx.put(
        f"{ASANA_BASE}/tasks/{task_gid}",
        headers=_headers(),
        json={"data": data},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})


def add_comment(task_gid: str, text: str) -> dict:
    """Add a comment (story) to an Asana task."""
    resp = httpx.post(
        f"{ASANA_BASE}/tasks/{task_gid}/stories",
        headers=_headers(),
        json={"data": {"text": text}},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json().get("data", {})
