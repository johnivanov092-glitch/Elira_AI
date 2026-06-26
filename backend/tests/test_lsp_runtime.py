"""Tests for LSP server configuration + lifecycle (lsp_runtime).

Disabled-by-default is the headline invariant: no config file ⇒ no
servers ⇒ provider gives zero schemas. We also cover the opt-in gate
(start refuses a disabled / not-configured spec), config round-trips
(save/list/stop/restart), and isolation (no ``lsp_servers.json`` leaks
into the project's real ``data/`` — everything goes through a temp
``ELIRA_DATA_DIR``).

Isolation mirrors the projects-registry tests: set ``ELIRA_DATA_DIR``
to a TemporaryDirectory, then ``importlib.reload`` both ``data_files``
(it caches ``DATA_DIR`` at import) and ``lsp_runtime`` (it caches
``CONFIG_PATH`` at import). Pure unittest.TestCase, no real subprocess —
``start_server`` here only ever hits the "disabled"/"not configured"
guards, which return before any spawn.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _fresh_runtime(tmp_data: str):
    """Reload lsp_runtime bound to a temp data dir (clean module state)."""
    os.environ["ELIRA_DATA_DIR"] = tmp_data
    from app.core import data_files
    importlib.reload(data_files)
    from app.application.tool_providers import lsp_runtime
    importlib.reload(lsp_runtime)
    return lsp_runtime


def _spec(sid: str = "pyright", *, enabled: bool = False, **over) -> dict:
    base = {
        "id": sid,
        "language": "python",
        "command": "pyright-langserver",
        "args": ["--stdio"],
        "enabled": enabled,
    }
    base.update(over)
    return base


class DisabledByDefaultTest(unittest.TestCase):
    def test_no_config_file_means_no_servers(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                self.assertEqual(rt.list_servers(), [])
                self.assertEqual(rt.live_clients(), {})
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)

    def test_no_config_file_is_not_written_on_read(self) -> None:
        """Reading config must never create the file (so a fresh install
        with the LSP subsystem untouched leaves no artifact behind)."""
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                rt.list_servers()
                self.assertFalse(rt.CONFIG_PATH.exists())
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)

    def test_enabled_defaults_to_false(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                # A spec saved without an explicit `enabled` must come back
                # disabled — LSP is opt-in.
                rt.save_servers([{
                    "id": "p", "language": "python",
                    "command": "pyright-langserver", "args": [],
                }])
                servers = rt.list_servers()
                self.assertEqual(len(servers), 1)
                self.assertFalse(servers[0]["enabled"])
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)


class StartGuardsTest(unittest.TestCase):
    def test_start_not_configured_returns_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                res = rt.start_server("ghost")
                self.assertFalse(res["ok"])
                self.assertIn("not configured", res["error"])
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)

    def test_start_disabled_server_returns_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                rt.save_servers([_spec("pyright", enabled=False)])
                res = rt.start_server("pyright")
                self.assertFalse(res["ok"])
                self.assertIn("disabled", res["error"])
                # A refused start must not register a live client.
                self.assertEqual(rt.live_clients(), {})
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)

    def test_start_never_raises_on_bad_command(self) -> None:
        """Even an enabled spec with a bogus command must not raise — the
        spawn failure is captured, not propagated, so the agent keeps
        working on grep."""
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as proj:
            rt = _fresh_runtime(data)
            try:
                rt.save_servers([_spec(
                    "broken", enabled=True,
                    command="nonexistent_binary_12345xyz", args=[],
                )])
                res = rt.start_server("broken", root_path=proj)
                self.assertFalse(res["ok"])
                self.assertIn("error", res)
                # Status surfaces the failure for the UI.
                servers = {s["id"]: s for s in rt.list_servers()}
                self.assertEqual(servers["broken"]["status"], "error")
                self.assertTrue(servers["broken"]["last_error"])
                self.assertEqual(rt.live_clients(), {})
            finally:
                rt.stop_all_servers()
                os.environ.pop("ELIRA_DATA_DIR", None)


class ConfigRoundtripTest(unittest.TestCase):
    def test_save_then_list_preserves_fields(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                rt.save_servers([_spec("ts", language="typescript",
                                       command="typescript-language-server",
                                       args=["--stdio"], enabled=True)])
                servers = rt.list_servers()
                self.assertEqual(len(servers), 1)
                s = servers[0]
                self.assertEqual(s["id"], "ts")
                self.assertEqual(s["language"], "typescript")
                self.assertEqual(s["command"], "typescript-language-server")
                self.assertEqual(s["args"], ["--stdio"])
                self.assertTrue(s["enabled"])
                self.assertEqual(s["status"], "stopped")
                self.assertIsNone(s["last_error"])
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)

    def test_save_drops_malformed_and_duplicate_specs(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                rt.save_servers([
                    _spec("dup"),
                    _spec("dup", command="other"),     # dup id → dropped
                    {"id": "", "language": "x", "command": "y"},  # blank id
                    {"id": "noLang", "command": "y"},   # missing language
                    {"id": "noCmd", "language": "x"},   # missing command
                    "not-a-dict",                        # wrong type
                ])
                ids = [s["id"] for s in rt.list_servers()]
                self.assertEqual(ids, ["dup"])
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)

    def test_save_replaces_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                rt.save_servers([_spec("a"), _spec("b")])
                self.assertEqual({s["id"] for s in rt.list_servers()}, {"a", "b"})
                rt.save_servers([_spec("c")])  # full replace, not merge
                self.assertEqual([s["id"] for s in rt.list_servers()], ["c"])
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)


class StopRestartTest(unittest.TestCase):
    def test_stop_unconfigured_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                res = rt.stop_server("ghost")
                self.assertTrue(res["ok"])
                self.assertFalse(res["was_running"])
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)

    def test_restart_disabled_returns_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                rt.save_servers([_spec("pyright", enabled=False)])
                res = rt.restart_server("pyright")
                self.assertFalse(res["ok"])  # stop ok, but start refuses disabled
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)

    def test_get_live_client_none_when_nothing_running(self) -> None:
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                rt.save_servers([_spec("pyright", enabled=True)])
                self.assertIsNone(rt.get_live_client("pyright"))
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)


class IsolationTest(unittest.TestCase):
    def test_config_writes_land_in_temp_data_dir_only(self) -> None:
        """The whole suite must never touch the project's real data/ —
        save writes lsp_servers.json under the temp ELIRA_DATA_DIR."""
        with tempfile.TemporaryDirectory() as data:
            rt = _fresh_runtime(data)
            try:
                rt.save_servers([_spec("pyright", enabled=True)])
                written = Path(data) / "lsp_servers.json"
                self.assertTrue(written.exists())
                self.assertEqual(rt.CONFIG_PATH, written)
            finally:
                os.environ.pop("ELIRA_DATA_DIR", None)


if __name__ == "__main__":
    unittest.main()
