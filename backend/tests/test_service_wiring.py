"""Integration tests for service-layer wiring modules.

These modules are thin wrappers that wire DB_PATH + connect_func to the
underlying runtime functions.  Tests patch DB_PATH to a temp file and
verify end-to-end CRUD works through the wiring layer.
"""
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

import app.application.task_planner_service.runtime as tp_svc  # noqa: E402
import app.application.run_history_service.runtime as rh_svc   # noqa: E402
import app.application.image_generation.runtime as img_rt      # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# task_planner_service — integration through wiring layer
# ─────────────────────────────────────────────────────────────────────────────

class TaskPlannerServiceWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = tp_svc.DB_PATH
        tp_svc.DB_PATH = Path(self._tmpdir.name) / "tasks.db"
        tp_svc._init_db()

    def tearDown(self) -> None:
        tp_svc.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_create_and_list(self) -> None:
        result = tp_svc.create_task("Buy milk", category="personal", priority="low")
        self.assertTrue(result["ok"])
        listed = tp_svc.list_tasks()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["tasks"][0]["title"], "Buy milk")

    def test_get_task(self) -> None:
        created = tp_svc.create_task("Get me")
        result = tp_svc.get_task(created["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "Get me")

    def test_update_task(self) -> None:
        created = tp_svc.create_task("Old title")
        tp_svc.update_task(created["id"], title="New title", status="in_progress")
        result = tp_svc.get_task(created["id"])
        self.assertEqual(result["title"], "New title")
        self.assertEqual(result["status"], "in_progress")

    def test_delete_task(self) -> None:
        created = tp_svc.create_task("Delete me")
        tp_svc.delete_task(created["id"])
        self.assertEqual(tp_svc.list_tasks()["count"], 0)

    def test_task_stats_total(self) -> None:
        tp_svc.create_task("A")
        tp_svc.create_task("B")
        stats = tp_svc.task_stats()
        self.assertTrue(stats["ok"])
        self.assertEqual(stats["total"], 2)

    def test_constants_defined(self) -> None:
        self.assertIn("urgent", tp_svc.PRIORITIES)
        self.assertIn("done", tp_svc.STATUSES)

    def test_list_filter_by_status(self) -> None:
        t = tp_svc.create_task("Finish it")
        tp_svc.update_task(t["id"], status="done")
        tp_svc.create_task("Still todo")
        done = tp_svc.list_tasks(status="done")
        todos = tp_svc.list_tasks(status="todo")
        self.assertEqual(done["count"], 1)
        self.assertEqual(todos["count"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# run_history_service — integration through wiring layer
# ─────────────────────────────────────────────────────────────────────────────

class RunHistoryServiceWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = rh_svc.DB_PATH
        self._orig_legacy = rh_svc.LEGACY_JSON_PATHS
        rh_svc.DB_PATH = Path(self._tmpdir.name) / "run_history.db"
        # Suppress legacy-JSON migration so the temp DB starts truly empty
        rh_svc.LEGACY_JSON_PATHS = []
        rh_svc._init_db()

    def tearDown(self) -> None:
        rh_svc.DB_PATH = self._orig_db
        rh_svc.LEGACY_JSON_PATHS = self._orig_legacy
        self._tmpdir.cleanup()
        super().tearDown()

    def _make_service(self):
        return rh_svc.RunHistoryService()

    def test_start_and_finish_run(self) -> None:
        svc = self._make_service()
        run = svc.start_run("hello")
        svc.finish_run(run["run_id"], {"ok": True, "answer": "world", "meta": {"route": "chat", "model_name": "llama3"}})
        rows = svc.list_runs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["route"], "chat")
        self.assertEqual(rows[0]["model"], "llama3")
        self.assertEqual(rows[0]["ok"], 1)

    def test_multiple_runs_listed(self) -> None:
        svc = self._make_service()
        for i in range(3):
            run = svc.start_run(f"query {i}")
            svc.finish_run(run["run_id"], {"ok": True, "answer": "ok", "meta": {}})
        self.assertEqual(len(svc.list_runs()), 3)

    def test_failed_run_recorded(self) -> None:
        svc = self._make_service()
        run = svc.start_run("bad query")
        svc.finish_run(run["run_id"], {"ok": False, "error": "crashed", "meta": {}})
        rows = svc.list_runs()
        self.assertEqual(rows[0]["ok"], 0)
        self.assertEqual(rows[0]["error"], "crashed")

    def test_max_runs_constant_positive(self) -> None:
        self.assertGreater(rh_svc._MAX_RUNS, 0)

    def test_legacy_json_paths_is_list(self) -> None:
        self.assertIsInstance(rh_svc.LEGACY_JSON_PATHS, list)


# ─────────────────────────────────────────────────────────────────────────────
# image_generation — status and unload (no VRAM required)
# ─────────────────────────────────────────────────────────────────────────────

class ImageGenerationStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        # Reset pipeline state before each test
        img_rt._pipe = None

    def tearDown(self) -> None:
        img_rt._pipe = None

    def test_get_status_no_model_loaded(self) -> None:
        with patch.object(img_rt, "_HAS_TORCH", False):
            result = img_rt.get_status()
        self.assertTrue(result["ok"])
        self.assertFalse(result["loaded"])
        self.assertEqual(result["model"], img_rt._MODEL_ID)
        self.assertEqual(result["gpu"], "CPU only")

    def test_get_status_model_loaded(self) -> None:
        img_rt._pipe = object()   # any truthy value
        with patch.object(img_rt, "_HAS_TORCH", False):
            result = img_rt.get_status()
        self.assertTrue(result["loaded"])

    def test_unload_model(self) -> None:
        img_rt._pipe = object()
        result = img_rt.unload_model()
        self.assertTrue(result["ok"])
        self.assertIsNone(img_rt._pipe)

    def test_unload_model_when_not_loaded(self) -> None:
        result = img_rt.unload_model()
        self.assertTrue(result["ok"])

    def test_generate_no_diffusers_returns_error(self) -> None:
        """generate_image gracefully handles missing diffusers library."""
        import sys
        # Ensure diffusers appears absent at import time inside _get_pipe
        with patch.dict(sys.modules, {"diffusers": None}):
            result = img_rt.generate_image("a cat")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_model_id_is_flux(self) -> None:
        self.assertIn("FLUX", img_rt._MODEL_ID)

    def test_output_dir_created(self) -> None:
        self.assertTrue(img_rt.OUTPUT_DIR.exists())


if __name__ == "__main__":
    unittest.main()
