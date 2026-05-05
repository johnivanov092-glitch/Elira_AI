"""Tests for application/ollama_models, application/ollama_runtime,
application/python_runner (sandboxed execution), and
application/library_sqlite (pure helpers + patched DB/UPLOADS)."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.ollama_models.runtime as om_rt  # noqa: E402
import app.application.ollama_runtime.runtime as or_rt  # noqa: E402
from app.application.python_runner.runtime import (  # noqa: E402
    ALLOWED_IMPORTS,
    SAFE_BUILTINS,
    _safe_import,
    execute_python,
)
import app.application.library_sqlite.runtime as lib_rt  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# ollama_models — get_models (mocked requests)
# ─────────────────────────────────────────────────────────────────────────────

class GetModelsTest(unittest.TestCase):
    def _make_response(self, models):
        resp = MagicMock()
        resp.json.return_value = {"models": models}
        resp.raise_for_status.return_value = None
        return resp

    def test_get_models_ok(self) -> None:
        fake_models = [
            {"name": "gemma3:4b", "model": "gemma3:4b", "size": 1000, "modified_at": "2024", "digest": "abc"},
            {"name": "llama3:8b", "model": "llama3:8b", "size": 2000, "modified_at": "2024", "digest": "def"},
        ]
        with patch("requests.get", return_value=self._make_response(fake_models)):
            result = om_rt.get_models()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        names = [m["name"] for m in result["models"]]
        self.assertIn("gemma3:4b", names)

    def test_get_models_empty_list(self) -> None:
        with patch("requests.get", return_value=self._make_response([])):
            result = om_rt.get_models()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["models"], [])

    def test_get_models_connection_error(self) -> None:
        with patch("requests.get", side_effect=Exception("connection refused")):
            result = om_rt.get_models()
        self.assertFalse(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertIn("error", result)

    def test_get_models_fields_present(self) -> None:
        fake = [{"name": "m", "model": "m", "size": 0, "modified_at": "", "digest": ""}]
        with patch("requests.get", return_value=self._make_response(fake)):
            result = om_rt.get_models()
        model = result["models"][0]
        for key in ("name", "model", "size", "modified_at", "digest"):
            self.assertIn(key, model)

    def test_tags_url_is_local(self) -> None:
        self.assertIn("127.0.0.1", om_rt.OLLAMA_TAGS_URL)


# ─────────────────────────────────────────────────────────────────────────────
# ollama_runtime — list_ollama_models (async, mocked ollama.list)
# ─────────────────────────────────────────────────────────────────────────────

class ListOllamaModelsTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_object_response_ok(self) -> None:
        model = SimpleNamespace(name="gemma3:4b", size=1000)
        resp = SimpleNamespace(models=[model])
        with patch("ollama.list", return_value=resp):
            result = self._run(or_rt.list_ollama_models())
        self.assertIn("models", result)
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["name"], "gemma3:4b")
        self.assertEqual(result["models"][0]["size"], 1000)

    def test_dict_response_ok(self) -> None:
        resp = {"models": [{"name": "llama3:8b", "size": 4000}]}
        with patch("ollama.list", return_value=resp):
            result = self._run(or_rt.list_ollama_models())
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["name"], "llama3:8b")

    def test_object_with_model_attribute(self) -> None:
        model = SimpleNamespace(model="qwen2:7b", size=2000)  # no .name, has .model
        resp = SimpleNamespace(models=[model])
        with patch("ollama.list", return_value=resp):
            result = self._run(or_rt.list_ollama_models())
        self.assertEqual(result["models"][0]["name"], "qwen2:7b")

    def test_empty_models_list(self) -> None:
        resp = SimpleNamespace(models=[])
        with patch("ollama.list", return_value=resp):
            result = self._run(or_rt.list_ollama_models())
        self.assertEqual(result["models"], [])

    def test_ollama_error_returns_error_key(self) -> None:
        with patch("ollama.list", side_effect=Exception("Ollama not running")):
            result = self._run(or_rt.list_ollama_models())
        self.assertIn("error", result)
        self.assertEqual(result["models"], [])

    def test_model_with_no_name_skipped(self) -> None:
        # Model with empty name should be filtered out
        model = SimpleNamespace(name="", size=0)
        resp = SimpleNamespace(models=[model])
        with patch("ollama.list", return_value=resp):
            result = self._run(or_rt.list_ollama_models())
        self.assertEqual(result["models"], [])


# ─────────────────────────────────────────────────────────────────────────────
# python_runner — _safe_import and execute_python
# ─────────────────────────────────────────────────────────────────────────────

class SafeImportTest(unittest.TestCase):
    def test_allowed_import_succeeds(self) -> None:
        mod = _safe_import("math")
        self.assertIsNotNone(mod)

    def test_blocked_import_raises(self) -> None:
        with self.assertRaises(ImportError):
            _safe_import("os")

    def test_blocked_import_subprocess(self) -> None:
        with self.assertRaises(ImportError):
            _safe_import("subprocess")

    def test_blocked_import_sys(self) -> None:
        with self.assertRaises(ImportError):
            _safe_import("sys")

    def test_allowed_imports_set_not_empty(self) -> None:
        self.assertGreater(len(ALLOWED_IMPORTS), 0)
        self.assertIn("json", ALLOWED_IMPORTS)
        self.assertIn("math", ALLOWED_IMPORTS)

    def test_safe_builtins_not_empty(self) -> None:
        self.assertIn("print", SAFE_BUILTINS)
        self.assertIn("len", SAFE_BUILTINS)
        self.assertNotIn("open", SAFE_BUILTINS)
        self.assertNotIn("exec", SAFE_BUILTINS)


class ExecutePythonTest(unittest.TestCase):
    def test_empty_code_returns_error(self) -> None:
        result = execute_python("")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_none_code_returns_error(self) -> None:
        result = execute_python(None)
        self.assertFalse(result["ok"])

    def test_simple_arithmetic(self) -> None:
        result = execute_python("x = 2 + 2")
        self.assertTrue(result["ok"])
        self.assertEqual(result["locals"]["x"], "4")

    def test_print_captured(self) -> None:
        result = execute_python("print('hello')")
        self.assertTrue(result["ok"])
        self.assertIn("hello", result["stdout"])

    def test_multiple_assignments(self) -> None:
        result = execute_python("a = 1\nb = 2\nc = a + b")
        self.assertTrue(result["ok"])
        self.assertEqual(result["locals"]["c"], "3")

    def test_allowed_import_math(self) -> None:
        result = execute_python("import math\nx = math.pi")
        self.assertTrue(result["ok"])
        self.assertIn("x", result["locals"])

    def test_blocked_import_os_raises(self) -> None:
        result = execute_python("import os")
        self.assertFalse(result["ok"])
        self.assertIn("Import blocked", result["error"])

    def test_blocked_import_subprocess_raises(self) -> None:
        result = execute_python("import subprocess")
        self.assertFalse(result["ok"])

    def test_syntax_error_returns_ok_false(self) -> None:
        result = execute_python("def bad syntax(")
        self.assertFalse(result["ok"])

    def test_runtime_error_returns_ok_false(self) -> None:
        result = execute_python("raise ValueError('test error')")
        self.assertFalse(result["ok"])
        self.assertIn("ValueError", result["error"])

    def test_stdout_and_stderr_keys_present(self) -> None:
        result = execute_python("x = 1")
        self.assertIn("stdout", result)
        self.assertIn("stderr", result)

    def test_locals_excludes_dunder_vars(self) -> None:
        result = execute_python("x = 1")
        for key in result.get("locals", {}):
            self.assertFalse(key.startswith("__"))

    def test_list_comprehension(self) -> None:
        result = execute_python("squares = [x**2 for x in range(5)]")
        self.assertTrue(result["ok"])
        self.assertIn("squares", result["locals"])

    def test_json_module_allowed(self) -> None:
        result = execute_python("import json\nd = json.dumps({'a': 1})")
        self.assertTrue(result["ok"])

    def test_division_by_zero_returns_error(self) -> None:
        result = execute_python("x = 1 / 0")
        self.assertFalse(result["ok"])
        # error message is the exception message ("division by zero"),
        # full type name is in traceback
        self.assertIn("division by zero", result["error"])


# ─────────────────────────────────────────────────────────────────────────────
# library_sqlite — pure helpers (no DB)
# ─────────────────────────────────────────────────────────────────────────────

class SafeDiskNameTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        result = lib_rt.safe_disk_name("report.pdf", b"content")
        self.assertIsInstance(result, str)

    def test_preserves_extension(self) -> None:
        result = lib_rt.safe_disk_name("file.txt", b"data")
        self.assertTrue(result.endswith(".txt"))

    def test_stem_sanitized(self) -> None:
        result = lib_rt.safe_disk_name("my file (1).txt", b"data")
        self.assertNotIn(" ", result)
        self.assertNotIn("(", result)
        self.assertNotIn(")", result)

    def test_includes_sha256_digest(self) -> None:
        r1 = lib_rt.safe_disk_name("file.txt", b"data1")
        r2 = lib_rt.safe_disk_name("file.txt", b"data2")
        # Different content → different digest in name
        self.assertNotEqual(r1, r2)

    def test_same_content_same_name(self) -> None:
        r1 = lib_rt.safe_disk_name("file.txt", b"same content")
        r2 = lib_rt.safe_disk_name("file.txt", b"same content")
        self.assertEqual(r1, r2)

    def test_empty_filename_uses_fallback(self) -> None:
        result = lib_rt.safe_disk_name("", b"data")
        self.assertGreater(len(result), 0)

    def test_stem_max_80_chars(self) -> None:
        long_name = "a" * 200 + ".txt"
        result = lib_rt.safe_disk_name(long_name, b"data")
        stem = Path(result).stem
        # stem includes digest suffix separated by underscore
        self.assertLessEqual(len(stem), 100)


class ExtractPreviewTest(unittest.TestCase):
    def test_text_file_extracts_content(self) -> None:
        content = b"Hello world, this is a text file."
        preview = lib_rt.extract_preview("notes.txt", content)
        self.assertIn("Hello world", preview)

    def test_python_file_extracts_content(self) -> None:
        content = b"def hello(): pass\n"
        preview = lib_rt.extract_preview("script.py", content)
        self.assertIn("def hello", preview)

    def test_markdown_file_extracts_content(self) -> None:
        content = b"# Title\nSome text."
        preview = lib_rt.extract_preview("README.md", content)
        self.assertIn("Title", preview)

    def test_json_file_extracts_content(self) -> None:
        content = b'{"key": "value"}'
        preview = lib_rt.extract_preview("data.json", content)
        self.assertIn("key", preview)

    def test_unknown_extension_returns_empty(self) -> None:
        preview = lib_rt.extract_preview("file.xyz_unknown", b"\x00\x01\x02")
        self.assertEqual(preview, "")

    def test_binary_content_returns_empty_for_unsupported(self) -> None:
        preview = lib_rt.extract_preview("image.png", b"\x89PNG\r\n\x1a\n")
        self.assertEqual(preview, "")

    def test_content_truncated_at_12000(self) -> None:
        content = ("x" * 20000).encode()
        preview = lib_rt.extract_preview("big.txt", content)
        self.assertLessEqual(len(preview), 12000)


# ─────────────────────────────────────────────────────────────────────────────
# library_sqlite — CRUD with patched DB_PATH and UPLOADS_DIR
# ─────────────────────────────────────────────────────────────────────────────

class LibrarySQLiteCRUDTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name).resolve()
        self._uploads = tmp / "uploads"
        self._uploads.mkdir()
        self._orig_db = lib_rt.DB_PATH
        self._orig_uploads = lib_rt.UPLOADS_DIR
        lib_rt.DB_PATH = tmp / "library.db"
        lib_rt.UPLOADS_DIR = self._uploads
        lib_rt.init_db()

    def tearDown(self) -> None:
        lib_rt.DB_PATH = self._orig_db
        lib_rt.UPLOADS_DIR = self._orig_uploads
        self._tmpdir.cleanup()

    def test_list_files_empty_initially(self) -> None:
        result = lib_rt.list_files()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_add_file_empty_rejected(self) -> None:
        result = lib_rt.add_file("file.txt", b"", "text/plain", False)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_add_file_success(self) -> None:
        result = lib_rt.add_file("notes.txt", b"Hello world content", "text/plain", True)
        self.assertTrue(result["ok"])
        self.assertIn("id", result)

    def test_list_files_after_add(self) -> None:
        lib_rt.add_file("test.py", b"print('hello')", "text/plain", False)
        result = lib_rt.list_files()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["name"], "test.py")

    def test_add_file_writes_to_disk(self) -> None:
        lib_rt.add_file("data.txt", b"file contents here", "text/plain", False)
        disk_files = list(self._uploads.iterdir())
        self.assertEqual(len(disk_files), 1)

    def test_search_files_finds_by_name(self) -> None:
        lib_rt.add_file("myreport.txt", b"quarterly earnings report data", "text/plain", False)
        result = lib_rt.search_files("myreport")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)

    def test_search_files_no_match(self) -> None:
        lib_rt.add_file("data.txt", b"some content", "text/plain", False)
        result = lib_rt.search_files("nonexistent_xyz")
        self.assertEqual(result["count"], 0)

    def test_toggle_context_on(self) -> None:
        added = lib_rt.add_file("notes.txt", b"content here", "text/plain", False)
        result = lib_rt.toggle_context(added["id"], True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["use_in_context"])

    def test_toggle_context_off(self) -> None:
        added = lib_rt.add_file("notes.txt", b"content here", "text/plain", True)
        result = lib_rt.toggle_context(added["id"], False)
        self.assertFalse(result["use_in_context"])

    def test_get_context_files_empty_initially(self) -> None:
        lib_rt.add_file("notes.txt", b"content", "text/plain", False)
        result = lib_rt.get_context_files()
        self.assertTrue(result["ok"])
        # File added with use_in_context=False, so 0 context files
        self.assertEqual(result["count"], 0)

    def test_get_context_files_after_toggle(self) -> None:
        added = lib_rt.add_file("notes.txt", b"some important notes", "text/plain", False)
        lib_rt.toggle_context(added["id"], True)
        result = lib_rt.get_context_files()
        self.assertGreater(result["count"], 0)

    def test_delete_file_removes_from_db(self) -> None:
        added = lib_rt.add_file("del.txt", b"to be deleted", "text/plain", False)
        lib_rt.delete_file(added["id"])
        result = lib_rt.list_files()
        self.assertEqual(result["count"], 0)

    def test_delete_file_removes_from_disk(self) -> None:
        added = lib_rt.add_file("del.txt", b"to be deleted from disk", "text/plain", False)
        disk_files_before = list(self._uploads.iterdir())
        lib_rt.delete_file(added["id"])
        disk_files_after = list(self._uploads.iterdir())
        self.assertLess(len(disk_files_after), len(disk_files_before))


if __name__ == "__main__":
    unittest.main()
