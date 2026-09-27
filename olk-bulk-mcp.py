"""Bulk move/delete MCP server wrapping the `olk` CLI.

olk's own `mail move`/`mail delete` (and its `olk mcp` tool equivalents) each
act on exactly one message ID -- there's no native bulk mode. This exposes
two additional tools, `bulk_move_messages` and `bulk_delete_messages`, that
take a KQL search query and act on every match, so Hermes gets bulk
semantics in one tool call instead of one call per message.

Registered as its own MCP server (alongside the base `olk` server, not a
replacement for it) via:
    hermes mcp add olk-bulk --command "<hermes>\\tools\\python-*\\python.exe" \\
        --args "<hermes.repo>\\olk-bulk-mcp.py" --account <email>

Safety:
  - Both tools default to dry_run=True: they report exactly what WOULD move
    or be deleted without acting, until called again with dry_run=False.
  - Both are marked readOnlyHint=False (destructiveHint=True for delete), so
    Hermes's own approval gate (tools/mcp_tool.py: a tool needs approval
    before its RPC fires unless readOnlyHint is exactly True at discovery
    time) still requires manual approval per call under
    approvals.mode: manual, on top of the dry_run default.
  - `top` is capped at 200 per call, to bound how much a single call (or a
    single approval) can affect.
"""

import json
import subprocess
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

ACCOUNT: str | None = None
if "--account" in sys.argv:
    ACCOUNT = sys.argv[sys.argv.index("--account") + 1]

MAX_TOP = 200

server = MCPServer(name="olk-bulk")


def _olk(*args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    cmd = ["olk", *args]
    if ACCOUNT:
        cmd += ["--account", ACCOUNT]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _search(query: str, top: int) -> list[dict[str, Any]]:
    top = max(1, min(top, MAX_TOP))
    proc = _olk("mail", "search", query, "-n", str(top), "--json", "--results-only", "--select=id,subject,from")
    if proc.returncode != 0:
        raise RuntimeError(f"olk mail search failed: {(proc.stderr or proc.stdout).strip()}")
    return json.loads(proc.stdout or "[]")


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True))
def bulk_move_messages(query: str, folder: str, top: int = 50, dry_run: bool = True) -> dict[str, Any]:
    """Move every message matching a KQL search query to a destination folder.

    Args:
        query: KQL search query (from:, subject:, hasAttachment:, etc. -- same syntax as `olk mail search`)
        folder: Destination folder ID, well-known name, or path (e.g. "Archive", "Inbox/2026")
        top: Max number of matching messages to act on in this call (safety cap, max 200)
        dry_run: If true (default), only report what WOULD move -- moves nothing. Call again
            with dry_run=false, after reviewing the dry-run result, to actually move messages.
    """
    matches = _search(query, top)
    if dry_run:
        return {
            "dry_run": True,
            "matched": len(matches),
            "would_move_to": folder,
            "messages": [{"id": m["id"], "subject": m.get("subject", ""), "from": m.get("from", "")} for m in matches],
        }
    moved: list[str] = []
    failed: list[dict[str, str]] = []
    for m in matches:
        proc = _olk("mail", "move", m["id"], folder)
        label = m.get("subject") or m["id"]
        if proc.returncode == 0:
            moved.append(label)
        else:
            failed.append({"subject": label, "error": (proc.stderr or proc.stdout).strip()})
    return {"dry_run": False, "moved_to": folder, "moved_count": len(moved), "failed_count": len(failed),
            "moved": moved, "failed": failed}


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
def bulk_delete_messages(query: str, top: int = 50, dry_run: bool = True) -> dict[str, Any]:
    """Delete every message matching a KQL search query.

    This is Microsoft Graph's standard delete (moves to Deleted Items), not a hard purge --
    deleting from Deleted Items itself needs a second pass with a query scoped to that folder.

    Args:
        query: KQL search query (from:, subject:, hasAttachment:, etc. -- same syntax as `olk mail search`)
        top: Max number of matching messages to act on in this call (safety cap, max 200)
        dry_run: If true (default), only report what WOULD be deleted -- deletes nothing. Call
            again with dry_run=false, after reviewing the dry-run result, to actually delete.
    """
    matches = _search(query, top)
    if dry_run:
        return {
            "dry_run": True,
            "matched": len(matches),
            "messages": [{"id": m["id"], "subject": m.get("subject", ""), "from": m.get("from", "")} for m in matches],
        }
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for m in matches:
        proc = _olk("mail", "delete", m["id"])
        label = m.get("subject") or m["id"]
        if proc.returncode == 0:
            deleted.append(label)
        else:
            failed.append({"subject": label, "error": (proc.stderr or proc.stdout).strip()})
    return {"dry_run": False, "deleted_count": len(deleted), "failed_count": len(failed),
            "deleted": deleted, "failed": failed}


if __name__ == "__main__":
    server.run(transport="stdio")
