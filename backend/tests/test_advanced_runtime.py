"""Tests for advanced/runtime:
  BLOCKED_DIRS, TEXT_EXTS constants; open_project,
    get_project_info, project_tree, read_project_file, search_in_project,
    close_project (global _project_path state, tested with tempfile)
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.advanced.runtime as adv  # noqa: E402


def _normalize(text: str) -> str:
    return (text or "").lower().strip()


def _qhash(norm: str, model: str, profile: str) -> str:
    import hashlib
    return hashlib.sha256(f"{norm}|{model}|{profile}".encode()).hexdigest()


_LONG_QUERY = "explain the theory of relativity in simple terms please"
_LONG_RESP = "A" * 100  # >= 20 chars, no error prefix


# ---
# advanced/runtime - BLOCKED_DIRS / TEXT_EXTS constants
# ---

class AdvancedConstantsTest(unittest.TestCase):
    def test_blocked_dirs_is_set(self) -> None:
        self.assertIsInstance(adv.BLOCKED_DIRS, (set, frozenset))

    def test_blocked_dirs_contains_git(self) -> None:
        self.assertIn(".git", adv.BLOCKED_DIRS)

    def test_blocked_dirs_contains_node_modules(self) -> None:
        self.assertIn("node_modules", adv.BLOCKED_DIRS)

    def test_text_exts_is_set(self) -> None:
        self.assertIsInstance(adv.TEXT_EXTS, (set, frozenset))

    def test_text_exts_contains_py(self) -> None:
        self.assertIn(".py", adv.TEXT_EXTS)

    def test_text_exts_contains_js(self) -> None:
        self.assertIn(".js", adv.TEXT_EXTS)


# ---
# advanced/runtime - open_project / get_project_info / close_project
# ---

class OpenCloseProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        adv.close_project()  # ensure clean state
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        adv.close_project()
        self._tmpdir.cleanup()

    def test_open_valid_dir_ok_true(self) -> None:
        result = adv.open_project(self._tmpdir.name)
        self.assertTrue(result["ok"])

    def test_open_stores_path(self) -> None:
        adv.open_project(self._tmpdir.name)
        info = adv.get_project_info()
        self.assertTrue(info["ok"])

    def test_open_nonexistent_ok_false(self) -> None:
        result = adv.open_project("/nonexistent/dir/xyz")
        self.assertFalse(result["ok"])

    def test_open_nonexistent_has_error(self) -> None:
        result = adv.open_project("/nonexistent/dir/xyz")
        self.assertIn("error", result)

    def test_get_info_no_project_open(self) -> None:
        result = adv.get_project_info()
        self.assertFalse(result["ok"])

    def test_get_info_after_open_has_name(self) -> None:
        adv.open_project(self._tmpdir.name)
        info = adv.get_project_info()
        self.assertIn("name", info)

    def test_close_returns_ok(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.close_project()
        self.assertTrue(result["ok"])

    def test_close_clears_project(self) -> None:
        adv.open_project(self._tmpdir.name)
        adv.close_project()
        info = adv.get_project_info()
        self.assertFalse(info["ok"])


# ---
# advanced/runtime - project_tree
# ---

class ProjectTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        adv.close_project()
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        # Create some test files/dirs
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('hello')", encoding="utf-8")
        (root / "src" / "utils.py").write_text("# utils", encoding="utf-8")
        (root / "README.md").write_text("# Project", encoding="utf-8")

    def tearDown(self) -> None:
        adv.close_project()
        self._tmpdir.cleanup()

    def test_no_project_returns_error(self) -> None:
        result = adv.project_tree()
        self.assertFalse(result["ok"])

    def test_with_project_ok_true(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        self.assertTrue(result["ok"])

    def test_returns_items_list(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        self.assertIsInstance(result["items"], list)

    def test_finds_py_file(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        paths = [item["path"] for item in result["items"]]
        self.assertTrue(any("main.py" in p for p in paths))

    def test_finds_dir(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        types = [item["type"] for item in result["items"]]
        self.assertIn("dir", types)

    def test_count_matches_items_len(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.project_tree()
        self.assertEqual(result["count"], len(result["items"]))


# ---
# advanced/runtime - read_project_file
# ---

class ReadProjectFileTest(unittest.TestCase):
    def setUp(self) -> None:
        adv.close_project()
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name)
        (self._root / "hello.py").write_text("print('hello')", encoding="utf-8")

    def tearDown(self) -> None:
        adv.close_project()
        self._tmpdir.cleanup()

    def test_no_project_returns_error(self) -> None:
        result = adv.read_project_file("hello.py")
        self.assertFalse(result["ok"])

    def test_reads_existing_file(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("hello.py")
        self.assertTrue(result["ok"])

    def test_content_is_string(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("hello.py")
        self.assertIsInstance(result["content"], str)

    def test_content_correct(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("hello.py")
        self.assertIn("hello", result["content"])

    def test_nonexistent_file_ok_false(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("missing.py")
        self.assertFalse(result["ok"])

    def test_path_traversal_blocked(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("../../etc/passwd")
        self.assertFalse(result["ok"])

    def test_cp1251_file_decoded_not_mojibake(self) -> None:
        # Legacy Russian VBA files are often saved as cp1251, not UTF-8.
        # The reader must decode them as readable Cyrillic, not U+FFFD soup.
        cyrillic = "Привет мир"
        (self._root / "Module1.bas").write_bytes(cyrillic.encode("cp1251"))
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("Module1.bas")
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], cyrillic)
        self.assertNotIn("�", result["content"])

    def test_utf8_bom_stripped(self) -> None:
        # A UTF-8 BOM must not leak into the content as a stray U+FEFF char.
        (self._root / "bom.txt").write_bytes(b"\xef\xbb\xbfhello")
        adv.open_project(self._tmpdir.name)
        result = adv.read_project_file("bom.txt")
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "hello")


# ---
# advanced/runtime - search_in_project
# ---

class SearchInProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        adv.close_project()
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        (root / "alpha.py").write_text("x = hello_world()", encoding="utf-8")
        (root / "beta.py").write_text("y = goodbye()", encoding="utf-8")

    def tearDown(self) -> None:
        adv.close_project()
        self._tmpdir.cleanup()

    def test_no_project_returns_error(self) -> None:
        result = adv.search_in_project("hello")
        self.assertFalse(result["ok"])

    def test_finds_matching_lines(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("hello")
        self.assertTrue(result["ok"])
        self.assertGreater(result["count"], 0)

    def test_items_are_dicts_with_path(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("hello")
        for item in result["items"]:
            self.assertIn("path", item)
            self.assertIn("line", item)
            self.assertIn("text", item)

    def test_no_match_returns_empty_list(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("zzzznotfound99999")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["items"], [])

    def test_query_reflected_in_result(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("hello")
        self.assertEqual(result["query"], "hello")

    def test_max_results_limits_count(self) -> None:
        adv.open_project(self._tmpdir.name)
        result = adv.search_in_project("", max_results=1)
        self.assertLessEqual(result["count"], 1)


if __name__ == "__main__":
    unittest.main()
