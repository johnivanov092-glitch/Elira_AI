"""Tests for application/project_brain_engine, application/project_brain,
application/project_brain_loop_service, application/project_map_service,
application/project_patch_service, and application/runtime_status."""
from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.project_brain_engine.runtime import ProjectBrainEngineService  # noqa: E402
from app.application.project_brain_loop_service.runtime import ProjectBrainLoopService  # noqa: E402
from app.application.project_map_service.runtime import ProjectMapService  # noqa: E402
import app.application.runtime_status.runtime as rs_rt  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# project_brain_engine — ProjectBrainEngineService (async)
# ─────────────────────────────────────────────────────────────────────────────

class ProjectBrainEngineHealthTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name).resolve()
        self._svc = ProjectBrainEngineService(project_root=str(self._root))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_health_ok(self) -> None:
        result = self._run(self._svc.health())
        self.assertEqual(result["status"], "ok")
        self.assertIn("project_root", result)
        self.assertIn("integrations", result)

    def test_health_all_integrations_false_when_none(self) -> None:
        result = self._run(self._svc.health())
        for v in result["integrations"].values():
            self.assertFalse(v)

    def test_health_integration_true_when_provided(self) -> None:
        svc = ProjectBrainEngineService(
            project_root=str(self._root),
            event_bus=MagicMock(),
        )
        result = self._run(svc.health())
        self.assertTrue(result["integrations"]["event_bus"])
        self.assertFalse(result["integrations"]["dependency_graph_service"])


class ProjectBrainEngineSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name).resolve()
        # Seed some files
        (self._root / "src").mkdir()
        (self._root / "src" / "main.py").write_text("import os\n", encoding="utf-8")
        (self._root / "src" / "utils.py").write_text("def helper(): pass\n", encoding="utf-8")
        (self._root / "README.md").write_text("# Project\n", encoding="utf-8")
        self._svc = ProjectBrainEngineService(project_root=str(self._root))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_snapshot_ok(self) -> None:
        result = self._run(self._svc.build_project_snapshot())
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["files_count"], 0)
        self.assertIn("files", result)

    def test_snapshot_contains_python_files(self) -> None:
        result = self._run(self._svc.build_project_snapshot())
        paths = [f["path"] for f in result["files"]]
        self.assertTrue(any("main.py" in p for p in paths))

    def test_snapshot_nonexistent_dir(self) -> None:
        svc = ProjectBrainEngineService(project_root="/this/does/not/exist")
        result = self._run(svc.build_project_snapshot())
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(result["files"], [])

    def test_snapshot_max_index_files(self) -> None:
        svc = ProjectBrainEngineService(project_root=str(self._root), max_index_files=1)
        result = self._run(svc.build_project_snapshot())
        self.assertLessEqual(result["files_count"], 1)


class ProjectBrainEngineSemanticIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name).resolve()
        (self._root / "app.py").write_text("def run(): pass\n", encoding="utf-8")
        (self._root / "notes.txt").write_text("just a note\n", encoding="utf-8")
        self._svc = ProjectBrainEngineService(project_root=str(self._root))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_semantic_index_ok(self) -> None:
        result = self._run(self._svc.build_semantic_index())
        self.assertEqual(result["status"], "ok")
        self.assertIn("documents", result)
        self.assertIn("documents_count", result)

    def test_semantic_index_only_includes_extensions(self) -> None:
        result = self._run(self._svc.build_semantic_index(include_extensions=[".py"]))
        for doc in result["documents"]:
            self.assertTrue(doc["path"].endswith(".py"))

    def test_chunk_text_empty(self) -> None:
        svc = ProjectBrainEngineService()
        self.assertEqual(svc._chunk_text(""), [])

    def test_chunk_text_short(self) -> None:
        svc = ProjectBrainEngineService()
        chunks = svc._chunk_text("hello world", chunk_size=100)
        self.assertEqual(chunks, ["hello world"])

    def test_chunk_text_splits_long(self) -> None:
        svc = ProjectBrainEngineService()
        text = "a" * 2500
        chunks = svc._chunk_text(text, chunk_size=1000)
        self.assertEqual(len(chunks), 3)
        for ch in chunks:
            self.assertLessEqual(len(ch), 1000)


class ProjectBrainEngineSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._root = Path(self._tmpdir.name).resolve()
        (self._root / "auth.py").write_text("def authenticate(user): pass\n", encoding="utf-8")
        (self._root / "main.py").write_text("import auth\n", encoding="utf-8")
        self._svc = ProjectBrainEngineService(project_root=str(self._root))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_search_finds_match(self) -> None:
        result = self._run(self._svc.search_index("auth"))
        self.assertEqual(result["status"], "ok")
        self.assertGreater(result["results_count"], 0)

    def test_search_no_match(self) -> None:
        result = self._run(self._svc.search_index("nonexistent_xyz_abc_123"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["results_count"], 0)

    def test_search_respects_limit(self) -> None:
        result = self._run(self._svc.search_index("a", limit=1))
        self.assertLessEqual(len(result["results"]), 1)

    def test_analyze_project_goal_ok(self) -> None:
        result = self._run(self._svc.analyze_project_goal("authenticate users"))
        self.assertEqual(result["status"], "ok")
        self.assertIn("goal", result)
        self.assertIn("snapshot_summary", result)
        self.assertIn("analysis", result)

    def test_create_refactor_plan_ok(self) -> None:
        result = self._run(self._svc.create_refactor_plan("refactor auth module"))
        self.assertEqual(result["status"], "ok")
        self.assertIn("plan_id", result)
        self.assertIn("steps", result)
        self.assertIsInstance(result["steps"], list)
        self.assertGreater(len(result["steps"]), 0)


# ─────────────────────────────────────────────────────────────────────────────
# project_brain_loop_service — ProjectBrainLoopService (stub)
# ─────────────────────────────────────────────────────────────────────────────

class ProjectBrainLoopServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._svc = ProjectBrainLoopService()

    def test_run_loop_returns_stub(self) -> None:
        result = self._svc.run_loop()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "stub")

    def test_run_loop_accepts_args(self) -> None:
        result = self._svc.run_loop("arg1", key="val")
        self.assertFalse(result["ok"])

    def test_analyze_returns_stub(self) -> None:
        result = self._svc.analyze()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "stub")

    def test_analyze_accepts_kwargs(self) -> None:
        result = self._svc.analyze(goal="do something")
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# project_map_service — ProjectMapService (stub)
# ─────────────────────────────────────────────────────────────────────────────

class ProjectMapServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._svc = ProjectMapService()

    def test_build_returns_stub(self) -> None:
        result = self._svc.build()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "stub")

    def test_get_map_returns_stub(self) -> None:
        result = self._svc.get_map()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "stub")

    def test_build_accepts_args(self) -> None:
        result = self._svc.build("arg", k="v")
        self.assertFalse(result["ok"])

    def test_get_map_accepts_args(self) -> None:
        result = self._svc.get_map("some_id")
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# runtime_status — get_runtime_status (mocked deps)
# ─────────────────────────────────────────────────────────────────────────────

class RuntimeStatusTest(unittest.TestCase):
    def _fake_persona_status(self):
        return {"active_version": 1, "model": "gemma3:4b"}

    def _fake_web_status(self):
        return {
            "primary_engine": "duckduckgo",
            "fallback_engines": [],
            "available_engines": ["duckduckgo"],
            "supported_engines": ["duckduckgo", "tavily"],
            "api_keys_present": {"tavily": False},
            "degraded_mode": False,
            "warnings": [],
        }

    def test_get_runtime_status_ok(self) -> None:
        with patch("app.application.runtime_status.runtime.get_persona_status",
                   return_value=self._fake_persona_status()), \
             patch("app.application.runtime_status.runtime.get_web_engine_status",
                   return_value=self._fake_web_status()), \
             patch("app.application.runtime_status.runtime.init_db"), \
             patch("app.application.runtime_status.runtime._chat_count_for", return_value=3):
            result = rs_rt.get_runtime_status()
        self.assertTrue(result["ok"])

    def test_get_runtime_status_required_keys(self) -> None:
        with patch("app.application.runtime_status.runtime.get_persona_status",
                   return_value=self._fake_persona_status()), \
             patch("app.application.runtime_status.runtime.get_web_engine_status",
                   return_value=self._fake_web_status()), \
             patch("app.application.runtime_status.runtime.init_db"), \
             patch("app.application.runtime_status.runtime._chat_count_for", return_value=0):
            result = rs_rt.get_runtime_status()
        for key in ("python_executable", "process_id", "cwd", "data_dir",
                    "active_db_path", "active_chat_count", "storage_mode",
                    "persona_version", "primary_engine"):
            self.assertIn(key, result)

    def test_get_runtime_status_chat_count(self) -> None:
        with patch("app.application.runtime_status.runtime.get_persona_status",
                   return_value=self._fake_persona_status()), \
             patch("app.application.runtime_status.runtime.get_web_engine_status",
                   return_value=self._fake_web_status()), \
             patch("app.application.runtime_status.runtime.init_db"), \
             patch("app.application.runtime_status.runtime._chat_count_for", return_value=7):
            result = rs_rt.get_runtime_status()
        self.assertEqual(result["active_chat_count"], 7)

    def test_get_runtime_status_persona_version(self) -> None:
        with patch("app.application.runtime_status.runtime.get_persona_status",
                   return_value={"active_version": 5}), \
             patch("app.application.runtime_status.runtime.get_web_engine_status",
                   return_value=self._fake_web_status()), \
             patch("app.application.runtime_status.runtime.init_db"), \
             patch("app.application.runtime_status.runtime._chat_count_for", return_value=0):
            result = rs_rt.get_runtime_status()
        self.assertEqual(result["persona_version"], 5)

    def test_get_runtime_status_degraded_mode(self) -> None:
        web_degraded = self._fake_web_status()
        web_degraded["degraded_mode"] = True
        web_degraded["warnings"] = ["no api key for tavily"]
        with patch("app.application.runtime_status.runtime.get_persona_status",
                   return_value=self._fake_persona_status()), \
             patch("app.application.runtime_status.runtime.get_web_engine_status",
                   return_value=web_degraded), \
             patch("app.application.runtime_status.runtime.init_db"), \
             patch("app.application.runtime_status.runtime._chat_count_for", return_value=0):
            result = rs_rt.get_runtime_status()
        self.assertTrue(result["degraded_mode"])
        self.assertIsNotNone(result["warning"])

    def test_get_runtime_status_no_warnings_when_clean(self) -> None:
        with patch("app.application.runtime_status.runtime.get_persona_status",
                   return_value=self._fake_persona_status()), \
             patch("app.application.runtime_status.runtime.get_web_engine_status",
                   return_value=self._fake_web_status()), \
             patch("app.application.runtime_status.runtime.init_db"), \
             patch("app.application.runtime_status.runtime._chat_count_for", return_value=0):
            result = rs_rt.get_runtime_status()
        self.assertIsNone(result["warning"])
        self.assertEqual(result["web_warnings"], [])

    def test_init_runtime_state_calls_init_db_and_returns_status(self) -> None:
        init_called = []
        with patch("app.application.runtime_status.runtime.get_persona_status",
                   return_value=self._fake_persona_status()), \
             patch("app.application.runtime_status.runtime.get_web_engine_status",
                   return_value=self._fake_web_status()), \
             patch("app.application.runtime_status.runtime.init_db",
                   side_effect=lambda: init_called.append(1)), \
             patch("app.application.runtime_status.runtime._chat_count_for", return_value=0):
            result = rs_rt.init_runtime_state()
        self.assertTrue(result["ok"])
        # init_db is called at least once (once for init_runtime_state, once inside get_runtime_status)
        self.assertGreaterEqual(len(init_called), 1)

    def test_storage_mode_rooted(self) -> None:
        # _storage_mode returns a string
        mode = rs_rt._storage_mode()
        self.assertIsInstance(mode, str)
        self.assertIn(mode, ("rooted_sqlite", "custom_data_dir"))

    def test_chat_count_for_bad_path_returns_zero(self) -> None:
        # A nonexistent DB path should return 0, not raise
        result = rs_rt._chat_count_for(Path("/nonexistent/path/to.db"))
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
