# F-olk-bulk

- What: `olk-bulk-mcp.py`, a stdio MCP server Hermes spawns with its bundled Python; wraps `olk mail` for bulk
  move/delete (dry_run by default) and the sender-policy loop.
- Reach: Hermes registers it as `mcp_servers.olk-bulk` (absolute, version-pinned interpreter).
- Drive: `python tools/mcp_probe.py --hermes-python --expect bulk_move_messages,bulk_delete_messages,sender_engagement_report,record_sender_decision,cleanup_approved_senders -- olk-bulk-mcp.py`
- End state: exit 0; JSON lists all five tools, `missing: []`.
- Gotchas: `--hermes-python` uses the interpreter Hermes is configured with, not the newest bundled one (on
  2026-11-23 the newest, python-3.14.9, had no `mcp` module while the registered 3.14.7 did). Calling a tool
  (even dry_run) reaches Microsoft Graph: wait list.
