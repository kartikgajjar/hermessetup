"""Minimal MCP stdio client (stdlib only) -- just enough to call chrome-mcp tools.

MCP's stdio transport is newline-delimited JSON-RPC 2.0: initialize, then the
notifications/initialized notification, then tools/call. Server stderr is left
attached to ours so chrome-mcp's own diagnostics stay visible.

A second chrome-mcp process (this one) next to the Hermes gateway's does not
fight over the port: it joins the running server as a peer and relays through
it (chrome-mcp src/bridge/peer.ts), so the extension pairing is shared.
"""
from __future__ import annotations

import json
import subprocess
import threading


class McpError(RuntimeError):
    pass


class McpStdioClient:
    def __init__(self, argv: list[str]):
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._next_id = 0
        self._lock = threading.Lock()
        self._request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "fastnav", "version": "0.1"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _request(self, method: str, params: dict) -> dict:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            assert self._proc.stdout is not None
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    raise McpError(f"server exited (code {self._proc.poll()}) during {method}")
                msg = json.loads(line)
                if msg.get("id") != rid:
                    continue  # notifications / logging from the server
                if "error" in msg:
                    raise McpError(f"{method}: {msg['error'].get('message')}")
                return msg["result"]

    def call(self, tool: str, args: dict | None = None):
        """Call a tool; returns parsed JSON when the text block is JSON, else the text."""
        res = self._request("tools/call", {"name": tool, "arguments": args or {}})
        text = "\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise McpError(f"{tool}: {text}")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except Exception:
            self._proc.kill()
