#!/usr/bin/env python3
"""Asana MCP Server.

Exposes tools for updating tasks and adding comments in Asana.
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

from tools.asana_api import add_comment, get_task, search_tasks, update_task

SERVER_NAME = "asana"
SERVER_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "asana_search_tasks",
        "description": "Search for Asana tasks by text query within a workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_gid": {
                    "type": "string",
                    "description": "Asana workspace GID. Uses ASANA_WORKSPACE_GID env var if omitted.",
                },
                "query": {"type": "string", "description": "Text to search for in task names"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "asana_get_task",
        "description": "Get full details of an Asana task by its GID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_gid": {"type": "string", "description": "The Asana task GID"},
            },
            "required": ["task_gid"],
        },
    },
    {
        "name": "asana_update_task",
        "description": (
            "Update an Asana task. Can change name, notes, completed status, "
            "assignee, due date, and custom fields."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_gid": {"type": "string", "description": "The Asana task GID"},
                "name": {"type": "string", "description": "New task name"},
                "notes": {"type": "string", "description": "New task description/notes"},
                "completed": {"type": "boolean", "description": "Mark task as complete or incomplete"},
                "assignee": {"type": "string", "description": "Assignee user GID or 'me'"},
                "due_on": {"type": "string", "description": "Due date in YYYY-MM-DD format"},
            },
            "required": ["task_gid"],
        },
    },
    {
        "name": "asana_add_comment",
        "description": "Add a comment (story) to an Asana task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_gid": {"type": "string", "description": "The Asana task GID"},
                "text": {"type": "string", "description": "Comment text to add"},
            },
            "required": ["task_gid", "text"],
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
    import os

    if name == "asana_search_tasks":
        ws_gid = args.get("workspace_gid") or os.environ.get("ASANA_WORKSPACE_GID", "")
        if not ws_gid:
            raise ValueError(
                "workspace_gid not provided and ASANA_WORKSPACE_GID not set in environment."
            )
        tasks = search_tasks(ws_gid, args["query"])
        if not tasks:
            return f"No tasks found matching '{args['query']}'."
        lines = []
        for t in tasks:
            status = "completed" if t.get("completed") else "open"
            lines.append(f"  {t['gid']}  {t['name']}  ({status})")
        return f"Found {len(tasks)} task(s):\n" + "\n".join(lines)

    if name == "asana_get_task":
        task = get_task(args["task_gid"])
        assignee = task.get("assignee", {})
        assignee_name = assignee.get("name", "Unassigned") if assignee else "Unassigned"
        return (
            f"Name: {task.get('name')}\n"
            f"Completed: {task.get('completed')}\n"
            f"Assignee: {assignee_name}\n"
            f"Due: {task.get('due_on', 'None')}\n"
            f"Notes: {(task.get('notes') or '')[:500]}"
        )

    if name == "asana_update_task":
        data = {}
        for key in ("name", "notes", "completed", "assignee", "due_on"):
            if key in args and key != "task_gid":
                data[key] = args[key]
        if not data:
            raise ValueError("No fields to update. Provide at least one of: name, notes, completed, assignee, due_on.")
        result = update_task(args["task_gid"], data)
        return f"Task updated: {result.get('name')} (completed: {result.get('completed')})"

    if name == "asana_add_comment":
        result = add_comment(args["task_gid"], args["text"])
        return f"Comment added (story GID: {result.get('gid')})"

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
