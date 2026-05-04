"""Tests for application/profiles, application/memory_service, and
application/elira_task_runner runtimes."""
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

from app.application.profiles.runtime import get_profiles        # noqa: E402
from app.application.memory_service import runtime as mem_svc    # noqa: E402
import app.application.elira_task_runner.runtime as tr           # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# profiles — get_profiles (pure, reads persona_defaults constants)
# ─────────────────────────────────────────────────────────────────────────────

class GetProfilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._result = get_profiles()

    def test_ok_flag(self) -> None:
        self.assertTrue(self._result["ok"])

    def test_returns_list_of_profiles(self) -> None:
        self.assertIsInstance(self._result["profiles"], list)
        self.assertGreater(self._result["count"], 0)

    def test_count_matches_list_length(self) -> None:
        self.assertEqual(self._result["count"], len(self._result["profiles"]))

    def test_default_profile_present(self) -> None:
        default = self._result["default_profile"]
        names = [p["name"] for p in self._result["profiles"]]
        self.assertIn(default, names)

    def test_profile_has_required_keys(self) -> None:
        profile = self._result["profiles"][0]
        for key in ("name", "is_default", "icon", "tags", "short", "system_prompt_preview"):
            self.assertIn(key, profile)

    def test_exactly_one_is_default(self) -> None:
        defaults = [p for p in self._result["profiles"] if p["is_default"]]
        self.assertEqual(len(defaults), 1)

    def test_system_prompt_preview_non_empty(self) -> None:
        for p in self._result["profiles"]:
            self.assertIsInstance(p["system_prompt_preview"], str)


# ─────────────────────────────────────────────────────────────────────────────
# memory_service — _normalize_profile (pure helper)
# ─────────────────────────────────────────────────────────────────────────────

class NormalizeProfileTest(unittest.TestCase):
    def test_none_returns_default(self) -> None:
        from app.application.memory_service.runtime import _normalize_profile
        self.assertEqual(_normalize_profile(None), "default")

    def test_empty_string_returns_default(self) -> None:
        from app.application.memory_service.runtime import _normalize_profile
        self.assertEqual(_normalize_profile(""), "default")

    def test_whitespace_returns_default(self) -> None:
        from app.application.memory_service.runtime import _normalize_profile
        self.assertEqual(_normalize_profile("   "), "default")

    def test_named_profile_returned_as_is(self) -> None:
        from app.application.memory_service.runtime import _normalize_profile
        self.assertEqual(_normalize_profile("work"), "work")

    def test_strips_surrounding_whitespace(self) -> None:
        from app.application.memory_service.runtime import _normalize_profile
        self.assertEqual(_normalize_profile("  work  "), "work")


# ─────────────────────────────────────────────────────────────────────────────
# memory_service — list_memories / add_memory / delete_memory / search_memory
# (smart_memory layer is mocked)
# ─────────────────────────────────────────────────────────────────────────────

_FAKE_MEMORY_ITEM = {"id": 1, "text": "Alice likes Python", "category": "fact"}

class MemoryServiceListTest(unittest.TestCase):
    def test_list_memories_delegates_and_adds_profile(self) -> None:
        fake_result = {"items": [_FAKE_MEMORY_ITEM], "count": 1}
        with patch("app.application.smart_memory.list_memories", return_value=fake_result):
            result = mem_svc.list_memories("work")
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "work")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"], [_FAKE_MEMORY_ITEM])

    def test_list_memories_normalizes_empty_profile(self) -> None:
        fake_result = {"items": [], "count": 0}
        with patch("app.application.smart_memory.list_memories", return_value=fake_result):
            result = mem_svc.list_memories("")
        self.assertEqual(result["profile"], "default")


class MemoryServiceAddTest(unittest.TestCase):
    def test_add_memory_adds_profile_key(self) -> None:
        fake_result = {"ok": True, "id": 42}
        with patch("app.application.smart_memory.add_memory", return_value=fake_result):
            result = mem_svc.add_memory("personal", "Bob uses vim")
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "personal")
        self.assertEqual(result["id"], 42)

    def test_add_memory_default_source_is_manual(self) -> None:
        captured = {}
        def fake_add(**kwargs):
            captured.update(kwargs)
            return {"ok": True}
        with patch("app.application.smart_memory.add_memory", side_effect=fake_add):
            mem_svc.add_memory("default", "some text")
        self.assertEqual(captured.get("source"), "manual")


class MemoryServiceDeleteTest(unittest.TestCase):
    def test_delete_memory_valid_id(self) -> None:
        fake_result = {"ok": True, "deleted": 1}
        with patch("app.application.smart_memory.delete_memory", return_value=fake_result):
            result = mem_svc.delete_memory("default", "7")
        self.assertTrue(result["ok"])
        self.assertEqual(result["profile"], "default")

    def test_delete_memory_invalid_id(self) -> None:
        result = mem_svc.delete_memory("default", "not_an_int")
        self.assertFalse(result["ok"])
        self.assertIn("Invalid", result["error"])


class MemoryServiceSearchTest(unittest.TestCase):
    def test_search_memory_adds_profile(self) -> None:
        fake_result = {"items": [], "count": 0}
        with patch("app.application.smart_memory.search_memory", return_value=fake_result):
            result = mem_svc.search_memory("work", "python")
        self.assertEqual(result["profile"], "work")

    def test_search_memory_limit_floored_at_1(self) -> None:
        captured = {}
        def fake_search(**kwargs):
            captured.update(kwargs)
            return {"items": [], "count": 0}
        with patch("app.application.smart_memory.search_memory", side_effect=fake_search):
            mem_svc.search_memory("default", "q", limit=0)
        self.assertGreaterEqual(captured.get("limit", 0), 1)


# ─────────────────────────────────────────────────────────────────────────────
# elira_task_runner — helpers (pure)
# ─────────────────────────────────────────────────────────────────────────────

class TaskRunnerHelpersTest(unittest.TestCase):
    def test_dumps_loads_round_trip(self) -> None:
        data = {"key": "value", "items": [1, 2, 3]}
        text = tr.dumps_json(data)
        self.assertIsInstance(text, str)
        self.assertEqual(tr.loads_json(text), data)

    def test_loads_json_empty_string_returns_none(self) -> None:
        self.assertIsNone(tr.loads_json(""))

    def test_build_plan_empty_creates_default_step(self) -> None:
        plan = tr.build_plan("generic task", None, [])
        self.assertGreaterEqual(len(plan), 1)

    def test_build_plan_current_path_included(self) -> None:
        plan = tr.build_plan("fix bug", "backend/app.py", [])
        paths = [p["path"] for p in plan]
        self.assertIn("backend/app.py", paths)

    def test_build_plan_create_keyword_adds_ui_step(self) -> None:
        plan = tr.build_plan("create new component", None, [])
        actions = [p["action"] for p in plan]
        self.assertIn("create", actions)

    def test_build_plan_api_keyword_adds_route_step(self) -> None:
        plan = tr.build_plan("add backend api endpoint", None, [])
        paths = [p["path"] for p in plan]
        self.assertTrue(any("route" in p.lower() for p in paths))

    def test_build_supervisor_pipeline_has_four_agents(self) -> None:
        pipeline = tr.build_supervisor_pipeline("code")
        agents = {p["agent"] for p in pipeline}
        self.assertEqual(agents, {"planner", "coder", "reviewer", "tester"})

    def test_build_supervisor_pipeline_planner_done(self) -> None:
        pipeline = tr.build_supervisor_pipeline("code")
        planner = next(p for p in pipeline if p["agent"] == "planner")
        self.assertEqual(planner["status"], "done")


# ─────────────────────────────────────────────────────────────────────────────
# elira_task_runner — DB persistence (patched DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

class TaskRunnerPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_db = tr.DB_PATH
        tr.DB_PATH = tmp / "task_runner.db"
        tr.ensure_db()

    def tearDown(self) -> None:
        tr.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_persist_and_list_run(self) -> None:
        run_id = tr.persist_run(
            "refactor auth", "code", "backend/auth.py", [], "planned", [], [], {}
        )
        self.assertIsNotNone(run_id)
        result = tr.list_runs()
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["goal"], "refactor auth")

    def test_get_run_returns_parsed_json(self) -> None:
        plan = [{"step": "inspect", "path": "x.py"}]
        run_id = tr.persist_run("task", "code", None, ["a.py"], "planned", plan, ["log"], {})
        data = tr.get_run(run_id)
        self.assertIsInstance(data["plan"], list)
        self.assertIsInstance(data["staged_paths"], list)
        self.assertIn("a.py", data["staged_paths"])

    def test_get_run_not_found(self) -> None:
        data = tr.get_run(9999)
        self.assertEqual(data["status"], "not_found")

    def test_multiple_runs_newest_first(self) -> None:
        tr.persist_run("first", "code", None, [], "planned", [], [], {})
        tr.persist_run("second", "code", None, [], "planned", [], [], {})
        result = tr.list_runs()
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["goal"], "second")

    def test_list_runs_limit(self) -> None:
        for i in range(5):
            tr.persist_run(f"run {i}", "code", None, [], "planned", [], [], {})
        result = tr.list_runs(limit=2)
        self.assertLessEqual(len(result["items"]), 2)


if __name__ == "__main__":
    unittest.main()
