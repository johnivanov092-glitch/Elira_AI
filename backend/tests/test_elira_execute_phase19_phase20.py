"""Tests for application/elira_execute, application/elira_phase19,
and application/elira_phase20 runtimes."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.elira_execute.runtime as exe_rt      # noqa: E402
import app.application.elira_phase19.runtime as p19_rt      # noqa: E402
import app.application.elira_phase20.runtime as p20_rt      # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# elira_execute — build_mode_reply (pure)
# ─────────────────────────────────────────────────────────────────────────────

class BuildModeReplyTest(unittest.TestCase):
    def _reply(self, content, mode, model=None, profile=None):
        return exe_rt.build_mode_reply(content, mode, model, profile)

    def test_chat_mode_default(self) -> None:
        r = self._reply("hello", "chat")
        self.assertEqual(r["mode"], "chat")
        self.assertEqual(r["status"], "ok")
        self.assertIn("hello", r["assistant_content"])

    def test_code_mode(self) -> None:
        r = self._reply("sort list", "code")
        self.assertEqual(r["mode"], "code")
        self.assertIn("Code mode", r["assistant_content"])

    def test_research_mode(self) -> None:
        r = self._reply("quantum", "research")
        self.assertEqual(r["mode"], "research")
        self.assertIn("Research mode", r["assistant_content"])

    def test_image_mode(self) -> None:
        r = self._reply("cat picture", "image")
        self.assertEqual(r["mode"], "image")
        self.assertIn("Image", r["assistant_content"])

    def test_orchestrator_mode(self) -> None:
        r = self._reply("complex task", "orchestrator")
        self.assertEqual(r["mode"], "orchestrator")
        self.assertIn("Orchestrator", r["assistant_content"])

    def test_unknown_mode_falls_back_to_chat(self) -> None:
        r = self._reply("hello", "unknown_xyz")
        self.assertEqual(r["mode"], "unknown_xyz")
        self.assertIn("Chat mode", r["assistant_content"])

    def test_mode_lowercased(self) -> None:
        r = self._reply("hello", "CODE")
        self.assertEqual(r["mode"], "code")
        self.assertIn("Code mode", r["assistant_content"])

    def test_model_and_profile_passed_through(self) -> None:
        r = self._reply("q", "chat", model="gemma", profile="work")
        self.assertEqual(r["model"], "gemma")
        self.assertEqual(r["agent_profile"], "work")

    def test_content_stripped(self) -> None:
        r = self._reply("  hello  ", "chat")
        self.assertIn("hello", r["assistant_content"])

    def test_none_mode_defaults_to_chat(self) -> None:
        r = self._reply("hi", None)
        self.assertEqual(r["mode"], "chat")


# ─────────────────────────────────────────────────────────────────────────────
# elira_execute — memory CRUD (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class EliraExecuteMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = exe_rt.DB_PATH
        exe_rt.DB_PATH = Path(self._tmpdir.name) / "exec.db"
        exe_rt.ensure_db()

    def tearDown(self) -> None:
        exe_rt.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_list_memory_empty(self) -> None:
        result = exe_rt.list_memory()
        self.assertEqual(result["items"], [])

    def test_save_and_list_memory(self) -> None:
        exe_rt.save_memory("Python is great", None, "notes", "manual", False)
        result = exe_rt.list_memory()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["content"], "Python is great")

    def test_save_memory_returns_id(self) -> None:
        r = exe_rt.save_memory("hello", None, None, "chat", False)
        self.assertIn("id", r)
        self.assertIsNotNone(r["id"])

    def test_save_memory_pinned(self) -> None:
        exe_rt.save_memory("pinned note", None, "title", "manual", True)
        items = exe_rt.list_memory()["items"]
        self.assertTrue(items[0]["pinned"])

    def test_list_memory_search_filter(self) -> None:
        exe_rt.save_memory("Python is great", None, None, "manual", False)
        exe_rt.save_memory("JavaScript rules", None, None, "manual", False)
        result = exe_rt.list_memory("Python")
        self.assertEqual(len(result["items"]), 1)
        self.assertIn("Python", result["items"][0]["content"])

    def test_delete_memory(self) -> None:
        r = exe_rt.save_memory("to delete", None, None, "manual", False)
        mem_id = r["id"]
        exe_rt.delete_memory(mem_id)
        result = exe_rt.list_memory()
        self.assertEqual(result["items"], [])

    def test_delete_memory_returns_status_ok(self) -> None:
        r = exe_rt.save_memory("x", None, None, "manual", False)
        result = exe_rt.delete_memory(r["id"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["deleted_id"], r["id"])

    def test_multiple_items_ordered_by_pinned(self) -> None:
        exe_rt.save_memory("unpinned", None, None, "manual", False)
        exe_rt.save_memory("pinned", None, None, "manual", True)
        items = exe_rt.list_memory()["items"]
        self.assertTrue(items[0]["pinned"])


# ─────────────────────────────────────────────────────────────────────────────
# elira_phase19 — helpers (pure)
# ─────────────────────────────────────────────────────────────────────────────

class Phase19HelpersTest(unittest.TestCase):
    def test_dumps_loads_round_trip(self) -> None:
        data = {"key": "value", "list": [1, 2]}
        self.assertEqual(p19_rt.loads(p19_rt.dumps(data)), data)

    def test_loads_none_returns_none(self) -> None:
        self.assertIsNone(p19_rt.loads(None))

    def test_build_project_reasoning_has_required_keys(self) -> None:
        r = p19_rt.build_project_reasoning("fix auth bug", ["backend/auth.py"])
        for key in ("scope", "goal_summary", "selected_paths", "advice"):
            self.assertIn(key, r)

    def test_build_project_reasoning_scope_backend(self) -> None:
        r = p19_rt.build_project_reasoning("add api route", [])
        self.assertEqual(r["scope"], "backend")

    def test_build_project_reasoning_scope_ui(self) -> None:
        r = p19_rt.build_project_reasoning("add ui button", [])
        self.assertEqual(r["scope"], "ui")

    def test_build_project_reasoning_scope_multi(self) -> None:
        r = p19_rt.build_project_reasoning("multi-file workflow update", [])
        self.assertEqual(r["scope"], "multi-file")

    def test_build_multi_file_plan_with_selected_paths(self) -> None:
        plan = p19_rt.build_multi_file_plan("fix", ["backend/a.py", "backend/b.py"], [])
        actions = [s["action"] for s in plan]
        self.assertTrue(all(a == "modify" for a in actions))
        self.assertEqual(len(plan), 2)

    def test_build_multi_file_plan_create_keyword(self) -> None:
        plan = p19_rt.build_multi_file_plan("create new component", [], [])
        actions = [s["action"] for s in plan]
        self.assertIn("create", actions)

    def test_build_multi_file_plan_api_keyword(self) -> None:
        plan = p19_rt.build_multi_file_plan("add api endpoint", [], [])
        paths = [s["path"] for s in plan]
        self.assertTrue(any("route" in p.lower() for p in paths))

    def test_build_multi_file_plan_empty_goal_no_selected_falls_back(self) -> None:
        plan = p19_rt.build_multi_file_plan("fix stuff", [], ["project/main.py"])
        self.assertGreaterEqual(len(plan), 1)

    def test_build_file_operations_modify(self) -> None:
        plan = [{"action": "modify", "path": "a.py", "reason": "x"}]
        ops = p19_rt.build_file_operations(plan)
        self.assertEqual(ops[0]["operation"], "preview-edit")
        self.assertEqual(ops[0]["status"], "ready")

    def test_build_file_operations_create(self) -> None:
        plan = [{"action": "create", "path": "b.py", "reason": "x"}]
        ops = p19_rt.build_file_operations(plan)
        self.assertEqual(ops[0]["operation"], "create-file")

    def test_build_file_operations_inspect(self) -> None:
        plan = [{"action": "inspect", "path": "c.py", "reason": "x"}]
        ops = p19_rt.build_file_operations(plan)
        self.assertEqual(ops[0]["operation"], "inspect")

    def test_build_verify_summary_has_required_keys(self) -> None:
        plan = [{"action": "modify", "path": "x.py"}]
        v = p19_rt.build_verify_summary(plan)
        for key in ("preview_targets", "verify_targets", "checks"):
            self.assertIn(key, v)

    def test_build_verify_summary_only_modify_and_create_targets(self) -> None:
        plan = [
            {"action": "modify", "path": "a.py"},
            {"action": "inspect", "path": "b.py"},
            {"action": "create", "path": "c.py"},
        ]
        v = p19_rt.build_verify_summary(plan)
        self.assertIn("a.py", v["preview_targets"])
        self.assertIn("c.py", v["preview_targets"])
        self.assertNotIn("b.py", v["preview_targets"])


# ─────────────────────────────────────────────────────────────────────────────
# elira_phase19 — DB persistence (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class Phase19PersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = p19_rt.DB_PATH
        p19_rt.DB_PATH = Path(self._tmpdir.name) / "p19.db"
        p19_rt.ensure_db()

    def tearDown(self) -> None:
        p19_rt.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_persist_and_list_run(self) -> None:
        run_id = p19_rt.persist(
            "add login", "code", ["auth.py"],
            [{"action": "modify", "path": "auth.py", "reason": "x"}],
            {"scope": "backend"},
            [{"operation": "preview-edit", "path": "auth.py", "status": "ready"}],
            {"preview_targets": ["auth.py"]},
        )
        self.assertIsNotNone(run_id)
        result = p19_rt.list_runs()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["goal"], "add login")

    def test_get_run_returns_parsed_data(self) -> None:
        plan = [{"action": "modify", "path": "app.py", "reason": "test"}]
        run_id = p19_rt.persist(
            "goal", "code", ["app.py"], plan, {"scope": "ui"},
            [], {"preview_targets": []},
        )
        data = p19_rt.get_run(run_id)
        self.assertIsInstance(data["plan"], list)
        self.assertIsInstance(data["selected_paths"], list)
        self.assertIn("app.py", data["selected_paths"])

    def test_get_run_not_found(self) -> None:
        data = p19_rt.get_run(9999)
        self.assertEqual(data["status"], "not_found")

    def test_multiple_runs_newest_first(self) -> None:
        for goal in ("first", "second", "third"):
            p19_rt.persist(goal, "code", [], [], {}, [], {})
        items = p19_rt.list_runs()["items"]
        self.assertEqual(items[0]["goal"], "third")

    def test_list_runs_limit(self) -> None:
        for i in range(5):
            p19_rt.persist(f"run {i}", "code", [], [], {}, [], {})
        result = p19_rt.list_runs(limit=2)
        self.assertLessEqual(len(result["items"]), 2)


# ─────────────────────────────────────────────────────────────────────────────
# elira_phase20 — helpers (pure)
# ─────────────────────────────────────────────────────────────────────────────

class Phase20HelpersTest(unittest.TestCase):
    def test_dumps_loads_round_trip(self) -> None:
        data = {"a": 1, "b": [2, 3]}
        self.assertEqual(p20_rt.loads(p20_rt.dumps(data)), data)

    def test_loads_none_or_empty_returns_none(self) -> None:
        self.assertIsNone(p20_rt.loads(None))

    def test_build_reasoning_scope_backend(self) -> None:
        r = p20_rt.build_reasoning("add api route", [], [])
        self.assertEqual(r["scope"], "backend")

    def test_build_reasoning_scope_ui(self) -> None:
        r = p20_rt.build_reasoning("add ui button", [], [])
        self.assertEqual(r["scope"], "ui")

    def test_build_reasoning_scope_multi_file_default(self) -> None:
        r = p20_rt.build_reasoning("refactor everything", [], [])
        self.assertEqual(r["scope"], "multi-file")

    def test_build_reasoning_has_required_keys(self) -> None:
        r = p20_rt.build_reasoning("goal", ["a.py"], ["b.py"])
        for key in ("scope", "goal_summary", "selected_paths", "advice"):
            self.assertIn(key, r)

    def test_build_planner_with_selected_paths(self) -> None:
        r = p20_rt.build_planner("fix", ["a.py", "b.py"], [])
        self.assertEqual(r["status"], "done")
        self.assertEqual(len(r["items"]), 2)
        self.assertTrue(all(item["action"] == "modify" for item in r["items"]))

    def test_build_planner_create_keyword(self) -> None:
        r = p20_rt.build_planner("create new component", [], [])
        actions = [item["action"] for item in r["items"]]
        self.assertIn("create", actions)

    def test_build_planner_api_keyword(self) -> None:
        r = p20_rt.build_planner("add api endpoint", [], [])
        paths = [item["path"] for item in r["items"]]
        self.assertTrue(any("route" in p.lower() for p in paths))

    def test_build_coder_preview_targets(self) -> None:
        planner = {
            "status": "done",
            "items": [
                {"action": "modify", "path": "a.py", "reason": "x"},
                {"action": "create", "path": "b.py", "reason": "x"},
                {"action": "inspect", "path": "c.py", "reason": "x"},
            ],
        }
        coder = p20_rt.build_coder(planner)
        self.assertIn("a.py", coder["preview_targets"])
        self.assertIn("b.py", coder["preview_targets"])
        self.assertNotIn("c.py", coder["preview_targets"])
        self.assertEqual(coder["status"], "ready")

    def test_build_reviewer_has_required_keys(self) -> None:
        planner = {"status": "done", "items": [{"action": "modify", "path": "a.py", "reason": "x"}]}
        coder = {"status": "ready", "preview_targets": ["a.py"], "operations": []}
        reviewer = p20_rt.build_reviewer(planner, coder)
        for key in ("status", "diff_targets", "history_targets", "notes"):
            self.assertIn(key, reviewer)

    def test_build_tester_has_verify_targets(self) -> None:
        coder = {"status": "ready", "preview_targets": ["x.py"], "operations": []}
        tester = p20_rt.build_tester(coder)
        self.assertIn("x.py", tester["verify_targets"])
        self.assertEqual(tester["status"], "ready")

    def test_build_execution_apply_recommended_when_targets(self) -> None:
        planner = {"status": "done", "items": []}
        coder = {"preview_targets": ["a.py"], "operations": []}
        reviewer = {"diff_targets": [], "history_targets": [], "notes": []}
        tester = {"verify_targets": ["a.py"], "checks": []}
        exe = p20_rt.build_execution(planner, coder, reviewer, tester)
        self.assertTrue(exe["apply_recommended"])
        self.assertTrue(exe["verify_recommended"])
        self.assertIn("flow", exe)

    def test_build_execution_not_recommended_when_no_targets(self) -> None:
        planner = {"status": "done", "items": []}
        coder = {"preview_targets": [], "operations": []}
        reviewer = {"diff_targets": [], "history_targets": [], "notes": []}
        tester = {"verify_targets": [], "checks": []}
        exe = p20_rt.build_execution(planner, coder, reviewer, tester)
        self.assertFalse(exe["apply_recommended"])
        self.assertFalse(exe["verify_recommended"])


# ─────────────────────────────────────────────────────────────────────────────
# elira_phase20 — DB persistence (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class Phase20PersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = p20_rt.DB_PATH
        p20_rt.DB_PATH = Path(self._tmpdir.name) / "p20.db"
        p20_rt.ensure_db()

    def tearDown(self) -> None:
        p20_rt.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def _persist_one(self, goal="test goal"):
        return p20_rt.persist(
            goal, ["a.py"],
            {"scope": "backend"},
            {"status": "done", "items": []},
            {"status": "ready", "preview_targets": [], "operations": []},
            {"status": "ready", "diff_targets": [], "history_targets": [], "notes": []},
            {"status": "ready", "verify_targets": [], "checks": []},
            {"status": "ready", "flow": [], "preview_targets": [],
             "apply_recommended": False, "verify_recommended": False},
        )

    def test_persist_and_list(self) -> None:
        self._persist_one("multi-agent refactor")
        result = p20_rt.list_runs()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["goal"], "multi-agent refactor")

    def test_get_run_parsed(self) -> None:
        run_id = self._persist_one()
        data = p20_rt.get_run(run_id)
        for key in ("reasoning", "planner", "coder", "reviewer", "tester", "execution"):
            self.assertIn(key, data)
        self.assertIsInstance(data["selected_paths"], list)

    def test_get_run_not_found(self) -> None:
        self.assertEqual(p20_rt.get_run(9999)["status"], "not_found")

    def test_multiple_runs_newest_first(self) -> None:
        self._persist_one("first")
        self._persist_one("second")
        items = p20_rt.list_runs()["items"]
        self.assertEqual(items[0]["goal"], "second")

    def test_list_runs_limit(self) -> None:
        for _ in range(4):
            self._persist_one()
        self.assertLessEqual(len(p20_rt.list_runs(limit=2)["items"]), 2)


if __name__ == "__main__":
    unittest.main()
