"""Tests for application/elira_devtools (pure FS + pure helpers) and
application/elira_settings (patched DB)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.elira_devtools.runtime as dt       # noqa: E402
import app.application.elira_settings.runtime as es       # noqa: E402
import app.application.elira_memory_sqlite.runtime as msql  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# elira_devtools — resolve_project_path / is_allowed_path (pure)
# ─────────────────────────────────────────────────────────────────────────────

class DevtoolsPathHelpersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._fake_root = Path(self._tmpdir.name).resolve()
        self._orig_root = dt.PROJECT_ROOT
        dt.PROJECT_ROOT = self._fake_root

    def tearDown(self) -> None:
        dt.PROJECT_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_valid_relative_path(self) -> None:
        path, err = dt.resolve_project_path("backend/app.py")
        self.assertIsNone(err)
        self.assertIsNotNone(path)
        self.assertEqual(path, self._fake_root / "backend" / "app.py")

    def test_path_traversal_outside_root(self) -> None:
        path, err = dt.resolve_project_path("../../escape.py")
        self.assertIsNone(path)
        self.assertEqual(err, "outside_root")

    def test_blocked_path_dot_git(self) -> None:
        path, err = dt.resolve_project_path(".git/config")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")

    def test_blocked_path_node_modules(self) -> None:
        path, err = dt.resolve_project_path("node_modules/lodash/index.js")
        self.assertIsNone(path)
        self.assertEqual(err, "blocked")

    def test_is_allowed_path_clean(self) -> None:
        clean = self._fake_root / "src" / "app.py"
        self.assertTrue(dt.is_allowed_path(clean))

    def test_is_allowed_path_blocked(self) -> None:
        blocked = self._fake_root / ".git" / "HEAD"
        self.assertFalse(dt.is_allowed_path(blocked))


# ─────────────────────────────────────────────────────────────────────────────
# elira_devtools — parse_imports
# ─────────────────────────────────────────────────────────────────────────────

class ParseImportsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write(self, name, content):
        p = Path(self._tmpdir.name) / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_python_import(self) -> None:
        p = self._write("a.py", "import os\nimport sys\n")
        imports = dt.parse_imports(p)
        self.assertIn("os", imports)
        self.assertIn("sys", imports)

    def test_python_from_import(self) -> None:
        p = self._write("b.py", "from pathlib import Path\n")
        imports = dt.parse_imports(p)
        self.assertTrue(any("pathlib" in i for i in imports))

    def test_js_import(self) -> None:
        p = self._write("c.js", "import React from 'react';\nimport { useState } from 'react';\n")
        imports = dt.parse_imports(p)
        self.assertEqual(len(imports), 2)

    def test_unsupported_extension_returns_empty(self) -> None:
        p = self._write("d.txt", "import os")
        imports = dt.parse_imports(p)
        self.assertEqual(imports, [])

    def test_nonexistent_file_returns_empty(self) -> None:
        p = Path(self._tmpdir.name) / "missing.py"
        imports = dt.parse_imports(p)
        self.assertEqual(imports, [])

    def test_limit_30_imports(self) -> None:
        lines = "\n".join(f"import mod{i}" for i in range(50))
        p = self._write("big.py", lines)
        imports = dt.parse_imports(p)
        self.assertLessEqual(len(imports), 30)


# ─────────────────────────────────────────────────────────────────────────────
# elira_devtools — build_patch_plan (pure)
# ─────────────────────────────────────────────────────────────────────────────

class BuildPatchPlanTest(unittest.TestCase):
    def test_required_keys(self) -> None:
        result = dt.build_patch_plan("fix auth", "backend/auth.py", [])
        for key in ("status", "goal", "items", "notes"):
            self.assertIn(key, result)

    def test_current_path_included(self) -> None:
        result = dt.build_patch_plan("fix", "backend/auth.py", [])
        paths = [item["path"] for item in result["items"]]
        self.assertIn("backend/auth.py", paths)

    def test_staged_paths_included(self) -> None:
        result = dt.build_patch_plan("fix", None, ["a.py", "b.py"])
        paths = [item["path"] for item in result["items"]]
        self.assertIn("a.py", paths)
        self.assertIn("b.py", paths)

    def test_create_keyword_adds_create_step(self) -> None:
        result = dt.build_patch_plan("create new component", None, [])
        actions = [item["action"] for item in result["items"]]
        self.assertIn("create", actions)

    def test_api_keyword_adds_route_step(self) -> None:
        result = dt.build_patch_plan("add api endpoint", None, [])
        paths = [item["path"] for item in result["items"]]
        self.assertTrue(any("route" in p.lower() for p in paths))

    def test_empty_goal_no_paths_adds_inspect(self) -> None:
        result = dt.build_patch_plan("generic task", None, [])
        actions = [item["action"] for item in result["items"]]
        self.assertIn("inspect", actions)

    def test_notes_not_empty(self) -> None:
        result = dt.build_patch_plan("anything", None, [])
        self.assertGreater(len(result["notes"]), 0)

    def test_staged_not_duplicated_with_current(self) -> None:
        """If current_path is also in staged, it should not appear twice."""
        result = dt.build_patch_plan("fix", "auth.py", ["auth.py", "b.py"])
        paths = [item["path"] for item in result["items"]]
        self.assertEqual(paths.count("auth.py"), 1)


# ─────────────────────────────────────────────────────────────────────────────
# elira_devtools — fs_create / fs_delete / fs_rename (FS ops, patched root)
# ─────────────────────────────────────────────────────────────────────────────

class DevtoolsFSOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._fake_root = Path(self._tmpdir.name).resolve()
        self._orig_root = dt.PROJECT_ROOT
        dt.PROJECT_ROOT = self._fake_root

    def tearDown(self) -> None:
        dt.PROJECT_ROOT = self._orig_root
        self._tmpdir.cleanup()

    def test_fs_create_new_file(self) -> None:
        result, err = dt.fs_create("new_file.py", "print('hello')")
        self.assertIsNone(err)
        self.assertEqual(result["status"], "ok")
        self.assertTrue((self._fake_root / "new_file.py").exists())

    def test_fs_create_outside_root(self) -> None:
        result, err = dt.fs_create("../../escape.py", "evil")
        self.assertIsNone(result)
        self.assertEqual(err, "outside_root")

    def test_fs_create_already_exists(self) -> None:
        (self._fake_root / "existing.py").write_text("x")
        result, err = dt.fs_create("existing.py", "y")
        self.assertIsNone(result)
        self.assertEqual(err, "already_exists")

    def test_fs_delete_existing_file(self) -> None:
        (self._fake_root / "del_me.py").write_text("x")
        result, err = dt.fs_delete("del_me.py")
        self.assertIsNone(err)
        self.assertEqual(result["status"], "ok")
        self.assertFalse((self._fake_root / "del_me.py").exists())

    def test_fs_delete_not_found(self) -> None:
        result, err = dt.fs_delete("nope.py")
        self.assertIsNone(result)
        self.assertEqual(err, "not_found")

    def test_fs_delete_is_directory(self) -> None:
        (self._fake_root / "mydir").mkdir()
        result, err = dt.fs_delete("mydir")
        self.assertIsNone(result)
        self.assertEqual(err, "is_directory")

    def test_fs_rename_success(self) -> None:
        (self._fake_root / "old.py").write_text("x")
        result, err = dt.fs_rename("old.py", "new.py")
        self.assertIsNone(err)
        self.assertEqual(result["status"], "ok")
        self.assertFalse((self._fake_root / "old.py").exists())
        self.assertTrue((self._fake_root / "new.py").exists())

    def test_fs_rename_source_not_found(self) -> None:
        result, err = dt.fs_rename("ghost.py", "new.py")
        self.assertIsNone(result)
        self.assertEqual(err, "source_not_found")

    def test_fs_rename_target_exists(self) -> None:
        (self._fake_root / "src.py").write_text("x")
        (self._fake_root / "dst.py").write_text("y")
        result, err = dt.fs_rename("src.py", "dst.py")
        self.assertIsNone(result)
        self.assertEqual(err, "target_exists")


# ─────────────────────────────────────────────────────────────────────────────
# elira_settings — get_settings / save_settings / get_route_model_map
# (both DB_PATH values patched to same temp file)
# ─────────────────────────────────────────────────────────────────────────────

class EliraSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp_db = Path(self._tmpdir.name) / "state.db"
        # Both elira_settings and elira_memory_sqlite must use the same file
        self._orig_es_db = es.DB_PATH
        self._orig_msql_db = msql.DB_PATH
        es.DB_PATH = tmp_db
        msql.DB_PATH = tmp_db
        # Bootstrap schema (creates settings table + inserts row 1)
        msql.init_db()

    def tearDown(self) -> None:
        es.DB_PATH = self._orig_es_db
        msql.DB_PATH = self._orig_msql_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_get_settings_returns_expected_keys(self) -> None:
        settings = es.get_settings()
        for key in ("ollama_context", "default_model", "agent_profile", "route_model_map"):
            self.assertIn(key, settings)

    def test_get_settings_defaults(self) -> None:
        settings = es.get_settings()
        self.assertEqual(settings["ollama_context"], 8192)
        self.assertEqual(settings["default_model"], "gemma3:4b")

    def test_save_and_get_settings(self) -> None:
        es.save_settings(
            ollama_context=4096,
            default_model="llama3",
            agent_profile="work",
        )
        settings = es.get_settings()
        self.assertEqual(settings["ollama_context"], 4096)
        self.assertEqual(settings["default_model"], "llama3")
        self.assertEqual(settings["agent_profile"], "work")

    def test_save_settings_custom_route_map(self) -> None:
        custom = {"chat": ["gemma3:4b"], "code": ["qwen2.5-coder:7b"]}
        es.save_settings(8192, "gemma3:4b", "default", route_model_map=custom)
        settings = es.get_settings()
        self.assertEqual(settings["route_model_map"]["chat"], ["gemma3:4b"])

    def test_get_route_model_map_has_all_routes(self) -> None:
        route_map = es.get_route_model_map()
        for route in ("code", "project", "research", "chat"):
            self.assertIn(route, route_map)

    def test_route_model_map_values_are_lists(self) -> None:
        route_map = es.get_route_model_map()
        for models in route_map.values():
            self.assertIsInstance(models, list)
            self.assertGreater(len(models), 0)

    def test_default_route_map_constant_is_dict(self) -> None:
        self.assertIsInstance(es.DEFAULT_ROUTE_MAP, dict)
        self.assertIn("chat", es.DEFAULT_ROUTE_MAP)

    def test_save_settings_returns_dict(self) -> None:
        result = es.save_settings(8192, "gemma3:4b", "default")
        self.assertIn("ollama_context", result)
        self.assertIn("route_model_map", result)


if __name__ == "__main__":
    unittest.main()
