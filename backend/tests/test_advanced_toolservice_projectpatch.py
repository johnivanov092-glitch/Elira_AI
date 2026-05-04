"""Tests for application/advanced (project FS ops), application/tool_service
(thin adapter wrappers), and application/project_patch (class-based runtime)."""
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

import app.application.advanced.runtime as adv_rt                    # noqa: E402
import app.application.tool_service.runtime as ts_rt                 # noqa: E402
from app.application.project_patch.runtime import ProjectPatchRuntime  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# advanced — open/info/tree/read/search/close (module-level _project_path)
# ─────────────────────────────────────────────────────────────────────────────

class AdvancedRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name).resolve()
        # Create some files and subdirs
        (self._root / "src").mkdir()
        (self._root / "src" / "main.py").write_text("import os\nx = 1\n", encoding="utf-8")
        (self._root / "src" / "utils.py").write_text("def helper(): pass\n", encoding="utf-8")
        (self._root / "README.md").write_text("# Test Project\n", encoding="utf-8")
        # Reset module state
        self._orig_path = adv_rt._project_path
        adv_rt._project_path = ""

    def tearDown(self) -> None:
        adv_rt._project_path = self._orig_path
        self._tmpdir.cleanup()

    # ── open_project ──────────────────────────────────────────────────────────

    def test_open_existing_dir_ok(self) -> None:
        r = adv_rt.open_project(str(self._root))
        self.assertTrue(r["ok"])
        self.assertIn("path", r)
        self.assertIn("name", r)

    def test_open_nonexistent_dir_fails(self) -> None:
        r = adv_rt.open_project("/this/does/not/exist")
        self.assertFalse(r["ok"])

    def test_open_file_as_dir_fails(self) -> None:
        r = adv_rt.open_project(str(self._root / "README.md"))
        self.assertFalse(r["ok"])

    # ── get_project_info ──────────────────────────────────────────────────────

    def test_get_project_info_no_project_open(self) -> None:
        r = adv_rt.get_project_info()
        self.assertFalse(r["ok"])

    def test_get_project_info_after_open(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.get_project_info()
        self.assertTrue(r["ok"])
        self.assertIn("path", r)
        self.assertIn("name", r)

    # ── project_tree ─────────────────────────────────────────────────────────

    def test_project_tree_no_project(self) -> None:
        r = adv_rt.project_tree()
        self.assertFalse(r["ok"])

    def test_project_tree_after_open(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.project_tree()
        self.assertTrue(r["ok"])
        self.assertIn("items", r)
        names = {item["name"] for item in r["items"]}
        self.assertIn("src", names)
        self.assertIn("README.md", names)

    def test_project_tree_max_items(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.project_tree(max_items=1)
        self.assertLessEqual(r["count"], 1)

    # ── read_project_file ─────────────────────────────────────────────────────

    def test_read_file_no_project(self) -> None:
        r = adv_rt.read_project_file("src/main.py")
        self.assertFalse(r["ok"])

    def test_read_file_success(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.read_project_file("src/main.py")
        self.assertTrue(r["ok"])
        self.assertIn("import os", r["content"])

    def test_read_nonexistent_file(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.read_project_file("missing.py")
        self.assertFalse(r["ok"])

    def test_read_file_path_escape_rejected(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.read_project_file("../../etc/passwd")
        self.assertFalse(r["ok"])

    # ── search_in_project ─────────────────────────────────────────────────────

    def test_search_no_project(self) -> None:
        r = adv_rt.search_in_project("import")
        self.assertFalse(r["ok"])

    def test_search_finds_match(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.search_in_project("import os")
        self.assertTrue(r["ok"])
        self.assertGreater(r["count"], 0)
        self.assertTrue(any("main.py" in item["path"] for item in r["items"]))

    def test_search_no_match(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.search_in_project("xyz_nonexistent_pattern_123")
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 0)

    # ── close_project ─────────────────────────────────────────────────────────

    def test_close_project(self) -> None:
        adv_rt.open_project(str(self._root))
        r = adv_rt.close_project()
        self.assertTrue(r["ok"])
        # After close, project info should fail
        info = adv_rt.get_project_info()
        self.assertFalse(info["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# tool_service — thin adapter wrappers (mocked dependencies)
# ─────────────────────────────────────────────────────────────────────────────

class ToolServiceListToolsTest(unittest.TestCase):
    def test_list_tools_ok(self) -> None:
        fake_tools = [
            {"name": "tool_a", "category": "general"},
            {"name": "tool_b", "category": "dev"},
        ]
        with patch(
            "app.application.tool_registry.runtime.list_tools_with_schemas",
            return_value=fake_tools,
        ):
            result = ts_rt.list_tools()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["tools"], fake_tools)

    def test_list_tools_empty(self) -> None:
        with patch(
            "app.application.tool_registry.runtime.list_tools_with_schemas",
            return_value=[],
        ):
            result = ts_rt.list_tools()
        self.assertEqual(result["count"], 0)


class ToolServiceSearchMemoryTest(unittest.TestCase):
    def test_search_memory_tool_adds_profile(self) -> None:
        fake_result = {"items": [{"text": "Python fact"}], "count": 1}
        with patch("app.application.smart_memory.search_memory", return_value=fake_result):
            result = ts_rt.search_memory_tool("work", "python")
        self.assertEqual(result["profile"], "work")
        self.assertEqual(result["count"], 1)

    def test_search_memory_tool_default_profile(self) -> None:
        with patch("app.application.smart_memory.search_memory", return_value={"items": [], "count": 0}):
            result = ts_rt.search_memory_tool("", "query")
        self.assertEqual(result["profile"], "default")

    def test_search_memory_tool_limit_floor(self) -> None:
        captured = {}
        def fake_search(**kwargs):
            captured.update(kwargs)
            return {"items": [], "count": 0}
        with patch("app.application.smart_memory.search_memory", side_effect=fake_search):
            ts_rt.search_memory_tool("default", "q", limit=0)
        self.assertGreaterEqual(captured.get("limit", 0), 1)


class ToolServiceRunToolTest(unittest.TestCase):
    def test_run_tool_success(self) -> None:
        fake_result = {"ok": True, "output": "done"}
        with patch("app.application.tool_registry.runtime.execute_tool", return_value=fake_result), \
             patch("app.application.event_bus.runtime.emit_event"):
            result = ts_rt.run_tool("my_tool", {"arg": "val"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["output"], "done")

    def test_run_tool_failure(self) -> None:
        fake_result = {"ok": False, "error": "not found"}
        with patch("app.application.tool_registry.runtime.execute_tool", return_value=fake_result), \
             patch("app.application.event_bus.runtime.emit_event"):
            result = ts_rt.run_tool("bad_tool", {})
        self.assertFalse(result["ok"])

    def test_run_tool_event_bus_failure_does_not_raise(self) -> None:
        fake_result = {"ok": True}
        with patch("app.application.tool_registry.runtime.execute_tool", return_value=fake_result), \
             patch("app.application.event_bus.runtime.emit_event", side_effect=Exception("bus error")):
            # Should NOT raise, event bus errors are swallowed
            result = ts_rt.run_tool("my_tool", None)
        self.assertTrue(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# project_patch — ProjectPatchRuntime (class-based, callback-driven)
# ─────────────────────────────────────────────────────────────────────────────

def _make_ppr(tmp_root: Path):
    """Build a ProjectPatchRuntime with simple in-memory file store."""
    file_store: dict[str, str] = {}

    def read_file(path: str, max_chars: int = 500000) -> dict:
        if path not in file_store:
            return {"ok": False, "error": f"File not found: {path}"}
        return {"ok": True, "path": path, "content": file_store[path]}

    def write_file(path: str, content: str) -> dict:
        file_store[path] = content
        return {"ok": True, "path": path}

    ppr = ProjectPatchRuntime(
        project_root=tmp_root,
        read_project_file_func=read_file,
        write_project_file_func=write_file,
    )
    return ppr, file_store


class ProjectPatchRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name).resolve()
        self._ppr, self._store = _make_ppr(self._root)
        # Seed a file
        self._store["app.py"] = "x = 1\ny = 2\n"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_preview_patch_not_found(self) -> None:
        r = self._ppr.preview_patch("missing.py", "new content")
        self.assertFalse(r["ok"])

    def test_preview_patch_changed(self) -> None:
        r = self._ppr.preview_patch("app.py", "x = 99\ny = 2\n")
        self.assertTrue(r["ok"])
        self.assertTrue(r["changed"])
        self.assertIn("diff", r)
        self.assertIn("-x = 1", r["diff"])

    def test_preview_patch_unchanged(self) -> None:
        r = self._ppr.preview_patch("app.py", "x = 1\ny = 2\n")
        self.assertTrue(r["ok"])
        self.assertFalse(r["changed"])
        self.assertEqual(r["diff"], "")

    def test_preview_patch_hashes(self) -> None:
        r = self._ppr.preview_patch("app.py", "new content")
        self.assertIn("old_hash", r)
        self.assertIn("new_hash", r)
        self.assertNotEqual(r["old_hash"], r["new_hash"])

    def test_apply_patch_success(self) -> None:
        r = self._ppr.apply_patch("app.py", "x = 99\ny = 2\n")
        self.assertTrue(r["ok"])
        self.assertTrue(r["changed"])
        self.assertEqual(self._store["app.py"], "x = 99\ny = 2\n")

    def test_apply_patch_no_change(self) -> None:
        r = self._ppr.apply_patch("app.py", "x = 1\ny = 2\n")
        self.assertTrue(r["ok"])
        self.assertFalse(r["changed"])

    def test_replace_in_file_success(self) -> None:
        r = self._ppr.replace_in_file("app.py", "x = 1", "x = 42")
        self.assertTrue(r["ok"])
        self.assertTrue(r["changed"])

    def test_replace_in_file_old_text_not_found(self) -> None:
        r = self._ppr.replace_in_file("app.py", "not_there", "replacement")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_list_backups_empty_initially(self) -> None:
        r = self._ppr.list_backups()
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 0)

    def test_list_backups_after_apply(self) -> None:
        self._ppr.apply_patch("app.py", "changed content here\n")
        r = self._ppr.list_backups()
        self.assertTrue(r["ok"])
        self.assertGreater(r["count"], 0)

    def test_rollback_after_apply(self) -> None:
        original = self._store["app.py"]
        r = self._ppr.apply_patch("app.py", "changed content here\n")
        backup_id = r["backup"]["backup_id"]
        rollback = self._ppr.rollback_patch("app.py", backup_id)
        self.assertTrue(rollback["ok"])
        self.assertEqual(self._store["app.py"], original)

    def test_rollback_missing_backup(self) -> None:
        r = self._ppr.rollback_patch("app.py", "nonexistent_backup_id")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
