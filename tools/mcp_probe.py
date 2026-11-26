#!/usr/bin/env python
"""mcp_probe.py: launch a stdio MCP server, initialize it, list its tools, shut it down.

  python tools/mcp_probe.py [--expect NAME,NAME] [--timeout S] [--hermes-python] -- <server command...>
--hermes-python puts the interpreter olk-bulk is registered with in Hermes (%LOCALAPPDATA%/hermes/config.yaml,
mcp_servers.olk-bulk.command) in front of the command - the real surface, not the newest bundled python.
Prints one JSON line {server, tools:[...], missing:[...]}; exit 0 when every expected tool is listed.
Read-only: it never calls a tool.
"""
from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--" not in argv:
        print(__doc__)
        return 2
    i = argv.index("--")
    ap = argparse.ArgumentParser(prog="mcp_probe.py")
    ap.add_argument("--expect", default="")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--hermes-python", action="store_true")
    a = ap.parse_args(argv[:i])
    cmd = argv[i + 1:]
    if a.hermes_python:
        import os
        import re
        cfg = os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "config.yaml")
        try:
            text = open(cfg, encoding="utf-8").read()
        except OSError:
            text = ""
        m = re.search(r"^\s+olk-bulk:\s*\n\s+command:\s*(\S.*?)\s*$", text, re.MULTILINE)
        if not m:
            print(json.dumps({"error": "olk-bulk command not found in %s" % cfg}))
            return 1
        cmd = [m.group(1).strip("'\"")] + cmd
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    q: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [q.put(l) for l in p.stdout], daemon=True).start()

    def send(obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()

    def wait_for(id_):
        while True:
            line = q.get(timeout=a.timeout)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == id_:
                return msg

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "factory-probe", "version": "1"}}})
        init = wait_for(1)
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = wait_for(2).get("result", {}).get("tools", [])
    except queue.Empty:
        print(json.dumps({"error": "timeout after %ss" % a.timeout}))
        return 1
    finally:
        try:
            p.stdin.close()
            p.terminate()
            p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            p.kill()
    names = [t["name"] for t in tools]
    expect = [x for x in a.expect.split(",") if x]
    missing = [x for x in expect if x not in names]
    print(json.dumps({"server": init.get("result", {}).get("serverInfo", {}), "tools": names, "missing": missing}))
    return 1 if missing or not names else 0


if __name__ == "__main__":
    sys.exit(main())
