"""Tests for pure helpers across two modules.

  core/files.py
    — should_include_project_file
    — format_chat_search_results
    — build_uploaded_signature
    — _chat_folder_path

  application/workflows/execution.py
    — workflow_duration_ms

All functions are pure or depend only on module-level path constants
(no DB writes, no HTTP, FS reads only for should_include_project_file which
just inspects Path attributes without opening the file).
"""
from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.files import (  # noqa: E402
    should_include_project_file,
    format_chat_search_results,
    build_uploaded_signature,
    _chat_folder_path,
    CHAT_DIR,
)
from app.application.workflows.execution import (  # noqa: E402
    workflow_duration_ms,
)


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — should_include_project_file
# ─────────────────────────────────────────────────────────────────────────────

class ShouldIncludeProjectFileTest(unittest.TestCase):
    """Tests for ``should_include_project_file(path: Path) -> bool``."""

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_bool(self) -> None:
        self.assertIsInstance(should_include_project_file(Path("src/main.py")), bool)

    # ── allowed suffixes ──────────────────────────────────────────────────────

    def test_py_file_included(self) -> None:
        self.assertTrue(should_include_project_file(Path("src/module.py")))

    def test_md_file_included(self) -> None:
        self.assertTrue(should_include_project_file(Path("README.md")))

    def test_json_file_included(self) -> None:
        self.assertTrue(should_include_project_file(Path("config.json")))

    def test_ts_file_included(self) -> None:
        self.assertTrue(should_include_project_file(Path("src/app.ts")))

    def test_yaml_file_included(self) -> None:
        self.assertTrue(should_include_project_file(Path("config.yaml")))

    def test_sql_file_included(self) -> None:
        self.assertTrue(should_include_project_file(Path("schema.sql")))

    # ── blocked by suffix ─────────────────────────────────────────────────────

    def test_exe_file_excluded(self) -> None:
        self.assertFalse(should_include_project_file(Path("app.exe")))

    def test_png_file_excluded(self) -> None:
        self.assertFalse(should_include_project_file(Path("logo.png")))

    def test_zip_file_excluded(self) -> None:
        self.assertFalse(should_include_project_file(Path("archive.zip")))

    # ── blocked by directory part ─────────────────────────────────────────────

    def test_git_dir_excluded(self) -> None:
        self.assertFalse(should_include_project_file(Path(".git") / "config"))

    def test_node_modules_excluded(self) -> None:
        self.assertFalse(should_include_project_file(Path("node_modules") / "pkg" / "index.js"))

    def test_pycache_excluded(self) -> None:
        self.assertFalse(should_include_project_file(Path("src") / "__pycache__" / "main.cpython-311.pyc"))

    def test_venv_excluded(self) -> None:
        self.assertFalse(should_include_project_file(Path(".venv") / "lib" / "site-packages" / "util.py"))


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — format_chat_search_results
# ─────────────────────────────────────────────────────────────────────────────

class FormatChatSearchResultsTest(unittest.TestCase):
    """Tests for ``format_chat_search_results(results: list) -> str``."""

    def _one_result(self) -> list:
        return [{"file": "test.md", "score": 0.9, "content": "hello world"}]

    def _two_results(self) -> list:
        return [
            {"file": "a.md", "score": 0.95, "content": "first match"},
            {"file": "b.py", "score": 0.7, "content": "second match"},
        ]

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_string(self) -> None:
        self.assertIsInstance(format_chat_search_results(self._one_result()), str)

    def test_empty_list_returns_empty_string(self) -> None:
        self.assertEqual(format_chat_search_results([]), "")

    # ── content ───────────────────────────────────────────────────────────────

    def test_filename_in_output(self) -> None:
        result = format_chat_search_results(self._one_result())
        self.assertIn("test.md", result)

    def test_score_in_output(self) -> None:
        result = format_chat_search_results(self._one_result())
        self.assertIn("0.9", result)

    def test_content_in_output(self) -> None:
        result = format_chat_search_results(self._one_result())
        self.assertIn("hello world", result)

    def test_two_results_separated(self) -> None:
        result = format_chat_search_results(self._two_results())
        self.assertIn("a.md", result)
        self.assertIn("b.py", result)

    def test_two_results_nonempty(self) -> None:
        result = format_chat_search_results(self._two_results())
        self.assertGreater(len(result), 0)


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — build_uploaded_signature
# ─────────────────────────────────────────────────────────────────────────────

class BuildUploadedSignatureTest(unittest.TestCase):
    """Tests for ``build_uploaded_signature(files) -> str``."""

    def _mock_file(self, name: str, size: int = 100):
        """Return a simple namespace that mimics an uploaded file object."""
        f = types.SimpleNamespace()
        f.name = name
        f.size = size
        return f

    # ── edge cases ────────────────────────────────────────────────────────────

    def test_empty_list_returns_empty_string(self) -> None:
        self.assertEqual(build_uploaded_signature([]), "")

    def test_none_returns_empty_string(self) -> None:
        self.assertEqual(build_uploaded_signature(None), "")

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_string(self) -> None:
        self.assertIsInstance(build_uploaded_signature([self._mock_file("a.py")]), str)

    # ── content ───────────────────────────────────────────────────────────────

    def test_single_file_contains_name(self) -> None:
        result = build_uploaded_signature([self._mock_file("report.pdf", 5000)])
        self.assertIn("report.pdf", result)

    def test_single_file_contains_size(self) -> None:
        result = build_uploaded_signature([self._mock_file("report.pdf", 5000)])
        self.assertIn("5000", result)

    def test_two_files_separated_by_pipe(self) -> None:
        files = [self._mock_file("a.py", 100), self._mock_file("b.md", 200)]
        result = build_uploaded_signature(files)
        self.assertIn("|", result)

    def test_two_files_both_names_present(self) -> None:
        files = [self._mock_file("a.py", 100), self._mock_file("b.md", 200)]
        result = build_uploaded_signature(files)
        self.assertIn("a.py", result)
        self.assertIn("b.md", result)

    def test_file_without_size_attr_uses_zero(self) -> None:
        f = types.SimpleNamespace()
        f.name = "no_size.txt"
        result = build_uploaded_signature([f])
        self.assertIn("no_size.txt", result)
        self.assertIn("0", result)


# ─────────────────────────────────────────────────────────────────────────────
# core/files.py — _chat_folder_path
# ─────────────────────────────────────────────────────────────────────────────

class ChatFolderPathTest(unittest.TestCase):
    """Tests for ``_chat_folder_path(folder: str | None) -> Path``."""

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_path(self) -> None:
        self.assertIsInstance(_chat_folder_path("My Folder"), Path)

    # ── default/special values return CHAT_DIR ────────────────────────────────

    def test_none_returns_chat_dir(self) -> None:
        self.assertEqual(_chat_folder_path(None), CHAT_DIR)

    def test_empty_string_returns_chat_dir(self) -> None:
        self.assertEqual(_chat_folder_path(""), CHAT_DIR)

    def test_default_keyword_returns_chat_dir(self) -> None:
        self.assertEqual(_chat_folder_path("default"), CHAT_DIR)

    # ── non-default folder is child of CHAT_DIR ───────────────────────────────

    def test_custom_folder_is_child_of_chat_dir(self) -> None:
        result = _chat_folder_path("My Chats")
        self.assertEqual(result.parent, CHAT_DIR)

    def test_folder_with_slashes_sanitized(self) -> None:
        result = _chat_folder_path("test/subfolder")
        # Slashes are replaced in sanitize_chat_name
        self.assertNotIn("/", result.name)

    def test_folder_with_colons_sanitized(self) -> None:
        result = _chat_folder_path("name:with:colons")
        self.assertNotIn(":", result.name)

    def test_different_folders_give_different_paths(self) -> None:
        p1 = _chat_folder_path("FolderA")
        p2 = _chat_folder_path("FolderB")
        self.assertNotEqual(p1, p2)


# ─────────────────────────────────────────────────────────────────────────────
# application/workflows/execution.py — workflow_duration_ms
# ─────────────────────────────────────────────────────────────────────────────

class WorkflowDurationMsTest(unittest.TestCase):
    """Tests for ``workflow_duration_ms(run: dict) -> int``."""

    # ── return type ───────────────────────────────────────────────────────────

    def test_returns_int(self) -> None:
        self.assertIsInstance(workflow_duration_ms({"started_at": ""}), int)

    # ── invalid / missing started_at returns 0 ────────────────────────────────

    def test_empty_started_at_returns_zero(self) -> None:
        self.assertEqual(workflow_duration_ms({"started_at": ""}), 0)

    def test_none_started_at_returns_zero(self) -> None:
        self.assertEqual(workflow_duration_ms({"started_at": None}), 0)

    def test_missing_started_at_returns_zero(self) -> None:
        self.assertEqual(workflow_duration_ms({}), 0)

    def test_invalid_date_string_returns_zero(self) -> None:
        self.assertEqual(workflow_duration_ms({"started_at": "not-a-date"}), 0)

    # ── valid past timestamp returns non-negative int ─────────────────────────

    def test_past_timestamp_returns_nonnegative(self) -> None:
        result = workflow_duration_ms({"started_at": "2020-01-01T00:00:00+00:00"})
        self.assertGreaterEqual(result, 0)

    def test_past_timestamp_returns_positive(self) -> None:
        result = workflow_duration_ms({"started_at": "2020-01-01T00:00:00+00:00"})
        self.assertGreater(result, 0)

    def test_very_recent_timestamp_returns_nonneg(self) -> None:
        # 1 second in the past
        from datetime import datetime, timezone, timedelta
        ts = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        result = workflow_duration_ms({"started_at": ts})
        self.assertGreaterEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
