"""Tests for application/project_service and application/web_service."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.project_service.runtime as proj_rt  # noqa: E402
from app.application.web_service.runtime import search_web  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# project_service — helpers
# ─────────────────────────────────────────────────────────────────────────────

class ProjectServiceHelperTest(unittest.TestCase):
    """Tests for _is_safe_path and _normalize_rel_path."""

    def test_safe_path_inside_base(self) -> None:
        from app.application.project_service.runtime import BASE_DIR, _is_safe_path
        self.assertTrue(_is_safe_path(BASE_DIR / "backend"))

    def test_safe_path_rejects_escape(self) -> None:
        from app.application.project_service.runtime import _is_safe_path
        self.assertFalse(_is_safe_path(Path("/etc/passwd")))

    def test_normalize_strips_leading_slash(self) -> None:
        from app.application.project_service.runtime import BASE_DIR, _normalize_rel_path
        result = _normalize_rel_path("/backend")
        self.assertTrue(str(result).startswith(str(BASE_DIR)))

    def test_normalize_raises_on_escape(self) -> None:
        from app.application.project_service.runtime import _normalize_rel_path
        with self.assertRaises(ValueError):
            _normalize_rel_path("../../etc/passwd")


# ─────────────────────────────────────────────────────────────────────────────
# read_project_file / write_project_file — using a fake BASE_DIR
# ─────────────────────────────────────────────────────────────────────────────

class ProjectFileRWTest(unittest.TestCase):
    """Patches BASE_DIR to a temp directory so tests don't touch the real repo."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._fake_root = Path(self._tmpdir.name)
        self._orig_base = proj_rt.BASE_DIR
        proj_rt.BASE_DIR = self._fake_root

    def tearDown(self) -> None:
        proj_rt.BASE_DIR = self._orig_base
        self._tmpdir.cleanup()
        super().tearDown()

    # write ──────────────────────────────────────────────────────────────────

    def test_write_creates_file(self) -> None:
        result = proj_rt.write_project_file("hello.py", "print('hi')")
        self.assertTrue(result["ok"])
        self.assertTrue((self._fake_root / "hello.py").exists())

    def test_write_creates_parent_dirs(self) -> None:
        result = proj_rt.write_project_file("sub/dir/file.py", "x = 1")
        self.assertTrue(result["ok"])
        self.assertTrue((self._fake_root / "sub" / "dir" / "file.py").exists())

    def test_write_returns_chars_written(self) -> None:
        content = "x = 42\n"
        result = proj_rt.write_project_file("test.py", content)
        self.assertEqual(result["chars_written"], len(content))

    def test_write_unsupported_ext_rejected(self) -> None:
        result = proj_rt.write_project_file("binary.exe", "data")
        self.assertFalse(result["ok"])
        self.assertIn("Unsupported", result["error"])

    # read ───────────────────────────────────────────────────────────────────

    def test_read_existing_file(self) -> None:
        (self._fake_root / "readme.md").write_text("# Hello", encoding="utf-8")
        result = proj_rt.read_project_file("readme.md")
        self.assertTrue(result["ok"])
        self.assertIn("Hello", result["content"])

    def test_read_nonexistent_file(self) -> None:
        result = proj_rt.read_project_file("no_such.py")
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    def test_read_unsupported_ext(self) -> None:
        (self._fake_root / "img.png").write_bytes(b"\x89PNG")
        result = proj_rt.read_project_file("img.png")
        self.assertFalse(result["ok"])
        self.assertIn("Unsupported", result["error"])

    def test_read_max_chars_truncated(self) -> None:
        (self._fake_root / "big.txt").write_text("A" * 500, encoding="utf-8")
        result = proj_rt.read_project_file("big.txt", max_chars=100)
        self.assertTrue(result["ok"])
        self.assertEqual(result["chars"], 100)
        self.assertEqual(len(result["content"]), 100)

    def test_roundtrip_write_then_read(self) -> None:
        proj_rt.write_project_file("sample.py", "answer = 42")
        read = proj_rt.read_project_file("sample.py")
        self.assertTrue(read["ok"])
        self.assertIn("42", read["content"])

    # list_project_tree ───────────────────────────────────────────────────────

    def test_list_tree_returns_ok(self) -> None:
        (self._fake_root / "a.py").write_text("x")
        (self._fake_root / "sub").mkdir()
        (self._fake_root / "sub" / "b.py").write_text("y")
        result = proj_rt.list_project_tree(max_depth=2)
        self.assertTrue(result["ok"])
        paths = [item["path"] for item in result["items"]]
        self.assertIn("a.py", paths)

    def test_list_tree_ignores_ignored_dirs(self) -> None:
        (self._fake_root / "__pycache__").mkdir()
        (self._fake_root / "__pycache__" / "cached.pyc").write_bytes(b"")
        result = proj_rt.list_project_tree(max_depth=2)
        paths = [item["path"] for item in result["items"]]
        self.assertFalse(any("__pycache__" in p for p in paths))

    def test_list_tree_respects_max_items(self) -> None:
        for i in range(20):
            (self._fake_root / f"file{i}.py").write_text("")
        result = proj_rt.list_project_tree(max_items=5)
        self.assertLessEqual(result["count"], 5)

    # search_project ──────────────────────────────────────────────────────────

    def test_search_finds_hit(self) -> None:
        (self._fake_root / "code.py").write_text("SECRET_KEY = 'abc123'")
        result = proj_rt.search_project("SECRET_KEY")
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["count"], 1)
        self.assertIn("code.py", result["hits"][0]["path"])

    def test_search_empty_query_returns_error(self) -> None:
        result = proj_rt.search_project("")
        self.assertFalse(result["ok"])

    def test_search_no_match_returns_empty(self) -> None:
        (self._fake_root / "other.py").write_text("unrelated code")
        result = proj_rt.search_project("NONEXISTENT_SYMBOL_XYZ")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)


# ─────────────────────────────────────────────────────────────────────────────
# web_service
# ─────────────────────────────────────────────────────────────────────────────

_FAKE_SOURCES = [
    {"title": "Python docs", "url": "https://docs.python.org", "snippet": "official", "engine": "ddg"},
    {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Python", "snippet": "wiki", "engine": "wiki"},
]


class SearchWebTest(unittest.TestCase):
    def _search(self, query: str, sources=None):
        with patch(
            "app.application.web_service.runtime.core_search_web",
            return_value=sources if sources is not None else _FAKE_SOURCES,
        ), patch(
            "app.application.web_service.runtime.format_search_results",
            return_value="formatted context",
        ):
            return search_web(query)

    def test_successful_search(self) -> None:
        result = self._search("Python tutorial")
        self.assertTrue(result["ok"])
        self.assertEqual(result["query"], "Python tutorial")
        self.assertEqual(result["count"], 2)
        self.assertIn("formatted context", result["context"])

    def test_empty_query_returns_not_ok(self) -> None:
        result = search_web("")
        self.assertFalse(result["ok"])
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["count"], 0)

    def test_whitespace_query_returns_not_ok(self) -> None:
        result = search_web("   ")
        self.assertFalse(result["ok"])

    def test_no_results_returns_not_ok(self) -> None:
        result = self._search("obscure query", sources=[])
        self.assertFalse(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_engines_used_deduplicated(self) -> None:
        sources = [
            {"engine": "ddg", "title": "a", "url": "http://a"},
            {"engine": "ddg", "title": "b", "url": "http://b"},
            {"engine": "wiki", "title": "c", "url": "http://c"},
        ]
        result = self._search("q", sources=sources)
        self.assertEqual(len(result["engines_used"]), 2)

    def test_engine_links_always_present(self) -> None:
        result = self._search("q")
        self.assertIsInstance(result["engine_links"], list)
        self.assertGreater(len(result["engine_links"]), 0)

    def test_result_shape(self) -> None:
        result = self._search("q")
        for key in ("ok", "query", "sources", "engines_used", "context", "count", "engine_links"):
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()
