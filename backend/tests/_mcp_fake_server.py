"""Tiny stdio MCP server used as a fixture in tests.

Speaks just enough of the protocol to satisfy McpClient: handles
initialize, notifications/initialized, tools/list, tools/call,
resources/list, resources/read, resources/templates/list, prompts/list,
and prompts/get.
Reads one JSON-RPC message per line from stdin and writes one per
line to stdout.

Behavior can be tweaked via env vars (set by the test before spawn):
  FAKE_MCP_FAIL_INIT=1     — return JSON-RPC error on initialize
  FAKE_MCP_HANG_INIT=1     — never respond to initialize (timeout test)
  FAKE_MCP_TOOLS_FAIL=1    — return error from tools/list
  FAKE_MCP_CALL_FAIL=1     — return isError=True from tools/call
  FAKE_MCP_NO_RESOURCES=1  — omit resources capability
  FAKE_MCP_NO_PROMPTS=1    — omit prompts capability
  FAKE_MCP_BIG_RESOURCE=1  — return oversized resource text
  FAKE_MCP_PROTOCOL_VERSION=<date> — server-negotiated protocol version
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
            capabilities = {"tools": {}}
            if not os.environ.get("FAKE_MCP_NO_RESOURCES"):
                capabilities["resources"] = {}
            if not os.environ.get("FAKE_MCP_NO_PROMPTS"):
                capabilities["prompts"] = {}
            write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": os.environ.get("FAKE_MCP_PROTOCOL_VERSION", "2024-11-05"),
                    "capabilities": capabilities,
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

        elif method == "resources/list":
            write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "resources": [
                        {
                            "uri": "file:///fake/readme.md",
                            "name": "readme.md",
                            "description": "Fake text resource.",
                            "mimeType": "text/markdown",
                        }
                    ],
                },
            })

        elif method == "resources/templates/list":
            write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "resourceTemplates": [
                        {
                            "uriTemplate": "file:///fake/{name}.md",
                            "name": "Fake markdown docs",
                            "description": "Parameterized fake resource.",
                            "mimeType": "text/markdown",
                        }
                    ],
                },
            })

        elif method == "resources/read":
            params = req.get("params", {}) or {}
            uri = params.get("uri") or "file:///fake/readme.md"
            text = "R" * 10000 if os.environ.get("FAKE_MCP_BIG_RESOURCE") else "Fake resource text."
            write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "text/plain",
                            "text": text,
                        }
                    ],
                },
            })

        elif method == "prompts/list":
            write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "prompts": [
                        {
                            "name": "review",
                            "description": "Review code.",
                            "arguments": [
                                {"name": "code", "description": "Code to review", "required": True}
                            ],
                        }
                    ],
                },
            })

        elif method == "prompts/get":
            params = req.get("params", {}) or {}
            args = params.get("arguments", {}) or {}
            code = args.get("code") or "print('x')"
            write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "description": "Review code prompt.",
                    "messages": [
                        {
                            "role": "user",
                            "content": {
                                "type": "text",
                                "text": f"Please review: {code}",
                            },
                        }
                    ],
                },
            })

        else:
            write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
