"""Tests — Instruction loader (P3 Шаг 7).

Verifies:
1. Files are loaded in order: global → project → local.
2. Missing files are silently skipped.
3. Per-file (4 000 char) and total (12 000 char) limits are enforced.
4. Duplicate content (same SHA-256) is loaded only once.
5. _build_system_prompt uses the loader (no dead _read_project_prompt).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.instructions.loader import (  # noqa: E402
    _FILE_CHAR_LIMIT,
    _TOTAL_CHAR_LIMIT,
    load_instructions,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestLoadInstructions(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "project"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _global_path(self) -> Path:
        return Path.home() / ".elira" / "agent.md"

    # ── Basic loading ────────────────────────────────────────────────────────

    def test_empty_when_no_files(self):
        with mock.patch("app.application.instructions.loader.Path.home",
                        return_value=Path(self._tmp.name) / "fake_home"):
            result = load_instructions(self.root)
        self.assertEqual(result, "")

    def test_project_file_only(self):
        _write(self.root / ".elira" / "agent.md", "Project rules here.")
        with mock.patch("app.application.instructions.loader.Path.home",
                        return_value=Path(self._tmp.name) / "no_home"):
            result = load_instructions(self.root)
        self.assertEqual(result, "Project rules here.")

    def test_local_file_appended_after_project(self):
        _write(self.root / ".elira" / "agent.md", "Project.")
        _write(self.root / ".elira" / "agent.local.md", "Local override.")
        with mock.patch("app.application.instructions.loader.Path.home",
                        return_value=Path(self._tmp.name) / "no_home"):
            result = load_instructions(self.root)
        self.assertIn("Project.", result)
        self.assertIn("Local override.", result)
        self.assertLess(result.index("Project."), result.index("Local override."))

    def test_global_file_comes_first(self):
        fake_home = Path(self._tmp.name) / "home"
        _write(fake_home / ".elira" / "agent.md", "Global rules.")
        _write(self.root / ".elira" / "agent.md", "Project rules.")
        with mock.patch("app.application.instructions.loader.Path.home",
                        return_value=fake_home):
            result = load_instructions(self.root)
        self.assertLess(result.index("Global rules."), result.index("Project rules."))

    # ── Limits ──────────────────────────────────────────────────────────────

    def test_file_truncated_to_4000_chars(self):
        big = "X" * (_FILE_CHAR_LIMIT + 500)
        _write(self.root / ".elira" / "agent.md", big)
        with mock.patch("app.application.instructions.loader.Path.home",
                        return_value=Path(self._tmp.name) / "no_home"):
            result = load_instructions(self.root)
        self.assertEqual(len(result), _FILE_CHAR_LIMIT)

    def test_total_capped_at_12000_chars(self):
        fake_home = Path(self._tmp.name) / "home2"
        # Each file just at the per-file limit → 3 × 4000 = 12000 ≤ 12000 OK
        _write(fake_home / ".elira" / "agent.md",          "A" * _FILE_CHAR_LIMIT)
        _write(self.root / ".elira" / "agent.md",           "B" * _FILE_CHAR_LIMIT)
        _write(self.root / ".elira" / "agent.local.md",     "C" * _FILE_CHAR_LIMIT)
        with mock.patch("app.application.instructions.loader.Path.home",
                        return_value=fake_home):
            result = load_instructions(self.root)
        # Separators ("\n\n") add 4 chars, content is 3×4000 = 12000 → total may be
        # slightly over due to separators, but content chars ≤ _TOTAL_CHAR_LIMIT
        content_chars = len(result.replace("\n\n", ""))
        self.assertLessEqual(content_chars, _TOTAL_CHAR_LIMIT)

    def test_total_limit_stops_loading_extra_files(self):
        """With a tight total cap, the third file is skipped when budget is full."""
        fake_home = Path(self._tmp.name) / "home3"
        # Use a small total cap (300) equal to 2 × file cap (150) so that
        # global + project exactly fill the budget and local is skipped.
        _write(fake_home / ".elira" / "agent.md",        "G" * 150)
        _write(self.root / ".elira" / "agent.md",         "P" * 150)
        _write(self.root / ".elira" / "agent.local.md",   "LOCAL_MARKER")

        import app.application.instructions.loader as _loader
        with mock.patch.object(_loader, "_FILE_CHAR_LIMIT", 150), \
             mock.patch.object(_loader, "_TOTAL_CHAR_LIMIT", 300), \
             mock.patch("app.application.instructions.loader.Path.home",
                        return_value=fake_home):
            result = load_instructions(self.root)
        # global (150) + project (150) = 300 ≥ total cap → local skipped
        self.assertNotIn("LOCAL_MARKER", result)
        self.assertIn("G" * 150, result)
        self.assertIn("P" * 150, result)

    # ── Deduplication ────────────────────────────────────────────────────────

    def test_identical_files_deduplicated(self):
        same_text = "Identical instructions for both scopes."
        fake_home = Path(self._tmp.name) / "home4"
        _write(fake_home / ".elira" / "agent.md",  same_text)
        _write(self.root / ".elira" / "agent.md",   same_text)
        with mock.patch("app.application.instructions.loader.Path.home",
                        return_value=fake_home):
            result = load_instructions(self.root)
        # Should appear exactly once
        self.assertEqual(result.count(same_text), 1)

    def test_different_files_not_deduplicated(self):
        fake_home = Path(self._tmp.name) / "home5"
        _write(fake_home / ".elira" / "agent.md",  "Alpha rules.")
        _write(self.root / ".elira" / "agent.md",   "Beta rules.")
        with mock.patch("app.application.instructions.loader.Path.home",
                        return_value=fake_home):
            result = load_instructions(self.root)
        self.assertIn("Alpha rules.", result)
        self.assertIn("Beta rules.", result)


class TestBuildSystemPromptUsesLoader(unittest.TestCase):
    """_build_system_prompt should delegate to load_instructions, not have
    its own dead _read_project_prompt path."""

    def test_no_read_project_prompt_function(self):
        """The old _read_project_prompt helper must not exist as a separate function."""
        import app.application.code_agent.agent_loop as loop_mod
        self.assertFalse(
            hasattr(loop_mod, "_read_project_prompt"),
            "_read_project_prompt should have been removed; loader.load_instructions is the canonical path",
        )

    def test_build_system_prompt_includes_project_instructions(self):
        from app.application.code_agent.agent_loop import _build_system_prompt

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".elira").mkdir()
            (root / ".elira" / "agent.md").write_text("TEST_INSTRUCTION_MARKER", encoding="utf-8")
            with mock.patch("app.application.instructions.loader.Path.home",
                            return_value=Path(tmp) / "no_home"):
                prompt = _build_system_prompt(root)
        self.assertIn("TEST_INSTRUCTION_MARKER", prompt)

    def test_build_system_prompt_no_instructions_returns_base(self):
        from app.application.code_agent.agent_loop import _build_system_prompt

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("app.application.instructions.loader.Path.home",
                            return_value=Path(tmp) / "no_home"):
                prompt = _build_system_prompt(root)
        self.assertIn("Elira", prompt)  # base prompt always present
        self.assertNotIn("Instructions", prompt)


if __name__ == "__main__":
    unittest.main()
