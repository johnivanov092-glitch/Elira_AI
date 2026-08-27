"""Tiny stdio LSP server used as a fixture in tests.

Speaks just enough of the Language Server Protocol to satisfy
``LspClient``: handles ``initialize``, ``initialized``, ``textDocument/
didOpen`` (answered with a ``publishDiagnostics`` push), ``textDocument/
definition``, ``textDocument/references``, ``shutdown`` and ``exit``.

Unlike the MCP fake (line-delimited JSON), the LSP wire format is
``Content-Length: N\\r\\n\\r\\n`` + N bytes of UTF-8 JSON body, read from
and written to the **binary** stdio buffers. This is exactly the framing
``LspClient`` parses, so the fixture doubles as a framing round-trip test.

Behavior can be tweaked via env vars (set by the test before spawn):
  FAKE_LSP_FAIL_INIT=1       — return JSON-RPC error on initialize
  FAKE_LSP_HANG_INIT=1       — never respond to initialize (timeout test)
  FAKE_LSP_NO_DIAGNOSTICS=1  — no publishDiagnostics push (settle-timeout)
  FAKE_LSP_BIG_REFERENCES=1  — references returns more than the cap
  FAKE_LSP_SPLIT_FRAME=1      — flush header and body separately (partial read)
  FAKE_LSP_REQUEST_CONFIGURATION=1 — require a workspace/configuration reply
  FAKE_LSP_EMPTY_THEN_DIAGNOSTIC=1 — publish an empty warm-up before real diagnostics
"""
from __future__ import annotations

import json
import os
import sys
import time


def _write(msg: dict) -> None:
    """Write one Content-Length framed JSON message to binary stdout.

    When ``FAKE_LSP_SPLIT_FRAME`` is set, the header and body are flushed
    as two separate writes so the client's reader must cope with a frame
    that arrives in pieces.
    """
    body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    out = sys.stdout.buffer
    if os.environ.get("FAKE_LSP_SPLIT_FRAME"):
        out.write(header)
        out.flush()
        time.sleep(0.02)
        out.write(body)
        out.flush()
    else:
        out.write(header + body)
        out.flush()


def _read_frame() -> dict | None:
    """Read one Content-Length framed JSON message from binary stdin.

    Returns the parsed dict, or ``None`` at EOF. Mirrors the client's
    own framing parser.
    """
    stream = sys.stdin.buffer
    content_length: int | None = None
    while True:
        line = stream.readline()
        if line == b"":
            return None  # EOF
        line = line.strip()
        if line == b"":
            break  # end of headers
        if b":" in line:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                try:
                    content_length = int(value.strip())
                except ValueError:
                    content_length = None
    if content_length is None or content_length < 0:
        return None
    chunks: list[bytes] = []
    remaining = content_length
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None  # EOF mid-body
        chunks.append(chunk)
        remaining -= len(chunk)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _location(uri: str, line: int, char: int) -> dict:
    """A single LSP Location with a one-character range."""
    return {
        "uri": uri,
        "range": {
            "start": {"line": line, "character": char},
            "end": {"line": line, "character": char + 1},
        },
    }


def main() -> None:
    configuration_received = False
    pending_open: dict | None = None

    def publish_diagnostics(params: dict) -> None:
        text_doc = params.get("textDocument") or {}
        uri = text_doc.get("uri") or "file:///fake.py"
        _write({
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": uri,
                "diagnostics": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 5},
                        },
                        "severity": 1,
                        "message": "fake error: undefined name",
                        "source": "fake-lsp",
                    },
                    {
                        "range": {
                            "start": {"line": 2, "character": 4},
                            "end": {"line": 2, "character": 9},
                        },
                        "severity": 2,
                        "message": "fake warning: unused variable",
                        "source": "fake-lsp",
                    },
                ],
            },
        })

    while True:
        req = _read_frame()
        if req is None:
            return  # EOF / pipe closed
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            if os.environ.get("FAKE_LSP_HANG_INIT"):
                # Deliberately never respond — drives the client init timeout.
                time.sleep(60)
                continue
            if os.environ.get("FAKE_LSP_FAIL_INIT"):
                _write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "init failed (test)"}})
                continue
            _write({
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "capabilities": {
                        "textDocumentSync": 1,
                        "definitionProvider": True,
                        "referencesProvider": True,
                    },
                    "serverInfo": {"name": "fake-lsp", "version": "0.1"},
                },
            })

        elif method == "initialized":
            if os.environ.get("FAKE_LSP_REQUEST_CONFIGURATION"):
                _write({
                    "jsonrpc": "2.0",
                    "id": 9001,
                    "method": "workspace/configuration",
                    "params": {"items": [{"section": "fake"}]},
                })

        elif method is None and rid == 9001:
            configuration_received = req.get("result") == [None]
            if configuration_received and pending_open is not None:
                publish_diagnostics(pending_open)
                pending_open = None

        elif method == "textDocument/didOpen":
            if os.environ.get("FAKE_LSP_NO_DIAGNOSTICS"):
                continue  # no push → client.get_diagnostics settle-times out
            if os.environ.get("FAKE_LSP_REQUEST_CONFIGURATION") and not configuration_received:
                pending_open = params
                continue
            if os.environ.get("FAKE_LSP_EMPTY_THEN_DIAGNOSTIC"):
                text_doc = params.get("textDocument") or {}
                _write({
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {
                        "uri": text_doc.get("uri") or "file:///fake.py",
                        "diagnostics": [],
                    },
                })
                time.sleep(0.15)
            # Push a diagnostic (notification, no id) — this is how real
            # servers report problems, asynchronously after didOpen.
            publish_diagnostics(params)

        elif method == "textDocument/definition":
            text_doc = params.get("textDocument") or {}
            uri = text_doc.get("uri") or "file:///fake.py"
            _write({"jsonrpc": "2.0", "id": rid, "result": _location(uri, 10, 4)})

        elif method == "textDocument/references":
            text_doc = params.get("textDocument") or {}
            uri = text_doc.get("uri") or "file:///fake.py"
            if os.environ.get("FAKE_LSP_BIG_REFERENCES"):
                # More than the provider's _RESULT_LIMIT (50) so the test can
                # assert truncation + meta.truncated.
                locations = [_location(uri, i, 0) for i in range(120)]
            else:
                locations = [_location(uri, 10, 4), _location(uri, 20, 8)]
            _write({"jsonrpc": "2.0", "id": rid, "result": locations})

        elif method == "shutdown":
            _write({"jsonrpc": "2.0", "id": rid, "result": None})

        elif method == "exit":
            return

        elif rid is not None:
            # Unknown request → error response (keeps the client unblocked).
            _write({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}})
        # Unknown notification → silently ignored.


if __name__ == "__main__":
    main()
