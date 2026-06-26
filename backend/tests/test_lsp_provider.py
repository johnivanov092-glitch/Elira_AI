"""Tests for the LSP ToolProvider (read-only facts → agent tool calls).

Covers the provider contract D2 requires: disabled-by-default (no live
server ⇒ no schemas), the three static read-only schemas, the
never-raises dispatch invariant, sandbox enforcement (a path outside the
project root errors *before* any server talk), result limits + provenance
(`meta.lsp_server` / `meta.truncated` / `meta.total_results`), and the
"still indexing, retry" path when the server pushes no diagnostics.

Provider-driving tests stand up a *real* fake language server subprocess
and register it in `lsp_runtime`'s live-client map — the provider picks
its client from there and reads the sandbox root from `client._cwd`, so
the client MUST be started with ``cwd=<temp project dir>``.

Windows lifecycle note: a live subprocess whose working directory is the
temp project dir holds an OS lock on it, so the dir cannot be deleted
while the server runs. Hence the temp dir is NOT a ``with`` block inside
each test (which would tear down *before* the server is stopped) — it is
created in ``setUp`` and removed in ``tearDown`` *after*
``stop_all_servers()``, with a short retry loop because Windows can lag in
releasing the handle.

Pure unittest.TestCase, mirroring test_lsp_client / test_mcp_provider.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_providers import lsp_runtime  # noqa: E402
from app.application.tool_providers.lsp_client import LspClient  # noqa: E402
from app.application.tool_providers.lsp_provider import (  # noqa: E402
    LspToolProvider,
    _RESULT_LIMIT,
    _TOOL_NAMES,
    build_lsp_providers,
)


FAKE_SERVER = Path(__file__).parent / "_lsp_fake_server.py"


def _rmtree_with_retry(path: str, *, attempts: int = 10, delay: float = 0.1) -> None:
    """Delete a temp dir that may still be transiently locked on Windows.

    The live LSP subprocess held this dir as its cwd; even after we ask it
    to stop, Windows can keep the directory handle briefly. Retry a few
    times, then fall back to ``ignore_errors`` so a lingering lock never
    fails an otherwise-passing test."""
    for _ in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except (PermissionError, OSError):
            time.sleep(delay)
    shutil.rmtree(path, ignore_errors=True)


class _LspProviderTestBase(unittest.TestCase):
    """Owns the temp project dir + live-server lifecycle.

    ``setUp`` stops any stragglers and makes a fresh temp project dir;
    ``tearDown`` stops all servers FIRST (releasing the cwd lock), then
    removes the dir with a retry. Tests register fake servers against
    ``self.proj`` and never manage temp dirs themselves."""

    def setUp(self) -> None:
        lsp_runtime.stop_all_servers()
        self.proj = tempfile.mkdtemp(prefix="lsp_prov_test_")

    def tearDown(self) -> None:
        lsp_runtime.stop_all_servers()
        _rmtree_with_retry(self.proj)

    def _register_fake_server(
        self,
        *,
        server_id: str = "fake",
        language: str = "python",
        big_references: bool = False,
        no_diagnostics: bool = False,
    ) -> LspClient:
        """Start the fake LSP server with ``cwd=self.proj`` and register it
        in lsp_runtime's live-client map, exactly as `start_server` would.
        The provider reads its sandbox root from ``client._cwd``."""
        env = dict(os.environ)
        if big_references:
            env["FAKE_LSP_BIG_REFERENCES"] = "1"
        if no_diagnostics:
            env["FAKE_LSP_NO_DIAGNOSTICS"] = "1"
        client = LspClient(
            language=language,
            command=sys.executable,
            args=[str(FAKE_SERVER)],
            env=env,
            cwd=self.proj,
        )
        client.start()
        lsp_runtime._LIVE_CLIENTS[server_id] = client
        return client

    def _write_source(self, name: str = "mod.py") -> str:
        """Create a real source file under the project root so the
        provider's ``target.is_file()`` sandbox check passes. Returns the
        relative path."""
        (Path(self.proj) / name).write_text("x = 1\n", encoding="utf-8")
        return name


class DisabledByDefaultTest(unittest.TestCase):
    """No live server ⇒ provider is invisible to the agent."""

    def setUp(self) -> None:
        lsp_runtime.stop_all_servers()

    def test_no_live_server_means_not_enabled(self) -> None:
        provider = LspToolProvider()
        self.assertFalse(provider.is_enabled())

    def test_no_live_server_means_zero_schemas(self) -> None:
        provider = LspToolProvider()
        self.assertEqual(provider.get_schemas(), [])

    def test_build_returns_empty_when_no_server(self) -> None:
        self.assertEqual(build_lsp_providers(), [])


class SchemaTest(_LspProviderTestBase):
    """The schema surface is static and exactly the three read-only tools."""

    def test_schemas_are_exactly_three_lsp_tools(self) -> None:
        self._register_fake_server()
        provider = LspToolProvider()
        schemas = provider.get_schemas()
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(schemas), 3)
        self.assertEqual(names, {"lsp_diagnostics", "lsp_definition", "lsp_references"})
        self.assertEqual(names, _TOOL_NAMES)

    def test_schema_descriptions_carry_lsp_provenance(self) -> None:
        self._register_fake_server()
        provider = LspToolProvider()
        for schema in provider.get_schemas():
            self.assertTrue(schema["function"]["description"].startswith("[lsp]"))

    def test_owns_only_the_three_tools(self) -> None:
        provider = LspToolProvider()
        self.assertTrue(provider.owns("lsp_diagnostics"))
        self.assertTrue(provider.owns("lsp_definition"))
        self.assertTrue(provider.owns("lsp_references"))
        self.assertFalse(provider.owns("tool_grep"))
        self.assertFalse(provider.owns("lsp_rename"))

    def test_build_returns_single_provider_when_live(self) -> None:
        self._register_fake_server()
        providers = build_lsp_providers()
        self.assertEqual(len(providers), 1)
        self.assertIsInstance(providers[0], LspToolProvider)


class DispatchSuccessTest(_LspProviderTestBase):
    """Happy-path dispatch returns rendered text + provenance meta."""

    def test_diagnostics_returns_text_and_provenance(self) -> None:
        self._register_fake_server(server_id="fake")
        rel = self._write_source()
        provider = LspToolProvider()
        res = provider.dispatch("lsp_diagnostics", {"path": rel})
        self.assertIn("text", res)
        self.assertNotIn("ERROR", res["text"])
        # Two diagnostics pushed by the fake (one error, one warning).
        self.assertEqual(res["meta"]["lsp_server"], "fake")
        self.assertEqual(res["meta"]["total_results"], 2)
        self.assertFalse(res["meta"]["truncated"])

    def test_definition_returns_a_location(self) -> None:
        self._register_fake_server()
        rel = self._write_source()
        provider = LspToolProvider()
        res = provider.dispatch(
            "lsp_definition", {"path": rel, "line": 0, "character": 0}
        )
        self.assertNotIn("ERROR", res["text"])
        self.assertEqual(res["meta"]["total_results"], 1)
        self.assertFalse(res["meta"]["truncated"])

    def test_references_returns_locations(self) -> None:
        self._register_fake_server()
        rel = self._write_source()
        provider = LspToolProvider()
        res = provider.dispatch(
            "lsp_references", {"path": rel, "line": 0, "character": 0}
        )
        self.assertNotIn("ERROR", res["text"])
        self.assertEqual(res["meta"]["total_results"], 2)
        self.assertFalse(res["meta"]["truncated"])


class ResultLimitTest(_LspProviderTestBase):
    """A flood of locations is capped, and the cap is surfaced in meta."""

    def test_references_are_capped_with_truncation_meta(self) -> None:
        self._register_fake_server(big_references=True)
        rel = self._write_source()
        provider = LspToolProvider()
        res = provider.dispatch(
            "lsp_references", {"path": rel, "line": 0, "character": 0}
        )
        # The fake returns 120; the provider caps the rendered list at 50
        # but reports the true total and flags truncation.
        self.assertEqual(res["meta"]["total_results"], 120)
        self.assertTrue(res["meta"]["truncated"])
        # _RESULT_LIMIT location lines, then the "[... N more results omitted]"
        # line. Count the rendered location lines explicitly rather than
        # guessing from a raw newline count.
        omitted_lines = [
            ln for ln in res["text"].splitlines() if "omitted" in ln
        ]
        location_lines = [
            ln
            for ln in res["text"].splitlines()
            if ln.strip() and "omitted" not in ln
        ]
        self.assertEqual(len(location_lines), _RESULT_LIMIT)
        self.assertEqual(len(omitted_lines), 1)
        self.assertIn("omitted", res["text"])


class RetryPathTest(_LspProviderTestBase):
    """No diagnostics push within settle ⇒ "still indexing, retry"."""

    def test_diagnostics_not_ready_returns_retry_note(self) -> None:
        self._register_fake_server(server_id="fake", no_diagnostics=True)
        rel = self._write_source()
        provider = LspToolProvider()
        res = provider.dispatch("lsp_diagnostics", {"path": rel})
        self.assertIn("retry", res["text"].lower())
        self.assertEqual(res["meta"]["lsp_server"], "fake")
        self.assertEqual(res["meta"]["note"], "not yet ready, retry")
        self.assertFalse(res["meta"]["truncated"])


class DispatchNeverRaisesTest(_LspProviderTestBase):
    """Every failure path returns {"text": "ERROR: ..."}, never an exception."""

    def test_no_running_server_is_clean_error(self) -> None:
        # No fake server registered for this one — stop the base-class
        # straggler check left nothing live.
        lsp_runtime.stop_all_servers()
        provider = LspToolProvider()
        res = provider.dispatch("lsp_diagnostics", {"path": "x.py"})
        self.assertTrue(res["text"].startswith("ERROR"))

    def test_missing_path_is_clean_error(self) -> None:
        self._register_fake_server()
        provider = LspToolProvider()
        res = provider.dispatch("lsp_diagnostics", {})
        self.assertTrue(res["text"].startswith("ERROR"))
        self.assertIn("path", res["text"])

    def test_path_outside_project_root_is_rejected(self) -> None:
        """The sandbox must reject traversal out of the project root with an
        ERROR, without ever opening the file or hitting the server."""
        self._register_fake_server()
        provider = LspToolProvider()
        res = provider.dispatch(
            "lsp_diagnostics", {"path": "../../../etc/passwd"}
        )
        self.assertTrue(res["text"].startswith("ERROR"))

    def test_nonexistent_file_is_clean_error(self) -> None:
        self._register_fake_server()
        provider = LspToolProvider()
        res = provider.dispatch(
            "lsp_diagnostics", {"path": "does_not_exist.py"}
        )
        self.assertTrue(res["text"].startswith("ERROR"))

    def test_non_integer_position_is_clean_error(self) -> None:
        self._register_fake_server()
        rel = self._write_source()
        provider = LspToolProvider()
        res = provider.dispatch(
            "lsp_definition", {"path": rel, "line": "x", "character": 0}
        )
        self.assertTrue(res["text"].startswith("ERROR"))

    def test_unknown_language_is_clean_error(self) -> None:
        self._register_fake_server(language="python")
        rel = self._write_source()
        provider = LspToolProvider()
        res = provider.dispatch(
            "lsp_diagnostics", {"path": rel, "language": "haskell"}
        )
        self.assertTrue(res["text"].startswith("ERROR"))


if __name__ == "__main__":
    unittest.main()
