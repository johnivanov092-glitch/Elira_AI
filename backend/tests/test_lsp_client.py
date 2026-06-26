"""Tests for the LSP stdio client.

These spawn a real subprocess running tests/_lsp_fake_server.py (a fake
language server that speaks Content-Length framed JSON-RPC). End-to-end
coverage of: framing round-trip (including split frames), handshake
ok/fail/hang, diagnostics push + settle-timeout, definition, references,
explicit shutdown waking pending requests, and process-tree teardown.

Mirrors test_mcp_client.py: real subprocess fake toggled by env flags,
pure unittest.TestCase classes grouped by concern, try/finally: stop().
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_providers.lsp_client import (  # noqa: E402
    LspClient,
    LspError,
    _frame,
    _read_frame,
)


FAKE_SERVER = Path(__file__).parent / "_lsp_fake_server.py"
FAKE_URI = "file:///fake.py"


def _client(
    *,
    fail_init: bool = False,
    hang_init: bool = False,
    no_diagnostics: bool = False,
    big_references: bool = False,
    split_frame: bool = False,
    cwd: str | None = None,
) -> LspClient:
    env = dict(os.environ)
    if fail_init:
        env["FAKE_LSP_FAIL_INIT"] = "1"
    if hang_init:
        env["FAKE_LSP_HANG_INIT"] = "1"
    if no_diagnostics:
        env["FAKE_LSP_NO_DIAGNOSTICS"] = "1"
    if big_references:
        env["FAKE_LSP_BIG_REFERENCES"] = "1"
    if split_frame:
        env["FAKE_LSP_SPLIT_FRAME"] = "1"
    return LspClient(
        language="python",
        command=sys.executable,
        args=[str(FAKE_SERVER)],
        env=env,
        cwd=cwd,
    )


class FramingTest(unittest.TestCase):
    """The Content-Length codec is the load-bearing difference from MCP."""

    def test_frame_roundtrip(self) -> None:
        import io

        msg = {"jsonrpc": "2.0", "id": 1, "method": "x", "params": {"a": "ы"}}
        framed = _frame(msg)
        self.assertTrue(framed.startswith(b"Content-Length: "))
        self.assertIn(b"\r\n\r\n", framed)
        parsed = _read_frame(io.BytesIO(framed))
        self.assertEqual(parsed, msg)

    def test_read_frame_eof_returns_none(self) -> None:
        import io

        self.assertIsNone(_read_frame(io.BytesIO(b"")))

    def test_read_frame_missing_length_raises(self) -> None:
        import io

        with self.assertRaises(LspError):
            _read_frame(io.BytesIO(b"X-Header: 1\r\n\r\n{}"))


class HandshakeTest(unittest.TestCase):
    def test_initialize_succeeds(self) -> None:
        client = _client()
        try:
            client.start()
            self.assertTrue(client.is_alive())
            self.assertTrue(client._initialized)
        finally:
            client.stop()

    def test_initialize_error_raises_and_kills_process(self) -> None:
        client = _client(fail_init=True)
        with self.assertRaises(LspError):
            client.start()
        # The failed handshake must tear the subprocess down, not leak it.
        self.assertFalse(client.is_alive())

    def test_unknown_command_raises(self) -> None:
        client = LspClient(
            language="python", command="nonexistent_binary_12345xyz"
        )
        with self.assertRaises(LspError):
            client.start()

    def test_stop_is_idempotent(self) -> None:
        client = _client()
        client.start()
        client.stop()
        client.stop()  # no exception
        self.assertFalse(client.is_alive())

    def test_initialize_hang_eventually_times_out(self) -> None:
        client = _client(hang_init=True)
        with patch(
            "app.application.tool_providers.lsp_client.INITIALIZE_TIMEOUT",
            0.5,
        ):
            with self.assertRaises(LspError) as ctx:
                client.start()
            self.assertIn("timed out", str(ctx.exception).lower())
        # start() self-tears-down on a failed handshake.
        self.assertFalse(client.is_alive())


class SplitFrameTest(unittest.TestCase):
    def test_handshake_survives_split_frames(self) -> None:
        """Server flushes header and body separately — the client's reader
        must reassemble a frame that arrives in pieces."""
        client = _client(split_frame=True)
        try:
            client.start()
            self.assertTrue(client.is_alive())
            client.did_open(FAKE_URI, "python", "x = 1\n")
            diags = client.get_diagnostics(FAKE_URI, settle=5.0)
            self.assertIsNotNone(diags)
            self.assertEqual(len(diags or []), 2)
        finally:
            client.stop()


class DiagnosticsTest(unittest.TestCase):
    def test_did_open_pushes_diagnostics(self) -> None:
        client = _client()
        try:
            client.start()
            client.did_open(FAKE_URI, "python", "x = 1\n")
            diags = client.get_diagnostics(FAKE_URI, settle=5.0)
            self.assertIsNotNone(diags)
            self.assertEqual(len(diags or []), 2)
            severities = sorted(d["severity"] for d in diags or [])
            self.assertEqual(severities, [1, 2])  # one error, one warning
            self.assertEqual(diags[0]["source"], "fake-lsp")
        finally:
            client.stop()

    def test_clean_file_is_distinguishable(self) -> None:
        """An empty diagnostics list (analysed, clean) must be returned as
        ``[]`` — distinct from ``None`` (never analysed / still indexing)."""
        client = _client()
        try:
            client.start()
            client.did_open(FAKE_URI, "python", "x = 1\n")
            diags = client.get_diagnostics(FAKE_URI, settle=5.0)
            self.assertIsInstance(diags, list)
        finally:
            client.stop()

    def test_no_push_settle_timeout_returns_none(self) -> None:
        """When the server never pushes, get_diagnostics returns None (the
        provider's "not ready yet, retry" signal) rather than blocking."""
        client = _client(no_diagnostics=True)
        try:
            client.start()
            client.did_open(FAKE_URI, "python", "x = 1\n")
            diags = client.get_diagnostics(FAKE_URI, settle=0.3)
            self.assertIsNone(diags)
        finally:
            client.stop()


class DefinitionTest(unittest.TestCase):
    def test_definition_returns_single_location(self) -> None:
        client = _client()
        try:
            client.start()
            client.did_open(FAKE_URI, "python", "x = 1\n")
            locs = client.definition(FAKE_URI, 0, 0)
            self.assertEqual(len(locs), 1)
            self.assertEqual(locs[0]["uri"], FAKE_URI)
            self.assertEqual(locs[0]["range"]["start"]["line"], 10)
            self.assertEqual(locs[0]["range"]["start"]["character"], 4)
        finally:
            client.stop()


class ReferencesTest(unittest.TestCase):
    def test_references_returns_locations(self) -> None:
        client = _client()
        try:
            client.start()
            client.did_open(FAKE_URI, "python", "x = 1\n")
            refs = client.references(FAKE_URI, 0, 0)
            self.assertEqual(len(refs), 2)
            self.assertEqual(refs[0]["range"]["start"]["line"], 10)
            self.assertEqual(refs[1]["range"]["start"]["line"], 20)
        finally:
            client.stop()

    def test_big_references_returned_uncapped_by_client(self) -> None:
        """The client itself does not cap — capping is the provider's job.
        The fake returns 120 locations; the client passes them all through."""
        client = _client(big_references=True)
        try:
            client.start()
            client.did_open(FAKE_URI, "python", "x = 1\n")
            refs = client.references(FAKE_URI, 0, 0)
            self.assertEqual(len(refs), 120)
        finally:
            client.stop()


class ShutdownTest(unittest.TestCase):
    def test_stop_wakes_pending_requests(self) -> None:
        """A request in flight when stop() runs must wake with an LspError
        rather than hang until the timeout."""
        client = _client()
        client.start()

        results: list[object] = []

        def _call() -> None:
            try:
                client.references(FAKE_URI, 0, 0)
                results.append("ok")
            except LspError as exc:
                results.append(exc)

        # Fire a request, then immediately stop. The pending request should
        # be woken by _wake_all_pending. (It may also legitimately complete;
        # either way it must not hang and must not raise anything else.)
        t = threading.Thread(target=_call)
        t.start()
        client.stop()
        t.join(timeout=5.0)
        self.assertFalse(t.is_alive())

    def test_request_after_stop_raises(self) -> None:
        client = _client()
        client.start()
        client.stop()
        with self.assertRaises(LspError):
            client.definition(FAKE_URI, 0, 0)


class ProcessTreeTest(unittest.TestCase):
    def test_stop_terminates_the_process(self) -> None:
        """stop() must end the subprocess (process-tree kill on Windows),
        not leave it running."""
        client = _client()
        client.start()
        proc = client._proc
        self.assertIsNotNone(proc)
        client.stop()
        # poll() is non-None once the OS has reaped the process.
        assert proc is not None
        proc.wait(timeout=5.0)
        self.assertIsNotNone(proc.poll())

    def test_crash_unblocks_next_request(self) -> None:
        """If the server dies, the next request fails fast (poll-first
        guard) instead of blocking until the timeout."""
        client = _client()
        try:
            client.start()
            assert client._proc is not None
            client._proc.kill()
            client._proc.wait(timeout=5.0)
            with self.assertRaises(LspError):
                with patch(
                    "app.application.tool_providers.lsp_client."
                    "DEFAULT_REQUEST_TIMEOUT",
                    2.0,
                ):
                    client.definition(FAKE_URI, 0, 0)
        finally:
            client.stop()


class ReadOnlyTest(unittest.TestCase):
    def test_client_exposes_no_mutating_operations(self) -> None:
        """Read-only by construction: the client must not offer any method
        that asks a server to mutate (didChange, rename, formatting, ...)."""
        for forbidden in (
            "did_change",
            "did_save",
            "will_save",
            "rename",
            "formatting",
            "code_action",
            "execute_command",
        ):
            self.assertFalse(
                hasattr(LspClient, forbidden),
                f"LspClient must not expose {forbidden!r}",
            )


if __name__ == "__main__":
    unittest.main()
