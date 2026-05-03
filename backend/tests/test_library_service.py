"""Tests for application/library_sqlite and application/library_service."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.library_sqlite.runtime as lib_rt  # noqa: E402
import app.application.library_service.runtime as svc_rt  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — redirect DB_PATH / UPLOADS_DIR to temp dirs before each test
# ─────────────────────────────────────────────────────────────────────────────

class LibraryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        # library_sqlite patches
        self._orig_db_path = lib_rt.DB_PATH
        self._orig_uploads = lib_rt.UPLOADS_DIR
        lib_rt.DB_PATH = tmp / "library.db"
        lib_rt.UPLOADS_DIR = tmp / "uploads"
        lib_rt.UPLOADS_DIR.mkdir()
        lib_rt.init_db()

        # library_service patches (uses its own SQLITE_DB)
        self._orig_svc_db = svc_rt.SQLITE_DB
        self._orig_svc_uploads = svc_rt.LEGACY_UPLOADS_DIR
        svc_rt.SQLITE_DB = lib_rt.DB_PATH        # share the same DB
        svc_rt.LEGACY_UPLOADS_DIR = lib_rt.UPLOADS_DIR

    def tearDown(self) -> None:
        lib_rt.DB_PATH = self._orig_db_path
        lib_rt.UPLOADS_DIR = self._orig_uploads
        svc_rt.SQLITE_DB = self._orig_svc_db
        svc_rt.LEGACY_UPLOADS_DIR = self._orig_svc_uploads
        self._tmpdir.cleanup()
        super().tearDown()

    # ── convenience ───────────────────────────────────────────────────────────

    def _add(self, name="test.txt", content=b"hello world", use_in_context=True):
        return lib_rt.add_file(name, content, "text/plain", use_in_context)


# ─────────────────────────────────────────────────────────────────────────────
# library_sqlite — safe_disk_name
# ─────────────────────────────────────────────────────────────────────────────

class SafeDiskNameTest(unittest.TestCase):
    def test_basic_name(self) -> None:
        name = lib_rt.safe_disk_name("readme.txt", b"data")
        self.assertTrue(name.endswith(".txt"))
        self.assertIn("readme", name)

    def test_sha256_suffix_added(self) -> None:
        name1 = lib_rt.safe_disk_name("doc.txt", b"content A")
        name2 = lib_rt.safe_disk_name("doc.txt", b"content B")
        # Different content → different digest suffix
        self.assertNotEqual(name1, name2)

    def test_special_chars_sanitised(self) -> None:
        name = lib_rt.safe_disk_name("my file (2024).py", b"x")
        self.assertNotIn(" ", name)
        self.assertNotIn("(", name)
        self.assertNotIn(")", name)

    def test_long_stem_truncated(self) -> None:
        long_name = "a" * 200 + ".md"
        result = lib_rt.safe_disk_name(long_name, b"x")
        stem = Path(result).stem
        self.assertLessEqual(len(stem), 100)   # stem + digest ≤ 80+13


# ─────────────────────────────────────────────────────────────────────────────
# library_sqlite — extract_preview
# ─────────────────────────────────────────────────────────────────────────────

class ExtractPreviewTest(unittest.TestCase):
    def test_text_file_preview(self) -> None:
        preview = lib_rt.extract_preview("notes.txt", b"This is a note")
        self.assertIn("This is a note", preview)

    def test_python_file_preview(self) -> None:
        code = b"def hello():\n    return 42\n"
        preview = lib_rt.extract_preview("module.py", code)
        self.assertIn("hello", preview)

    def test_unknown_ext_returns_empty(self) -> None:
        preview = lib_rt.extract_preview("image.png", b"\x89PNG")
        self.assertEqual(preview, "")

    def test_max_chars_respected(self) -> None:
        big = b"x" * 20000
        preview = lib_rt.extract_preview("big.txt", big)
        self.assertLessEqual(len(preview), 12001)


# ─────────────────────────────────────────────────────────────────────────────
# library_sqlite — CRUD
# ─────────────────────────────────────────────────────────────────────────────

class LibrarySqliteCRUDTest(LibraryTestBase):
    def test_add_file_returns_id(self) -> None:
        result = self._add()
        self.assertTrue(result["ok"])
        self.assertIn("id", result)
        self.assertIn("stored_path", result)

    def test_add_empty_file_rejected(self) -> None:
        result = lib_rt.add_file("empty.txt", b"", "text/plain", True)
        self.assertFalse(result["ok"])

    def test_add_file_writes_to_disk(self) -> None:
        result = self._add("saved.txt", b"disk content")
        self.assertTrue(Path(result["stored_path"]).exists())

    def test_list_files_after_add(self) -> None:
        self._add("a.txt", b"aaa")
        self._add("b.txt", b"bbb")
        items = lib_rt.list_files()
        self.assertTrue(items["ok"])
        self.assertEqual(items["count"], 2)

    def test_list_files_empty(self) -> None:
        result = lib_rt.list_files()
        self.assertEqual(result["count"], 0)

    def test_delete_file_removes_row_and_disk(self) -> None:
        r = self._add("del.txt", b"bye")
        stored = r["stored_path"]
        lib_rt.delete_file(r["id"])
        self.assertEqual(lib_rt.list_files()["count"], 0)
        self.assertFalse(Path(stored).exists())

    def test_toggle_context_off(self) -> None:
        r = self._add()
        lib_rt.toggle_context(r["id"], False)
        ctx = lib_rt.get_context_files()
        # No files in context
        self.assertEqual(ctx["count"], 0)

    def test_toggle_context_on(self) -> None:
        r = self._add(use_in_context=False)
        lib_rt.toggle_context(r["id"], True)
        ctx = lib_rt.get_context_files()
        self.assertGreaterEqual(ctx["count"], 1)

    def test_search_files_by_name(self) -> None:
        self._add("report_2026.txt", b"financial data")
        self._add("notes.txt", b"other text")
        result = lib_rt.search_files("report")
        self.assertEqual(result["count"], 1)
        self.assertIn("report_2026.txt", result["items"][0]["name"])

    def test_search_files_by_preview_content(self) -> None:
        self._add("alpha.txt", b"alpha is unique keyword")
        self._add("beta.txt",  b"completely different")
        result = lib_rt.search_files("unique keyword")
        self.assertEqual(result["count"], 1)

    def test_get_context_files_only_active(self) -> None:
        self._add("active.txt", b"active", use_in_context=True)
        self._add("inactive.txt", b"inactive", use_in_context=False)
        ctx = lib_rt.get_context_files()
        names = [i["name"] for i in ctx["items"]]
        self.assertIn("active.txt", names)
        self.assertNotIn("inactive.txt", names)


# ─────────────────────────────────────────────────────────────────────────────
# library_service — public API (uses shared DB via patched SQLITE_DB)
# ─────────────────────────────────────────────────────────────────────────────

class LibraryServiceTest(LibraryTestBase):
    def test_list_library_files_empty(self) -> None:
        result = svc_rt.list_library_files()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_list_library_files_after_add(self) -> None:
        self._add("doc.md", b"# Docs")
        result = svc_rt.list_library_files()
        self.assertEqual(result["count"], 1)
        f = result["files"][0]
        self.assertEqual(f["name"], "doc.md")
        self.assertIn("active", f)   # has use_in_context alias

    def test_set_library_active_false(self) -> None:
        self._add("toggle.txt", b"text")
        result = svc_rt.set_library_active("toggle.txt", False)
        self.assertTrue(result["ok"])
        self.assertFalse(result["active"])

    def test_set_library_active_nonexistent(self) -> None:
        result = svc_rt.set_library_active("ghost.txt", True)
        self.assertFalse(result["ok"])

    def test_delete_library_file(self) -> None:
        self._add("rm.txt", b"delete me")
        result = svc_rt.delete_library_file("rm.txt")
        self.assertTrue(result["ok"])
        self.assertEqual(svc_rt.list_library_files()["count"], 0)

    def test_delete_library_file_nonexistent(self) -> None:
        result = svc_rt.delete_library_file("no_file.txt")
        self.assertFalse(result["ok"])

    def test_build_library_context_empty(self) -> None:
        result = svc_rt.build_library_context()
        self.assertTrue(result["ok"])
        self.assertEqual(result["active_count"], 0)
        self.assertEqual(result["context"], "")

    def test_build_library_context_with_active_file(self) -> None:
        self._add("ctx.txt", b"context content here", use_in_context=True)
        result = svc_rt.build_library_context()
        self.assertTrue(result["ok"])
        self.assertIn("ctx.txt", result["context"])
        self.assertIn("context content here", result["context"])

    def test_build_library_context_respects_max_files(self) -> None:
        for i in range(5):
            self._add(f"file{i}.txt", f"content {i}".encode())
        result = svc_rt.build_library_context(max_files=2)
        self.assertLessEqual(len(result["used_files"]), 2)


if __name__ == "__main__":
    unittest.main()
