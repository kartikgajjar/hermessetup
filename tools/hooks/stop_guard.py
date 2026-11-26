#!/usr/bin/env python
"""stop_guard.py: Stop / SubagentStop hook (bootstrap v2.6, 2.4).

Exit 2 (reason on stderr) keeps the agent going while its own background tasks still run. For a
factory subagent (agent_type hs-*), its last message must state 'background tasks: none'. When
stop_hook_active is true the agent is already continuing because of this hook: let it finish.
"""
from __future__ import annotations

import json
import re
import sys

RUNNING = {"running", "pending", "in_progress", "started", "active"}


def running_tasks(bt):
    if not bt:
        return []
    items = bt if isinstance(bt, list) else (list(bt.values()) if isinstance(bt, dict) else [])
    out = []
    for t in items:
        if isinstance(t, dict):
            st = str(t.get("status", "running")).lower()
            if st in RUNNING:
                out.append(t.get("description") or t.get("command") or t.get("id") or "task")
        else:
            out.append(str(t))
    return out


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        return 0
    if data.get("stop_hook_active"):
        return 0
    try:
        tasks = running_tasks(data.get("background_tasks"))
        if tasks:
            print("Your background tasks still run: %s. Stop them, then state 'background tasks: none'."
                  % ", ".join(map(str, tasks[:5])), file=sys.stderr)
            return 2
        if data.get("hook_event_name") == "SubagentStop" and str(data.get("agent_type", "")).startswith("hs-"):
            msg = data.get("last_assistant_message") or ""
            if not re.search(r"background tasks:\s*none", str(msg), re.IGNORECASE):
                print("Before reporting, stop your own background tasks and state 'background tasks: none'.",
                      file=sys.stderr)
                return 2
    except Exception as e:  # noqa: BLE001
        print("stop_guard.py internal error (stop allowed): %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
