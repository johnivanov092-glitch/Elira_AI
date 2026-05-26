"""Tests for the per-project Python sandbox.

These exercise the real subprocess + venv path (no mocks) since the
whole point of the sandbox is to genuinely isolate execution. They're
slower than pure tests (~10-15 s for the first run, mostly venv
creation) but verify real behavior end-to-end.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class SandboxRuntimeTest(unittest.TestCase):
    """Live subprocess tests against the real `sandbox` module.

    setUp re-imports `sandbox` with `ELIRA_DATA_DIR` pointing at a
    temp dir so each test gets a fresh sandbox root that's cleaned
    up in tearDown.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name

        # Reload data_files so DATA_DIR picks up the new env var,
        # then reload sandbox so it captures the new DATA_DIR.
        import importlib
        from app.core import data_files
        importlib.reload(data_files)
        from app.application.code_agent import sandbox as sandbox_module
        importlib.reload(sandbox_module)
        self.sandbox = sandbox_module

        self.project_root = Path(self._tmp.name) / "fake_proj"
        self.project_root.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop("ELIRA_DATA_DIR", None)

    # ── status / reset ──────────────────────────────────────────

    def test_status_before_first_run_reports_missing(self) -> None:
        status = self.sandbox.sandbox_status(self.project_root)
        self.assertFalse(status["exists"])

    def test_reset_is_noop_when_sandbox_doesnt_exist(self) -> None:
        result = self.sandbox.reset_sandbox(self.project_root)
        self.assertTrue(result["ok"])
        self.assertFalse(result["existed"])

    # ── basic execution ────────────────────────────────────────

    def test_simple_print_captured(self) -> None:
        result = self.sandbox.run_in_sandbox(
            self.project_root,
            code="print('hello sandbox')",
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello sandbox", result["stdout"])

    def test_runtime_error_surfaces_in_stderr(self) -> None:
        result = self.sandbox.run_in_sandbox(
            self.project_root,
            code="raise ValueError('boom')",
        )
        self.assertFalse(result["ok"])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("ValueError", result["stderr"])
        self.assertIn("boom", result["stderr"])

    def test_syntax_error_caught(self) -> None:
        result = self.sandbox.run_in_sandbox(
            self.project_root,
            code="def broken(:",
        )
        self.assertFalse(result["ok"])
        self.assertIn("SyntaxError", result["stderr"])

    def test_empty_code_treated_as_no_op(self) -> None:
        # Empty script still runs (just exits 0), but a wrapper layer
        # should reject empty code earlier; here we verify the runtime
        # doesn't crash on it.
        result = self.sandbox.run_in_sandbox(self.project_root, code="")
        self.assertEqual(result["exit_code"], 0)

    # ── isolation ──────────────────────────────────────────────

    def test_cwd_is_work_dir_not_project_root(self) -> None:
        """Script's getcwd() must be inside the sandbox work/ dir,
        not the actual project root. Note the slug derives from the
        project basename so the sandbox path coincidentally contains
        the same word — what matters is the resolved path differs."""
        result = self.sandbox.run_in_sandbox(
            self.project_root,
            code="import os; print(os.getcwd())",
        )
        cwd = Path(result["stdout"].strip()).resolve()
        self.assertNotEqual(cwd, self.project_root.resolve())
        self.assertEqual(cwd.name, "work")
        # Sandbox dir is `<data>/sandbox/<slug>/work`, must be under DATA_DIR
        self.assertIn("sandbox", str(cwd))

    def test_writes_land_in_work_dir(self) -> None:
        result = self.sandbox.run_in_sandbox(
            self.project_root,
            code="open('out.txt', 'w').write('payload from sandbox')",
        )
        self.assertTrue(result["ok"])
        # The file must NOT appear under project_root
        self.assertFalse((self.project_root / "out.txt").exists())
        # It must appear under the sandbox work dir
        sandbox_path = Path(result["sandbox_path"])
        produced = sandbox_path / "work" / "out.txt"
        self.assertTrue(produced.exists())
        self.assertEqual(produced.read_text(encoding="utf-8"), "payload from sandbox")

    def test_persistence_between_runs(self) -> None:
        """A file written by run 1 must still be readable in run 2 of
        the same project — that's the whole point of the persistent
        sandbox."""
        r1 = self.sandbox.run_in_sandbox(
            self.project_root,
            code="open('state.txt', 'w').write('first run')",
        )
        self.assertTrue(r1["ok"])
        r2 = self.sandbox.run_in_sandbox(
            self.project_root,
            code="print(open('state.txt').read())",
        )
        self.assertTrue(r2["ok"])
        self.assertIn("first run", r2["stdout"])

    # ── timeout ────────────────────────────────────────────────

    def test_timeout_returns_error(self) -> None:
        result = self.sandbox.run_in_sandbox(
            self.project_root,
            code="import time; time.sleep(5)",
            timeout=1,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("timed out", result.get("error", "").lower())

    # ── reset ──────────────────────────────────────────────────

    def test_reset_after_run_wipes_state(self) -> None:
        # Set up some state
        self.sandbox.run_in_sandbox(
            self.project_root,
            code="open('marker.txt', 'w').write('x')",
        )
        sandbox_path = Path(self.sandbox._sandbox_dir(self.project_root))
        self.assertTrue(sandbox_path.exists())

        # Reset → directory disappears
        result = self.sandbox.reset_sandbox(self.project_root)
        self.assertTrue(result["ok"])
        self.assertTrue(result["existed"])
        self.assertFalse(sandbox_path.exists())

        # Next run rebuilds from scratch — marker file gone
        r = self.sandbox.run_in_sandbox(
            self.project_root,
            code="import os; print('exists' if os.path.exists('marker.txt') else 'gone')",
        )
        self.assertIn("gone", r["stdout"])

    # ── install ────────────────────────────────────────────────

    def test_install_blocks_shell_metas(self) -> None:
        """A malicious package spec with shell metacharacters must be
        silently dropped from the pip args."""
        # Build a fake-but-syntactically-fine package list containing
        # one safe entry and one shell-injection attempt. The runtime
        # should drop the bad one but NOT abort the whole call.
        result = self.sandbox.run_in_sandbox(
            self.project_root,
            code="print('ok')",
            install=["; rm -rf /"],   # only the bad one — everything dropped
        )
        # Since the only "package" was malicious, nothing got installed
        # but the script itself runs fine.
        self.assertTrue(result["ok"])
        self.assertIn("ok", result["stdout"])

    # ── output truncation ──────────────────────────────────────

    def test_huge_stdout_is_truncated(self) -> None:
        # Emit ~30K chars (cap is 16K in the module)
        result = self.sandbox.run_in_sandbox(
            self.project_root,
            code="print('A' * 30000)",
        )
        self.assertTrue(result["ok"])
        self.assertLess(len(result["stdout"]), 17000)
        self.assertIn("truncated", result["stdout"])


class SlugTest(unittest.TestCase):
    """`_slug` produces filesystem-safe keys from project basenames."""

    def setUp(self) -> None:
        from app.application.code_agent import sandbox
        self.slug = sandbox._slug

    def test_lowercase_ascii_kept(self) -> None:
        self.assertEqual(self.slug(Path("myproject")), "myproject")

    def test_uppercase_lowered(self) -> None:
        self.assertEqual(self.slug(Path("MyProject")), "myproject")

    def test_spaces_become_underscore(self) -> None:
        self.assertEqual(self.slug(Path("my project")), "my_project")

    def test_dots_become_underscore(self) -> None:
        self.assertEqual(self.slug(Path("my.project.v2")), "my_project_v2")

    def test_cyrillic_becomes_underscore(self) -> None:
        # Non-ASCII gets collapsed, result must be non-empty
        result = self.slug(Path("Проект"))
        self.assertEqual(result, "default")  # all non-ASCII → empty → "default"

    def test_empty_falls_back_to_default(self) -> None:
        self.assertEqual(self.slug(Path("")), "default")
        self.assertEqual(self.slug(Path("___")), "default")


if __name__ == "__main__":
    unittest.main()
