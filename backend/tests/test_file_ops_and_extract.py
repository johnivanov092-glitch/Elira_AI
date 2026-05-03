"""Tests for application/file_ops and application/file_extract runtimes."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.file_ops.runtime as fo_rt  # noqa: E402
from app.application.file_extract.runtime import (  # noqa: E402
    TEXT_EXTS,
    extract_file,
    extract_text,
    extract_zip,
)


# ─────────────────────────────────────────────────────────────────────────────
# file_ops — patched WORKSPACE so tests never touch real data
# ─────────────────────────────────────────────────────────────────────────────

class FileOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._fake_ws = Path(self._tmpdir.name) / "workspace"
        self._fake_ws.mkdir()
        self._orig_ws = fo_rt.WORKSPACE
        fo_rt.WORKSPACE = self._fake_ws

    def tearDown(self) -> None:
        fo_rt.WORKSPACE = self._orig_ws
        self._tmpdir.cleanup()
        super().tearDown()

    # safe_path ───────────────────────────────────────────────────────────────

    def test_safe_path_empty_returns_empty_error(self) -> None:
        path, err = fo_rt.safe_path("")
        self.assertIsNone(path)
        self.assertEqual(err, "empty")

    def test_safe_path_whitespace_returns_empty_error(self) -> None:
        path, err = fo_rt.safe_path("   ")
        self.assertIsNone(path)
        self.assertEqual(err, "empty")

    def test_safe_path_blocked_dir_refused(self) -> None:
        path, err = fo_rt.safe_path("node_modules/evil.js")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")

    def test_safe_path_outside_workspace_refused(self) -> None:
        path, err = fo_rt.safe_path("../../etc/passwd")
        self.assertIsNone(path)
        self.assertEqual(err, "outside_workspace")

    def test_safe_path_valid_returns_full_path(self) -> None:
        path, err = fo_rt.safe_path("subdir/file.py")
        self.assertIsNone(err)
        self.assertIsNotNone(path)
        self.assertTrue(str(path).startswith(str(self._fake_ws)))

    # write_file ──────────────────────────────────────────────────────────────

    def test_write_creates_new_file(self) -> None:
        result, err = fo_rt.write_file("hello.py", "print('hi')")
        self.assertIsNone(err)
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "created")
        self.assertTrue((self._fake_ws / "hello.py").exists())

    def test_write_updates_existing_file(self) -> None:
        fo_rt.write_file("file.txt", "old")
        result, err = fo_rt.write_file("file.txt", "new content")
        self.assertIsNone(err)
        self.assertEqual(result["action"], "updated")
        self.assertIsNotNone(result["old_size"])

    def test_write_creates_parent_dirs(self) -> None:
        result, err = fo_rt.write_file("a/b/c/file.py", "x = 1")
        self.assertIsNone(err)
        self.assertTrue((self._fake_ws / "a" / "b" / "c" / "file.py").exists())

    def test_write_too_large_rejected(self) -> None:
        big = "x" * (fo_rt.MAX_FILE_SIZE + 1)
        result, err = fo_rt.write_file("big.txt", big)
        self.assertIsNone(result)
        self.assertEqual(err, "too_large")

    def test_write_records_size(self) -> None:
        content = "hello world"
        result, _ = fo_rt.write_file("size.txt", content)
        self.assertEqual(result["size"], len(content))

    # read_file ───────────────────────────────────────────────────────────────

    def test_read_existing_file(self) -> None:
        (self._fake_ws / "read_me.txt").write_text("content here", encoding="utf-8")
        result, err = fo_rt.read_file("read_me.txt")
        self.assertIsNone(err)
        self.assertTrue(result["ok"])
        self.assertIn("content here", result["content"])

    def test_read_nonexistent_returns_not_found(self) -> None:
        result, err = fo_rt.read_file("no_file.txt")
        self.assertIsNone(result)
        self.assertEqual(err, "not_found")

    def test_read_directory_returns_not_a_file(self) -> None:
        (self._fake_ws / "adir").mkdir()
        result, err = fo_rt.read_file("adir")
        self.assertIsNone(result)
        self.assertEqual(err, "not_a_file")

    def test_read_max_chars_truncates(self) -> None:
        (self._fake_ws / "long.txt").write_text("A" * 1000, encoding="utf-8")
        result, _ = fo_rt.read_file("long.txt", max_chars=100)
        self.assertIn("truncated", result["content"])
        self.assertTrue(len(result["content"]) > 100)  # has the truncation note

    # file_tree ───────────────────────────────────────────────────────────────

    def test_file_tree_empty_workspace(self) -> None:
        result = fo_rt.file_tree()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_file_tree_lists_files_and_dirs(self) -> None:
        (self._fake_ws / "sub").mkdir()
        (self._fake_ws / "sub" / "a.py").write_text("x")
        (self._fake_ws / "b.txt").write_text("y")
        result = fo_rt.file_tree()
        paths = [item["path"] for item in result["items"]]
        self.assertIn("b.txt", paths)
        self.assertIn("sub", paths)

    def test_file_tree_respects_max_items(self) -> None:
        for i in range(10):
            (self._fake_ws / f"file{i}.txt").write_text("")
        result = fo_rt.file_tree(max_items=3)
        self.assertLessEqual(result["count"], 3)

    def test_file_tree_hidden_files_excluded(self) -> None:
        (self._fake_ws / ".hidden").write_text("secret")
        result = fo_rt.file_tree()
        self.assertFalse(any(item["path"].startswith(".") for item in result["items"]))

    # diff_file ───────────────────────────────────────────────────────────────

    def test_diff_unchanged_file(self) -> None:
        (self._fake_ws / "same.py").write_text("x = 1")
        result, _ = fo_rt.diff_file("same.py", "x = 1")
        self.assertFalse(result["changed"])
        self.assertEqual(result["stats"]["added"], 0)
        self.assertEqual(result["stats"]["removed"], 0)

    def test_diff_changed_file(self) -> None:
        (self._fake_ws / "change.py").write_text("x = 1")
        result, _ = fo_rt.diff_file("change.py", "x = 2")
        self.assertTrue(result["changed"])
        self.assertGreater(result["stats"]["added"], 0)
        self.assertGreater(result["stats"]["removed"], 0)

    def test_diff_nonexistent_file_treated_as_new(self) -> None:
        result, _ = fo_rt.diff_file("new_file.py", "x = 1")
        self.assertIsNotNone(result)
        self.assertFalse(result["exists"])

    # mkdir / delete ──────────────────────────────────────────────────────────

    def test_mkdir_creates_directory(self) -> None:
        result, err = fo_rt.mkdir_dir("newdir/sub")
        self.assertIsNone(err)
        self.assertTrue((self._fake_ws / "newdir" / "sub").is_dir())

    def test_delete_file(self) -> None:
        (self._fake_ws / "del_me.txt").write_text("bye")
        result, err = fo_rt.delete_path("del_me.txt")
        self.assertIsNone(err)
        self.assertEqual(result["action"], "deleted")
        self.assertFalse((self._fake_ws / "del_me.txt").exists())

    def test_delete_directory(self) -> None:
        d = self._fake_ws / "rmdir"
        d.mkdir()
        (d / "inner.txt").write_text("x")
        result, err = fo_rt.delete_path("rmdir")
        self.assertIsNone(err)
        self.assertFalse(d.exists())

    def test_delete_nonexistent_returns_not_found(self) -> None:
        result, err = fo_rt.delete_path("ghost.txt")
        self.assertIsNone(result)
        self.assertEqual(err, "not_found")


# ─────────────────────────────────────────────────────────────────────────────
# file_extract
# ─────────────────────────────────────────────────────────────────────────────

class ExtractTextTest(unittest.TestCase):
    def test_utf8_text(self) -> None:
        data = "Hello, world!".encode("utf-8")
        self.assertEqual(extract_text(data), "Hello, world!")

    def test_empty_bytes_returns_empty_string(self) -> None:
        self.assertEqual(extract_text(b""), "")

    def test_max_chars_truncated(self) -> None:
        data = ("x" * 200).encode("utf-8")
        result = extract_text(data, max_chars=50)
        self.assertEqual(len(result), 50)

    def test_fallback_encoding_cp1251(self) -> None:
        data = "Привет мир".encode("cp1251")
        result = extract_text(data)
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_invalid_encoding_replaced(self) -> None:
        # Raw bytes that are not valid in any standard encoding
        data = bytes(range(128, 256))
        result = extract_text(data)
        self.assertIsInstance(result, str)


class ExtractZipTest(unittest.TestCase):
    def _make_zip(self, files: dict[str, str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return buf.getvalue()

    def test_basic_zip_extraction(self) -> None:
        data = self._make_zip({"readme.txt": "Hello from zip"})
        result = extract_zip(data)
        self.assertIn("readme.txt", result)
        self.assertIn("Hello from zip", result)

    def test_zip_lists_all_files(self) -> None:
        data = self._make_zip({
            "a.txt": "aaa",
            "b.py": "bbb",
            "c.json": '{"ok": true}',
        })
        result = extract_zip(data)
        self.assertIn("a.txt", result)
        self.assertIn("b.py", result)
        self.assertIn("c.json", result)

    def test_zip_non_text_file_listed_not_extracted(self) -> None:
        data = self._make_zip({"image.png": "\x89PNG"})
        result = extract_zip(data)
        self.assertIn("image.png", result)
        # PNG content should NOT appear inline
        self.assertNotIn("\x89PNG", result)

    def test_invalid_zip_returns_error_string(self) -> None:
        result = extract_zip(b"not a zip file")
        self.assertIn("[ZIP error", result)


class ExtractFileDispatchTest(unittest.TestCase):
    def test_dispatch_txt(self) -> None:
        result = extract_file("notes.txt", b"plain text content")
        self.assertTrue(result["ok"])
        self.assertEqual(result["type"], ".txt")
        self.assertIn("plain text", result["text"])

    def test_dispatch_py(self) -> None:
        result = extract_file("script.py", b"print('hello')")
        self.assertTrue(result["ok"])
        self.assertEqual(result["type"], ".py")

    def test_dispatch_zip(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("hello.txt", "hi there")
        result = extract_file("archive.zip", buf.getvalue())
        self.assertTrue(result["ok"])
        self.assertIn("hello.txt", result["text"])

    def test_result_has_required_keys(self) -> None:
        result = extract_file("data.txt", b"test")
        for key in ("ok", "filename", "size", "text", "chars", "type"):
            self.assertIn(key, result)

    def test_size_matches_input(self) -> None:
        data = b"hello world"
        result = extract_file("test.txt", data)
        self.assertEqual(result["size"], len(data))

    def test_text_ext_set_non_empty(self) -> None:
        self.assertIn(".py", TEXT_EXTS)
        self.assertIn(".txt", TEXT_EXTS)
        self.assertIn(".json", TEXT_EXTS)

    def test_pdf_import_error_returns_string(self) -> None:
        # If pdf deps missing, extract_file returns an error string not an exception
        result = extract_file("doc.pdf", b"%PDF-fake")
        self.assertTrue(result["ok"])
        self.assertIsInstance(result["text"], str)

    def test_docx_import_error_returns_string(self) -> None:
        result = extract_file("doc.docx", b"PK\x03\x04fake")
        self.assertTrue(result["ok"])
        self.assertIsInstance(result["text"], str)


if __name__ == "__main__":
    unittest.main()
