---
name: verify-hermessetup
description: Verify a hermessetup change on its real surface - static checks of the scripts, live read-only drives of the MCP servers (olk-bulk, chrome-mcp) over stdio, and the chrome-mcp suite. Used by hs-verifier; never mutates the live Hermes install.
---

# verify-hermessetup

The surface: PowerShell scripts that mutate the live Hermes install (no sandbox copy), and two MCP servers
Hermes spawns over stdio. Live drives here are read-only. Anything that would change the install or reach
Microsoft Graph, GitHub or the Hermes upstream is on the wait list: report it as `blocked: needs user`.
All commands run from the repo root (the brief's worktree) with paths relative to it; no `cd`.

## Launch
- Static surface: nothing to launch.
- MCP servers: `tools/mcp_probe.py` starts the server itself over stdio from an empty state (no session),
  reads `initialize` and `tools/list` (the ready signal), then terminates it (teardown).

## Doctor (one read-only check that the instance is worth driving)
```
python tools/check.py
```
Must print `check: PASS` (all .ps1 parse, all .py compile). If it fails at the base too, that is a baseline
problem: one report line, not a FAIL.

## Drive (stable handles: script names, tool names, test names; never coordinates)
- F-olk-bulk: `python tools/mcp_probe.py --expect bulk_move_messages,bulk_delete_messages,sender_engagement_report,record_sender_decision,cleanup_approved_senders --hermes-python -- olk-bulk-mcp.py`
- F-chrome-mcp: `npm --prefix chrome-mcp test` then
  `python tools/mcp_probe.py --expect tabs_list,snapshot,get_text -- node chrome-mcp/dist/src/cli.js --no-auto-update --no-telemetry`
- F-scripts: `python tools/check.py --help-render` (every script has its help block; the scripts' user-facing contract)
- F-update-plan: `pwsh -NoProfile -File 05-update-hermes.ps1 -Plan` contacts the Hermes upstream: wait list,
  report `blocked: needs user` unless the brief says the user approved it for this run.
See `docs/features/` for each feature's end state and gotchas.

## Evidence
The real user path: the command, the action (what was listed or run) and the resulting state (tool names
present, test counts, parse result). Keep the command output in your JSON `evidence` (short) - the launcher
saves the full log under `.factory/evidence/` and records its path in the verdict.

## Cleanup
Only what the run started: `mcp_probe.py` terminates its own server; `npm test` rebuilds `chrome-mcp/dist`
(gitignored, expected). Never stop the Hermes gateway, Chrome, Docker or any process you did not start.
The evidence survives cleanup.

## Rules
- Never weaken a journey to make it pass. Inconclusive or the wrong surface is not a pass.
- Never edit files. Report findings; the lead decides.
