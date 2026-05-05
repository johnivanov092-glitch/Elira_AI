"""Tests for application/autopipeline (CRUD + scheduler state) and
application/run_history_service (thin wrapper class)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.autopipeline.runtime as ap_rt        # noqa: E402
import app.application.run_history_service.runtime as rhs_rt  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# autopipeline — CRUD + scheduler with patched DB_PATH
# ─────────────────────────────────────────────────────────────────────────────

class AutopipelineCRUDTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = ap_rt.DB_PATH
        # Redirect to temp DB and stop the module-level scheduler
        ap_rt.DB_PATH = Path(self._tmpdir.name) / "autopipelines.db"
        ap_rt._init_db()
        ap_rt.stop_scheduler()

    def tearDown(self) -> None:
        ap_rt.stop_scheduler()
        ap_rt.DB_PATH = self._orig_db
        self._tmpdir.cleanup()

    # ── create_pipeline ───────────────────────────────────────────────────────

    def test_create_pipeline_ok(self) -> None:
        result = ap_rt.create_pipeline("My Pipeline", task_type="prompt")
        self.assertTrue(result["ok"])
        self.assertIn("id", result)

    def test_create_pipeline_has_name(self) -> None:
        result = ap_rt.create_pipeline("Alpha Task", task_type="http")
        self.assertEqual(result["name"], "Alpha Task")

    def test_create_pipeline_has_next_run(self) -> None:
        result = ap_rt.create_pipeline("Timer Task", interval_minutes=5)
        self.assertIn("next_run", result)
        self.assertIsNotNone(result["next_run"])

    def test_create_pipeline_with_task_data(self) -> None:
        result = ap_rt.create_pipeline(
            "Prompt Task",
            task_type="prompt",
            task_data={"prompt": "Summarize today", "model": "gemma3:4b"},
        )
        self.assertTrue(result["ok"])

    # ── list_pipelines ────────────────────────────────────────────────────────

    def test_list_pipelines_empty(self) -> None:
        result = ap_rt.list_pipelines()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_list_pipelines_after_create(self) -> None:
        ap_rt.create_pipeline("Pipe A")
        result = ap_rt.list_pipelines()
        self.assertEqual(result["count"], 1)

    def test_list_pipelines_has_items_key(self) -> None:
        result = ap_rt.list_pipelines()
        self.assertIn("pipelines", result)

    def test_list_pipelines_multiple(self) -> None:
        ap_rt.create_pipeline("Pipe B1")
        ap_rt.create_pipeline("Pipe B2")
        result = ap_rt.list_pipelines()
        self.assertEqual(result["count"], 2)

    # ── get_pipeline ──────────────────────────────────────────────────────────

    def test_get_pipeline_found(self) -> None:
        pid = ap_rt.create_pipeline("Fetch Me")["id"]
        result = ap_rt.get_pipeline(pid)
        self.assertTrue(result["ok"])
        self.assertEqual(result["name"], "Fetch Me")

    def test_get_pipeline_not_found(self) -> None:
        result = ap_rt.get_pipeline("nonexistent_id_xyz")
        self.assertFalse(result["ok"])

    def test_get_pipeline_task_data_parsed(self) -> None:
        pid = ap_rt.create_pipeline("JSON Task", task_data={"key": "value"})["id"]
        result = ap_rt.get_pipeline(pid)
        self.assertIsInstance(result["task_data"], dict)

    def test_get_pipeline_enabled_is_bool(self) -> None:
        pid = ap_rt.create_pipeline("Bool Check")["id"]
        result = ap_rt.get_pipeline(pid)
        self.assertIsInstance(result["enabled"], bool)

    # ── update_pipeline ───────────────────────────────────────────────────────

    def test_update_pipeline_name(self) -> None:
        pid = ap_rt.create_pipeline("Old Name")["id"]
        result = ap_rt.update_pipeline(pid, name="New Name")
        self.assertTrue(result["ok"])
        self.assertIn("name", result["updated"])

    def test_update_pipeline_interval(self) -> None:
        pid = ap_rt.create_pipeline("Interval Task")["id"]
        result = ap_rt.update_pipeline(pid, interval_minutes=120)
        self.assertTrue(result["ok"])

    def test_update_pipeline_no_valid_fields(self) -> None:
        pid = ap_rt.create_pipeline("No Fields")["id"]
        result = ap_rt.update_pipeline(pid, unknown_field="ignored")
        self.assertFalse(result["ok"])

    def test_update_pipeline_enabled_bool(self) -> None:
        pid = ap_rt.create_pipeline("Toggle Task")["id"]
        result = ap_rt.update_pipeline(pid, enabled=False)
        self.assertTrue(result["ok"])
        info = ap_rt.get_pipeline(pid)
        self.assertFalse(info["enabled"])

    # ── delete_pipeline ───────────────────────────────────────────────────────

    def test_delete_pipeline_ok(self) -> None:
        pid = ap_rt.create_pipeline("Delete Me")["id"]
        result = ap_rt.delete_pipeline(pid)
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], pid)

    def test_delete_pipeline_removes_from_list(self) -> None:
        pid = ap_rt.create_pipeline("Gone Soon")["id"]
        ap_rt.delete_pipeline(pid)
        self.assertEqual(ap_rt.list_pipelines()["count"], 0)

    def test_delete_pipeline_get_fails_after(self) -> None:
        pid = ap_rt.create_pipeline("Short Lived")["id"]
        ap_rt.delete_pipeline(pid)
        result = ap_rt.get_pipeline(pid)
        self.assertFalse(result["ok"])

    # ── get_pipeline_logs ─────────────────────────────────────────────────────

    def test_get_pipeline_logs_empty(self) -> None:
        pid = ap_rt.create_pipeline("Log Checker")["id"]
        result = ap_rt.get_pipeline_logs(pid)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_get_pipeline_logs_has_logs_key(self) -> None:
        pid = ap_rt.create_pipeline("Log Key Check")["id"]
        result = ap_rt.get_pipeline_logs(pid)
        self.assertIn("logs", result)
        self.assertIsInstance(result["logs"], list)

    # ── scheduler_status / start / stop ──────────────────────────────────────

    def test_scheduler_status_ok(self) -> None:
        result = ap_rt.scheduler_status()
        self.assertTrue(result["ok"])

    def test_scheduler_status_has_running(self) -> None:
        result = ap_rt.scheduler_status()
        self.assertIn("running", result)

    def test_scheduler_stopped_in_setUp(self) -> None:
        result = ap_rt.scheduler_status()
        self.assertFalse(result["running"])

    def test_start_scheduler_sets_running(self) -> None:
        ap_rt.start_scheduler()
        self.assertTrue(ap_rt.scheduler_status()["running"])

    def test_stop_scheduler_clears_running(self) -> None:
        ap_rt.start_scheduler()
        ap_rt.stop_scheduler()
        self.assertFalse(ap_rt.scheduler_status()["running"])

    def test_start_scheduler_idempotent(self) -> None:
        ap_rt.start_scheduler()
        result = ap_rt.start_scheduler()  # Second call → already_running
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already_running")

    def test_scheduler_status_has_tick_interval(self) -> None:
        result = ap_rt.scheduler_status()
        self.assertIn("tick_interval", result)
        self.assertIsInstance(result["tick_interval"], int)


# ─────────────────────────────────────────────────────────────────────────────
# run_history_service — thin wrapper class (uses real shared DB)
# ─────────────────────────────────────────────────────────────────────────────

class RunHistoryServiceWrapperTest(unittest.TestCase):
    """RunHistoryService: start_run / finish_run / list_runs / add_event."""

    def setUp(self) -> None:
        self._svc = rhs_rt.RunHistoryService()

    def test_service_is_instantiable(self) -> None:
        self.assertIsNotNone(self._svc)

    def test_service_has_start_run(self) -> None:
        self.assertTrue(hasattr(self._svc, "start_run"))
        self.assertTrue(callable(self._svc.start_run))

    def test_service_has_finish_run(self) -> None:
        self.assertTrue(hasattr(self._svc, "finish_run"))

    def test_service_has_list_runs(self) -> None:
        self.assertTrue(hasattr(self._svc, "list_runs"))

    def test_service_has_add_event(self) -> None:
        self.assertTrue(hasattr(self._svc, "add_event"))

    def test_start_run_returns_dict(self) -> None:
        result = self._svc.start_run("test query")
        self.assertIsInstance(result, dict)

    def test_start_run_has_run_id(self) -> None:
        result = self._svc.start_run("what is Python")
        self.assertIn("run_id", result)

    def test_start_run_has_user_input(self) -> None:
        result = self._svc.start_run("explain lists")
        self.assertEqual(result["user_input"], "explain lists")

    def test_finish_run_returns_none(self) -> None:
        run = self._svc.start_run("finish test")
        result = self._svc.finish_run(run["run_id"], {"ok": True, "answer_len": 20})
        self.assertIsNone(result)

    def test_list_runs_returns_list(self) -> None:
        result = self._svc.list_runs(limit=5)
        self.assertIsInstance(result, list)

    def test_list_runs_after_start_finish(self) -> None:
        run = self._svc.start_run("list test query")
        self._svc.finish_run(run["run_id"], {"ok": True, "answer_len": 10})
        results = self._svc.list_runs(limit=50)
        run_ids = [r.get("run_id") for r in results]
        self.assertIn(run["run_id"], run_ids)

    def test_add_event_does_not_raise(self) -> None:
        run = self._svc.start_run("event test query")
        # Should not raise any exception
        self._svc.add_event(run["run_id"], "tool_call", {"tool": "search"})

    def test_module_db_path_defined(self) -> None:
        self.assertIsNotNone(rhs_rt.DB_PATH)
        self.assertIsInstance(rhs_rt.DB_PATH, Path)


if __name__ == "__main__":
    unittest.main()
