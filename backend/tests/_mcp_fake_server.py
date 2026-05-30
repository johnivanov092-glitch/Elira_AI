"""Tiny stdio MCP server used as a fixture in tests.

Speaks just enough of the protocol to satisfy McpClient: handles
initialize, notifications/initialized, tools/list and tools/call.
Reads one JSON-RPC message per line from stdin and writes one per
line to stdout.

Behavior can be tweaked via env vars (set by the test before spawn):
  FAKE_MCP_FAIL_INIT=1     — return JSON-RPC error on initialize
  FAKE_MCP_HANG_INIT=1     — never respond to initialize (timeout test)
  FAKE_MCP_TOOLS_FAIL=1    — return error from tools/list
  FAKE_MCP_CALL_FAIL=1     — return isError=True from tools/call
"""
from __future__ import annotations

import json
import os
import sys
import time


def write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    initialized = False
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        rid = req.get("id")

        if method == "initialize":
            if os.environ.get("FAKE_MCP_HANG_INIT"):
                # Deliberately don't respond to test client timeout
                time.sleep(60)
                continue
            if os.environ.get("FAKE_MCP_FAIL_INIT"):
                write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "init failed (test)"}})
                continue
            write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "0.1"},
                },
            })

        elif method == "notifications/initialized":
            initialized = True
            # No response — it's a notification

        elif method == "tools/list":
            if os.environ.get("FAKE_MCP_TOOLS_FAIL"):
                write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "tools failed"}})
                continue
            write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo the input text back.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        },
                        {
                            "name": "search",
                            "description": "Fake search.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"q": {"type": "string"}},
                                "required": ["q"],
                            },
                        },
                    ],
                },
            })

        elif method == "tools/call":
            params = req.get("params", {}) or {}
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            if os.environ.get("FAKE_MCP_CALL_FAIL"):
                write({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "fake error"}],
                    },
                })
                continue
            if tool_name == "echo":
                text = (arguments.get("text") or "").strip()
                write({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "isError": False,
                        "content": [{"type": "text", "text": f"echo: {text}"}],
                    },
                })
            elif tool_name == "search":
                q = (arguments.get("q") or "").strip()
                write({
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "isError": False,
                        "content": [
                            {"type": "text", "text": f"hit 1 for {q}"},
                            {"type": "text", "text": f"hit 2 for {q}"},
                        ],
                    },
                })
            else:
                write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown tool {tool_name}"}})

        else:
            write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
